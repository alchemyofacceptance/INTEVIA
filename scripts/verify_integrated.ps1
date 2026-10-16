[CmdletBinding()]
param(
    [string]$StateFile,
    [string]$StateSha256,
    [switch]$StallGuardSelfTest,
    [switch]$LaunchSelfTest,
    [ValidateSet("FULL", "DELTA")][string]$VerificationKind = "FULL",
    [string[]]$Labels = @(),
    [string]$DeltaAuthorityFile,
    [string]$DeltaAuthoritySha256,
    [string]$ImpactRecordFile,
    [string]$ImpactRecordSha256,
    [string]$RotationInput
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "integrated_state.ps1")
. (Join-Path $PSScriptRoot "integrated_guards.ps1")
if ($LaunchSelfTest) {
    $selfTestRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    Set-Location -LiteralPath $selfTestRoot
    $selfTestEnvironment = @{
        DJANGO_SETTINGS_MODULE = "intevia.test_settings"
        INTEVIA_S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
        INTEVIA_POSTGRES_TEST_DB = "test_intevia_living_organism_tu1_vr_selftest"
    }
    $previousSelfTestEnvironment = @{}
    foreach ($name in $selfTestEnvironment.Keys) {
        $existing = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        $previousSelfTestEnvironment[$name] = [PSCustomObject]@{
            Present = $null -ne $existing
            Value = if ($null -ne $existing) { [string]$existing.Value } else { $null }
        }
    }
    $selfTestExitCode = 1
    try {
        foreach ($name in $selfTestEnvironment.Keys) {
            Set-Item -LiteralPath "Env:$name" -Value $selfTestEnvironment[$name]
        }
        & python -m verification.isolation.run_plan self-test
        $selfTestExitCode = $LASTEXITCODE
    }
    finally {
        $environmentRestoreFailures = [System.Collections.Generic.List[string]]::new()
        foreach ($name in $previousSelfTestEnvironment.Keys) {
            $previous = $previousSelfTestEnvironment[$name]
            try {
                if ($previous.Present) {
                    Set-Item -LiteralPath "Env:$name" -Value $previous.Value
                }
                else {
                    Remove-Item -LiteralPath "Env:$name" -ErrorAction Stop
                }
                $restoredValue = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
                if (
                    ($previous.Present -and
                        ($null -eq $restoredValue -or
                            [string]$restoredValue.Value -cne $previous.Value)) -or
                    (-not $previous.Present -and $null -ne $restoredValue)
                ) {
                    $environmentRestoreFailures.Add($name)
                }
            }
            catch {
                $environmentRestoreFailures.Add($name)
            }
        }
        if ($environmentRestoreFailures.Count -eq 0) {
            Write-Output "R9 self-test environment restore=PASS REFUSED=any process value differs from its snapshot ACCEPTED=all three process values restored exactly"
        }
        else {
            $failedNames = @($environmentRestoreFailures | Sort-Object -Unique)
            Write-Output "R9 self-test environment restore=FAIL REFUSED=any process value differs from its snapshot ACCEPTED=all three process values restored exactly MISMATCHED_VARIABLES=$($failedNames -join ',')"
            $selfTestExitCode = 1
        }
    }
    if ($selfTestExitCode -ne 0) { throw "R9: verifier launch self-test failed" }
    return
}
if ([string]::IsNullOrWhiteSpace($StateFile) -or
    $StateSha256 -cnotmatch "^[0-9a-fA-F]{64}$") {
    throw "R9: authorized state file and digest are required"
}
if ($RotationInput) { throw "R10: verifier never accepts rotation input" }
if ($VerificationKind -eq "FULL" -and
    ($Labels.Count -gt 0 -or $DeltaAuthorityFile -or $DeltaAuthoritySha256 -or $ImpactRecordFile -or $ImpactRecordSha256)) {
    throw "R10: full verification does not accept a label subset or delta authority"
}
if ($VerificationKind -eq "DELTA" -and
    (-not $DeltaAuthorityFile -or -not $DeltaAuthoritySha256 -or -not $ImpactRecordFile -or -not $ImpactRecordSha256)) {
    throw "R10: delta verification requires bound decision and impact-record files"
}
$script:guardStallMinutes = 5
$script:armedGuardLines = @(
    "FAILFAST=ON"
    "LOCK_TIMEOUT_MS=120000"
    "STALL_GUARD_MINUTES=5"
)
Write-IntegratedGuardSummary -FailFast -StallMinutes $script:guardStallMinutes
Write-Output ("MODE=" + $(if ($StallGuardSelfTest) { "guard-self-check" } else { "clone" }))
$originalLocation = Get-Location
$expectedRoute = "LIVING_ORGANISM_TEST_LIFECYCLE"
$expectedCoreHead = "0047_sek1_platform_rule_statement_start"
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
    "VERIFICATION_TEMPLATE_NAMESPACE",
    "VERIFICATION_TEMPLATE_CLEANUP",
    "INTEVIA_RUN_PLAN_FILE",
    "INTEVIA_RUN_PLAN_SHA256",
    "INTEVIA_RUN_SPEC_SHA256",
    "INTEVIA_RUN_SUFFIX",
    "INTEVIA_CANDIDATE_BINDING_SHA256",
    "INTEVIA_DATABASE_TAG",
    "INTEVIA_EVIDENCE_ROOT",
    "INTEVIA_EVIDENCE_ROOT_PATH",
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
$runProblems = $failureReasons
$script:printedSummary = [ordered]@{
    tests_run = $null
    failures = $null
    errors = $null
    skipped = $null
    discovered = $null
    resolved = $null
    skip_ids = [System.Collections.Generic.List[string]]::new()
    run_lines = 0
    result_seen = $false
    status = $null
}
$script:setupSeconds = $null
$script:templateBuildSeconds = $null
$script:preflightTemplateBuildSeconds = $null
$script:clonePreflightSetupSeconds = $null
$script:clonePreflightPassed = $false
$script:discoveredTestCount = $null
$script:testStartsBeforeStall = 0
$script:testPassesBeforeStall = 0
$script:lastTestLine = "UNAVAILABLE"
$script:watchdogStalled = $false
$script:permittedSkipIds = @(
    "tests.test_s015_application_authority.ProductionApplicationAuthorityTests.test_ap6_non_superuser_application_role_when_present",
    "tests.test_sbg_governance.SBGGovernanceDepartureCascadeTests.test_w_d3_5_departure_vacates_the_seat_for_equal_time_succession",
    "tests.test_s007_authentication_shell.AuthenticationShellTests.test_stale_epoch_and_absolute_age_flush_sessions"
)
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
$templateStorePath = $null
# S2 run-of-record count; informational only.
$previousVerificationTestCount = $null
$runNonce = $null
$testDatabase = $null
$receiptPath = $null
$isolationPath = $null
$initialBinding = $null
$counts = [ordered]@{ tests_run = $null; failures = $null; errors = $null; skipped = $null }
$testsDiscovered = $null
$testsExecuted = $null
$testsResolved = $null
$testsNotReached = $null
$skipTestIds = @()
$armedLinesPresent = $false
$cloneProven = $false
$testCountDifference = $null
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
    return $Text
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
        $diffProcess = [System.Diagnostics.Process]::new()
        $diffStream = [System.IO.MemoryStream]::new()
        try {
            $gitCommand = Get-Command git -CommandType Application -ErrorAction Stop
            $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
            $startInfo.FileName = $gitCommand.Source
            $startInfo.WorkingDirectory = (Get-Location).Path
            $startInfo.UseShellExecute = $false
            $startInfo.RedirectStandardOutput = $true
            $startInfo.RedirectStandardError = $true
            foreach ($argument in @("--no-pager", "diff", "--binary", $authorizedState.BaseCommit)) {
                [void]$startInfo.ArgumentList.Add($argument)
            }
            $diffProcess.StartInfo = $startInfo
            if (-not $diffProcess.Start()) {
                throw "candidate diff calculation failed"
            }
            $stderrTask = $diffProcess.StandardError.ReadToEndAsync()
            $diffProcess.StandardOutput.BaseStream.CopyTo($diffStream)
            $diffProcess.WaitForExit()
            [void]$stderrTask.GetAwaiter().GetResult()
            if ($diffProcess.ExitCode -ne 0) {
                throw "candidate diff calculation failed"
            }
            $diffHash = Get-Sha256Bytes -Bytes $diffStream.ToArray()
        }
        finally {
            $diffStream.Dispose()
            $diffProcess.Dispose()
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
        State = $authorizedState.State
        EvidenceRoot = $authorizedState.EvidenceRoot
        DatabaseTag = $authorizedState.DatabaseTag
    }
}

function Set-RunSpecBinding {
    param([Parameter(Mandatory = $true)]$SourceBinding)

    $runSpecSha256 = [string]$SourceBinding.State.run_spec_sha256
    if ([string]::IsNullOrWhiteSpace($runSpecSha256)) {
        $runSpecSha256 = "UNAVAILABLE"
    }
    $env:INTEVIA_RUN_SPEC_SHA256 = $runSpecSha256
    return "RUN_SPEC_SHA256=$runSpecSha256"
}

function Get-NextVerificationRunNumber {
    param([Parameter(Mandatory = $true)][string]$VerificationRoot)

    $existing = @(Get-ChildItem -LiteralPath $VerificationRoot -Directory -Filter "run_*" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.Name -match "^run_(\d+)$") { [int]$Matches[1] } })
    $number = 1
    while ($existing -contains $number) { $number++ }
    return $number
}

function Invoke-VerificationGuardSelfCheck {
    param(
        [Parameter(Mandatory = $true)][string]$VerificationRoot,
        [Parameter(Mandatory = $true)][string]$DatabaseOwnershipRoot
    )

    $selfCheckRoot = Join-Path $VerificationRoot "guard-selfchecks"
    New-Item -ItemType Directory -Path $selfCheckRoot -Force | Out-Null
    $existing = @(Get-ChildItem -LiteralPath $selfCheckRoot -Directory -Filter "selfcheck_*" |
        ForEach-Object { if ($_.Name -match "^selfcheck_(\d+)$") { [int]$Matches[1] } })
    $number = 1
    while ($existing -contains $number) { $number++ }
    $numbering = "selfcheck_$number"
    $runRoot = Join-Path $selfCheckRoot $numbering
    if (Test-Path -LiteralPath $runRoot) {
        throw "verification guard self-check directory already exists; refusing reuse"
    }
    $databaseName = "$DatabaseOwnershipRoot$numbering"
    if ($databaseName.Length -gt 63 -or $databaseName -in @(
        "intevia", "intevia_dev"
    )) {
        throw "protected or overlength verification self-check database name refused"
    }
    New-Item -ItemType Directory -Path $runRoot | Out-Null
    $logPath = Join-Path $runRoot "stall-guard-self-check.log"
    New-Item -ItemType File -Path $logPath | Out-Null
    foreach ($line in @(
        "FAILFAST=ON"
        "LOCK_TIMEOUT_MS=120000"
        "STALL_GUARD_MINUTES=$script:guardStallMinutes"
        "MODE=guard-self-check"
        "STALL_GUARD_SELF_CHECK=STARTED"
        "SELF_CHECK_DATABASE_NAME=$databaseName"
    )) {
        Write-WatchdogLogLine -LineText $line -LogPath $logPath
    }

    $presence = @(Invoke-RunnerDatabasePython `
        -Action "check-self-check" `
        -DatabaseName $databaseName `
        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
        -Numbering $numbering)
    if ("SELF_CHECK_DATABASE_EXISTS=False" -notin $presence) {
        throw "verification self-check database absence could not be confirmed"
    }
    $created = @(Invoke-RunnerDatabasePython `
        -Action "create-self-check" `
        -DatabaseName $databaseName `
        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
        -Numbering $numbering)
    $oidLines = @($created | Where-Object { $_ -match "^CREATED_OID=[0-9]+$" })
    if ($oidLines.Count -ne 1 -or $oidLines[0] -notmatch "^CREATED_OID=([0-9]+)$") {
        throw "verification self-check database creation was not confirmed"
    }
    $databaseOid = [long]$Matches[1]
    Write-WatchdogLogLine -LineText "SELF_CHECK_DATABASE_CREATED=True" -LogPath $logPath
    Write-WatchdogLogLine -LineText "SELF_CHECK_DATABASE_OID=$databaseOid" -LogPath $logPath

    $selfCheckCommand = (
        "import time; print('test_stall_guard_self_check " +
        "(verification.stall_guard.selfcheck) ...', flush=True); time.sleep(360)"
    )
    $result = Invoke-WatchedPythonAndLog `
        -Label "Verification stall-guard self-check child" `
        -Arguments @("-c", $selfCheckCommand) `
        -LogPath $logPath `
        -RunRoot $runRoot `
        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
        -Numbering $numbering `
        -StallThresholdMinutes $script:guardStallMinutes `
        -DatabaseName $databaseName `
        -DatabaseOid $databaseOid `
        -SelfCheck
    Write-WatchdogLogLine `
        -LineText "STALL=$($result.Stalled)" `
        -LogPath $logPath
    Write-WatchdogLogLine `
        -LineText "SELF_CHECK_STALL_DETECTED=$($result.Stalled)" `
        -LogPath $logPath
    Write-WatchdogLogLine `
        -LineText "SELF_CHECK_PROCESS_ENDED=$($result.ProcessEnded)" `
        -LogPath $logPath
    Write-WatchdogLogLine `
        -LineText "SELF_CHECK_DATABASE_CONFIRMED_ABSENT=$($result.DatabaseAbsent)" `
        -LogPath $logPath
    Write-WatchdogLogLine `
        -LineText "SELF_CHECK_STALL_DETECTED_AFTER_SECONDS=$([Math]::Round($result.StallDetectedSeconds, 3))" `
        -LogPath $logPath
    $stallMarkerRecorded = "STALL=TRUE" -in @(
        Get-Content -LiteralPath $logPath -ErrorAction Stop
    )
    Write-WatchdogLogLine `
        -LineText "SELF_CHECK_STALL_MARKER_RECORDED=$stallMarkerRecorded" `
        -LogPath $logPath

    $after = @(Invoke-RunnerDatabasePython `
        -Action "check-self-check" `
        -DatabaseName $databaseName `
        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
        -Numbering $numbering)
    if (-not $result.Stalled -or -not $result.ProcessEnded -or
        -not $result.DatabaseAbsent -or -not $stallMarkerRecorded -or
        $result.ExitCode -ne 124 -or
        $null -eq $result.StallDetectedSeconds -or
        $result.StallDetectedSeconds -lt ($script:guardStallMinutes * 60) -or
        $result.StallDetectedSeconds -ge (($script:guardStallMinutes * 60) + 1) -or
        $result.Failures.Count -ne 0 -or
        "SELF_CHECK_DATABASE_EXISTS=False" -notin $after) {
        throw "verification stall-guard self-check did not prove stall termination and database removal"
    }
    Write-WatchdogLogLine -LineText "STALL_GUARD_SELF_CHECK=PASS" -LogPath $logPath
    Write-Output "STALL_GUARD_SELF_CHECK=PASS"
    Write-Output "SELF_CHECK_LOG=$logPath"
}

function Test-VerificationSuccess {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Evidence)

    $skipIds = @($Evidence.SkipTestIds)
    $permittedSkipIds = @($Evidence.PermittedSkipIds)
    $unknownSkips = @($skipIds | Where-Object { $_ -notin $permittedSkipIds })
    return (
        [int]$Evidence.Failures -eq 0 -and
        [int]$Evidence.Errors -eq 0 -and
        [int]$Evidence.TestsNotReached -eq 0 -and
        [int]$Evidence.TestsDiscovered -gt 0 -and
        [int]$Evidence.TestsDiscovered -eq [int]$Evidence.TestsExecuted -and
        [int]$Evidence.TestsExecuted -eq [int]$Evidence.TestsResolved -and
        [int]$Evidence.Skipped -eq $skipIds.Count -and
        (@($skipIds | Select-Object -Unique).Count -eq $skipIds.Count) -and
        $unknownSkips.Count -eq 0 -and
        $Evidence.IsolationVerdict -eq "PASS" -and
        [int]$Evidence.DatabasesCreated -gt 0 -and
        [int]$Evidence.DatabasesDestroyed -eq [int]$Evidence.DatabasesCreated -and
        [int]$Evidence.DatabaseCount -eq [int]$Evidence.DatabasesCreated -and
        $Evidence.DatabaseOidsMatch -eq $true -and
        $Evidence.DatabasesConfirmedAbsent -eq $true -and
        $Evidence.ArmedLinesPresent -eq $true -and
        $Evidence.CloneProven -eq $true
    )
}

function Set-PrintedSummaryFromLine {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)

    if ($Line -match "^Found ([0-9]+) test\(s\)\.?$") {
        $script:printedSummary.discovered = [int]$Matches[1]
    }
    if ($Line -match "^TESTS_RESOLVED=([0-9]+)$") {
        $script:printedSummary.resolved = [int]$Matches[1]
    }
    if ($Line -match "^test_[^\s]+\s+\((?<testId>tests\.[^)]+)\)\s+\.\.\.\s+skipped(?:\s|$)") {
        $script:printedSummary.skip_ids.Add($Matches.testId)
    }
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
        Write-WatchdogLogLine -LineText $lineText -LogPath $LogPath
        Set-PrintedSummaryFromLine -Line $lineText
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
    $presentEnvironmentNames = @(Get-ChildItem Env: | ForEach-Object { $_.Name })
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        if ($name -cnotin $presentEnvironmentNames) {
            throw "$name is required"
        }
    }

    $verificationRoot = $routes.VerificationRoot
    New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
    if ($StallGuardSelfTest) {
        $env:INTEVIA_DATABASE_ENGINE = "postgresql"
        $env:DJANGO_SETTINGS_MODULE = "intevia.settings"
        $env:PYTHONUNBUFFERED = "1"
        Invoke-VerificationGuardSelfCheck `
            -VerificationRoot $verificationRoot `
            -DatabaseOwnershipRoot $routes.VerificationDatabasePrefix
        $global:LASTEXITCODE = 0
        return
    }

    $runNumber = Get-NextVerificationRunNumber -VerificationRoot $verificationRoot
    $priorPasses = @()
    foreach ($priorDirectory in @(Get-ChildItem -LiteralPath $verificationRoot `
        -Directory -Filter "run_*" -ErrorAction SilentlyContinue)) {
        if ($priorDirectory.Name -notmatch "^run_(\d+)$") { continue }
        $priorRunNumber = [int]$Matches[1]
        $priorVerdictPath = Join-Path $priorDirectory.FullName "VERDICT.json"
        if (-not (Test-Path -LiteralPath $priorVerdictPath -PathType Leaf)) { continue }
        try {
            $priorVerdict = Get-Content -LiteralPath $priorVerdictPath -Raw -ErrorAction Stop |
                ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            Write-Output "PREVIOUS_VERIFICATION_COUNT_NOTE=prior verdict unreadable; count comparison unavailable"
            $priorVerdict = $null
        }
        if ($null -ne $priorVerdict -and $priorVerdict.verdict -eq "PASS" -and
            $null -ne $priorVerdict.tests_run) {
            $priorPasses += [PSCustomObject]@{
                RunNumber = $priorRunNumber
                TestsRun = [int]$priorVerdict.tests_run
            }
        }
    }
    if ($priorPasses.Count -gt 0) {
        $previousVerificationTestCount = ($priorPasses | Sort-Object RunNumber -Descending |
            Select-Object -First 1).TestsRun
    }
    Write-Output "PREVIOUS_VERIFICATION_TESTS=$(if ($null -eq $previousVerificationTestCount) { 'UNAVAILABLE' } else { $previousVerificationTestCount })"
    $runDirectory = Join-Path $verificationRoot ("run_{0}" -f $runNumber)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "verification run directory already exists; refusing reuse"
    }
    $testDatabase = "$($routes.VerificationDatabasePrefix)$runNumber"
    Write-Output "VERIFICATION_ROOT=$verificationRoot"
    Write-Output "VERIFICATION_DATABASE=$testDatabase"
    if ($testDatabase.Length -gt 63 -or $testDatabase -in @("intevia", "intevia_dev")) {
        throw "protected or overlength test database name refused"
    }
    New-Item -ItemType Directory -Path $runDirectory | Out-Null
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "integrated-clone-suite.log"
    foreach ($line in @(
        "STATE_FILE=$stateFilePath"
        "STATE_ID=$stateId"
        "STATE_SHA256=$stateHash"
        "SOURCE_KIND=$sourceKind"
        "HEAD=$head"
        "MERGE_COMMIT=$mergeCommit"
        "BINDING_SHA256=$bindingHash"
    ) + $script:armedGuardLines + @(
        "MODE=clone"
    )) {
        Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
    }
    Write-Output "RUN_DIRECTORY=$runDirectory"

    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_POSTGRES_TEST_DB = $testDatabase
    $env:INTEVIA_CANDIDATE_BINDING_SHA256 = $bindingHash
    $env:INTEVIA_DATABASE_TAG = [string]$initialBinding.DatabaseTag
    $env:INTEVIA_EVIDENCE_ROOT = [string]$initialBinding.EvidenceRoot
    $env:INTEVIA_EVIDENCE_ROOT_PATH = [System.IO.Path]::GetFullPath((Split-Path -Parent $verificationRoot))
    $env:INTEVIA_S015_TEST_ROUTE = $expectedRoute
    $env:VERIFICATION_RUN_NONCE = $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = $expectedCoreHead
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = "clone"
    # TU1-T13: retain this candidate's template store across runs in the same evidence root.
    $templateStorePath = Join-Path $verificationRoot "tooling-template-store"
    $env:VERIFICATION_TEMPLATE_STORE = $templateStorePath
    $env:VERIFICATION_TEMPLATE_NAMESPACE = "vr"
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
        "tests.test_sbg_governance",
        "tests.test_se_entitlement"
    )

    $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
    $impactRecord = $null
    $deltaAuthorityDigest = "NONE"
    $impactRecordDigest = "NONE"
    if ($VerificationKind -eq "DELTA") {
        foreach ($input in @(
            @{ Path = $DeltaAuthorityFile; Digest = $DeltaAuthoritySha256; Name = "decision" },
            @{ Path = $ImpactRecordFile; Digest = $ImpactRecordSha256; Name = "impact record" }
        )) {
            if ($input.Digest -cnotmatch "^[0-9a-fA-F]{64}$" -or
                -not (Test-Path -LiteralPath $input.Path -PathType Leaf)) {
                throw "R10: delta $($input.Name) path and SHA-256 are required"
            }
            $actualDigest = (Get-FileHash -LiteralPath $input.Path -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualDigest -cne $input.Digest.ToLowerInvariant()) {
                throw "R10: delta $($input.Name) digest mismatch"
            }
            if ($input.Name -eq "decision") { $deltaAuthorityDigest = $actualDigest }
            else { $impactRecordDigest = $actualDigest }
        }
        $decisionText = Get-Content -LiteralPath $DeltaAuthorityFile -Raw -ErrorAction Stop
        if ($decisionText -notmatch "(?i)delta" -or $decisionText -notmatch "(?i)Human Governor") {
            throw "R10: delta authority is not an identified Human Governor decision"
        }
        $impactRecord = Get-Content -LiteralPath $ImpactRecordFile -Raw -ErrorAction Stop |
            ConvertFrom-Json -ErrorAction Stop
        $impactLabels = if ($null -ne $impactRecord.labels) {
            @($impactRecord.labels)
        }
        else {
            @($impactRecord.tests | Where-Object { $_ -is [string] })
        }
        if ($impactLabels.Count -eq 0) { throw "R10: impact record must provide loader labels and a test selection" }
        if ($Labels.Count -gt 0 -and
            (@($Labels) -join "`n") -cne ($impactLabels -join "`n")) {
            throw "R10: supplied labels differ from the impact record"
        }
        $suite = $impactLabels
    }
    $planFile = Join-Path $runDirectory "verification-plan.json"
    $planLogPath = Join-Path $runDirectory "verification-plan.log"
    $planPartsDirectory = Join-Path $runDirectory "verification-plan-parts"
    New-Item -ItemType Directory -Path $planPartsDirectory -Force | Out-Null
    $allExpandedLabels = [System.Collections.Generic.List[string]]::new()
    $allTestIds = [System.Collections.Generic.List[string]]::new()
    $partPlans = [System.Collections.Generic.List[object]]::new()
    for ($offset = 0; $offset -lt $suite.Count; $offset += 20) {
        $batch = @($suite | Select-Object -Skip $offset -First 20)
        $partNumber = [int]($offset / 20) + 1
        $partFile = Join-Path $planPartsDirectory ("part-{0:D2}.json" -f $partNumber)
        $partLogPath = Join-Path $planPartsDirectory ("part-{0:D2}.log" -f $partNumber)
        $planArguments = @(
            "-m", "verification.isolation.run_plan", "plan",
            "--output", $partFile
        )
        foreach ($label in $batch) { $planArguments += @("--label", $label) }
        $planStatus = Invoke-PythonAndCapture `
            -Label "R3/R4 verification plan batch $partNumber" `
            -LogPath $partLogPath -Arguments $planArguments
        if ($planStatus -ne 0) {
            throw "R3/R4: verifier selection batch $partNumber did not resolve before database setup"
        }
        $partDigestLine = @(Get-Content -LiteralPath $partLogPath |
            Where-Object { $_ -match "^PLAN_SHA256=[0-9a-f]{64}$" } | Select-Object -Last 1)
        if ($partDigestLine.Count -ne 1 -or
            $partDigestLine[0] -notmatch "^PLAN_SHA256=([0-9a-f]{64})$") {
            throw "R4: verifier plan batch $partNumber digest was not recorded"
        }
        $partDigest = $Matches[1]
        $actualPartDigest = (Get-FileHash -LiteralPath $partFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($partDigest -cne $actualPartDigest) {
            throw "R4: verifier plan batch $partNumber digest differs from its bytes"
        }
        $partPlan = Get-Content -LiteralPath $partFile -Raw -ErrorAction Stop |
            ConvertFrom-Json -ErrorAction Stop
        $partPlans.Add($partPlan)
        foreach ($label in @($partPlan.expanded_labels)) { $allExpandedLabels.Add([string]$label) }
        foreach ($testId in @($partPlan.tests)) { $allTestIds.Add([string]$testId) }
    }
    if ($allTestIds.Count -eq 0 -or
        @($allTestIds | Sort-Object -Unique).Count -ne $allTestIds.Count) {
        throw "R3/R4: batched verifier plan is empty or contains duplicate test identifiers"
    }
    $databaseRoute = if (@($partPlans | Where-Object { $_.database_route -eq "DATABASE" }).Count -gt 0) {
        "DATABASE"
    }
    else {
        "NO_DATABASE"
    }
    $combinedPlan = [ordered]@{
        schema_version = 1
        labels = @($suite)
        expanded_labels = $allExpandedLabels.ToArray()
        tests = $allTestIds.ToArray()
        database_route = $databaseRoute
        ordered_groups = @()
        parent_plan_sha256 = $null
        rotation_side = "NONE"
        rotation_unit = "NONE"
        counts = [ordered]@{ tests = $allTestIds.Count; groups = 0 }
    }
    $planJson = ConvertTo-Json -InputObject $combinedPlan -Depth 20
    $planJson = [regex]::Replace($planJson, "\r?\n", "`n")
    [System.IO.File]::WriteAllText(
        $planFile,
        $planJson + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    $planStatus = Invoke-PythonAndCapture `
        -Label "R3/R4 merged verification plan validation" `
        -LogPath $planLogPath `
        -Arguments @(
            "-m", "verification.isolation.run_plan", "validate-plan",
            "--plan", $planFile
        )
    if ($planStatus -ne 0) {
        throw "R3/R4: merged verifier selection did not resolve before database setup"
    }
    $planDigestLine = @(Get-Content -LiteralPath $planLogPath |
        Where-Object { $_ -match "^PLAN_SHA256=[0-9a-f]{64}$" } | Select-Object -Last 1)
    if ($planDigestLine.Count -ne 1 -or
        $planDigestLine[0] -notmatch "^PLAN_SHA256=([0-9a-f]{64})$") {
        throw "R4: verifier plan digest was not recorded"
    }
    $planDigest = $Matches[1]
    $actualPlanDigest = (Get-FileHash -LiteralPath $planFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($planDigest -cne $actualPlanDigest) {
        throw "R4: merged verifier plan digest differs from its bytes"
    }
    if ($VerificationKind -eq "DELTA") {
        $deltaStatus = Invoke-PythonAndCapture -Label "R10 authorized delta selection" `
            -LogPath $planLogPath -Arguments @(
                "-m", "verification.isolation.run_plan", "delta-check",
                "--output", $planFile, "--impact", $ImpactRecordFile
            )
        if ($deltaStatus -ne 0) { throw "R10: delta plan differs from its impact record" }
    }
    $env:INTEVIA_RUN_PLAN_FILE = $planFile
    $env:INTEVIA_RUN_PLAN_SHA256 = $planDigest
    $runSpecBindingLine = Set-RunSpecBinding -SourceBinding $initialBinding
    $env:INTEVIA_RUN_SUFFIX = [string]$runNumber
    Write-Output "VERIFICATION_KIND=$VerificationKind"
    Write-Output $runSpecBindingLine
    Write-Output "PLAN_SHA256=$planDigest"
    Write-Output "PARENT_PLAN_SHA256=NONE"
    Write-Output "ROTATION=NONE"
    Write-Output "ROTATION_UNIT=NONE"
    if ($VerificationKind -eq "DELTA") {
        Write-Output "DELTA_AUTHORITY=$deltaAuthorityDigest"
        Write-Output "IMPACT_RECORD=$impactRecordDigest"
    }
    $loaderLogPath = Join-Path $runDirectory "test-selection-loader.log"
    $loaderCode = @'
import sys
from contextlib import ExitStack
from unittest.mock import patch

import django
django.setup()

from django.db import connections
from verification.isolation.runner import IsolationRunner

with ExitStack() as stack:
    for alias in connections:
        stack.enter_context(
            patch.object(
                connections[alias],
                "ensure_connection",
                side_effect=RuntimeError("test selection loader attempted database access"),
            )
        )
    runner = IsolationRunner(verbosity=0, interactive=False, parallel=1)
    suite = runner.build_suite(sys.argv[1:])
    count = suite.countTestCases()
for alias in connections:
    if connections[alias].connection is not None:
        raise RuntimeError("test selection loader opened a database connection")
if count <= 0:
    raise RuntimeError("test selection resolved no test cases")
print("TESTS_RESOLVED=%d" % count)
'@
    $loaderStatus = Invoke-PythonAndCapture `
        -Label "No-database test-loader selection" `
        -LogPath $loaderLogPath `
        -Arguments (@("-c", $loaderCode) + $suite)
    $loaderResolved = $loaderStatus -eq 0 -and $null -ne $script:printedSummary.resolved
    if (-not $loaderResolved) {
        Add-Failure "test loader did not resolve the suite before database creation"
    }
    else {
        Write-Output "TESTS_RESOLVED=$($script:printedSummary.resolved)"
        Write-WatchdogLogLine `
            -LineText "TESTS_RESOLVED=$($script:printedSummary.resolved)" `
            -LogPath $logPath
        $checkCode = Invoke-PythonAndCapture -Label "Django system check" -LogPath $logPath `
            -Arguments @("manage.py", "check", "--settings=intevia.test_settings")
        if ($checkCode -ne 0) {
            Add-Failure "Django system check returned status $checkCode"
        }
    }

    $canRunTests = $failureReasons.Count -eq 0
    if ($canRunTests) {
        $tagRegisterPath = Join-Path $HOME "INTEVIA_AGENT_STAGING\database-tag-register.jsonl"
        $tagRegisterCode = @'
import json
import sys

from verification.isolation.template import register_database_tag

result = register_database_tag(sys.argv[1], sys.argv[2], sys.argv[3])
print("DATABASE_TAG_REGISTER=" + json.dumps(result, sort_keys=True))
'@
        $tagRegisterStatus = Invoke-PythonAndCapture `
            -Label "TU-1b database tag register" `
            -LogPath $logPath `
            -Arguments @(
                "-c", $tagRegisterCode, $tagRegisterPath,
                $env:INTEVIA_DATABASE_TAG, $env:INTEVIA_EVIDENCE_ROOT
            )
        if ($tagRegisterStatus -ne 0) {
            Add-Failure "TU-1b database tag register refused the verification launch"
            $canRunTests = $false
        }
    }

    if ($canRunTests) {
        $preflightCodeSource = @'
import os
import time

import django
django.setup()

from django.db import connections
from verification.isolation.template import (
    _database_record,
    _drop_database_by_oid,
    create_clone_database,
    prepare_template,
)
from verification.ownership import read_receipts, write_receipt

connection = connections["default"]
nonce = os.environ["VERIFICATION_RUN_NONCE"]
probe_name = "test_intevia_living_organism_tt1_witness_" + nonce[:12]
template_started = time.perf_counter()
template_receipt, rebuilt = prepare_template(
    connection,
    "default",
    os.environ["VERIFICATION_TEMPLATE_STORE"],
)
template_build_seconds = time.perf_counter() - template_started
print("TT1_PREFLIGHT_TEMPLATE_BUILD_SECONDS=%.6f" % template_build_seconds)
if template_receipt is None:
    raise RuntimeError("template preparation returned no receipt")

clone_started = time.perf_counter()
try:
    create_clone_database(connection, template_receipt, probe_name)
finally:
    records, problem = read_receipts(
        os.environ["VERIFICATION_DB_RECEIPT"],
        nonce,
    )
    if problem is not None:
        raise RuntimeError("clone preflight receipts are untrustworthy")
    created = [
        record for record in records
        if record.get("event") == "created" and record.get("name") == probe_name
    ]
    if len(created) > 1:
        raise RuntimeError("clone preflight has duplicate creation receipts")
    database = _database_record(connection, probe_name)
    if created:
        oid = int(created[0]["oid"])
        if database is not None and database["oid"] != oid:
            raise RuntimeError("clone preflight database OID differs from its receipt")
        connection.close()
        absent = _drop_database_by_oid(connection, probe_name, oid)
        write_receipt(
            "destroyed",
            name=probe_name,
            oid=oid,
            confirmed_absent=absent,
        )
        if not absent:
            raise RuntimeError("clone preflight cleanup was not confirmed")
    elif database is not None:
        raise RuntimeError("unreceipted clone preflight database left untouched")
    print(
        "CLONE_PREFLIGHT_SETUP_SECONDS=%.6f"
        % (time.perf_counter() - clone_started)
    )

print("TEMPLATE_READY=PASS")
print("TEMPLATE_REBUILT=" + ("YES" if rebuilt else "NO"))
print("TEMPLATE_ACTION=" + ("BUILT" if rebuilt else "REUSED"))
print("TEMPLATE_BUILD_SECONDS=%.6f" % template_build_seconds if rebuilt else "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE")
print("CLONE_PREFLIGHT=PASS")
print("CLONE_PROBE_DATABASE=" + probe_name)
'@
        $previousPgOptions = $env:PGOPTIONS
        $lockTimeoutOption = "-c lock_timeout=$($script:lockTimeoutMilliseconds / 1000)s"
        $env:PGOPTIONS = if ([string]::IsNullOrWhiteSpace($previousPgOptions)) {
            $lockTimeoutOption
        }
        else {
            "$previousPgOptions $lockTimeoutOption"
        }
        try {
            $preflightCode = Invoke-PythonAndCapture `
                -Label "Verification clone template and database preflight" `
                -LogPath $logPath `
                -Arguments @("-c", $preflightCodeSource)
        }
        finally {
            $env:PGOPTIONS = $previousPgOptions
        }
        $preflightLines = @(Get-Content -LiteralPath $logPath -ErrorAction Stop)
        $script:clonePreflightPassed =
            $preflightCode -eq 0 -and
            "TEMPLATE_READY=PASS" -in $preflightLines -and
            "CLONE_PREFLIGHT=PASS" -in $preflightLines -and
            @($preflightLines | Where-Object { $_ -match "^CLONE_PROBE_DATABASE=" }).Count -eq 1
        $templateBuildLine = @($preflightLines | Where-Object {
            $_ -match "^TT1_PREFLIGHT_TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$"
        } | Select-Object -Last 1)
        if ($templateBuildLine.Count -eq 1 -and
            $templateBuildLine[0] -match "^TT1_PREFLIGHT_TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $script:preflightTemplateBuildSeconds = [double]::Parse(
                $Matches[1], [System.Globalization.CultureInfo]::InvariantCulture
            )
        }
        $cloneSetupLine = @($preflightLines | Where-Object {
            $_ -match "^CLONE_PREFLIGHT_SETUP_SECONDS=([0-9]+(?:\.[0-9]+)?)$"
        } | Select-Object -Last 1)
        if ($cloneSetupLine.Count -eq 1 -and
            $cloneSetupLine[0] -match "^CLONE_PREFLIGHT_SETUP_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $script:clonePreflightSetupSeconds = [double]::Parse(
                $Matches[1], [System.Globalization.CultureInfo]::InvariantCulture
            )
        }
        if (-not $script:clonePreflightPassed -or
            $null -eq $script:preflightTemplateBuildSeconds -or
            $null -eq $script:clonePreflightSetupSeconds) {
            Write-Output "CLONE_PREFLIGHT=FAIL"
            Add-Failure "verification clone preflight failed; test suite was not launched"
            $canRunTests = $false
        }
    }

    if ($canRunTests) {
        $script:printedSummary.tests_run = $null
        $script:printedSummary.failures = $null
        $script:printedSummary.errors = $null
        $script:printedSummary.skipped = $null
        $script:printedSummary.discovered = $null
        $script:printedSummary.run_lines = 0
        $script:printedSummary.result_seen = $false
        $script:printedSummary.status = $null
        $script:printedSummary.skip_ids.Clear()
        $script:discoveredTestCount = $null
        $testArguments = @(
            "manage.py", "test", "--settings=intevia.test_settings",
            "--testrunner=verification.isolation.runner.IsolationRunner",
            "--parallel", "1", "--noinput", "--verbosity=2", "--failfast"
        )
        $testRun = Invoke-WatchedPythonAndLog `
            -Label "R1/R2/S1-S3/R3 plus TT-1 full suite" `
            -LogPath $logPath `
            -Arguments $testArguments `
            -RunRoot $runDirectory `
            -DatabaseOwnershipRoot $routes.VerificationDatabasePrefix `
            -Numbering ([string]$runNumber) `
            -StallThresholdMinutes $script:guardStallMinutes `
            -DatabaseName $testDatabase `
            -ReceiptPath $receiptPath `
            -RunNonce $runNonce
        if ($testRun.Stalled) {
            Add-Failure "STALL: full-suite process was ended after five minutes without log growth"
        }
        Write-WatchdogLogLine -LineText "STALL=$($testRun.Stalled)" -LogPath $logPath
        if ($testRun.ExitCode -ne 0) {
            Add-Failure "full suite returned status $($testRun.ExitCode)"
        }
        foreach ($line in @(Get-Content -LiteralPath $logPath -ErrorAction Stop)) {
            Set-PrintedSummaryFromLine -Line $line
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
                $resolvedCount = if ($null -eq $script:printedSummary.resolved) {
                    "UNAVAILABLE"
                }
                else {
                    [string]$script:printedSummary.resolved
                }
                $summaryCheckStatus = Invoke-PythonAndCapture `
                    -Label "R7 verifier summary" `
                    -LogPath $logPath `
                    -Arguments @(
                        "-m", "verification.isolation.run_plan", "summary-check",
                        "--isolation", $isolationPath,
                        "--plan", $planFile,
                        "--resolved", $resolvedCount
                    )
                if ($summaryCheckStatus -ne 0) {
                    $summaryRefusal = @(Get-Content -LiteralPath $logPath |
                        Where-Object { $_ -match "^R7: " } |
                        Select-Object -Last 1)
                    if ($summaryRefusal.Count -eq 1) {
                        Add-Failure $summaryRefusal[0]
                    }
                    else {
                        Add-Failure "R7: verifier summary check refused without a refusal message"
                    }
                }
                Write-Output "TESTS_PLANNED=$($summary.tests_planned)"
                Write-Output "TESTS_EXECUTED=$($summary.tests_executed)"
                Write-Output "TESTS_NOT_REACHED=$($summary.tests_not_reached)"
                Write-Output "EXECUTED_MATCHES_PLAN=$($summary.executed_matches_plan)"
                Write-Output "PARENT_PLAN_SHA256=$($summary.parent_plan_sha256)"
                Write-Output "ROTATION=$($summary.rotation)"
                Write-Output "ROTATION_UNIT=$($summary.rotation_unit)"
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

    $testsDiscovered = $script:printedSummary.discovered
    $testsExecuted = $script:printedSummary.tests_run
    $testsResolved = $script:printedSummary.resolved
    $testsNotReached = $null
    if ($null -ne $testsDiscovered -and $null -ne $testsExecuted) {
        $testsNotReached = [Math]::Max(0, [int]$testsDiscovered - [int]$testsExecuted)
    }
    if ($null -eq $testsDiscovered -or $null -eq $testsExecuted -or $null -eq $testsResolved) {
        Add-Failure "discovered, executed, or loader-resolved test count is absent"
    }
    elseif ([int]$testsDiscovered -ne [int]$testsExecuted -or
        [int]$testsExecuted -ne [int]$testsResolved) {
        Add-Failure "discovered, executed, and loader-resolved test counts do not match"
    }
    if ($null -eq $counts.failures -or [int]$counts.failures -ne 0) {
        Add-Failure "full-suite failure count is not zero"
    }
    if ($null -eq $counts.errors -or [int]$counts.errors -ne 0) {
        Add-Failure "full-suite error count is not zero"
    }
    $skipTestIds = @($script:printedSummary.skip_ids.ToArray())
    if ($null -eq $counts.skipped -or [int]$counts.skipped -ne $skipTestIds.Count) {
        Add-Failure "every reported skip must have one named test identifier"
    }
    $unpermittedSkips = @($skipTestIds | Where-Object {
        $_ -notin $script:permittedSkipIds
    })
    foreach ($skipId in $unpermittedSkips) {
        Add-Failure "unpermitted skipped test: $skipId"
    }

    $runLogLines = if (Test-Path -LiteralPath $logPath -PathType Leaf) {
        @(Get-Content -LiteralPath $logPath -ErrorAction Stop)
    }
    else {
        @()
    }
    $armedLinesPresent = @($script:armedGuardLines | Where-Object {
        $_ -notin $runLogLines
    }).Count -eq 0
    if (-not $armedLinesPresent) {
        Add-Failure "one or more armed guard summary lines are absent from the run log"
    }
    $templateStoreInVerificationRoot = $false
    if (-not [string]::IsNullOrWhiteSpace($templateStorePath)) {
        $storeFullPath = [System.IO.Path]::GetFullPath($templateStorePath)
        $verificationRootFullPath = [System.IO.Path]::GetFullPath($verificationRoot)
        $verificationPrefix = $verificationRootFullPath.TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar
        ) + [System.IO.Path]::DirectorySeparatorChar
        $templateStoreInVerificationRoot = $storeFullPath.StartsWith(
            $verificationPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    }
    $cloneProven = $templateStoreInVerificationRoot -and
        "MODE=clone" -in $runLogLines -and
        $script:clonePreflightPassed -and
        $null -ne $script:preflightTemplateBuildSeconds -and
        $null -ne $script:clonePreflightSetupSeconds -and
        $null -ne $script:setupSeconds
    if (-not $cloneProven) {
        Add-Failure "verification clone mode, preflight, or timing evidence is incomplete"
    }

    $verificationEvidence = [ordered]@{
        Failures = $counts.failures
        Errors = $counts.errors
        TestsNotReached = $testsNotReached
        TestsDiscovered = $testsDiscovered
        TestsExecuted = $testsExecuted
        TestsResolved = $testsResolved
        Skipped = $counts.skipped
        SkipTestIds = $skipTestIds
        PermittedSkipIds = $script:permittedSkipIds
        IsolationVerdict = $isolationVerdict
        DatabasesCreated = $teardown.created
        DatabasesDestroyed = $teardown.destroyed
        DatabaseCount = $teardown.database_count
        DatabaseOidsMatch = $teardown.oids_match
        DatabasesConfirmedAbsent = $teardown.confirmed_absent
        ArmedLinesPresent = $armedLinesPresent
        CloneProven = $cloneProven
    }
    $verificationEvidencePass = Test-VerificationSuccess -Evidence $verificationEvidence
    if (-not $verificationEvidencePass) {
        Add-Failure "one or more required S4 success conditions were not met"
    }
    $testCountDifference = if ($null -ne $previousVerificationTestCount -and
        $null -ne $testsExecuted) {
        [int]$testsExecuted - [int]$previousVerificationTestCount
    }
    else {
        $null
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

    $verdict = if ($script:watchdogStalled) {
        "STALL"
    }
    elseif ($failureReasons.Count -eq 0 -and
        $sourceKind -in @("CANDIDATE", "COMMITTED") -and
        $isolationVerdict -eq "PASS" -and $cp0Met -and
        (Test-VerificationSuccess -Evidence $verificationEvidence)) {
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
        tests_discovered = $testsDiscovered
        tests_executed = $testsExecuted
        tests_resolved = $testsResolved
        tests_not_reached = $testsNotReached
        previous_verification_tests = $previousVerificationTestCount
        test_count_difference_from_previous = $testCountDifference
        failures = $counts.failures
        errors = $counts.errors
        skipped = $counts.skipped
        skip_test_ids = $skipTestIds
        isolation_verdict = $isolationVerdict
        cp0_met = $cp0Met
        mode = "clone"
        template_store = $templateStorePath
        template_build_seconds = $script:preflightTemplateBuildSeconds
        clone_preflight_setup_seconds = $script:clonePreflightSetupSeconds
        clone_setup_seconds = $script:setupSeconds
        armed_guard_lines_present = $armedLinesPresent
        clone_proven = $cloneProven
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
Write-Output "FAILFAST=ON"
Write-Output "LOCK_TIMEOUT_MS=120000"
Write-Output "STALL_GUARD_MINUTES=5"
Write-Output "MODE=clone"
Write-Output "TESTS_RUN=$($counts.tests_run)"
Write-Output "TESTS_DISCOVERED=$testsDiscovered"
Write-Output "TESTS_EXECUTED=$testsExecuted"
Write-Output "TESTS_RESOLVED=$testsResolved"
Write-Output "TESTS_NOT_REACHED=$testsNotReached"
Write-Output "PREVIOUS_VERIFICATION_TESTS=$(if ($null -eq $previousVerificationTestCount) { 'UNAVAILABLE' } else { $previousVerificationTestCount })"
Write-Output "TEST_COUNT_DIFFERENCE_FROM_PREVIOUS=$(if ($null -eq $testCountDifference) { 'UNAVAILABLE' } else { $testCountDifference })"
Write-Output "TEST_FAILURES=$($counts.failures)"
Write-Output "TEST_ERRORS=$($counts.errors)"
Write-Output "TEST_SKIPPED=$($counts.skipped)"
Write-Output "TEST_SKIP_IDS=$($skipTestIds -join ',')"
Write-Output "TT1_TEMPLATE_BUILD_SECONDS=$(if ($null -eq $script:preflightTemplateBuildSeconds) { 'UNAVAILABLE' } else { $script:preflightTemplateBuildSeconds.ToString('F6', [System.Globalization.CultureInfo]::InvariantCulture) })"
Write-Output "TT1_CLONE_SETUP_SECONDS=$(if ($null -eq $script:setupSeconds) { 'UNAVAILABLE' } else { $script:setupSeconds.ToString('F6', [System.Globalization.CultureInfo]::InvariantCulture) })"
Write-Output "CLONE_PREFLIGHT_SETUP_SECONDS=$(if ($null -eq $script:clonePreflightSetupSeconds) { 'UNAVAILABLE' } else { $script:clonePreflightSetupSeconds.ToString('F6', [System.Globalization.CultureInfo]::InvariantCulture) })"
Write-Output "CLONE_PREFLIGHT=$(if ($script:clonePreflightPassed) { 'PASS' } else { 'NOT PROVEN' })"
Write-Output "CLONE_PROVEN=$cloneProven"
Write-Output "ARMED_GUARD_LINES_PRESENT=$armedLinesPresent"
Write-Output "STALL=$($script:watchdogStalled)"
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
