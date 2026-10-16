[CmdletBinding()]
param(
    [switch]$HumanLaunch,
    [string]$StateFile = (Join-Path $HOME "INTEVIA_AGENT_STAGING\PKTB_TU2A\BINDINGS_B26\TU2A_STATE_S2.json"),
    [string]$StateSha256 = "5b2a2cb6867434b8161fb0fd2c40816357d48d4f1289d233e741a8ca9157d042"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
. (Join-Path $PSScriptRoot "integrated_state.ps1")

$lock1Hashes = [ordered]@{
    "src/intevia/services/organism_command_support.py" = "d081935a9a0b12f4384319e0b45abfa015ffe4ef"
    "src/intevia/services/organism_application_service.py" = "05dd081db5ca900c275c0b4aacd96500da96f5c1"
    "src/intevia/services/organism_governance_service.py" = "50dfa6f250593e1e34b221a3b0fea931707de082"
    "src/intevia/services/organism_membership_service.py" = "058436f2c9bc0cbfb59c861b72f8b9e06addbecb"
    "tests/test_s015_application_decision.py" = "18b8fc182a3a4c1f6783d4b80673f0c0c2c4b972"
    "tests/test_lock1_actor_lock_order.py" = "e67822ecb71c9681902168fd7ea2e7c091965c56"
}

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

$qualificationRoot = Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING\PKTB_TU2A") "ME_DEV"
$qualificationPlanReason = "Independent TU-2a qualification case; the latest failed test attempt in this evidence root is a deliberate qualification case or an earlier qualification attempt."
$leakPath = Join-Path $repoRoot "tests\test_tu2a_qualification_leak.py"
$leakBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($leakSource)
$leakHash = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($leakBytes)).ToLowerInvariant()
$caseResults = [System.Collections.Generic.List[object]]::new()
$createdLeakFile = $false
$qualificationDirectory = $null
$overall = "STOP"

function Assert-Lock1Bindings {
    param([string]$AuthorizedStateFile, [string]$AuthorizedStateSha256)

    if (-not (Test-Path -LiteralPath $AuthorizedStateFile -PathType Leaf)) {
        throw "R9: authorized TU-2a state file is absent"
    }
    $authorized = Get-IntegratedState -StateFile $AuthorizedStateFile -StateSha256 $AuthorizedStateSha256
    foreach ($path in $lock1Hashes.Keys) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "LOCK-1 protected path is absent: $path"
        }
        $actual = (& git hash-object -- $path).Trim().ToLowerInvariant()
        if ($LASTEXITCODE -ne 0 -or $actual -cne $lock1Hashes[$path]) {
            throw "LOCK-1 protected blob differs: $path"
        }
    }
    return $authorized
}

function New-Plan {
    param(
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string[]]$Labels,
        [Parameter(Mandatory = $true)][string]$PlanLog,
        [Parameter(Mandatory = $true)][string]$RunRoot,
        [Parameter(Mandatory = $true)][string]$NewParentPlanReason
    )

    $arguments = @(
        "-B", "-m", "verification.isolation.run_plan", "plan",
        "--output", $OutputPath,
        "--run-root", $RunRoot,
        "--new-parent-plan-reason", $NewParentPlanReason,
        "--qualification-case"
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
            "--plan", $OutputPath,
            "--run-root", $RunRoot,
            "--new-parent-plan-reason", $NewParentPlanReason,
            "--qualification-case"
        )
        $validationOutput = @(& python @validationArguments 2>&1)
        $validationExitCode = $LASTEXITCODE
        $output += $validationOutput
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
        -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "R3/R4/R5: plan resolution or launcher-equivalent validation failed for $($Labels -join ','): $($output -join ' | ')"
    }
    $plan = Get-Content -LiteralPath $OutputPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    return [PSCustomObject]@{
        Path = $OutputPath
        Data = $plan
        Sha256 = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        IdentifierCharacters = ((@($plan.tests) -join "`n") + "`n").Length
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
        [string]$ExpectedPlanSha256,
        [Parameter(Mandatory = $true)][string]$NewParentPlanReason
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
        "-PlanFile", $PlanPath,
        "-NewParentPlanReason", $NewParentPlanReason,
        "-QualificationCase"
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
    $authorizedState = Assert-Lock1Bindings -AuthorizedStateFile $StateFile -AuthorizedStateSha256 $StateSha256
    if (-not (Test-Path -LiteralPath (Join-Path $qualificationRoot "TU2A_IMPACT_RECORD.json") -PathType Leaf)) {
        throw "impact record is absent"
    }
    $null = Get-Content -LiteralPath (Join-Path $qualificationRoot "TU2A_IMPACT_RECORD.json") -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop

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

    $plans = [ordered]@{}
    $plans["Q-A"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-a-plan.json") `
        -Labels $qaLabels `
        -PlanLog (Join-Path $qualificationDirectory "q-a-plan.log") `
        -RunRoot $qualificationRoot `
        -NewParentPlanReason $qualificationPlanReason
    $qaTestCount = @($plans["Q-A"].Data.tests).Count
    $qaIdentifierCharacters = [int]$plans["Q-A"].IdentifierCharacters
    if ($qaTestCount -ne 311 -or $qaIdentifierCharacters -ne 34774) {
        throw "Q-A: loader selection differs from commissioned thresholds; actual tests=$qaTestCount identifier_characters=$qaIdentifierCharacters"
    }
    $plans["Q-B1"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-b1-plan.json") `
        -Labels @("tests.test_tu2a_qualification_leak.RowLeakQualificationTests") `
        -PlanLog (Join-Path $qualificationDirectory "q-b1-plan.log") `
        -RunRoot $qualificationRoot `
        -NewParentPlanReason $qualificationPlanReason
    $plans["Q-B2"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-b2-plan.json") `
        -Labels @("tests.test_tu2a_qualification_leak.SeedLeakQualificationTests") `
        -PlanLog (Join-Path $qualificationDirectory "q-b2-plan.log") `
        -RunRoot $qualificationRoot `
        -NewParentPlanReason $qualificationPlanReason
    $plans["Q-C"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-c-plan.json") `
        -Labels @("tests.test_s015_role_command.S015HistoricalAuthorityReferenceReplayTests") `
        -PlanLog (Join-Path $qualificationDirectory "q-c-plan.log") `
        -RunRoot $qualificationRoot `
        -NewParentPlanReason $qualificationPlanReason
    $plans["Q-D"] = New-Plan `
        -OutputPath (Join-Path $qualificationDirectory "q-d-plan.json") `
        -Labels @(
            "tests.test_s015_application_schema.ApplicationLineageSchema.test_application_chain_map_triggers_and_lock_route",
            "tests.test_s015_application_schema.HistoricalActorBytes.test_c1_historical_party_record_set_commitments_and_actor",
            "tests.test_s015_application_schema.ApplicationAuthorityMigration.test_0027_schema_and_historical_chain_bytes"
        ) `
        -PlanLog (Join-Path $qualificationDirectory "q-d-plan.log") `
        -RunRoot $qualificationRoot `
        -NewParentPlanReason $qualificationPlanReason
    if (@($plans["Q-B1"].Data.tests).Count -ne 5 -or
        @($plans["Q-B2"].Data.tests).Count -ne 5 -or
        @($plans["Q-C"].Data.tests).Count -lt 1 -or
        @($plans["Q-D"].Data.tests).Count -ne 3) {
        throw "Q-B/Q-C/Q-D plan sizes differ from the commission"
    }

    if (-not $HumanLaunch) {
        $overall = "DRY_CHECK"
        Write-Output "DRY_CHECK=PASS; bindings and Q-A/Q-B/Q-C/Q-D plans resolved; no qualification run launched"
        return
    }

    $nextSuffix = 910001
    foreach ($case in @("Q-A", "Q-B1", "Q-B2", "Q-C", "Q-D")) {
        while (Test-Path -LiteralPath (Join-Path $qualificationRoot ("attempt_{0}" -f $nextSuffix))) {
            $nextSuffix++
        }
        $caseLog = Join-Path $qualificationDirectory ($case.ToLowerInvariant() + ".log")
        $expectedPlanDigest = if ($case -ceq "Q-C") { "0" * 64 } else { $null }
        $run = Start-QualificationRun `
            -Case $case `
            -PlanPath $plans[$case].Path `
            -LogPath $caseLog `
            -RunSuffix $nextSuffix `
            -ExpectedPlanSha256 $expectedPlanDigest `
            -NewParentPlanReason $qualificationPlanReason
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
            "Candidate binding and per-run records are retained in `$qualificationDirectory`.",
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
