[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$StateFile,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9a-fA-F]{64}$")][string]$StateSha256
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "integrated_state.ps1")
$originalLocation = Get-Location
$expectedRoute = "LIVING_ORGANISM_TEST_LIFECYCLE"
$expectedCoreHead = "0045_sbg_governance_formation"
$environmentNames = @(
    "INTEVIA_DATABASE_ENGINE",
    "INTEVIA_S015_TEST_ROUTE",
    "DJANGO_SETTINGS_MODULE",
    "INTEVIA_POSTGRES_TEST_DB",
    "VERIFICATION_RUN_NONCE",
    "VERIFICATION_DB_RECEIPT",
    "VERIFICATION_ISOLATION_JSON",
    "VERIFICATION_EXPECTED_CORE_HEAD",
    "VERIFICATION_STEP",
    "VERIFICATION_TEMPLATE_MODE",
    "VERIFICATION_TEMPLATE_STORE",
    "VERIFICATION_TEMPLATE_CLEANUP",
    "PYTHONPATH",
    "PYTHONUNBUFFERED"
) + @(Get-ChildItem Env: | Where-Object { $_.Name -like "VERIFICATION_*" } | ForEach-Object { $_.Name })
$environmentBefore = @(Get-ChildItem Env:)
$environmentState = @{}
foreach ($name in $environmentNames | Sort-Object -Unique) {
    $existing = $environmentBefore | Where-Object { $_.Name -ieq $name } | Select-Object -First 1
    $environmentState[$name] = [PSCustomObject]@{
        Name = if ($null -ne $existing) { $existing.Name } else { $name }
        Present = $null -ne $existing
        Value = if ($null -ne $existing) { [string]$existing.Value } else { $null }
    }
}

$failureReasons = [System.Collections.Generic.List[string]]::new()
$script:printedSummary = [ordered]@{
    tests_run = $null
    failures = $null
    errors = $null
    skipped = $null
    run_lines = 0
    result_seen = $false
    status = $null
}
$script:teardownLines = 0
$sourceKind = $null
$head = $null
$mergeCommit = $null
$stateId = $null
$stateFilePath = $null
$stateHash = $null
$bindingHash = $null
$scriptHash = $null
$runDirectory = $null
$runNonce = $null
$testDatabase = $null
$receiptPath = $null
$isolationPath = $null
$initialBinding = $null
$counts = [ordered]@{ tests_run = $null; failures = $null; errors = $null; skipped = $null }
$verdict = "FAIL"
$isolationVerdict = "NOT ESTABLISHED"
$cp0Met = $false
$ordinary = $null
$teardown = [ordered]@{
    database = $null
    created = 0
    destroyed = 0
    created_oid = $null
    destroyed_oid = $null
    oids_match = $false
    confirmed_absent = $false
    database_count = 0
    databases = @()
    failure_reasons = @()
}

function Add-Failure {
    param([Parameter(Mandatory = $true)][string]$Message)

    if (-not [string]::IsNullOrWhiteSpace($Message)) {
        $failureReasons.Add($Message)
    }
}

function Protect-OutputText {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)

    $protected = $Text
    foreach ($name in @("INTEVIA_POSTGRES_PASSWORD", "DJANGO_SECRET_KEY")) {
        $secret = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrEmpty($secret)) {
            $protected = $protected.Replace($secret, "[REDACTED]")
        }
    }
    return $protected
}

function Get-RunDatabaseReconciliation {
    # P1-F5: ^test_intevia_living_organism_tt1_witness_[0-9a-f]+$ is the only naming exemption.
    param(
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$RunNonce,
        [Parameter(Mandatory = $true)][string]$PrimaryDatabase,
        [Parameter(Mandatory = $true)][int]$TeardownLines,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$IsolationVerdict
    )

    $failureReasons = [System.Collections.Generic.List[string]]::new()
    $receiptRecords = @()
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        $failureReasons.Add("database receipt file is absent")
    }
    else {
        $receiptRecords = @(Get-Content -LiteralPath $ReceiptPath -ErrorAction Stop |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
    }

    $logLines = @()
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        $failureReasons.Add("test run log is absent")
    }
    else {
        $logLines = @(Get-Content -LiteralPath $LogPath -ErrorAction Stop)
    }

    $creationMarkers = @($logLines | Where-Object { $_ -match "Creating test database for alias" })
    $destructionMarkers = @($logLines | Where-Object { $_ -match "Destroying test database for alias" })
    $createdLogEvents = @()
    $destroyedLogEvents = @()
    foreach ($line in $logLines) {
        if ($line -match "Creating test database for alias '([^']+)' \('([^']+)'\)") {
            $createdLogEvents += [PSCustomObject]@{ alias = $Matches[1]; name = $Matches[2] }
        }
        elseif ($line -match "Creating test database for alias") {
            $failureReasons.Add("a test database creation log line could not be parsed")
        }
        if ($line -match "Destroying test database for alias '([^']+)' \('([^']+)'\)") {
            $destroyedLogEvents += [PSCustomObject]@{ alias = $Matches[1]; name = $Matches[2] }
        }
        elseif ($line -match "Destroying test database for alias") {
            $failureReasons.Add("a test database teardown log line could not be parsed")
        }
    }
    if ($creationMarkers.Count -ne $createdLogEvents.Count) {
        $failureReasons.Add("not every test database creation log line could be parsed")
    }
    if ($destructionMarkers.Count -ne $destroyedLogEvents.Count) {
        $failureReasons.Add("not every test database teardown log line could be parsed")
    }

    $createdRecords = @($receiptRecords | Where-Object {
        $_.event -eq "created" -and $_.run_nonce -eq $RunNonce
    })
    $destroyedRecords = @($receiptRecords | Where-Object {
        $_.event -eq "destroyed" -and $_.run_nonce -eq $RunNonce
    })
    $testCreatedWitnessPattern = '^test_intevia_living_organism_tt1_witness_[0-9a-f]+$'
    $regularCreatedRecords = @($createdRecords | Where-Object {
        ([string]$_.name) -ceq $PrimaryDatabase -or
        ([string]$_.name) -cnotmatch $testCreatedWitnessPattern
    })
    $regularCreatedLogEvents = @($createdLogEvents | Where-Object {
        $_.name -ceq $PrimaryDatabase -or $_.name -cnotmatch $testCreatedWitnessPattern
    })
    $regularDestroyedLogEvents = @($destroyedLogEvents | Where-Object {
        $_.name -ceq $PrimaryDatabase -or $_.name -cnotmatch $testCreatedWitnessPattern
    })
    $witnessDestroyLogEvents = @($destroyedLogEvents | Where-Object {
        $_.name -cne $PrimaryDatabase -and $_.name -cmatch $testCreatedWitnessPattern
    })
    $regularTeardownLines = $TeardownLines - $witnessDestroyLogEvents.Count
    $createdNames = @($createdRecords | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
    $regularCreatedNames = @($regularCreatedRecords | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
    $createdLogNames = @($regularCreatedLogEvents | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
    $destroyedLogNames = @($regularDestroyedLogEvents | ForEach-Object { [string]$_.name } | Sort-Object -Unique)

    if ($createdRecords.Count -eq 0) {
        $failureReasons.Add("no database creation receipt belongs to this run")
    }
    if ($createdNames.Count -ne $createdRecords.Count) {
        $failureReasons.Add("database creation receipts contain duplicate or empty names")
    }
    if ($createdRecords.Count -lt 1 -or
        $regularCreatedLogEvents.Count -ne $regularCreatedRecords.Count) {
        $failureReasons.Add("database creation log lines do not match owned non-witness creation receipts")
    }
    if ($regularDestroyedLogEvents.Count -ne $regularCreatedRecords.Count -or
        $regularTeardownLines -ne $regularCreatedRecords.Count) {
        $failureReasons.Add("streamed database teardown lines do not match the owned database count")
    }
    if (($regularCreatedNames -join "`n") -cne ($createdLogNames -join "`n")) {
        $failureReasons.Add("a database touched by this run is absent from its ownership receipts")
    }
    if (($regularCreatedNames -join "`n") -cne ($destroyedLogNames -join "`n")) {
        $failureReasons.Add("database creation and teardown log names do not match")
    }
    $primaryCreateLogs = @($createdLogEvents | Where-Object {
        $_.alias -ceq "default" -and $_.name -ceq $PrimaryDatabase
    })
    $primaryDestroyLogs = @($destroyedLogEvents | Where-Object {
        $_.alias -ceq "default" -and $_.name -ceq $PrimaryDatabase
    })
    if ($primaryCreateLogs.Count -ne 1 -or $primaryDestroyLogs.Count -ne 1) {
        $failureReasons.Add("the primary test database was not created and torn down exactly once")
    }

    $secondaryPattern = "^" + [regex]::Escape($PrimaryDatabase) + "_[a-z0-9_]+$"
    $databaseNames = @()
    $databaseNames += @($createdRecords | ForEach-Object { [string]$_.name })
    $databaseNames += @($destroyedRecords | ForEach-Object { [string]$_.name })
    $databaseNames += @($createdLogEvents | ForEach-Object { [string]$_.name })
    $databaseNames += @($destroyedLogEvents | ForEach-Object { [string]$_.name })
    $databaseNames = @($databaseNames | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
    if ($databaseNames.Count -eq 0) {
        $failureReasons.Add("the run did not account for any database")
    }

    $databaseReports = @()
    foreach ($name in $databaseNames) {
        $databaseFailures = [System.Collections.Generic.List[string]]::new()
        $isTestCreatedWitness = $name -cne $PrimaryDatabase -and
            $name -cmatch $testCreatedWitnessPattern
        $databaseCreated = @($createdRecords | Where-Object { [string]$_.name -ceq $name })
        $databaseDestroyed = @($destroyedRecords | Where-Object { [string]$_.name -ceq $name })
        $databaseCreateLogs = @($createdLogEvents | Where-Object { $_.name -ceq $name })
        $databaseDestroyLogs = @($destroyedLogEvents | Where-Object { $_.name -ceq $name })

        if (-not $isTestCreatedWitness) {
            if ($name -ceq $PrimaryDatabase) {
                if ($databaseCreateLogs.Count -ne 1 -or $databaseCreateLogs[0].alias -cne "default" -or
                    $databaseDestroyLogs.Count -ne 1 -or $databaseDestroyLogs[0].alias -cne "default") {
                    $databaseFailures.Add("primary log events do not identify the default alias")
                }
            }
            elseif ($name -cnotmatch $secondaryPattern) {
                $databaseFailures.Add("secondary database name does not belong to the primary")
            }
        }
        if ($databaseCreated.Count -ne 1) {
            $databaseFailures.Add("database does not have exactly one owned creation receipt")
        }
        if ($databaseDestroyed.Count -ne 1) {
            $databaseFailures.Add("database does not have exactly one owned destruction receipt")
        }
        if (-not $isTestCreatedWitness) {
            if ($databaseCreateLogs.Count -ne 1 -or $databaseDestroyLogs.Count -ne 1) {
                $databaseFailures.Add("database does not have exactly one creation and teardown log pair")
            }
            elseif ($databaseCreateLogs[0].alias -cne $databaseDestroyLogs[0].alias) {
                $databaseFailures.Add("database creation and teardown aliases do not match")
            }
        }

        $createdOid = $null
        $destroyedOid = $null
        $oidsMatch = $false
        if ($databaseCreated.Count -eq 1 -and $databaseDestroyed.Count -eq 1) {
            $createdOid = $databaseCreated[0].oid
            $destroyedOid = $databaseDestroyed[0].oid
            if ($null -eq $createdOid -or [long]$createdOid -le 0) {
                $databaseFailures.Add("database creation receipt has no valid OID")
            }
            if ($null -eq $destroyedOid -or [long]$destroyedOid -le 0) {
                $databaseFailures.Add("database destruction receipt has no valid OID")
            }
            $oidsMatch = $null -ne $createdOid -and $null -ne $destroyedOid -and
                [string]$createdOid -ceq [string]$destroyedOid
            if (-not $oidsMatch) {
                $databaseFailures.Add("database creation and destruction OIDs do not match")
            }
            if ($databaseDestroyed[0].confirmed_absent -ne $true) {
                $databaseFailures.Add("database destruction receipt does not confirm absence")
            }
        }
        $confirmedAbsent = $databaseDestroyed.Count -eq 1 -and
            $databaseDestroyed[0].confirmed_absent -eq $true
        foreach ($problem in $databaseFailures) {
            $failureReasons.Add("database $name`: $problem")
        }
        $databaseIsolation = if ($name -ceq $PrimaryDatabase) {
            [ordered]@{ status = "checked"; instrument_verdict = $IsolationVerdict }
        }
        else {
            [ordered]@{ status = "unavailable" }
        }
        $databaseReport = [ordered]@{
            name = $name
            created_oid = $createdOid
            destroyed_oid = $destroyedOid
            creation_receipts = @($databaseCreated)
            destruction_receipts = @($databaseDestroyed)
            result = if ($databaseFailures.Count -eq 0) { "RECONCILED" } else { "REFUSED" }
            oids_match = $oidsMatch
            confirmed_absent = $confirmedAbsent
            isolation = $databaseIsolation
        }
        if ($isTestCreatedWitness) {
            $databaseReport["kind"] = "test_created_witness"
        }
        $databaseReports += [PSCustomObject]$databaseReport
    }

    if (@($destroyedRecords | Where-Object {
        ([string]$_.name) -notin $createdNames
    }).Count -gt 0) {
        $failureReasons.Add("a database destruction receipt has no matching owned creation receipt")
    }

    $primaryCreated = @($createdRecords | Where-Object { ([string]$_.name) -ceq $PrimaryDatabase })
    $primaryDestroyed = @($destroyedRecords | Where-Object { ([string]$_.name) -ceq $PrimaryDatabase })
    $badOidRows = @($databaseReports | Where-Object { $_.oids_match -ne $true })
    $badAbsentRows = @($databaseReports | Where-Object { $_.confirmed_absent -ne $true })
    return [PSCustomObject][ordered]@{
        database = $PrimaryDatabase
        created = $createdRecords.Count
        destroyed = $destroyedRecords.Count
        created_oid = if ($primaryCreated.Count -eq 1) { $primaryCreated[0].oid } else { $null }
        destroyed_oid = if ($primaryDestroyed.Count -eq 1) { $primaryDestroyed[0].oid } else { $null }
        oids_match = $databaseReports.Count -gt 0 -and $badOidRows.Count -eq 0
        confirmed_absent = $databaseReports.Count -gt 0 -and $badAbsentRows.Count -eq 0
        database_count = $databaseReports.Count
        databases = @($databaseReports)
        failure_reasons = @($failureReasons.ToArray())
    }
}

function Get-Sha256Bytes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return -join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SourceBinding {
    param(
        [Parameter(Mandatory = $true)][string]$AuthorizedStateFile,
        [Parameter(Mandatory = $true)][string]$AuthorizedStateSha256
    )

    $authorizedState = Get-IntegratedState -StateFile $AuthorizedStateFile -StateSha256 $AuthorizedStateSha256
    $currentHead = $authorizedState.Head
    $integratedMerge = $authorizedState.MergeCommit
    $diffHash = $null
    $tree = $null
    $parent = $null

    if ($authorizedState.SourceKind -eq "CANDIDATE") {
        if ($authorizedState.ChangedPaths.Count -eq 0) {
            throw "verifier candidate must contain at least one authorized change"
        }
        $diffPath = [System.IO.Path]::GetTempFileName()
        try {
            & git diff --binary $authorizedState.BaseCommit "--output=$diffPath"
            if ($LASTEXITCODE -ne 0) {
                throw "candidate diff calculation failed"
            }
            $diffHash = (Get-FileHash -LiteralPath $diffPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        finally {
            if (Test-Path -LiteralPath $diffPath) {
                Remove-Item -LiteralPath $diffPath -Force
            }
        }

        $untracked = @(& git ls-files --others --exclude-standard)
        if ($LASTEXITCODE -ne 0) {
            throw "candidate untracked-file inventory failed"
        }
        $rows = @()
        foreach ($path in $untracked) {
            $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            $rows += "$hash`:$path"
        }
        $rows = @($rows | Sort-Object)
        $parts = @(
            "state_sha256=$($authorizedState.StateSha256)",
            "baseline=$($authorizedState.BaseCommit)",
            "diff=$diffHash"
        ) + $rows
        $binding = Get-Sha256Bytes -Bytes ([System.Text.UTF8Encoding]::new($false).GetBytes(($parts -join "`n")))
    }
    else {
        $tree = (& git rev-parse "${currentHead}^{tree}").Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "committed source tree lookup failed"
        }
        $parent = $authorizedState.BaseCommit
        $parts = @(
            "state_sha256=$($authorizedState.StateSha256)",
            "commit=$currentHead",
            "tree=$tree",
            "parent=$parent"
        )
        $binding = Get-Sha256Bytes -Bytes ([System.Text.UTF8Encoding]::new($false).GetBytes(($parts -join "`n")))
    }

    return [PSCustomObject]@{
        SourceKind = $authorizedState.SourceKind
        Head = $currentHead
        MergeCommit = $integratedMerge
        Binding = $binding
        DiffHash = $diffHash
        Tree = $tree
        Parent = $parent
        StateId = $authorizedState.StateId
        StateFile = $authorizedState.StateFile
        StateSha256 = $authorizedState.StateSha256
        EvidenceRoot = $authorizedState.EvidenceRoot
        DatabaseTag = $authorizedState.DatabaseTag
    }
}

function Get-NextVerificationRunNumber {
    param([Parameter(Mandatory = $true)][string]$VerificationRoot)

    $existing = @(Get-ChildItem -LiteralPath $VerificationRoot -Directory -Filter "run_*" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.Name -match "^run_(\d+)$") { [int]$Matches[1] } })
    $number = 1
    while ($existing -contains $number) { $number++ }
    return $number
}

function Set-PrintedSummaryFromLine {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)

    if ($Line -match "^Ran\s+(\d+)\s+tests?\s+in\b") {
        $script:printedSummary.tests_run = [int]$Matches[1]
        $script:printedSummary.run_lines++
    }
    if ($Line -match "^OK(?:\s+\((.*)\))?$") {
        $script:printedSummary.status = "PASS"
        $script:printedSummary.result_seen = $true
        $script:printedSummary.failures = 0
        $script:printedSummary.errors = 0
        $script:printedSummary.skipped = 0
        if ($Matches[1] -match "(?:^|,\s*)skipped=(\d+)") {
            $script:printedSummary.skipped = [int]$Matches[1]
        }
    }
    elseif ($Line -match "^FAILED(?:\s+\((.*)\))?$") {
        $script:printedSummary.status = "FAIL"
        $script:printedSummary.result_seen = $true
        foreach ($key in @("failures", "errors", "skipped")) {
            $script:printedSummary[$key] = 0
            if ($Matches[1] -match "(?:^|,\s*)$key=(\d+)") {
                $script:printedSummary[$key] = [int]$Matches[1]
            }
        }
    }
}

function Invoke-PythonAndCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    Write-Host "COMMAND=$Label"
    Add-Content -LiteralPath $LogPath -Value "COMMAND=$Label" -Encoding utf8
    & python @Arguments 2>&1 | ForEach-Object {
        $lineText = Protect-OutputText -Text ([string]$_)
        Add-Content -LiteralPath $LogPath -Value $lineText -Encoding utf8
        Write-Host $lineText
        Set-PrintedSummaryFromLine -Line $lineText
        if ($lineText -match "Destroying test database for alias") {
            $script:teardownLines++
        }
    }
    $status = $LASTEXITCODE
    return [int]$status
}

try {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    Set-Location -LiteralPath $repoRoot

    $parseTokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) {
        throw "PowerShell syntax preflight failed: $($parseErrors[0].Message)"
    }

    $initialBinding = Get-SourceBinding -AuthorizedStateFile $StateFile -AuthorizedStateSha256 $StateSha256
    $sourceKind = $initialBinding.SourceKind
    $head = $initialBinding.Head
    $mergeCommit = $initialBinding.MergeCommit
    $stateId = $initialBinding.StateId
    $stateFilePath = $initialBinding.StateFile
    $stateHash = $initialBinding.StateSha256
    $bindingHash = $initialBinding.Binding
    $routes = Get-IntegratedEvidenceRoutes -AuthorizedState $initialBinding -HomeDirectory $HOME
    $scriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "STATE_FILE=$stateFilePath"
    Write-Output "STATE_ID=$stateId"
    Write-Output "STATE_SHA256=$stateHash"
    Write-Output "SOURCE_KIND=$sourceKind"
    Write-Output "HEAD=$head"
    Write-Output "MERGE_COMMIT=$mergeCommit"
    Write-Output "BINDING_SHA256=$bindingHash"
    Write-Output "SCRIPT_SHA256=$scriptHash"
    Write-Output "EVIDENCE_ROOT=$($initialBinding.EvidenceRoot)"
    Write-Output "DATABASE_TAG=$($initialBinding.DatabaseTag)"

    $inheritedVerification = @(Get-ChildItem Env: | Where-Object { $_.Name -like "VERIFICATION_*" })
    if ($inheritedVerification.Count -gt 0) {
        $names = @($inheritedVerification | ForEach-Object { $_.Name } | Sort-Object -Unique)
        throw "inherited VERIFICATION_* settings are refused: $($names -join ',')"
    }
    if ($env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected administrative database is refused"
    }
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name is required"
        }
    }

    $verificationRoot = $routes.VerificationRoot
    New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
    $runNumber = Get-NextVerificationRunNumber -VerificationRoot $verificationRoot
    $runDirectory = Join-Path $verificationRoot ("run_{0}" -f $runNumber)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "verification run directory already exists; refusing reuse"
    }
    $testDatabase = "$($routes.VerificationDatabasePrefix)$runNumber"
    Write-Output "VERIFICATION_ROOT=$verificationRoot"
    Write-Output "VERIFICATION_DATABASE=$testDatabase"
    if ($testDatabase.Length -gt 63 -or $testDatabase -in @("intevia", "intevia_dev", $env:INTEVIA_POSTGRES_DB)) {
        throw "protected or overlength test database name refused"
    }
    New-Item -ItemType Directory -Path $runDirectory | Out-Null
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "integrated-fresh-suite.log"
    foreach ($line in @(
        "STATE_FILE=$stateFilePath"
        "STATE_ID=$stateId"
        "STATE_SHA256=$stateHash"
        "SOURCE_KIND=$sourceKind"
        "HEAD=$head"
        "MERGE_COMMIT=$mergeCommit"
        "BINDING_SHA256=$bindingHash"
    )) {
        Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
    }
    Write-Output "RUN_DIRECTORY=$runDirectory"

    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_POSTGRES_TEST_DB = $testDatabase
    $env:INTEVIA_S015_TEST_ROUTE = $expectedRoute
    $env:VERIFICATION_RUN_NONCE = $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = $expectedCoreHead
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = "fresh"
    $env:VERIFICATION_TEMPLATE_STORE = Join-Path $runDirectory "tooling-template-store"
    $env:VERIFICATION_TEMPLATE_CLEANUP = "0"
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    $env:PYTHONUNBUFFERED = "1"
    $pythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $pythonPath += $env:PYTHONPATH
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $pythonPath)

    Write-Output "TEST_DATABASE=$testDatabase"
    Write-Output "EXPECTED_CORE_HEAD=$expectedCoreHead"
    Write-Output "DATABASE_CREDENTIALS=present (values withheld)"
    $suite = @(
        "tests.test_s015_application_authority",
        "tests.test_s015_application_service",
        "tests.test_s015_application_gateway",
        "tests.test_s015_application_decision",
        "tests.test_s015_application_reads",
        "tests.test_s015_application_schema",
        "tests.test_s015_authority",
        "tests.test_s015_role_command",
        "tests.test_s015_membership_command",
        "tests.test_s015_membership_service",
        "tests.test_s015_models",
        "tests.test_s015_migrations",
        "tests.test_s015_postgresql_guardians",
        "tests.test_s015_packet_guardians",
        "tests.test_s015_register_cleanup",
        "tests.test_s015_0021_positive_controls",
        "tests.test_s015_0021_negative_direct_sql",
        "tests.test_s015_0022_contract",
        "tests.test_s015_0024_commitment_preimages",
        "tests.test_s015_test_harness",
        "tests.test_tt1_identity",
        "tests.test_tt1_ownership",
        "tests.test_tt1_template_lifecycle",
        "tests.test_sb1_founding_terms_phases",
        "tests.test_sb2_birthing",
        "tests.test_p1_registration_service",
        "tests.test_p1_identity_guardian",
        "tests.test_p1_migration_restore",
        "tests.test_s007_authentication_shell",
        "tests.test_s007_identity_foundation",
        "tests.test_s007_postgresql",
        "tests.test_s007_provision_first_human",
        "tests.test_s010_direct_self_registration",
        "tests.test_s010_direct_self_registration_postgresql",
        "tests.test_education_course_migrations",
        "tests.test_event_attendance_migrations",
        "tests.test_event_registration_postgresql",
        "tests.test_event_resource_relationship_migrations",
        "tests.test_profile_effect_migrations",
        "tests.test_profile_effect_postgresql",
        "tests.test_s007_identity_migrations",
        "tests.test_service_activity_migrations",
        "tests.test_service_activity_models",
        "tests.test_care_foundation",
        "tests.test_contribution_archive",
        "tests.test_contribution_authority",
        "tests.test_contribution_correction",
        "tests.test_contribution_models",
        "tests.test_contribution_postgresql",
        "tests.test_contribution_privacy",
        "tests.test_contribution_service",
        "tests.test_education_course_models",
        "tests.test_education_course_postgresql",
        "tests.test_education_course_readback",
        "tests.test_education_course_service",
        "tests.test_event_attendance_foundation",
        "tests.test_event_attendance_postgresql",
        "tests.test_event_registration_foundation",
        "tests.test_event_resource_relationship_models",
        "tests.test_event_resource_relationship_policy",
        "tests.test_event_resource_relationship_postgresql",
        "tests.test_event_resource_relationship_readback",
        "tests.test_event_resource_relationship_service",
        "tests.test_events_authority",
        "tests.test_events_evidence",
        "tests.test_events_lifecycle",
        "tests.test_events_models",
        "tests.test_events_observation",
        "tests.test_library_exact_version_contract",
        "tests.test_library_exact_version_policy",
        "tests.test_library_exact_version_postgresql",
        "tests.test_library_exact_version_transaction_boundary",
        "tests.test_library_foundation",
        "tests.test_library_postgresql",
        "tests.test_observation_integration",
        "tests.test_profile_effect_models",
        "tests.test_profile_effect_service",
        "tests.test_s009_personal_event_home",
        "tests.test_service_activity_postgresql",
        "tests.test_service_activity_qualification",
        "tests.test_service_activity_readback",
        "tests.test_service_activity_service",
        "tests.test_service_delivery_evidence",
        "tests.test_service_event_association",
        "tests.test_service_library_association",
        "tests.test_service_lifecycle",
        "tests.test_service_models",
        "tests.test_service_postgresql",
        "tests.test_service_versions",
        "tests.test_sbg_governance"
    )

    $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
    $checkCode = Invoke-PythonAndCapture -Label "Django system check" -LogPath $logPath `
        -Arguments @("manage.py", "check", "--settings=intevia.test_settings")
    if ($checkCode -ne 0) {
        Add-Failure "Django system check returned status $checkCode"
    }
    else {
        $testArguments = @(
            "manage.py", "test", "--settings=intevia.test_settings",
            "--testrunner=verification.isolation.runner.IsolationRunner",
            "--parallel", "1", "--noinput", "--verbosity=2"
        ) + $suite
        $testCode = Invoke-PythonAndCapture -Label "R1/R2/S1-S3/R3 plus TT-1 full suite" `
            -LogPath $logPath -Arguments $testArguments
        if ($testCode -ne 0) {
            Add-Failure "full suite returned status $testCode"
        }
    }
    $wholeRun.Stop()
    Write-Output ("WHOLE_RUN_SECONDS={0:F6}" -f $wholeRun.Elapsed.TotalSeconds)
    Write-Output "LOG_PATH=$logPath"
}
catch {
    Add-Failure (Protect-OutputText -Text $_.Exception.Message)
}
finally {
    foreach ($name in $environmentState.Keys) {
        $saved = $environmentState[$name]
        if ($saved.Present) {
            Set-Item -LiteralPath ("Env:{0}" -f $saved.Name) -Value $saved.Value
        }
        else {
            Remove-Item -LiteralPath ("Env:{0}" -f $saved.Name) -ErrorAction SilentlyContinue
        }
    }
    Set-Location -LiteralPath $originalLocation.Path
}

if ($null -ne $runDirectory -and (Test-Path -LiteralPath $runDirectory -PathType Container)) {
    if (-not [string]::IsNullOrWhiteSpace($isolationPath) -and
        (Test-Path -LiteralPath $isolationPath -PathType Leaf)) {
        try {
            $isolationRecords = @(Get-Content -LiteralPath $isolationPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
            $cp0Records = @($isolationRecords | Where-Object { $_.checkpoint -eq "CP-0" })
            $summaryRecords = @($isolationRecords | Where-Object { $_.checkpoint -eq "SUMMARY" })
            if ($cp0Records.Count -eq 1) {
                $cp0Met = $cp0Records[0].requirements_met -eq $true
            }
            else {
                Add-Failure "isolation evidence must contain exactly one CP-0 record"
            }
            if ($summaryRecords.Count -eq 1) {
                $summary = $summaryRecords[0]
                $isolationVerdict = [string]$summary.isolation_verdict
                $ordinary = $summary.ordinary_test_verdict
                if ($isolationVerdict -ne "PASS") {
                    Add-Failure "isolation summary did not PASS"
                }
                if ($summary.cp0_requirements_met -ne $true -or -not $cp0Met) {
                    Add-Failure "CP-0 was not met"
                }
                if ($null -eq $ordinary -or $ordinary.was_successful -ne $true) {
                    Add-Failure "ordinary test summary did not report success"
                }
                else {
                    foreach ($key in @("tests_run", "failures", "errors", "skipped")) {
                        if ($null -eq $script:printedSummary[$key] -or
                            [long]$script:printedSummary[$key] -ne [long]$ordinary.$key) {
                            Add-Failure "printed $key count does not match the isolation summary"
                        }
                    }
                    if (-not $script:printedSummary.result_seen -or
                        $script:printedSummary.status -ne "PASS" -or $script:printedSummary.run_lines -ne 1) {
                        Add-Failure "printed test result summary is absent or unsuccessful"
                    }
                }
            }
            else {
                Add-Failure "isolation evidence must contain exactly one SUMMARY record"
            }
        }
        catch {
            Add-Failure ("isolation evidence could not be read: " + (Protect-OutputText -Text $_.Exception.Message))
        }
    }
    else {
        Add-Failure "isolation evidence file is absent"
    }

    try {
        $teardown = Get-RunDatabaseReconciliation `
            -ReceiptPath $receiptPath -LogPath $logPath -RunNonce $runNonce `
            -PrimaryDatabase $testDatabase -TeardownLines $script:teardownLines `
            -IsolationVerdict $isolationVerdict
        foreach ($problem in $teardown.failure_reasons) {
            Add-Failure $problem
        }
    }
    catch {
        Add-Failure ("database receipts or reconciliation could not be read: " +
            (Protect-OutputText -Text $_.Exception.Message))
    }

    if ($null -eq $ordinary) {
        $counts = [ordered]@{ tests_run = $null; failures = $null; errors = $null; skipped = $null }
    }
    else {
        $counts = [ordered]@{
            tests_run = $ordinary.tests_run
            failures = $ordinary.failures
            errors = $ordinary.errors
            skipped = $ordinary.skipped
        }
    }

    try {
        $finalBinding = Get-SourceBinding -AuthorizedStateFile $StateFile -AuthorizedStateSha256 $StateSha256
        if ($null -eq $initialBinding -or $finalBinding.SourceKind -ne $initialBinding.SourceKind -or
            $finalBinding.Head -ne $initialBinding.Head -or
            $finalBinding.MergeCommit -ne $initialBinding.MergeCommit -or
            $finalBinding.StateId -ne $initialBinding.StateId -or
            $finalBinding.StateSha256 -ne $initialBinding.StateSha256 -or
            $finalBinding.Binding -ne $bindingHash) {
            Add-Failure "reviewed source binding changed during verification"
        }
    }
    catch {
        Add-Failure ("source binding could not be re-checked: " + (Protect-OutputText -Text $_.Exception.Message))
    }
    try {
        $finalScriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($finalScriptHash -ne $scriptHash) {
            Add-Failure "verifier script bytes changed during verification"
        }
    }
    catch {
        Add-Failure ("verifier script hash could not be re-checked: " + $_.Exception.Message)
    }

    $verdict = if ($failureReasons.Count -eq 0 -and $sourceKind -in @("CANDIDATE", "COMMITTED") -and
        $isolationVerdict -eq "PASS" -and $cp0Met -and $teardown.created -ge 1 -and
        $teardown.destroyed -eq $teardown.created -and $teardown.oids_match -and
        $teardown.confirmed_absent) {
        "PASS"
    }
    else {
        "FAIL"
    }
    $verdictPath = Join-Path $runDirectory "VERDICT.json"
    $verdictDocument = [ordered]@{
        verdict = $verdict
        source_kind = $sourceKind
        state_id = $stateId
        state_sha256 = $stateHash
        head = $head
        merge_commit = $mergeCommit
        binding_sha256 = $bindingHash
        script_sha256 = $scriptHash
        tests_run = $counts.tests_run
        failures = $counts.failures
        errors = $counts.errors
        skipped = $counts.skipped
        isolation_verdict = $isolationVerdict
        cp0_met = $cp0Met
        teardown = $teardown
        failure_reasons = @($failureReasons)
    }
    try {
        $json = $verdictDocument | ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText($verdictPath, $json + "`n", [System.Text.UTF8Encoding]::new($false))
        Write-Output "VERDICT_FILE=$verdictPath"
    }
    catch {
        $verdict = "FAIL"
        Write-Output ("VERDICT_WRITE_FAILURE=" + (Protect-OutputText -Text $_.Exception.Message))
    }
}

Write-Output "ISOLATION_VERDICT=$isolationVerdict"
Write-Output "CP0_MET=$cp0Met"
Write-Output "TESTS_RUN=$($counts.tests_run)"
Write-Output "TEST_FAILURES=$($counts.failures)"
Write-Output "TEST_ERRORS=$($counts.errors)"
Write-Output "TEST_SKIPPED=$($counts.skipped)"
Write-Output "TEARDOWN_CREATED=$($teardown.created)"
Write-Output "TEARDOWN_DESTROYED=$($teardown.destroyed)"
Write-Output "TEARDOWN_OIDS_MATCH=$($teardown.oids_match)"
Write-Output "TEARDOWN_CONFIRMED_ABSENT=$($teardown.confirmed_absent)"
Write-Output "DATABASE_COUNT=$($teardown.database_count)"
foreach ($database in $teardown.databases) {
    Write-Output ("DATABASE_RECONCILIATION=" + ($database | ConvertTo-Json -Depth 8 -Compress))
}
if ($failureReasons.Count -gt 0) {
    foreach ($reason in $failureReasons) {
        Write-Output "FAILURE=$reason"
    }
}
Write-Output "VERDICT: $verdict"
$global:LASTEXITCODE = if ($verdict -eq "PASS") { 0 } else { 1 }
