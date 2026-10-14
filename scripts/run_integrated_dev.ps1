[CmdletBinding()]
param(
    [ValidateSet("fresh", "clone")][string]$Mode = "clone",
    [int]$AttemptNumber = 0,
    [ValidatePattern("^[1-9][0-9]*[ab]?$")][string]$RunSuffix,
    [string[]]$Labels = @(),
    [string]$StateFile,
    [string]$StateSha256,
    [switch]$CleanupTemplate,
    [switch]$FullSuiteParity,
    [switch]$FailFast,
    [ValidateRange(1, 60)][int]$StallMinutes = 5,
    [switch]$StallGuardSelfTest,
    [switch]$LaunchSelfTest,
    [string]$FreshAuthorityFile,
    [string]$FreshAuthoritySha256,
    [string[]]$OrderedGroups = @(),
    [string]$ParentPlanFile,
    [string]$PrecedingRunRecordFile,
    [string]$NewParentPlanReason,
    [ValidateSet("NONE", "A", "B")][string]$RotationSide = "NONE"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "integrated_guards.ps1")
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ($LaunchSelfTest) {
    Set-Location -LiteralPath $repoRoot
    $selfTestEnvironment = @{
        DJANGO_SETTINGS_MODULE = "intevia.test_settings"
        INTEVIA_S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
        INTEVIA_POSTGRES_TEST_DB = "test_intevia_living_organism_tu1_me_selftest"
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
    if ($selfTestExitCode -ne 0) { throw "R9: development launch self-test failed" }
    return
}
if (-not $FailFast) { throw "R1: fail-fast is mandatory and cannot be disabled" }
if ((-not $Labels -or $Labels.Count -eq 0) -and $RotationSide -eq "NONE") {
    throw "R3: at least one test label is required for a new parent plan"
}
if ([string]::IsNullOrWhiteSpace($StateFile) -or
    $StateSha256 -cnotmatch "^[0-9a-fA-F]{64}$") {
    throw "R9: authorized state file and digest are required"
}
Write-IntegratedGuardSummary -FailFast:$FailFast -StallMinutes $StallMinutes
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
    "tests.test_service_versions",
    "tests.test_sbg_governance",
    "tests.test_se_entitlement"
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
    "VERIFICATION_TEMPLATE_NAMESPACE",
    "VERIFICATION_TEMPLATE_CLEANUP",
    "INTEVIA_RUN_PLAN_FILE",
    "INTEVIA_RUN_PLAN_SHA256",
    "INTEVIA_RUN_SPEC_SHA256",
    "INTEVIA_RUN_SUFFIX",
    "INTEVIA_CANDIDATE_BINDING_SHA256",
    "INTEVIA_FRESH_AUTHORITY_FILE",
    "INTEVIA_FRESH_AUTHORITY_SHA256",
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
$templateAction = $null
$instrumentIsolationVerdict = "NOT ESTABLISHED"
$wholeRun = $null
$script:teardownLines = 0
$script:discoveredTestCount = $null
$script:testCountsSummaryReported = $false
$script:testStartsBeforeStall = 0
$script:testPassesBeforeStall = 0
$script:lastTestLine = "UNAVAILABLE"
$script:watchdogStalled = $false

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
        if ($lineText -match "^Found ([0-9]+) test\(s\)\.?$") {
            $script:discoveredTestCount = [int]$Matches[1]
        }
        if ($lineText -match "Destroying test database for alias") {
            $script:teardownLines++
        }
    }
    $status = $LASTEXITCODE
    return [int]$status
}

function Write-PreRunRefusalRecord {
    param(
        [Parameter(Mandatory = $true)][string]$RunDirectory,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$RefusalStage,
        [Parameter(Mandatory = $true)][string]$PlanHash,
        [Parameter(Mandatory = $true)]$PlanData,
        [Parameter(Mandatory = $true)][string]$CandidateBinding
    )

    $parentDigest = if ($PlanData.parent_plan_sha256) {
        [string]$PlanData.parent_plan_sha256
    }
    else {
        $PlanHash
    }
    $refusalRecord = [ordered]@{
        latest = $true
        classification = "PRE_RUN_REFUSAL"
        result = "REFUSED"
        refusal_stage = $RefusalStage
        plan_sha256 = $PlanHash
        parent_plan_sha256 = $parentDigest
        rotation_side = [string]$PlanData.rotation_side
        rotation_unit = [string]$PlanData.rotation_unit
        candidate = $CandidateBinding
        parent = $parentDigest
        tests_planned = [int]$PlanData.tests.Count
        tests_executed = 0
        tests_not_reached = [int]$PlanData.tests.Count
        executed_matches_plan = "NOT_STARTED"
    }
    $runRecordPath = Join-Path $RunDirectory "run-record.json"
    [System.IO.File]::WriteAllText(
        $runRecordPath,
        (($refusalRecord | ConvertTo-Json -Depth 8) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    $recordLine = "RUN_RECORD_CLASSIFICATION=PRE_RUN_REFUSAL"
    Write-Output $recordLine
    Add-Content -LiteralPath $LogPath -Value $recordLine -Encoding utf8
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
            throw "full-suite parity mode requires exactly the commissioned 91 verification entries"
        }
    }
    else {
        foreach ($label in $Labels) {
            if ($label -notmatch "^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){1,3}$") {
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
    if ($NewParentPlanReason -match "[\r\n]") {
        throw "R5: -NewParentPlanReason must be a single line"
    }
    if ($RotationSide -ne "NONE" -and
        -not [string]::IsNullOrWhiteSpace($NewParentPlanReason)) {
        throw "R5: -NewParentPlanReason is only valid for an unrotated new parent plan"
    }

    $authorizedState = Get-IntegratedState -StateFile $StateFile -StateSha256 $StateSha256
    $routes = Get-IntegratedEvidenceRoutes -AuthorizedState $authorizedState -HomeDirectory $HOME
    $stageRoot = $routes.DevelopmentRoot
    if ($AttemptNumber -lt 1 -and $RunSuffix) {
        if ($RunSuffix -notmatch "^([1-9][0-9]*)[ab]?$") {
            throw "R5: run suffix is invalid"
        }
        $AttemptNumber = [int]$Matches[1]
    }
    if ($AttemptNumber -lt 1) {
        $AttemptNumber = 1
        while (Test-Path -LiteralPath (Join-Path $stageRoot ("attempt_{0}" -f $AttemptNumber))) {
            $AttemptNumber++
        }
    }
    $runSuffix = if ($RunSuffix) { $RunSuffix } else { [string]$AttemptNumber }
    if ($runSuffix -cnotmatch ("^{0}[ab]?$" -f [regex]::Escape([string]$AttemptNumber))) {
        throw "R5: run suffix must match the attempt number, optionally followed by a or b"
    }
    Write-Output "STATE_FILE=$($authorizedState.StateFile)"
    Write-Output "STATE_ID=$($authorizedState.StateId)"
    Write-Output "STATE_SHA256=$($authorizedState.StateSha256)"
    Write-Output "SOURCE_KIND=$($authorizedState.SourceKind)"
    Write-Output "HEAD=$($authorizedState.Head)"
    Write-Output "EVIDENCE_ROOT=$($authorizedState.EvidenceRoot)"
    Write-Output "DATABASE_TAG=$($authorizedState.DatabaseTag)"
    Write-Output "ME_DEV_ROOT=$($routes.DevelopmentRoot)"
    Write-Output "ME_DATABASE=$($routes.DevelopmentDatabasePrefix)$runSuffix"

    $missingCredentials = [System.Collections.Generic.List[string]]::new()
    $presentEnvironmentNames = @(Get-ChildItem Env: | ForEach-Object { $_.Name })
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        $present = $name -cin $presentEnvironmentNames
        Write-Output ("CREDENTIAL_{0}={1}" -f $name, $(if ($present) { "set" } else { "UNSET" }))
        if (-not $present) {
            $missingCredentials.Add($name)
        }
    }
    if ($missingCredentials.Count -gt 0) {
        throw ("Required credentials are UNSET; no attempt folder was created: " + ($missingCredentials -join ","))
    }
    $freshAuthorityDigest = "NONE"
    if ($Mode -eq "fresh") {
        if ([string]::IsNullOrWhiteSpace($FreshAuthorityFile) -or
            $FreshAuthoritySha256 -cnotmatch "^[0-9a-fA-F]{64}$" -or
            -not (Test-Path -LiteralPath $FreshAuthorityFile -PathType Leaf)) {
            throw "R2: fresh mode requires an authority file and SHA-256"
        }
        $freshAuthorityDigest = (Get-FileHash -LiteralPath $FreshAuthorityFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($freshAuthorityDigest -cne $FreshAuthoritySha256.ToLowerInvariant()) {
            throw "R2: fresh authority digest mismatch"
        }
        $authorityText = Get-Content -LiteralPath $FreshAuthorityFile -Raw -ErrorAction Stop
        if ($authorityText -notmatch "(?i)fresh" -or
            $authorityText -notmatch [regex]::Escape($runSuffix) -or
            $authorityText -notmatch "(?i)Human Governor|commission") {
            throw "R2: authority must identify its issuer, fresh mode, and this run suffix"
        }
    }
    elseif ($FreshAuthorityFile -or $FreshAuthoritySha256) {
        throw "R2: fresh authority inputs are not valid in clone mode"
    }
    Write-Output "FRESH_AUTHORITY=$freshAuthorityDigest"
    Write-Output "MODE=$Mode"
    Write-Output "RUN_SPEC_SHA256=$($authorizedState.State.run_spec_sha256)"
    Write-Output "VERIFICATION_KIND=DEVELOPMENT"
    if ($StallGuardSelfTest) {
        $selfCheckLogPath = Join-Path $stageRoot ("STALL_GUARD_SELF_CHECK_{0}.log" -f $runSuffix)
        $selfCheckDatabase = "$($routes.DevelopmentDatabasePrefix)selfcheck_1"
        if (Test-Path -LiteralPath $selfCheckLogPath) {
            $priorSelfCheckLines = @(Get-Content -LiteralPath $selfCheckLogPath)
            if ($priorSelfCheckLines.Count -ne 3 -or
                $priorSelfCheckLines[0] -cne "STALL_GUARD_SELF_CHECK=STARTED" -or
                $priorSelfCheckLines[1] -cne "SELF_CHECK_DATABASE_NAME=$selfCheckDatabase" -or
                $priorSelfCheckLines[2] -notmatch (
                    "^FAILED_SETUP_ATTEMPT=Python executable selection failed; " +
                    "no database created and no child started; recorded=\d{4}-\d{2}-\d{2} " +
                    "\d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}$"
                )) {
                throw "existing self-check log does not contain only the authorized failed-setup record"
            }
        }
        else {
            New-Item -ItemType File -Path $selfCheckLogPath | Out-Null
            Write-WatchdogLogLine `
                -LineText "STALL_GUARD_SELF_CHECK=STARTED" `
                -LogPath $selfCheckLogPath
            Write-WatchdogLogLine `
                -LineText "SELF_CHECK_DATABASE_NAME=$selfCheckDatabase" `
                -LogPath $selfCheckLogPath
        }
        $presenceLines = @(Invoke-RunnerDatabasePython `
            -Action "check-self-check" `
            -DatabaseName $selfCheckDatabase `
            -DatabaseOwnershipRoot $routes.DevelopmentDatabasePrefix `
            -Numbering "selfcheck_1")
        $presenceMatches = @($presenceLines | Where-Object {
            $_ -match "^SELF_CHECK_DATABASE_EXISTS=(True|False)$"
        })
        if ($presenceMatches.Count -ne 1) {
            throw "stall-guard self-check database absence could not be confirmed"
        }
        if ($presenceMatches[0] -ceq "SELF_CHECK_DATABASE_EXISTS=True") {
            Write-WatchdogLogLine `
                -LineText "SELF_CHECK_DATABASE_EXISTS=True" `
                -LogPath $selfCheckLogPath
            Write-Output "SELF_CHECK_DATABASE_NAME=$selfCheckDatabase"
            throw "stall-guard self-check database already exists; refusing reuse"
        }
        Write-WatchdogLogLine `
            -LineText "SELF_CHECK_DATABASE_EXISTS=False" `
            -LogPath $selfCheckLogPath
        Write-WatchdogLogLine `
            -LineText ("SELF_CHECK_RUN=STARTED " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")) `
            -LogPath $selfCheckLogPath
        $createLines = @(Invoke-RunnerDatabasePython `
            -Action "create-self-check" `
            -DatabaseName $selfCheckDatabase `
            -DatabaseOwnershipRoot $routes.DevelopmentDatabasePrefix `
            -Numbering "selfcheck_1")
        $createdOidLines = @($createLines | Where-Object {
            $_ -match "^CREATED_OID=[0-9]+$"
        })
        $createdOidMatch = if ($createdOidLines.Count -eq 1) {
            [regex]::Match($createdOidLines[0], "^CREATED_OID=([0-9]+)$")
        }
        if ($createdOidLines.Count -ne 1 -or -not $createdOidMatch.Success) {
            throw "stall-guard self-check database creation was not confirmed"
        }
        $selfCheckOid = [long]$createdOidMatch.Groups[1].Value
        Write-WatchdogLogLine -LineText "SELF_CHECK_DATABASE_CREATED=True" -LogPath $selfCheckLogPath
        Write-WatchdogLogLine -LineText "SELF_CHECK_DATABASE_OID=$selfCheckOid" -LogPath $selfCheckLogPath

        $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
        $selfCheckCommand = (
            "import time; print('test_stall_guard_self_check " +
            "(tests.stall_guard.selfcheck) ...', flush=True); time.sleep(360)"
        )
        $selfCheckResult = Invoke-WatchedPythonAndLog `
            -Label "Stall-guard self-check child" `
            -Arguments @("-c", $selfCheckCommand) `
            -LogPath $selfCheckLogPath `
            -RunRoot $stageRoot `
            -DatabaseOwnershipRoot $routes.DevelopmentDatabasePrefix `
            -Numbering "selfcheck_1" `
            -StallThresholdMinutes $StallMinutes `
            -DatabaseName $selfCheckDatabase `
            -DatabaseOid $selfCheckOid `
            -SelfCheck
        $wholeRun.Stop()
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_STALL_DETECTED=$($selfCheckResult.Stalled)"
        ) -LogPath $selfCheckLogPath
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_PROCESS_ENDED=$($selfCheckResult.ProcessEnded)"
        ) -LogPath $selfCheckLogPath
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_DATABASE_CONFIRMED_ABSENT=$($selfCheckResult.DatabaseAbsent)"
        ) -LogPath $selfCheckLogPath
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_ELAPSED_SECONDS=$([Math]::Round($selfCheckResult.ElapsedSeconds, 3))"
        ) -LogPath $selfCheckLogPath
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_STALL_DETECTED_AFTER_SECONDS=$(
                [Math]::Round($selfCheckResult.StallDetectedSeconds, 3)
            )"
        ) -LogPath $selfCheckLogPath
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_CHILD_EXIT_CODE=$($selfCheckResult.ExitCode)"
        ) -LogPath $selfCheckLogPath
        $stallRecorded = "SELF_CHECK_STALL_DETECTED=True" -in @(
            Get-Content -LiteralPath $selfCheckLogPath -ErrorAction Stop
        )
        Write-WatchdogLogLine -LineText (
            "SELF_CHECK_STALL_RECORDED=$stallRecorded"
        ) -LogPath $selfCheckLogPath

        $selfCheckPassed = $selfCheckResult.Stalled -and
            $selfCheckResult.ProcessEnded -and
            $selfCheckResult.DatabaseAbsent -and
            $stallRecorded -and
            $selfCheckResult.ExitCode -ne 0 -and
            $null -ne $selfCheckResult.StallDetectedSeconds -and
            $selfCheckResult.StallDetectedSeconds -ge ($StallMinutes * 60) -and
            $selfCheckResult.StallDetectedSeconds -lt (($StallMinutes * 60) + 1) -and
            $selfCheckResult.Failures.Count -eq 0
        Write-WatchdogLogLine -LineText (
            "STALL_GUARD_SELF_CHECK_VERDICT=$(if ($selfCheckPassed) { 'PASS' } else { 'FAIL' })"
        ) -LogPath $selfCheckLogPath
        if (-not $selfCheckPassed) {
            throw "stall-guard self-check did not satisfy its required outcomes"
        }

        Write-Output "FAILURE=stall-guard self-check completed as expected"
        Write-Output "TESTS_EXECUTED=NOT_APPLICABLE"
        Write-Output "TESTS_DISCOVERED=NOT_APPLICABLE"
        Write-Output "TESTS_NOT_REACHED=NOT_APPLICABLE"
        Write-Output "SETUP_SECONDS=NOT_APPLICABLE"
        Write-Output "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE"
        Write-Output ("WHOLE_RUN_SECONDS={0:F6}" -f $wholeRun.Elapsed.TotalSeconds)
        Write-Output "DEV_RUN_VERDICT=STALL"
        Write-Output "SELF_CHECK_EXIT_CODE=$($selfCheckResult.ExitCode)"
        Write-Output "SELF_CHECK_DATABASE_NAME=$selfCheckDatabase"
        Write-Output "SELF_CHECK_LOG=$selfCheckLogPath"
        $global:LASTEXITCODE = 1
        return
    }

    $runDirectory = Join-Path $stageRoot ("attempt_{0}" -f $runSuffix)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "development attempt directory already exists; refusing reuse"
    }
    $attemptDatabase = "$($routes.DevelopmentDatabasePrefix)$runSuffix"
    if ($attemptDatabase.Length -gt 63 -or $attemptDatabase -in @("intevia", "intevia_dev")) {
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
    foreach ($line in @(
        "MODE=$Mode"
        "FRESH_AUTHORITY=$freshAuthorityDigest"
        "FAILFAST=ON"
        "RUN_SPEC_SHA256=$($authorizedState.State.run_spec_sha256)"
        "VERIFICATION_KIND=DEVELOPMENT"
    )) {
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
    $env:VERIFICATION_EXPECTED_CORE_HEAD = "0046_se_entitlement_foundation"
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = $Mode
    $env:VERIFICATION_TEMPLATE_STORE = $effectiveTemplateStore
    $env:VERIFICATION_TEMPLATE_NAMESPACE = "me"
    $env:VERIFICATION_TEMPLATE_CLEANUP = if ($CleanupTemplate) { "1" } else { "0" }
    $env:INTEVIA_RUN_SUFFIX = $runSuffix
    if ($Mode -eq "fresh") {
        $env:INTEVIA_FRESH_AUTHORITY_FILE = (Resolve-Path -LiteralPath $FreshAuthorityFile).Path
        $env:INTEVIA_FRESH_AUTHORITY_SHA256 = $freshAuthorityDigest
    }
    else {
        Remove-Item Env:INTEVIA_FRESH_AUTHORITY_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:INTEVIA_FRESH_AUTHORITY_SHA256 -ErrorAction SilentlyContinue
    }
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    $env:PYTHONUNBUFFERED = "1"
    $testPythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $testPythonPath += $env:PYTHONPATH
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $testPythonPath)
    $env:INTEVIA_RUN_SPEC_SHA256 = [string]$authorizedState.State.run_spec_sha256
    $bindingCode = Invoke-PythonAndLog `
        -Label "Candidate source binding" `
        -LogPath $logPath `
        -Arguments @(
            "-m", "verification.isolation.run_plan", "candidate-binding",
            "--root", $repoRoot, "--state", $authorizedState.StateFile
        )
    if ($bindingCode -ne 0) { throw "R9: candidate binding could not be calculated" }
    $bindingLine = @(Get-Content -LiteralPath $logPath |
        Where-Object { $_ -match "^CANDIDATE_BINDING_SHA256=[0-9a-f]{64}$" } | Select-Object -Last 1)
    if ($bindingLine.Count -ne 1) { throw "R9: candidate binding output is missing" }
    $candidateBinding = ($bindingLine[0] -replace "^CANDIDATE_BINDING_SHA256=", "")
    $env:INTEVIA_CANDIDATE_BINDING_SHA256 = $candidateBinding
    Write-Output $bindingLine[0]
    $planFile = Join-Path $runDirectory "run-plan.json"
    if ($RotationSide -eq "NONE") {
        if ($ParentPlanFile -or $PrecedingRunRecordFile) {
            throw "R5: rotation basis is incomplete or unexpected for a new parent plan"
        }
        $planArguments = @(
            "-m", "verification.isolation.run_plan", "plan",
            "--output", $planFile, "--run-root", $stageRoot
        )
        if (-not [string]::IsNullOrWhiteSpace($NewParentPlanReason)) {
            $planArguments += @("--new-parent-plan-reason", $NewParentPlanReason)
        }
        foreach ($label in $Labels) { $planArguments += @("--label", $label) }
        foreach ($group in $OrderedGroups) { $planArguments += @("--group", $group) }
    }
    else {
        if (-not $ParentPlanFile -or -not $PrecedingRunRecordFile -or
            -not (Test-Path -LiteralPath $ParentPlanFile -PathType Leaf) -or
            -not (Test-Path -LiteralPath $PrecedingRunRecordFile -PathType Leaf)) {
            throw "R5: rotated launch requires the bound parent plan and preceding run record"
        }
        if (-not $runSuffix.EndsWith($RotationSide.ToLowerInvariant())) {
            throw "R5: rotation side does not match the run suffix"
        }
        $planArguments = @(
            "-m", "verification.isolation.run_plan", "rotate",
            "--parent", $ParentPlanFile,
            "--preceding", $PrecedingRunRecordFile,
            "--side", $RotationSide,
            "--output", $planFile
        )
    }
    $planCode = Invoke-PythonAndLog -Label "R3/R4 no-database plan" -LogPath $logPath -Arguments $planArguments
    if ($planCode -ne 0) {
        $r5RefusalLine = @(Get-Content -LiteralPath $logPath |
            Where-Object { $_ -match "^R5_REFUSAL=R5:" } | Select-Object -Last 1)
        if ($r5RefusalLine.Count -eq 1 -and
            (Test-Path -LiteralPath $planFile -PathType Leaf)) {
            $planHash = (Get-FileHash -LiteralPath $planFile -Algorithm SHA256).Hash.ToLowerInvariant()
            $planData = Get-Content -LiteralPath $planFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            Write-PreRunRefusalRecord `
                -RunDirectory $runDirectory `
                -LogPath $logPath `
                -RefusalStage "R5" `
                -PlanHash $planHash `
                -PlanData $planData `
                -CandidateBinding $candidateBinding
            throw ($r5RefusalLine[0] -replace "^R5_REFUSAL=", "")
        }
        throw "R3/R4: labels did not resolve to a valid loader-built plan"
    }
    $planLine = @(Get-Content -LiteralPath $logPath | Where-Object { $_ -match "^PLAN_SHA256=[0-9a-f]{64}$" } | Select-Object -Last 1)
    if ($planLine.Count -ne 1 -or $planLine[0] -notmatch "^PLAN_SHA256=([0-9a-f]{64})$") {
        throw "R4: plan digest was not recorded"
    }
    $planHash = $Matches[1]
    $planData = Get-Content -LiteralPath $planFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $env:INTEVIA_RUN_PLAN_FILE = $planFile
    $env:INTEVIA_RUN_PLAN_SHA256 = $planHash
    $env:INTEVIA_RUN_SUFFIX = $runSuffix
    $precheckCode = Invoke-PythonAndLog `
        -Label "R14 changed-test no-database pre-check" `
        -LogPath $logPath `
        -Arguments @(
            "-m", "verification.isolation.run_plan", "precheck",
            "--root", $repoRoot, "--state", $authorizedState.StateFile
        )
        if ($precheckCode -ne 0) {
            Write-PreRunRefusalRecord `
                -RunDirectory $runDirectory `
                -LogPath $logPath `
                -RefusalStage "R14" `
                -PlanHash $planHash `
                -PlanData $planData `
                -CandidateBinding $candidateBinding
            throw "R14: changed-test no-database pre-check refused the launch"
        }

        Write-Output "ATTEMPT=$AttemptNumber"
    Write-Output "RUN_SUFFIX=$runSuffix"
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
        $preflightCode = 0
        if ($Mode -eq "clone") {
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
if template_receipt is None:
    raise RuntimeError("template preparation returned no receipt")

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
                $preflightCode = Invoke-PythonAndLog `
                    -Label "Clone template preflight" `
                    -LogPath $logPath `
                    -Arguments @("-c", $preflightCodeSource)
            }
            finally {
                $env:PGOPTIONS = $previousPgOptions
            }
            if ($preflightCode -ne 0) {
                $runProblems.Add("clone template preflight returned status $preflightCode; tests were not started")
            }
            else {
                $preflightLines = @(Get-Content -LiteralPath $logPath -ErrorAction Stop)
                $actionLines = @($preflightLines | Where-Object { $_ -match "^TEMPLATE_ACTION=(BUILT|REUSED)$" })
                $buildLines = @($preflightLines | Where-Object { $_ -match "^TEMPLATE_BUILD_SECONDS=(?:[0-9]+(?:\.[0-9]+)?|NOT_APPLICABLE)$" })
                if ($actionLines.Count -ne 1 -or $buildLines.Count -ne 1) {
                    $runProblems.Add("R8: clone preflight omitted template action or build timing")
                }
                else {
                    $templateAction = $actionLines[0].Substring("TEMPLATE_ACTION=".Length)
                    if ($templateAction -eq "BUILT" -and
                        $buildLines[0] -match "^TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
                        $templateBuildSeconds = [double]::Parse(
                            $Matches[1], [System.Globalization.CultureInfo]::InvariantCulture
                        )
                    }
                    elseif ($templateAction -ne "REUSED" -or
                        $buildLines[0] -ne "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE") {
                        $runProblems.Add("R8: template action and build timing contradict")
                    }
                }
            }
        }
        if ($preflightCode -eq 0) {
            $testArguments = @(
                "manage.py", "test", "--settings=intevia.test_settings",
                "--testrunner=verification.isolation.runner.IsolationRunner",
                "--parallel", "1", "--noinput", "--verbosity=2"
            )
            if ($FailFast) {
                $testArguments += "--failfast"
            }
            $testArguments += $Labels
            $testRun = Invoke-WatchedPythonAndLog `
                -Label "Explicit-label development run" `
                -LogPath $logPath `
                -Arguments $testArguments `
                -RunRoot $runDirectory `
                -DatabaseOwnershipRoot $routes.DevelopmentDatabasePrefix `
                -Numbering $runSuffix `
                -StallThresholdMinutes $StallMinutes `
                -DatabaseName $attemptDatabase `
                -ReceiptPath $receiptPath `
                -RunNonce $runNonce
            $testCode = $testRun.ExitCode
            if ($testCode -ne 0) {
                $runProblems.Add("development test batch returned status $testCode")
            }
        }
    }
    $wholeRun.Stop()

    if ($null -eq $setupSeconds) {
        $runProblems.Add("isolation runner did not report setup time")
    }
    if ($Mode -eq "clone" -and $null -eq $templateAction) {
        $runProblems.Add("clone preflight did not report the template action")
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
                if ($summary.plan_sha256 -cne $planHash -or
                    ($null -ne $script:printedSummary.discovered -and
                        [long]$summary.tests_planned -ne [long]$script:printedSummary.discovered) -or
                    $summary.executed_matches_plan -notin @("FULL", "PREFIX")) {
                    $runProblems.Add("R7: captured execution does not bind to the validated plan")
                }
                Write-Output "TESTS_PLANNED=$($summary.tests_planned)"
                Write-Output "TESTS_EXECUTED=$($summary.tests_executed)"
                Write-Output "TESTS_NOT_REACHED=$($summary.tests_not_reached)"
                Write-Output "EXECUTED_MATCHES_PLAN=$($summary.executed_matches_plan)"
                Write-Output "PARENT_PLAN_SHA256=$($summary.parent_plan_sha256)"
                Write-Output "ROTATION=$($summary.rotation)"
                Write-Output "ROTATION_UNIT=$($summary.rotation_unit)"
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
                if ($null -eq $counts) {
                    Write-Output "TESTS_EXECUTED=UNAVAILABLE"
                    Write-Output "TESTS_DISCOVERED=UNAVAILABLE"
                    Write-Output "TESTS_NOT_REACHED=UNAVAILABLE"
                    $runProblems.Add("ordinary test result counts were unavailable")
                }
                else {
                    Write-Output "TESTS_EXECUTED=$($counts.tests_run)"
                    if ($null -eq $script:discoveredTestCount) {
                        Write-Output "TESTS_DISCOVERED=UNAVAILABLE"
                        Write-Output "TESTS_NOT_REACHED=UNAVAILABLE"
                        $runProblems.Add("test discovery count was unavailable")
                    }
                    else {
                        $discoveredCount = $script:discoveredTestCount
                        $executedCount = [int]$counts.tests_run
                        Write-Output "TESTS_DISCOVERED=$discoveredCount"
                        if ($executedCount -gt $discoveredCount) {
                            Write-Output "TESTS_NOT_REACHED=INVALID"
                            $runProblems.Add("executed test count exceeded the discovered test count")
                        }
                        else {
                            $notReachedCount = $discoveredCount - $executedCount
                            Write-Output "TESTS_NOT_REACHED=$notReachedCount"
                            if ($notReachedCount -gt 0) {
                                $runProblems.Add(
                                    "test batch stopped before all discovered tests ran; verification is incomplete"
                                )
                            }
                        }
                    }
                }
                $script:testCountsSummaryReported = $true
                Write-Output "TEST_FAILURES=$($counts.failures)"
                Write-Output "TEST_ERRORS=$($counts.errors)"
                Write-Output "TEST_SKIPPED=$($counts.skipped)"
                if ($Mode -eq "clone" -and $cp0Records.Count -eq 1 -and
                    ($cp0Records[0].template_identity -eq $null -or
                     $cp0Records[0].template_cp0_digest -ne $cp0Records[0].clone_cp0_digest)) {
                    $runProblems.Add("clone CP-0 did not match the integrated template digest")
                }
                $runResult = if ($counts.errors -gt 0) { "ERROR" }
                    elseif ($counts.failures -gt 0) { "FAIL" }
                    else { "PASS" }
                $parentDigest = if ($summary.parent_plan_sha256 -eq "NONE") {
                    $planHash
                }
                else {
                    [string]$summary.parent_plan_sha256
                }
                $record = [ordered]@{
                    latest = $true
                    classification = "TEST_ATTEMPT"
                    result = $runResult
                    plan_sha256 = [string]$summary.plan_sha256
                    parent_plan_sha256 = $parentDigest
                    rotation_side = [string]$summary.rotation
                    rotation_failure = if ($summary.rotation_failure) {
                        [string]$summary.rotation_failure
                    }
                    else {
                        [string]$summary.failing_test
                    }
                    failing_test = [string]$summary.failing_test
                    candidate = $candidateBinding
                    parent = $parentDigest
                    tests_planned = [int]$summary.tests_planned
                    tests_executed = [int]$summary.tests_executed
                    tests_not_reached = [int]$summary.tests_not_reached
                    executed_matches_plan = [string]$summary.executed_matches_plan
                }
                $runRecordPath = Join-Path $runDirectory "run-record.json"
                [System.IO.File]::WriteAllText(
                    $runRecordPath,
                    (($record | ConvertTo-Json -Depth 8) + "`n"),
                    [System.Text.UTF8Encoding]::new($false)
                )
                Write-Output "RUN_RECORD_FILE=$runRecordPath"
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

if (-not $script:testCountsSummaryReported) {
    Write-Output "TESTS_EXECUTED=UNAVAILABLE"
    Write-Output "TESTS_DISCOVERED=UNAVAILABLE"
    Write-Output "TESTS_NOT_REACHED=UNAVAILABLE"
}
if ($null -eq $setupSeconds) {
    Write-Output "SETUP_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("SETUP_SECONDS={0:F6}" -f $setupSeconds)
}
if ($Mode -eq "fresh") {
    Write-Output "TEMPLATE_ACTION=NOT_APPLICABLE"
    Write-Output "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE"
}
elseif ($null -eq $templateAction) {
    Write-Output "TEMPLATE_ACTION=UNAVAILABLE"
    Write-Output "TEMPLATE_BUILD_SECONDS=UNAVAILABLE"
}
else {
    Write-Output "TEMPLATE_ACTION=$templateAction"
    if ($templateAction -eq "BUILT" -and $null -ne $templateBuildSeconds) {
        Write-Output ("TEMPLATE_BUILD_SECONDS={0:F6}" -f $templateBuildSeconds)
    }
    else {
        Write-Output "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE"
    }
}
if ($null -eq $wholeRun) {
    Write-Output "WHOLE_RUN_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("WHOLE_RUN_SECONDS={0:F6}" -f $wholeRun.Elapsed.TotalSeconds)
}
if ($script:watchdogStalled) {
    foreach ($problem in $runProblems) {
        Write-Output "FAILURE=$problem"
    }
    Write-Output "DEV_RUN_VERDICT=STALL"
    $global:LASTEXITCODE = 1
}
elseif ($null -eq $failure) {
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
