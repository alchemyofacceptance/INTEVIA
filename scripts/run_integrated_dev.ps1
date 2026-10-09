[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("fresh", "clone")][string]$Mode,
    [Parameter(Mandatory = $true)][ValidateRange(1, 99)][int]$AttemptNumber,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string[]]$Labels,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$StateFile,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9a-fA-F]{64}$")][string]$StateSha256,
    [switch]$CleanupTemplate,
    [switch]$FullSuiteParity
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "integrated_state.ps1")
$originalLocation = Get-Location
$fullSuiteParityLabels = @(
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
    "tests.test_service_versions"
)
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

$failure = $null
$runProblems = [System.Collections.Generic.List[string]]::new()
$setupSeconds = $null
$templateBuildSeconds = $null
$instrumentIsolationVerdict = "NOT ESTABLISHED"
$wholeRun = $null
$script:teardownLines = 0

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

function Invoke-PythonAndLog {
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
        if ($lineText -match "^TT1_(?:FRESH|CLONE)_SETUP_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $script:setupSeconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
        }
        elseif ($lineText -match "^TT1_TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $script:templateBuildSeconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
        }
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

    if ($FullSuiteParity) {
        if ($Mode -ne "clone" -or -not $CleanupTemplate) {
            throw "full-suite parity mode requires clone mode with template cleanup"
        }
        if ($Labels.Count -ne $fullSuiteParityLabels.Count -or
            @($Labels | Where-Object { $_ -cnotin $fullSuiteParityLabels }).Count -gt 0 -or
            @($fullSuiteParityLabels | Where-Object { $_ -cnotin $Labels }).Count -gt 0) {
            throw "full-suite parity mode requires exactly the commissioned 89 verification entries"
        }
    }
    else {
        foreach ($label in $Labels) {
            if ($label -notmatch "^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){2,3}$") {
                throw "test label must be module.Class or module.Class.method"
            }
        }
    }
    if (@($Labels | Sort-Object -Unique).Count -ne $Labels.Count) {
        throw "duplicate test labels are refused"
    }
    if ($CleanupTemplate -and $Mode -ne "clone") {
        throw "template cleanup is only valid for a clone-mode run"
    }

    $authorizedState = Get-IntegratedState -StateFile $StateFile -StateSha256 $StateSha256
    $routes = Get-IntegratedEvidenceRoutes -AuthorizedState $authorizedState -HomeDirectory $HOME
    Write-Output "STATE_FILE=$($authorizedState.StateFile)"
    Write-Output "STATE_ID=$($authorizedState.StateId)"
    Write-Output "STATE_SHA256=$($authorizedState.StateSha256)"
    Write-Output "SOURCE_KIND=$($authorizedState.SourceKind)"
    Write-Output "HEAD=$($authorizedState.Head)"
    Write-Output "EVIDENCE_ROOT=$($authorizedState.EvidenceRoot)"
    Write-Output "DATABASE_TAG=$($authorizedState.DatabaseTag)"
    Write-Output "ME_DEV_ROOT=$($routes.DevelopmentRoot)"
    Write-Output "ME_DATABASE=$($routes.DevelopmentDatabasePrefix)$AttemptNumber"

    $missingCredentials = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        $present = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))
        Write-Output ("CREDENTIAL_{0}={1}" -f $name, $(if ($present) { "set" } else { "UNSET" }))
        if (-not $present) {
            $missingCredentials.Add($name)
        }
    }
    if ($missingCredentials.Count -gt 0) {
        throw ("Required credentials are UNSET; no attempt folder was created: " + ($missingCredentials -join ","))
    }
    if ($env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected administrative database is refused"
    }

    $stageRoot = $routes.DevelopmentRoot
    $runDirectory = Join-Path $stageRoot ("attempt_{0}" -f $AttemptNumber)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "development attempt directory already exists; refusing reuse"
    }
    $attemptDatabase = "$($routes.DevelopmentDatabasePrefix)$AttemptNumber"
    if ($attemptDatabase.Length -gt 63 -or $attemptDatabase -in @("intevia", "intevia_dev", $env:INTEVIA_POSTGRES_DB)) {
        throw "protected or overlength test database name refused"
    }

    $templateStore = Join-Path $stageRoot "integrated-template-store"

    New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "integrated-development.log"
    $runHeader = @(
        "STATE_FILE=$($authorizedState.StateFile)"
        "STATE_ID=$($authorizedState.StateId)"
        "STATE_SHA256=$($authorizedState.StateSha256)"
        "SOURCE_KIND=$($authorizedState.SourceKind)"
        "HEAD=$($authorizedState.Head)"
    )
    foreach ($line in $runHeader) {
        Write-Output $line
        Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
    }
    $effectiveTemplateStore = if ($Mode -eq "fresh") {
        Join-Path $runDirectory "tooling-template-store"
    }
    else {
        $templateStore
    }

    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_POSTGRES_TEST_DB = $attemptDatabase
    $env:INTEVIA_S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
    $env:VERIFICATION_RUN_NONCE = $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = "0044_sb2_birthing_snapshot"
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = $Mode
    $env:VERIFICATION_TEMPLATE_STORE = $effectiveTemplateStore
    $env:VERIFICATION_TEMPLATE_CLEANUP = if ($CleanupTemplate) { "1" } else { "0" }
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    $env:PYTHONUNBUFFERED = "1"
    $testPythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $testPythonPath += $env:PYTHONPATH
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $testPythonPath)

    Write-Output "MODE=$Mode"
    Write-Output "ATTEMPT=$AttemptNumber"
    Write-Output "DATABASE=$attemptDatabase"
    Write-Output "RUN_DIRECTORY=$runDirectory"
    Write-Output "TEMPLATE_STORE=$effectiveTemplateStore"
    Write-Output "TEMPLATE_CLEANUP=$($env:VERIFICATION_TEMPLATE_CLEANUP)"
    Write-Output "LABELS=$($Labels -join ',')"

    $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
    $checkCode = Invoke-PythonAndLog -Label "Django system check" -LogPath $logPath `
        -Arguments @("manage.py", "check", "--settings=intevia.test_settings")
    if ($checkCode -ne 0) {
        $runProblems.Add("Django system check returned status $checkCode")
    }
    else {
        $testArguments = @(
            "manage.py", "test", "--settings=intevia.test_settings",
            "--testrunner=verification.isolation.runner.IsolationRunner",
            "--parallel", "1", "--noinput", "--verbosity=2"
        ) + $Labels
        $testCode = Invoke-PythonAndLog -Label "Explicit-label development run" `
            -LogPath $logPath -Arguments $testArguments
        if ($testCode -ne 0) {
            $runProblems.Add("development test batch returned status $testCode")
        }
    }
    $wholeRun.Stop()

    if ($null -eq $setupSeconds) {
        $runProblems.Add("isolation runner did not report setup time")
    }
    if ($Mode -eq "clone" -and $null -eq $templateBuildSeconds) {
        $runProblems.Add("isolation runner did not report template build time")
    }
    if (Test-Path -LiteralPath $isolationPath -PathType Leaf) {
        try {
            $records = @(Get-Content -LiteralPath $isolationPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
            $cp0Records = @($records | Where-Object { $_.checkpoint -eq "CP-0" })
            $summaryRecords = @($records | Where-Object { $_.checkpoint -eq "SUMMARY" })
            if ($cp0Records.Count -ne 1 -or $cp0Records[0].requirements_met -ne $true) {
                $runProblems.Add("CP-0 did not pass exactly once")
            }
            if ($summaryRecords.Count -ne 1) {
                $runProblems.Add("isolation evidence must contain exactly one SUMMARY record")
            }
            else {
                $summary = $summaryRecords[0]
                $instrumentIsolationVerdict = [string]$summary.isolation_verdict
                if ($summary.isolation_verdict -ne "PASS" -or
                    $summary.cp0_requirements_met -ne $true -or
                    $null -eq $summary.ordinary_test_verdict -or
                    $summary.ordinary_test_verdict.was_successful -ne $true) {
                    $runProblems.Add("isolation or ordinary test summary did not PASS")
                }
                $counts = $summary.ordinary_test_verdict
                Write-Output "ISOLATION_VERDICT=$($summary.isolation_verdict)"
                Write-Output "CP0_MET=$($summary.cp0_requirements_met)"
                Write-Output "TESTS_RUN=$($counts.tests_run)"
                Write-Output "TEST_FAILURES=$($counts.failures)"
                Write-Output "TEST_ERRORS=$($counts.errors)"
                Write-Output "TEST_SKIPPED=$($counts.skipped)"
                if ($Mode -eq "clone" -and $cp0Records.Count -eq 1 -and
                    ($cp0Records[0].template_identity -eq $null -or
                     $cp0Records[0].template_cp0_digest -ne $cp0Records[0].clone_cp0_digest)) {
                    $runProblems.Add("clone CP-0 did not match the integrated template digest")
                }
            }
        }
        catch {
            $runProblems.Add("isolation evidence could not be read: $($_.Exception.Message)")
        }
    }
    else { $runProblems.Add("isolation evidence file is absent") }

    try {
        $reconciliation = Get-RunDatabaseReconciliation `
            -ReceiptPath $receiptPath -LogPath $logPath -RunNonce $runNonce `
            -PrimaryDatabase $attemptDatabase -TeardownLines $script:teardownLines `
            -IsolationVerdict $instrumentIsolationVerdict
        Write-Output "DATABASE_CREATED_COUNT=$($reconciliation.created)"
        Write-Output "DATABASE_DESTROYED_COUNT=$($reconciliation.destroyed)"
        Write-Output "DATABASE_OIDS_MATCH=$($reconciliation.oids_match)"
        Write-Output "DATABASE_CONFIRMED_ABSENT=$($reconciliation.confirmed_absent)"
        Write-Output "DATABASE_COUNT=$($reconciliation.database_count)"
        foreach ($database in $reconciliation.databases) {
            Write-Output ("DATABASE_RECONCILIATION=" + ($database | ConvertTo-Json -Depth 8 -Compress))
        }
        foreach ($problem in $reconciliation.failure_reasons) {
            $runProblems.Add($problem)
        }

        $receipts = @()
        if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
            $receipts = @(Get-Content -LiteralPath $receiptPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
        }
        $primaryDatabaseReport = @($reconciliation.databases | Where-Object {
            $_.name -ceq $attemptDatabase
        } | Select-Object -First 1)
        $created = if ($primaryDatabaseReport.Count -eq 1) {
            @($primaryDatabaseReport[0].creation_receipts)
        }
        else {
            @()
        }

        if ($Mode -eq "clone") {
            $clones = @($receipts | Where-Object {
                $_.event -eq "clone_created" -and $_.name -eq $attemptDatabase -and $_.run_nonce -eq $runNonce
            })
            if ($clones.Count -ne 1 -or $created.Count -ne 1) {
                $runProblems.Add("clone ownership receipt does not bind the attempt and integrated template")
            }
            elseif ($clones[0].clone_oid -ne $created[0].oid -or
                $clones[0].template_oid -le 0 -or [string]::IsNullOrWhiteSpace($clones[0].template_receipt)) {
                $runProblems.Add("clone ownership receipt does not bind the attempt and integrated template")
            }
            elseif ($CleanupTemplate) {
                if (-not (Test-Path -LiteralPath $clones[0].template_receipt -PathType Leaf)) {
                    $runProblems.Add("integrated template receipt is absent after cleanup")
                }
                else {
                    $templateReceipt = Get-Content -LiteralPath $clones[0].template_receipt -Raw | ConvertFrom-Json -ErrorAction Stop
                    if ($templateReceipt.oid -ne $clones[0].template_oid -or
                        $templateReceipt.destroyed.oid -ne $templateReceipt.oid -or
                        $templateReceipt.destroyed.cleanup_run_nonce -ne $runNonce -or
                        $templateReceipt.destroyed.confirmed_absent -ne $true -or
                        $templateReceipt.final_clone_cp0_digest -ne $templateReceipt.cp0_digest) {
                        $runProblems.Add("integrated template cleanup receipt or final clone parity is invalid")
                    }
                    else {
                        Write-Output "TEMPLATE_DESTROYED_OID=$($templateReceipt.destroyed.oid)"
                        Write-Output "TEMPLATE_CONFIRMED_ABSENT=$($templateReceipt.destroyed.confirmed_absent)"
                        Write-Output "FINAL_CLONE_CP0_MATCHES_TEMPLATE=$($templateReceipt.final_clone_cp0_digest -eq $templateReceipt.cp0_digest)"
                    }
                }
            }
        }
    }
    catch {
        $runProblems.Add("database receipts or reconciliation could not be read: $($_.Exception.Message)")
    }

    Write-Output "RUN_LOG=$logPath"
}
catch {
    $failure = Protect-OutputText -Text $_.Exception.Message
    Write-Output "FAILURE=$failure"
}
finally {
    if ($null -ne $wholeRun -and $wholeRun.IsRunning) {
        $wholeRun.Stop()
    }
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

if ($null -eq $setupSeconds) {
    Write-Output "SETUP_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("SETUP_SECONDS={0:F6}" -f $setupSeconds)
}
if ($Mode -eq "fresh") {
    Write-Output "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE"
}
elseif ($null -eq $templateBuildSeconds) {
    Write-Output "TEMPLATE_BUILD_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("TEMPLATE_BUILD_SECONDS={0:F6}" -f $templateBuildSeconds)
}
if ($null -eq $wholeRun) {
    Write-Output "WHOLE_RUN_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("WHOLE_RUN_SECONDS={0:F6}" -f $wholeRun.Elapsed.TotalSeconds)
}
if ($null -eq $failure) {
    foreach ($problem in $runProblems) {
        Write-Output "FAILURE=$problem"
    }
    if ($runProblems.Count -eq 0) {
        Write-Output "DEV_RUN_VERDICT=PASS"
        $global:LASTEXITCODE = 0
    }
    else {
        Write-Output "DEV_RUN_VERDICT=FAIL"
        $global:LASTEXITCODE = 1
    }
}
else {
    Write-Output "FAILURE=$failure"
    foreach ($problem in $runProblems) {
        Write-Output "FAILURE=$problem"
    }
    Write-Output "DEV_RUN_VERDICT=FAIL"
    $global:LASTEXITCODE = 1
}
