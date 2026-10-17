[CmdletBinding()]
param(
    [switch]$HumanLaunch,
    [string]$StateFile = (Join-Path $HOME "INTEVIA_AGENT_STAGING\PKTB_TU2A_CI\BINDINGS\TU2ACI_STATE_S1_v2.json"),
    [string]$StateSha256 = "b75a3c363e424faafb4795c8bac5795331a82ef75673c12384abfffd7c10a4d7"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
. (Join-Path $PSScriptRoot "integrated_state.ps1")

$qaLabels = @(
    "tests.test_service_activity_service",
    "tests.test_event_attendance_foundation",
    "tests.test_service_authority",
    "tests.test_service_activity_readback",
    "tests.test_observation_journal",
    "tests.test_s009_personal_event_home",
    "tests.test_service_activity_models",
    "tests.test_event_resource_relationship_readback",
    "tests.test_s010_direct_self_registration",
    "tests.test_profile_effect_service",
    "tests.test_library_exact_version_contract",
    "tests.test_event_resource_relationship_service",
    "tests.test_event_registration_foundation",
    "tests.test_education_course_service",
    "tests.test_tt1_template_lifecycle"
)

$leakSource = @'
from django.db import connection
from django.test import TransactionTestCase


class _Tu2aLeakCase(TransactionTestCase):
    databases = {"default"}

    def _post_teardown(self):
        if getattr(self, "_leave_state", False):
            return
        super()._post_teardown()


class RowLeakQualificationTests(_Tu2aLeakCase):
    def test_00_before(self):
        pass

    def test_01_leave_row(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_role (name, description, created_at) "
                "VALUES ('TU2A_QB_ROW_LEAK', 'qualification probe', now())"
            )
        self._leave_state = True

    def test_02_after(self):
        pass

    def test_03_tail(self):
        pass

    def test_04_last(self):
        pass


class SeedLeakQualificationTests(_Tu2aLeakCase):
    def test_00_before(self):
        pass

    def test_01_change_description(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core_role SET description = description || ' TU2A_QB2' "
                "WHERE name = (SELECT name FROM core_role ORDER BY name LIMIT 1)"
            )
        self._leave_state = True

    def test_02_after(self):
        pass

    def test_03_tail(self):
        pass

    def test_04_last(self):
        pass
'@

$qualificationRoot = $null
$impactRecordPath = $null
$impactRecordSha256 = "cfb4cd7c050cf1d818fce0705411059661c1b9ee01d479325150d9c9d1d7da21"
$impactRecordSource = "C:\Users\Carewen\INTEVIA_AGENT_STAGING\PKTB_TU2A\ME_DEV\TU2A_IMPACT_RECORD.json"
$leakPath = Join-Path $repoRoot "tests\test_tu2a_qualification_leak.py"
$leakBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($leakSource)
$leakHash = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($leakBytes)).ToLowerInvariant()
$caseResults = [System.Collections.Generic.List[object]]::new()
$createdLeakFile = $false
$qualificationDirectory = $null
$candidateBinding = $null
$overall = "STOP"

function Assert-StateBinding {
    param([string]$AuthorizedStateFile, [string]$AuthorizedStateSha256)

    if (-not (Test-Path -LiteralPath $AuthorizedStateFile -PathType Leaf)) {
        throw "R9: authorized TU-2a state file is absent"
    }
    return Get-IntegratedState -StateFile $AuthorizedStateFile -StateSha256 $AuthorizedStateSha256
}

function Get-CandidateBinding {
    param([Parameter(Mandatory = $true)][string]$AuthorizedStateFile)

    $output = @(& python -B -m verification.isolation.run_plan candidate-binding `
        --root $repoRoot --state $AuthorizedStateFile 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "R9: candidate binding calculation failed: $($output -join ' | ')"
    }
    $bindingLines = @($output | Where-Object { $_ -match "^CANDIDATE_BINDING_SHA256=[0-9a-f]{64}$" })
    if ($bindingLines.Count -ne 1) {
        throw "R9: candidate binding output is missing or ambiguous"
    }
    return ($bindingLines[0] -replace "^CANDIDATE_BINDING_SHA256=", "")
}

function New-Plan {
    param(
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string[]]$Labels,
        [Parameter(Mandatory = $true)][string]$PlanLog
    )

    $arguments = @(
        "-B", "-m", "verification.isolation.run_plan", "plan",
        "--output", $OutputPath
    )
    foreach ($label in $Labels) {
        $arguments += @("--label", $label)
    }
    $pythonPathItem = Get-Item -LiteralPath "Env:PYTHONPATH" -ErrorAction SilentlyContinue
    $hadPythonPath = $null -ne $pythonPathItem
    $oldPythonPath = if ($hadPythonPath) { [string]$pythonPathItem.Value } else { $null }
    $pythonPaths = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($oldPythonPath)) {
        $pythonPaths += $oldPythonPath
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $pythonPaths)
    try {
        $output = @(& python @arguments 2>&1)
        $exitCode = $LASTEXITCODE
        $validationArguments = @(
            "-B", "-m", "verification.isolation.run_plan", "validate-plan",
            "--plan", $OutputPath
        )
        $validationOutput = @(& python @validationArguments 2>&1)
        $validationExitCode = $LASTEXITCODE
        $output += $validationOutput
        $planDigest = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $precheckOutput = @(& python -B -m verification.isolation.run_plan precheck `
            --root $repoRoot --state $StateFile 2>&1)
        $precheckExitCode = $LASTEXITCODE
        $output += $precheckOutput
        $precheckResults = @($precheckOutput | Where-Object {
            $_ -match "^PRECHECK_RESULT=(PASS|NONE_QUALIFY)$"
        })
        $precheckClasses = @($precheckOutput | Where-Object {
            $_ -match "^PRECHECK_CLASSES="
        })
        $precheckConnections = @($precheckOutput | Where-Object {
            $_ -ceq "PRECHECK_CONNECTION_ATTEMPTS=0"
        })
        $routeCode = @'
import os
import sys
from pathlib import Path

os.environ["DJANGO_SETTINGS_MODULE"] = "intevia.test_settings"
os.environ["INTEVIA_S015_TEST_ROUTE"] = "LIVING_ORGANISM_TEST_LIFECYCLE"
os.environ["INTEVIA_RUN_PLAN_FILE"] = sys.argv[1]
os.environ["INTEVIA_RUN_PLAN_SHA256"] = sys.argv[2]
os.environ["VERIFICATION_RUN_NONCE"] = "TU2AFU_DRY_CHECK"
os.environ["VERIFICATION_STEP"] = "QUALIFICATION_DRY_CHECK"

from verification.isolation.run_plan import (
    _forbid_database_connections,
    _setup_test_django,
    _standalone_action_environment,
)
from verification.isolation.runner import IsolationRunner

with _standalone_action_environment(Path.cwd(), "validate-plan"):
    connections = _setup_test_django("R7")
    attempts = {"count": 0}
    with _forbid_database_connections(connections, attempts, "R7"):
        runner = IsolationRunner(verbosity=0, interactive=False, keepdb=False)
        runner.build_suite()
    if attempts["count"]:
        raise SystemExit("R7: runner suite build attempted a database connection")
print("RUNNER_SUITE_BUILD=PASS R7=PASS CONNECTION_ATTEMPTS=0")
'@
        $routeOutput = @(& python -B -c $routeCode $OutputPath $planDigest 2>&1)
        $routeExitCode = $LASTEXITCODE
        $output += $routeOutput
        $routeEvidence = @($routeOutput | Where-Object {
            $_ -ceq "RUNNER_SUITE_BUILD=PASS R7=PASS CONNECTION_ATTEMPTS=0"
        })
    }
    finally {
        if ($hadPythonPath) {
            $env:PYTHONPATH = $oldPythonPath
        }
        else {
            Remove-Item -LiteralPath "Env:PYTHONPATH" -ErrorAction SilentlyContinue
        }
    }
    $output | Set-Content -LiteralPath $PlanLog -Encoding utf8
    if ($exitCode -ne 0 -or $validationExitCode -ne 0 -or
        $precheckExitCode -ne 0 -or $precheckResults.Count -ne 1 -or
        $precheckClasses.Count -ne 1 -or $precheckConnections.Count -ne 1 -or
        $routeExitCode -ne 0 -or $routeEvidence.Count -ne 1 -or
        -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "R3/R4/R7: plan resolution, launcher-equivalent validation, or no-database runner suite build failed for $($Labels -join ','): $($output -join ' | ')"
    }
    $plan = Get-Content -LiteralPath $OutputPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    return [PSCustomObject]@{
        Path = $OutputPath
        Data = $plan
        Sha256 = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        IdentifierCharacters = ((@($plan.tests) -join "`n") + "`n").Length
        RouteEvidence = "$($precheckResults[0]) $($precheckClasses[0]) $($precheckConnections[0]); $($routeEvidence[0])"
    }
}

function Get-RunRecord {
    param([Parameter(Mandatory = $true)][string]$LogPath)

    $directoryLine = @(Get-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^RUN_DIRECTORY=" } | Select-Object -Last 1)
    if ($directoryLine.Count -eq 1) {
        $directory = $directoryLine[0].Substring("RUN_DIRECTORY=".Length)
        $path = Join-Path $directory "run-record.json"
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return [PSCustomObject]@{
                Path = $path
                Data = (Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
                Directory = $directory
            }
        }
    }
    $recordLine = @(Get-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^RUN_RECORD_FILE=" } | Select-Object -Last 1)
    if ($recordLine.Count -eq 1) {
        $path = $recordLine[0].Substring("RUN_RECORD_FILE=".Length)
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return [PSCustomObject]@{
                Path = $path
                Data = (Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
                Directory = (Split-Path -Parent $path)
            }
        }
    }
    return $null
}

function Start-QualificationRun {
    param(
        [Parameter(Mandatory = $true)][string]$Case,
        [Parameter(Mandatory = $true)][string]$PlanPath,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][int]$RunSuffix,
        [string]$ExpectedPlanSha256
    )

    $hostExecutable = (Get-Process -Id $PID -ErrorAction Stop).Path
    if ([string]::IsNullOrWhiteSpace($hostExecutable)) {
        throw "qualification PowerShell executable could not be resolved"
    }
    $arguments = @(
        "-NoProfile", "-File", (Join-Path $PSScriptRoot "run_integrated_dev.ps1"),
        "-Mode", "clone", "-FailFast",
        "-RunSuffix", [string]$RunSuffix,
        "-StateFile", $StateFile, "-StateSha256", $StateSha256,
        "-PlanFile", $PlanPath
    )
    if ($ExpectedPlanSha256) {
        $arguments += @("-PlanExpectedSha256", $ExpectedPlanSha256)
    }
    $global:LASTEXITCODE = 0
    & $hostExecutable @arguments *> $LogPath
    $exitCode = $LASTEXITCODE
    return [PSCustomObject]@{
        ExitCode = $exitCode
        Record = Get-RunRecord -LogPath $LogPath
        LogPath = $LogPath
        Case = $Case
    }
}

function Add-CaseResult {
    param(
        [string]$Name,
        [string]$Expected,
        [string]$Observed,
        [bool]$Passed,
        [string]$RecordPath,
        [string]$LogPath
    )
    $digest = if ($LogPath -and (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        (Get-FileHash -LiteralPath $LogPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    else {
        "UNAVAILABLE"
    }
    $caseResults.Add([PSCustomObject]@{
        Name = $Name
        Expected = $Expected
        Observed = $Observed
        Result = if ($Passed) { "PASS" } else { "STOP" }
        RecordPath = $RecordPath
        LogSha256 = $digest
    })
    Write-Output "$Name | expected=$Expected | observed=$Observed | $(if ($Passed) { 'PASS' } else { 'STOP' })"
}

function Test-SuccessfulIsolationRun {
    param(
        [Parameter(Mandatory = $true)]$Run,
        [Parameter(Mandatory = $true)][int]$ExpectedTests,
        [switch]$RequireFullCapture
    )

    if ($null -eq $Run.Record -or
        $Run.Record.Data.classification -cne "TEST_ATTEMPT" -or
        $Run.Record.Data.result -cne "PASS" -or
        $Run.Record.Data.dev_run_verdict -cne "PASS" -or
        $Run.Record.Data.isolation_verdict -cne "PASS" -or
        $Run.Record.Data.executed_matches_plan -cne "FULL" -or
        [int]$Run.Record.Data.tests_planned -ne $ExpectedTests -or
        [int]$Run.Record.Data.tests_executed -ne $ExpectedTests) {
        return $false
    }
    $isolationPath = Join-Path $Run.Record.Directory "isolation.jsonl"
    if (-not (Test-Path -LiteralPath $isolationPath -PathType Leaf)) {
        return $false
    }
    $records = @(Get-Content -LiteralPath $isolationPath | ForEach-Object { $_ | ConvertFrom-Json })
    $cpz = @($records | Where-Object { $_.checkpoint -ceq "CP-Z" -and $_.established -eq $true })
    $cpa = @($records | Where-Object { $_.checkpoint -ceq "CP-A" })
    if ($cpz.Count -ne 1 -or $cpa.Count -ne $ExpectedTests) {
        return $false
    }
    if ($RequireFullCapture) {
        if (@($cpa | Where-Object { $_.capture -cne "full" -or $_.trigger -cne "declared" }).Count -gt 0) {
            return $false
        }
    }
    if (@(Get-Content -LiteralPath $Run.LogPath | Where-Object { $_ -match "^TIMING:" }).Count -lt 1) {
        return $false
    }
    return $true
}

try {
    $authorizedState = Assert-StateBinding -AuthorizedStateFile $StateFile -AuthorizedStateSha256 $StateSha256
    if ($authorizedState.StateId -cne "TU2ACI_S1" -or
        $authorizedState.DatabaseTag -cne "tu2aci" -or
        $authorizedState.EvidenceRoot -cne "PKTB_TU2A_CI") {
        throw "R9: authorized state does not bind the TU2A CI qualification"
    }
    $routes = Get-IntegratedEvidenceRoutes `
        -AuthorizedState $authorizedState `
        -HomeDirectory $HOME
    $impactRecordPath = $impactRecordSource
    $stagingRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $HOME "INTEVIA_AGENT_STAGING")
    )
    $qualificationRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $stagingRoot $authorizedState.EvidenceRoot)
    )
    $stagingPrefix = $stagingRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
        [System.IO.Path]::DirectorySeparatorChar
    if (-not $qualificationRoot.StartsWith(
        $stagingPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "R9: evidence root resolves outside INTEVIA_AGENT_STAGING"
    }
    if (-not (Test-Path -LiteralPath $impactRecordPath -PathType Leaf)) {
        throw "impact record is absent"
    }
    $actualImpactRecordSha256 = (Get-FileHash -LiteralPath $impactRecordPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualImpactRecordSha256 -cne $impactRecordSha256) {
        throw "R9: Stage 0 impact census digest differs from its accepted binding"
    }
    $null = Get-Content -LiteralPath $impactRecordPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    Write-Output "IMPACT_RECORD_SHA256=$actualImpactRecordSha256"

    if (-not (Test-Path -LiteralPath $qualificationRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $qualificationRoot -Force -ErrorAction Stop | Out-Null
    }

    if ($HumanLaunch) {
        $qualificationDirectory = Join-Path $qualificationRoot ("qualification_" + [guid]::NewGuid().ToString("N"))
    }
    else {
        $qualificationDirectory = Join-Path $qualificationRoot ("dry_check_" + [guid]::NewGuid().ToString("N"))
    }
    New-Item -ItemType Directory -Path $qualificationDirectory -ErrorAction Stop | Out-Null

    if (Test-Path -LiteralPath $leakPath) {
        throw "qualification leak module already exists; refusing to overwrite"
    }
    [System.IO.File]::WriteAllBytes($leakPath, $leakBytes)
    $createdLeakFile = $true
    $actualLeakHash = (Get-FileHash -LiteralPath $leakPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualLeakHash -cne $leakHash) {
        throw "qualification leak module bytes differ from the generated source"
    }
    $candidateBinding = Get-CandidateBinding -AuthorizedStateFile $StateFile

    $plans = [ordered]@{}
    $plans["Q-A"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-a-plan.json") `
        -Labels $qaLabels `
        -PlanLog (Join-Path $qualificationDirectory "q-a-plan.log")
    $qaTestCount = @($plans["Q-A"].Data.tests).Count
    $qaIdentifierCharacters = [int]$plans["Q-A"].IdentifierCharacters
    if ($qaTestCount -ne 311 -or $qaIdentifierCharacters -ne 34774) {
        throw "Q-A: loader selection differs from commissioned thresholds; actual tests=$qaTestCount identifier_characters=$qaIdentifierCharacters"
    }
    $plans["Q-B1"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-b1-plan.json") `
        -Labels @("tests.test_tu2a_qualification_leak.RowLeakQualificationTests") `
        -PlanLog (Join-Path $qualificationDirectory "q-b1-plan.log")
    $plans["Q-B2"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-b2-plan.json") `
        -Labels @("tests.test_tu2a_qualification_leak.SeedLeakQualificationTests") `
        -PlanLog (Join-Path $qualificationDirectory "q-b2-plan.log")
    $plans["Q-C"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-c-plan.json") `
        -Labels @("tests.test_s015_role_command.S015HistoricalAuthorityReferenceReplayTests") `
        -PlanLog (Join-Path $qualificationDirectory "q-c-plan.log")
    $plans["Q-D"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-d-plan.json") `
        -Labels @(
            "tests.test_s015_application_schema.ApplicationLineageSchema.test_application_chain_map_triggers_and_lock_route",
            "tests.test_s015_application_schema.HistoricalActorBytes.test_c1_historical_party_record_set_commitments_and_actor",
            "tests.test_s015_application_schema.ApplicationAuthorityMigration.test_0027_schema_and_historical_chain_bytes"
        ) `
        -PlanLog (Join-Path $qualificationDirectory "q-d-plan.log")
    if (@($plans["Q-B1"].Data.tests).Count -ne 5 -or
        @($plans["Q-B2"].Data.tests).Count -ne 5 -or
        @($plans["Q-C"].Data.tests).Count -lt 1 -or
        @($plans["Q-D"].Data.tests).Count -ne 3) {
        throw "Q-B/Q-C/Q-D plan sizes differ from the commission"
    }

    if (-not $HumanLaunch) {
        $overall = "DRY_CHECK"
        $leakRelativePath = "tests/test_tu2a_qualification_leak.py"
        $preflightFailures = [System.Collections.Generic.List[string]]::new()
        foreach ($case in @("Q-A", "Q-B1", "Q-B2", "Q-C", "Q-D")) {
            $launcherState = $null
            $caseCandidateBinding = $null
            $sourceKind = "UNAVAILABLE"
            $changedPathsText = "UNAVAILABLE"
            $changedPathStatus = "UNAVAILABLE"
            $candidateBindingStatus = "UNAVAILABLE"
            $preflightStatus = "FAIL"
            $preflightError = $null
            try {
                if (-not (Test-Path -LiteralPath $leakPath -PathType Leaf)) {
                    throw "throwaway leak module is absent"
                }
                $fixtureHash = (Get-FileHash -LiteralPath $leakPath -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($fixtureHash -cne $leakHash) {
                    throw "throwaway leak module bytes differ from the generated source"
                }
                $launcherState = Get-IntegratedState `
                    -StateFile $StateFile `
                    -StateSha256 $StateSha256
                $sourceKind = $launcherState.SourceKind
                $changedPaths = @($launcherState.ChangedPaths)
                $changedPathsText = $changedPaths -join ","
                if ($sourceKind -cne "CANDIDATE" -or
                    $launcherState.State.allowed_paths -cnotcontains $leakRelativePath -or
                    $changedPaths -cnotcontains $leakRelativePath) {
                    throw "launcher state validation did not accept the present leak module as an allowed candidate path"
                }
                $changedPathStatus = "PASS"
                $caseCandidateBinding = Get-CandidateBinding -AuthorizedStateFile $StateFile
                if ($caseCandidateBinding -cne $candidateBinding) {
                    throw "candidate binding differs from the qualification binding"
                }
                $candidateBindingStatus = "PASS"
                $preflightStatus = "PASS"
            }
            catch {
                $preflightError = $_.Exception.Message -replace "[\r\n|]", " "
                $preflightFailures.Add("$case`: $preflightError")
            }
            Write-Output "$case | LAUNCHER_STATE_CANDIDATE_VALIDATION=$preflightStatus | SOURCE_KIND=$sourceKind | CHANGED_PATHS_CHECK=$changedPathStatus | CHANGED_PATHS=$changedPathsText | CANDIDATE_BINDING_CHECK=$candidateBindingStatus | DATABASE_CONNECTIONS=0$(if ($preflightError) { " | ERROR=$preflightError" })"
        }
        if ($preflightFailures.Count -gt 0) {
            throw "launcher state/candidate validation failed: $($preflightFailures -join '; ')"
        }
        Write-Output "CANDIDATE_BINDING_SHA256=$candidateBinding"
        Write-Output "QUALIFICATION_DIRECTORY=$qualificationDirectory"
        foreach ($case in @("Q-A", "Q-B1", "Q-B2", "Q-C", "Q-D")) {
            $plan = $plans[$case]
            Write-Output "$case | PLAN_SHA256=$($plan.Sha256) | PLAN_DATABASE_ROUTE=$($plan.Data.database_route) | TESTS_PLANNED=$(@($plan.Data.tests).Count) | LABELS_PLANNED=$(@($plan.Data.labels).Count) | $($plan.RouteEvidence)"
        }
        Write-Output "DRY_CHECK=PASS; bindings, launcher state/candidate validation, and Q-A/Q-B/Q-C/Q-D plans resolved; no qualification run launched"
        return
    }

    $nextSuffix = 910001
    foreach ($case in @("Q-A", "Q-B1", "Q-B2", "Q-C", "Q-D")) {
        while (Test-Path -LiteralPath (Join-Path $routes.DevelopmentRoot ("attempt_{0}" -f $nextSuffix))) {
            $nextSuffix++
        }
        $caseLog = Join-Path $qualificationDirectory ($case.ToLowerInvariant() + ".log")
        $expectedPlanDigest = if ($case -ceq "Q-C") { "0" * 64 } else { $null }
        $run = Start-QualificationRun `
            -Case $case `
            -PlanPath $plans[$case].Path `
            -LogPath $caseLog `
            -RunSuffix $nextSuffix `
            -ExpectedPlanSha256 $expectedPlanDigest
        $nextSuffix++
        if ($case -ceq "Q-A") {
            $passed = $run.ExitCode -eq 0 -and
                (Test-SuccessfulIsolationRun -Run $run -ExpectedTests 311)
            Add-CaseResult -Name $case -Expected "PASS; R7 FULL; CP-0/CP-A/CP-Z; TIMING" `
                -Observed $(if ($run.Record) { "$($run.Record.Data.result)/$($run.Record.Data.executed_matches_plan)" } else { "NO_RUN_RECORD" }) `
                -Passed $passed `
                -RecordPath $(if ($run.Record) { $run.Record.Path } else { "UNAVAILABLE" }) `
                -LogPath $caseLog
        }
        elseif ($case -in @("Q-B1", "Q-B2")) {
            $leakId = if ($case -ceq "Q-B1") {
                "tests.test_tu2a_qualification_leak.RowLeakQualificationTests.test_01_leave_row"
            }
            else {
                "tests.test_tu2a_qualification_leak.SeedLeakQualificationTests.test_01_change_description"
            }
            $expectedMismatch = if ($case -ceq "Q-B1") { "Q2" } else { "Q4" }
            $isolationPath = if ($run.Record) { Join-Path $run.Record.Directory "isolation.jsonl" } else { $null }
            $mismatch = if ($isolationPath -and (Test-Path -LiteralPath $isolationPath -PathType Leaf)) {
                @(Get-Content -LiteralPath $isolationPath |
                    ForEach-Object { $_ | ConvertFrom-Json } |
                    Where-Object {
                        $_.checkpoint -ceq "CP-A" -and
                        $_.trigger -ceq "mismatch" -and
                        $_.capture -ceq "full" -and
                        $_.established -eq $false -and
                        $_.attributed_to -ceq $leakId -and
                        @($_.light_mismatches | Where-Object { $_ -match $expectedMismatch }).Count -gt 0
                    }).Count -gt 0
            }
            else {
                $false
            }
            $passed = $run.ExitCode -ne 0 -and $mismatch
            Add-CaseResult -Name $case -Expected "REFUSE after attributed $expectedMismatch mismatch with full diagnostic capture" `
                -Observed $(if ($mismatch) { "ATTRIBUTED_$expectedMismatch" } else { "UNEXPECTED_OUTCOME" }) `
                -Passed $passed `
                -RecordPath $(if ($run.Record) { $run.Record.Path } else { "UNAVAILABLE" }) `
                -LogPath $caseLog
        }
        elseif ($case -ceq "Q-C") {
            $refused = $run.Record -and
                $run.Record.Data.classification -ceq "PRE_RUN_REFUSAL" -and
                $run.Record.Data.refusal_stage -ceq "R7" -and
                $run.Record.Data.result -ceq "REFUSED"
            Add-CaseResult -Name $case -Expected "R7 refusal before database preparation" `
                -Observed $(if ($refused) { "R7_REFUSED" } else { "UNEXPECTED_OUTCOME" }) `
                -Passed $refused `
                -RecordPath $(if ($run.Record) { $run.Record.Path } else { "UNAVAILABLE" }) `
                -LogPath $caseLog
        }
        else {
            $passed = $run.ExitCode -eq 0 -and
                (Test-SuccessfulIsolationRun -Run $run -ExpectedTests 3 -RequireFullCapture)
            Add-CaseResult -Name $case -Expected "PASS; three full declared captures; CP-Z; TIMING" `
                -Observed $(if ($run.Record) { "$($run.Record.Data.result)/$($run.Record.Data.executed_matches_plan)" } else { "NO_RUN_RECORD" }) `
                -Passed $passed `
                -RecordPath $(if ($run.Record) { $run.Record.Path } else { "UNAVAILABLE" }) `
                -LogPath $caseLog
        }
        if ($caseResults[-1].Result -ne "PASS") {
            break
        }
    }
    $overall = if ($caseResults.Count -eq 5 -and
        @($caseResults | Where-Object { $_.Result -ne "PASS" }).Count -eq 0) {
        "PASS"
    }
    else {
        "STOP"
    }
}
catch {
    if ($HumanLaunch) {
        $caseResults.Add([PSCustomObject]@{
            Name = "PRECHECK"
            Expected = "all bindings and plans valid"
            Observed = "STOP: $($_.Exception.Message)"
            Result = "STOP"
            RecordPath = "UNAVAILABLE"
            LogSha256 = "UNAVAILABLE"
        })
    }
    else {
        Write-Output "DRY_CHECK=STOP; $($_.Exception.Message)"
    }
    $overall = "STOP"
}
finally {
    if ($createdLeakFile -and (Test-Path -LiteralPath $leakPath -PathType Leaf)) {
        $currentLeakHash = (Get-FileHash -LiteralPath $leakPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($currentLeakHash -ceq $leakHash) {
            Remove-Item -LiteralPath $leakPath -ErrorAction Stop
        }
        else {
            $overall = "STOP"
            $caseResults.Add([PSCustomObject]@{
                Name = "CLEANUP"
                Expected = "remove only the generated throwaway module"
                Observed = "STOP: throwaway module bytes changed"
                Result = "STOP"
                RecordPath = "UNAVAILABLE"
                LogSha256 = "UNAVAILABLE"
            })
        }
    }
    if ($HumanLaunch -and $qualificationDirectory) {
        foreach ($case in @("Q-A", "Q-B1", "Q-B2", "Q-C", "Q-D")) {
            if (@($caseResults | Where-Object { $_.Name -ceq $case }).Count -eq 0) {
                $caseResults.Add([PSCustomObject]@{
                    Name = $case
                    Expected = "run in qualification order"
                    Observed = "not reached"
                    Result = "STOP"
                    RecordPath = "UNAVAILABLE"
                    LogSha256 = "UNAVAILABLE"
                })
                Write-Output "$case | expected=run in qualification order | observed=not reached | STOP"
            }
        }
        $verdictPath = Join-Path $qualificationRoot "TU2A_QUALIFICATION_VERDICT.md"
        $lines = @(
            "# TU-2a Qualification Verdict",
            "",
            "Overall: $overall",
            "State SHA-256: $StateSha256",
            "Run specification SHA-256: $($authorizedState.State.run_spec_sha256)",
            "Impact record: $impactRecordPath",
            "Impact record SHA-256: $actualImpactRecordSha256",
            "Qualification directory: $qualificationDirectory",
            "Candidate binding SHA-256: $candidateBinding",
            ""
        )
        foreach ($result in $caseResults) {
            $lines += "- $($result.Name): expected $($result.Expected); observed $($result.Observed); $($result.Result); record $($result.RecordPath); log SHA-256 $($result.LogSha256)"
        }
        [System.IO.File]::WriteAllText(
            $verdictPath,
            (($lines -join "`n") + "`n"),
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Output "VERDICT=$overall"
    }
    elseif (-not $HumanLaunch -and $qualificationDirectory -and
        (Test-Path -LiteralPath $qualificationDirectory -PathType Container)) {
        $evidenceRoot = [System.IO.Path]::GetFullPath($qualificationRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
        $resolvedDirectory = [System.IO.Path]::GetFullPath($qualificationDirectory)
        if ($resolvedDirectory.StartsWith($evidenceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedDirectory -Recurse -Force -ErrorAction Stop
        }
    }
}

$successfulVerdict = if ($HumanLaunch) {
    $overall -eq "PASS"
}
else {
    $overall -eq "DRY_CHECK"
}
if ($successfulVerdict) {
    exit 0
}
exit 1
