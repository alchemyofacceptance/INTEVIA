[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$verifierPath = Join-Path $repoRoot "scripts\verify_integrated.ps1"
$developmentRunnerPath = Join-Path $repoRoot "scripts\run_integrated_dev.ps1"
$originalLocation = Get-Location
$originalHome = $HOME
$temporaryHome = Join-Path $env:TEMP ("int2-verifier-harness-" + [guid]::NewGuid().ToString("N"))
$stateFile = Join-Path $temporaryHome "INT2_TEST_STATE.json"
$testEvidenceRoot = "PKTB_TEST"
$testDatabaseTag = "test"
$stateSha256 = $null
$originalEnvironment = $null
$scenarioEnvironment = $null
$originalLastExitCode = $global:LASTEXITCODE
$harnessFailure = $null

function Get-EnvironmentSnapshot {
    $snapshot = [System.Collections.Generic.Dictionary[string, string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($item in Get-ChildItem Env:) {
        $snapshot[$item.Name] = [string]$item.Value
    }
    return ,$snapshot
}

function Test-EnvironmentSnapshot {
    param(
        [Parameter(Mandatory = $true)][System.Collections.Generic.Dictionary[string, string]]$Expected,
        [Parameter(Mandatory = $true)][System.Collections.Generic.Dictionary[string, string]]$Actual
    )

    if ($Expected.Count -ne $Actual.Count) { return $false }
    foreach ($name in $Expected.Keys) {
        if (-not $Actual.ContainsKey($name) -or $Expected[$name] -cne $Actual[$name]) {
            return $false
        }
    }
    return $true
}

function Restore-EnvironmentSnapshot {
    param([Parameter(Mandatory = $true)][System.Collections.Generic.Dictionary[string, string]]$Snapshot)

    foreach ($item in @(Get-ChildItem Env:)) {
        if (-not $Snapshot.ContainsKey($item.Name)) {
            Remove-Item -LiteralPath ("Env:{0}" -f $item.Name) -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in $Snapshot.Keys) {
        Set-Item -LiteralPath ("Env:{0}" -f $name) -Value $Snapshot[$name]
    }
}

function Assert-Harness {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) { throw $Message }
}

function python {
    $arguments = @($args | ForEach-Object { [string]$_ })
    if ($arguments.Count -gt 0 -and $arguments[0] -eq "-c") {
        $global:LASTEXITCODE = 0
        return
    }
    if ($arguments.Count -ge 2 -and $arguments[0] -eq "manage.py" -and $arguments[1] -eq "check") {
        Write-Output "INT1_CHECK_STREAM_BEFORE_EMPTY"
        Write-Output ""
        Write-Output "   "
        Write-Output "INT1_CHECK_STREAM_AFTER_WHITESPACE"
        Write-Output "System check identified no issues (0 silenced)."
        $global:LASTEXITCODE = 0
        return
    }
    if ($arguments.Count -lt 2 -or $arguments[0] -ne "manage.py" -or $arguments[1] -ne "test") {
        throw "unexpected Python invocation in the database-free harness"
    }

    $scenario = $env:INT1_HARNESS_SCENARIO
    $testCount = 4
    $failures = if ($scenario -eq "test_failure") { 1 } else { 0 }
    $errors = 0
    $skipped = if ($scenario -eq "pass") { 1 } else { 0 }
    $cp0 = $scenario -ne "isolation_failure"
    $isolation = if ($cp0) { "PASS" } else { "NOT ESTABLISHED" }
    $wasSuccessful = $failures -eq 0
    $testStatus = if ($wasSuccessful) { 0 } else { 1 }
    $database = $env:INTEVIA_POSTGRES_TEST_DB
    $nonce = $env:VERIFICATION_RUN_NONCE

    $cp0Record = [ordered]@{
        checkpoint = "CP-0"
        requirements_met = $cp0
        run_nonce = $nonce
    }
    $summary = [ordered]@{
        checkpoint = "SUMMARY"
        isolation_verdict = $isolation
        cp0_requirements_met = $cp0
        ordinary_test_verdict = [ordered]@{
            tests_run = $testCount
            errors = $errors
            failures = $failures
            skipped = $skipped
            was_successful = $wasSuccessful
        }
        run_nonce = $nonce
    }
    $isolationLines = @(
        ($cp0Record | ConvertTo-Json -Compress)
        ($summary | ConvertTo-Json -Compress)
    )
    [System.IO.File]::WriteAllLines(
        $env:VERIFICATION_ISOLATION_JSON,
        $isolationLines,
        [System.Text.UTF8Encoding]::new($false)
    )
    $receiptLines = @(
        ([ordered]@{ event = "created"; name = $database; oid = 81001; run_nonce = $nonce } |
            ConvertTo-Json -Compress)
    )
    if ($scenario -ne "teardown_failure") {
        $receiptLines += ([ordered]@{
            event = "destroyed"
            name = $database
            oid = 81001
            run_nonce = $nonce
            confirmed_absent = $true
        } | ConvertTo-Json -Compress)
    }
    [System.IO.File]::WriteAllLines(
        $env:VERIFICATION_DB_RECEIPT,
        $receiptLines,
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Output "INT1_TEST_STREAM_BEFORE_EMPTY"
    Write-Output ""
    Write-Output "   "
    Write-Output "INT1_TEST_STREAM_AFTER_WHITESPACE"
    $reportedCount = if ($scenario -eq "count_mismatch") { 3 } else { $testCount }
    Write-Output ("Ran {0} tests in 0.010s" -f $reportedCount)
    if ($wasSuccessful) {
        if ($skipped -gt 0) {
            Write-Output ("OK (skipped={0})" -f $skipped)
        }
        else {
            Write-Output "OK"
        }
    }
    else {
        Write-Output ("FAILED (failures={0}, errors={1}, skipped={2})" -f $failures, $errors, $skipped)
    }
    Write-Output ("ISOLATION VERDICT: {0}" -f $isolation)
    Write-Output "Destroying test database for alias 'default'..."
    $global:LASTEXITCODE = $testStatus
}

function Invoke-HarnessScenario {
    param(
        [Parameter(Mandatory = $true)][string]$Scenario,
        [Parameter(Mandatory = $true)][string]$RunName,
        [Parameter(Mandatory = $true)][string]$ScenarioLabel,
        [Parameter(Mandatory = $true)][string]$ExpectedVerdict
    )

    $scenarioWasPresent = Test-Path Env:\INT1_HARNESS_SCENARIO
    $previousScenario = $env:INT1_HARNESS_SCENARIO
    $env:INT1_HARNESS_SCENARIO = $Scenario
    $before = Get-EnvironmentSnapshot
    $output = @(& $verifierPath -StateFile $stateFile -StateSha256 $stateSha256 *>&1 |
        ForEach-Object { [string]$_ })
    $status = $global:LASTEXITCODE
    $after = Get-EnvironmentSnapshot
    $environmentRestored = Test-EnvironmentSnapshot -Expected $before -Actual $after
    if ($scenarioWasPresent) {
        $env:INT1_HARNESS_SCENARIO = $previousScenario
    }
    else {
        Remove-Item Env:\INT1_HARNESS_SCENARIO -ErrorAction SilentlyContinue
    }
    Assert-Harness -Condition $environmentRestored -Message "$RunName did not restore the environment"

    $verdictLines = @($output | Where-Object { $_ -eq "VERDICT: $ExpectedVerdict" })
    Assert-Harness -Condition ($verdictLines.Count -eq 1) -Message "$RunName did not produce exactly one expected VERDICT line"
    $runDirectory = Join-Path (Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") `
        (Join-Path $testEvidenceRoot "VERIFICATION_OF_RECORD")) $RunName
    $verdictPath = Join-Path $runDirectory "VERDICT.json"
    Assert-Harness -Condition (Test-Path -LiteralPath $verdictPath -PathType Leaf) `
        -Message "$RunName did not produce VERDICT.json"
    $document = Get-Content -LiteralPath $verdictPath -Raw | ConvertFrom-Json
    Assert-Harness -Condition ($document.verdict -eq $ExpectedVerdict) `
        -Message "$RunName VERDICT.json did not contain $ExpectedVerdict"
    Assert-Harness -Condition ($document.state_id -eq "INT2_TEST_STATE" -and
        $document.state_sha256 -eq $stateSha256) `
        -Message "$RunName VERDICT.json did not bind the authorized state"
    $logPath = Join-Path $runDirectory "integrated-fresh-suite.log"
    $loggedLines = [System.IO.File]::ReadAllLines($logPath)
    Assert-Harness -Condition ($loggedLines -contains "STATE_ID=INT2_TEST_STATE" -and
        $loggedLines -contains "STATE_SHA256=$stateSha256") `
        -Message "$RunName run header did not bind the authorized state"
    foreach ($prefix in @("INT1_CHECK_STREAM", "INT1_TEST_STREAM")) {
        $expectedLines = @(
            "${prefix}_BEFORE_EMPTY",
            [string]::Empty,
            "   ",
            "${prefix}_AFTER_WHITESPACE"
        )
        $start = [Array]::IndexOf($loggedLines, $expectedLines[0])
        $sequenceLogged = $start -ge 0 -and $start + $expectedLines.Count -le $loggedLines.Count
        if ($sequenceLogged) {
            for ($index = 0; $index -lt $expectedLines.Count; $index++) {
                if ($loggedLines[$start + $index] -cne $expectedLines[$index]) {
                    $sequenceLogged = $false
                    break
                }
            }
        }
        Assert-Harness -Condition $sequenceLogged `
            -Message "$RunName did not log the interleaved empty, whitespace, and normal streaming lines"
    }
    if ($ExpectedVerdict -eq "PASS") {
        Assert-Harness -Condition ($status -eq 0) -Message "$RunName returned a non-zero status on PASS"
    }
    else {
        Assert-Harness -Condition ($status -ne 0) -Message "$RunName returned zero on FAIL"
    }
    Write-Output $verdictLines[0]
    Write-Output ("{0}_JSON_VERDICT={1}" -f $ScenarioLabel, $document.verdict)
    Write-Output ("{0}_ENVIRONMENT_RESTORED={1}" -f $ScenarioLabel, $environmentRestored)
    Write-Output ("{0}_STREAM_SEQUENCE_LOGGED=True" -f $ScenarioLabel)
}

function Invoke-DevelopmentRunnerPreflight {
    param(
        [Parameter(Mandatory = $true)][string[]]$Labels,
        [string]$Mode = "clone",
        [switch]$CleanupTemplate,
        [switch]$FullSuiteParity
    )

    $parameters = @{
        Mode = $Mode
        AttemptNumber = 1
        Labels = $Labels
        StateFile = $stateFile
        StateSha256 = $stateSha256
    }
    if ($CleanupTemplate) { $parameters.CleanupTemplate = $true }
    if ($FullSuiteParity) { $parameters.FullSuiteParity = $true }
    $output = @(& $developmentRunnerPath @parameters 2>&1 | ForEach-Object { [string]$_ })
    return [PSCustomObject]@{
        Status = $global:LASTEXITCODE
        Text = $output -join "`n"
    }
}

try {
    Assert-Harness -Condition (Test-Path -LiteralPath $verifierPath -PathType Leaf) `
        -Message "integrated verifier script is absent"
    [System.IO.Directory]::CreateDirectory($temporaryHome) | Out-Null
    Set-Variable -Name HOME -Value $temporaryHome -Scope Script -Force
    $testState = [ordered]@{
        state_id = "INT2_TEST_STATE"
        branch = "pktb/int-r3-tt1"
        evidence_root = $testEvidenceRoot
        database_tag = $testDatabaseTag
        base_commit = "12f65f19bb5999eee3beb02cbd657c011882a460"
        integrated_root = "12f65f19bb5999eee3beb02cbd657c011882a460"
        merge = [ordered]@{
            commit = "af7d596d766811b0a8581bd830506c6320bdc729"
            parents = @(
                "a9dadb5e75106eb5b918a99d65e239526b166243"
                "76291565e040e0833670be330e1e2c1b5c7b346a"
            )
        }
        allowed_paths = @(
            "scripts/integrated_state.ps1"
            "scripts/run_integrated_dev.ps1"
            "scripts/verify_integrated.ps1"
            "tests/test_integrated_state.ps1"
            "tests/test_verify_integrated.ps1"
        )
        committed_child_allowed = $true
    }
    [System.IO.File]::WriteAllText(
        $stateFile,
        (($testState | ConvertTo-Json -Depth 6) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    $stateSha256 = (Get-FileHash -LiteralPath $stateFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $originalEnvironment = Get-EnvironmentSnapshot
    foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like "VERIFICATION_*" })) {
        Remove-Item -LiteralPath ("Env:{0}" -f $item.Name) -ErrorAction SilentlyContinue
    }
    $env:INTEVIA_DATABASE_ENGINE = "original-engine"
    $env:INTEVIA_POSTGRES_DB = "int1-harness-admin"
    $env:INTEVIA_POSTGRES_USER = "int1-harness-user"
    if ([string]::IsNullOrWhiteSpace($env:INTEVIA_POSTGRES_PASSWORD)) {
        $env:INTEVIA_POSTGRES_PASSWORD = "int1-harness-placeholder-not-a-secret"
    }
    if ([string]::IsNullOrWhiteSpace($env:DJANGO_SECRET_KEY)) {
        $env:DJANGO_SECRET_KEY = "int1-harness-placeholder-not-a-secret"
    }
    $env:INTEVIA_S015_TEST_ROUTE = "original-route"
    $env:DJANGO_SETTINGS_MODULE = "original-settings"
    $env:INTEVIA_POSTGRES_TEST_DB = "original-test-database"
    $env:PYTHONPATH = "original-python-path"
    $env:PYTHONUNBUFFERED = "0"
    $scenarioEnvironment = Get-EnvironmentSnapshot

    $script:runCounter = 0
    $nextRunName = {
        $script:runCounter++
        return "run_$script:runCounter"
    }
    $passRun = & $nextRunName
    Invoke-HarnessScenario -Scenario "pass" -RunName $passRun -ScenarioLabel "PASS_PATH" `
        -ExpectedVerdict "PASS"
    $passEnvironment = Test-EnvironmentSnapshot -Expected $scenarioEnvironment -Actual (Get-EnvironmentSnapshot)
    Assert-Harness -Condition $passEnvironment -Message "pass-path environment restoration check failed"
    Write-Output "PASS_PATH_ENVIRONMENT_RESTORED=True"

    foreach ($case in @(
        [PSCustomObject]@{ Scenario = "test_failure"; Name = "case_a_test_failure" },
        [PSCustomObject]@{ Scenario = "isolation_failure"; Name = "case_b_isolation_failure" },
        [PSCustomObject]@{ Scenario = "teardown_failure"; Name = "case_c_teardown_failure" },
        [PSCustomObject]@{ Scenario = "count_mismatch"; Name = "case_d_count_mismatch" }
    )) {
        $runName = & $nextRunName
        Invoke-HarnessScenario -Scenario $case.Scenario -RunName $runName `
            -ScenarioLabel $case.Name.ToUpperInvariant() -ExpectedVerdict "FAIL"
        $restored = Test-EnvironmentSnapshot -Expected $scenarioEnvironment -Actual (Get-EnvironmentSnapshot)
        Assert-Harness -Condition $restored -Message "$($case.Name) failure-path environment restoration check failed"
    }
    Write-Output "FAIL_PATH_ENVIRONMENT_RESTORED=True"

    Remove-Item Function:\python -ErrorAction Stop
    $pythonCommand = Get-Command python -ErrorAction Stop
    Assert-Harness -Condition ($pythonCommand.CommandType -eq "Application") `
        -Message "mock python was not removed or the real python command is unavailable"
    Write-Output "MOCK_PYTHON_REMOVED=True"
    Write-Output "GET_COMMAND_PYTHON=Application"
    Write-Output "DATABASE_ACCESS=NONE"

    $fullSuiteLabels = @(
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
        "tests.test_tt1_template_lifecycle"
    )
    $developmentEnvironment = Get-EnvironmentSnapshot
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        Remove-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
    }
    try {
        $attemptOne = Join-Path (Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") `
            (Join-Path $testEvidenceRoot "ME_DEV")) "attempt_1"
        Assert-Harness -Condition (-not (Test-Path -LiteralPath $attemptOne)) `
            -Message "attempt_1 already exists before database-free label preflight"

        $accepted = Invoke-DevelopmentRunnerPreflight -Labels $fullSuiteLabels `
            -CleanupTemplate -FullSuiteParity
        Assert-Harness -Condition ($accepted.Text.Contains("CREDENTIAL_INTEVIA_DATABASE_ENGINE=UNSET") -and
            -not $accepted.Text.Contains("full-suite parity mode requires") -and
            -not $accepted.Text.Contains("test label must be module.Class")) `
            -Message "exact commissioned full-suite module set did not pass label preflight"
        Assert-Harness -Condition (-not (Test-Path -LiteralPath $attemptOne)) `
            -Message "exact full-suite label preflight created an attempt folder"
        Write-Output "FULL_SUITE_MODULE_SET_ACCEPTED=True"

        $changedModuleSet = @($fullSuiteLabels)
        $changedModuleSet[0] = "tests.test_uncommissioned_module"
        $rejected = Invoke-DevelopmentRunnerPreflight -Labels $changedModuleSet `
            -CleanupTemplate -FullSuiteParity
        Assert-Harness -Condition ($rejected.Text.Contains(
            "full-suite parity mode requires exactly the commissioned 23 module labels"
        )) -Message "non-exact module-only set was not rejected by full-suite parity validation"
        Assert-Harness -Condition (-not (Test-Path -LiteralPath $attemptOne)) `
            -Message "non-exact module-only label preflight created an attempt folder"
        Write-Output "NONEXACT_MODULE_SET_REJECTED=True"

        $moduleSetWithoutMode = Invoke-DevelopmentRunnerPreflight -Labels $fullSuiteLabels `
            -CleanupTemplate
        Assert-Harness -Condition ($moduleSetWithoutMode.Text.Contains(
            "test label must be module.Class or module.Class.method"
        )) -Message "module-only labels were accepted without full-suite parity mode"
        Write-Output "MODULE_SET_WITHOUT_PARITY_REJECTED=True"

        $ordinary = Invoke-DevelopmentRunnerPreflight -Labels @(
            "tests.test_tt1_identity.CanonicalTemplateIdentityTests"
        )
        Assert-Harness -Condition ($ordinary.Text.Contains("CREDENTIAL_INTEVIA_DATABASE_ENGINE=UNSET") -and
            -not $ordinary.Text.Contains("test label must be module.Class")) `
            -Message "ordinary module.Class label did not pass rule-2 label preflight"
        Assert-Harness -Condition (-not (Test-Path -LiteralPath $attemptOne)) `
            -Message "ordinary label preflight created an attempt folder"
        Write-Output "ORDINARY_CLASS_LABEL_ACCEPTED=True"

        $focusedCloneLabels = @(
            "tests.test_tt1_identity.CanonicalTemplateIdentityTests",
            "tests.test_tt1_template_lifecycle.TemplateLifecycleToolingTests.test_template_clone_parity_and_receipt_cleanup"
        )
        $focused = Invoke-DevelopmentRunnerPreflight -Labels $focusedCloneLabels
        Assert-Harness -Condition ($focused.Text.Contains("STATE_SHA256=$stateSha256") -and
            $focused.Text.Contains("ME_DEV_ROOT=$(Join-Path (Join-Path (Join-Path $HOME 'INTEVIA_AGENT_STAGING') $testEvidenceRoot) 'ME_DEV')") -and
            $focused.Text.Contains("ME_DATABASE=test_intevia_living_organism_${testDatabaseTag}_me_1") -and
            $focused.Text.Contains("CREDENTIAL_INTEVIA_DATABASE_ENGINE=UNSET") -and
            -not $focused.Text.Contains("test label must be module.Class")) `
            -Message "focused clone labels or state binding failed the runner's preflight"
        Assert-Harness -Condition (-not (Test-Path -LiteralPath $attemptOne)) `
            -Message "focused clone label preflight created an attempt folder"
        Write-Output "FOCUSED_CLONE_LABELS_ACCEPTED=True"
    }
    finally {
        Restore-EnvironmentSnapshot -Snapshot $developmentEnvironment
    }
    Assert-Harness -Condition (Test-EnvironmentSnapshot -Expected $developmentEnvironment `
        -Actual (Get-EnvironmentSnapshot)) `
        -Message "development label preflight did not restore the environment"
    Write-Output "DEVELOPMENT_PREFLIGHT_ENVIRONMENT_RESTORED=True"
    Write-Output "ATTEMPT_1_CREATED=False"
    Write-Output "DEVELOPMENT_PREFLIGHT_DATABASE_ACCESS=NONE"
}
catch {
    $harnessFailure = $_.Exception.Message
}
finally {
    if (Test-Path Function:\python) {
        Remove-Item Function:\python -ErrorAction SilentlyContinue
    }
    if ($null -ne $originalEnvironment) {
        Restore-EnvironmentSnapshot -Snapshot $originalEnvironment
    }
    if (Test-Path -LiteralPath $temporaryHome) {
        Remove-Item -LiteralPath $temporaryHome -Recurse -Force
    }
    Set-Variable -Name HOME -Value $originalHome -Scope Script -Force
    Set-Location -LiteralPath $originalLocation
    $global:LASTEXITCODE = $originalLastExitCode
}

if ($null -ne $harnessFailure) {
    Write-Output "HARNESS=FAIL"
    Write-Output "FAILURE=$harnessFailure"
    $global:LASTEXITCODE = 1
}
else {
    Write-Output "HARNESS=PASS"
    $global:LASTEXITCODE = 0
}
