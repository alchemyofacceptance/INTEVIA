[CmdletBinding()]
param(
    [switch]$SelfTestForceFailure
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

$developmentRunnerPath = Join-Path $repoRoot "scripts\run_integrated_dev.ps1"
$originalLocation = Get-Location
$originalHome = $HOME
$temporaryHome = Join-Path $env:TEMP ("int2-verifier-harness-" + [guid]::NewGuid().ToString("N"))
$stateFile = Join-Path $temporaryHome "INT2_TEST_STATE.json"
$testEvidenceRoot = "PKTB_TEST"
$testDatabaseTag = "test"
$stateSha256 = $null
$originalEnvironment = $null

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

    $current = Get-EnvironmentSnapshot
    foreach ($item in @(Get-ChildItem Env:)) {
        if (-not $Snapshot.ContainsKey($item.Name)) {
            [System.Environment]::SetEnvironmentVariable(
                $item.Name,
                $null,
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }
    foreach ($name in $Snapshot.Keys) {
        if (-not $current.ContainsKey($name) -or $current[$name] -cne $Snapshot[$name]) {
            [System.Environment]::SetEnvironmentVariable(
                $name,
                $Snapshot[$name],
                [System.EnvironmentVariableTarget]::Process
            )
        }
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
    throw "unexpected Python invocation in the database-free harness"
}

function Invoke-DevelopmentRunnerPreflight {
    param(
        [Parameter(Mandatory = $true)][string[]]$Labels,
        [string]$Mode = "clone",
        [switch]$CleanupTemplate

    )

    $parameters = @{
        Mode = $Mode
        AttemptNumber = 1
        Labels = $Labels
        StateFile = $stateFile
        StateSha256 = $stateSha256
    }
    if ($CleanupTemplate) { $parameters.CleanupTemplate = $true }
    $parameters.FailFast = $true
    $output = @(& $developmentRunnerPath @parameters 2>&1 | ForEach-Object { [string]$_ })
    return [PSCustomObject]@{
        Status = $global:LASTEXITCODE
        Text = $output -join "`n"
    }
}

try {
    [System.IO.Directory]::CreateDirectory($temporaryHome) | Out-Null
    Set-Variable -Name HOME -Value $temporaryHome -Scope Script -Force
    $testState = [ordered]@{
        state_id = "INT2_TEST_STATE"
        branch = "pktb/aa-r4"
        evidence_root = $testEvidenceRoot
        database_tag = $testDatabaseTag
        run_spec_version = "v1.38"
        run_spec_sha256 = "facc5bdf8db2cd065cb760de8a8d57fa755d264e9ac068cf544e94ecaadae891"
        base_commit = "39986973186fe43fc2baa2abfe7e8d54da7c1225"
        integrated_root = "12f65f19bb5999eee3beb02cbd657c011882a460"
        merge = [ordered]@{
            commit = "af7d596d766811b0a8581bd830506c6320bdc729"
            parents = @(
                "a9dadb5e75106eb5b918a99d65e239526b166243"
                "76291565e040e0833670be330e1e2c1b5c7b346a"
            )
        }
        allowed_paths = @(
            ".github/workflows/s015-verification.yml"
            "scripts/integrated_guards.ps1"
            "scripts/integrated_state.ps1"
            "scripts/run_integrated_dev.ps1"
            "scripts/verify_integrated.ps1"
            "tests/test_integrated_state.ps1"
            "tests/test_tu1b_run_records.py"
            "tests/test_verify_integrated.ps1"
            "verification/isolation/runner.py"
            "verification/isolation/selfcheck_tests.py"
            "verification/ownership.py"
            "verification/route.py"
            "verification/run.py"
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

    Remove-Item Function:\python -ErrorAction Stop
    $pythonCommand = Get-Command python -ErrorAction Stop
    Assert-Harness -Condition ($pythonCommand.CommandType -eq "Application") `
        -Message "mock python was not removed or the real python command is unavailable"
    Write-Output "MOCK_PYTHON_REMOVED=True"
    Write-Output "GET_COMMAND_PYTHON=Application"
    Write-Output "DATABASE_ACCESS=NONE"

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

        $moduleOnly = Invoke-DevelopmentRunnerPreflight -Labels @("tests.test_tt1_identity")
        Assert-Harness -Condition ($moduleOnly.Text.Contains("CREDENTIAL_INTEVIA_DATABASE_ENGINE=UNSET") -and
            -not $moduleOnly.Text.Contains("test label must be module.Class")) `
            -Message "module-only label did not pass loader preflight"
        Assert-Harness -Condition (-not (Test-Path -LiteralPath $attemptOne)) `
            -Message "module-only label preflight created an attempt folder"
        Write-Output "MODULE_ONLY_LABEL_ACCEPTED=True"

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
    $developmentEnvironmentAfter = Get-EnvironmentSnapshot
    $changedEnvironmentNames = @(
        foreach ($name in $developmentEnvironment.Keys) {
            if (-not $developmentEnvironmentAfter.ContainsKey($name) -or
                $developmentEnvironment[$name] -cne $developmentEnvironmentAfter[$name]) {
                $name
            }
        }
        foreach ($name in $developmentEnvironmentAfter.Keys) {
            if (-not $developmentEnvironment.ContainsKey($name)) {
                $name
            }
        }
    )
    $environmentMessage = "development label preflight did not restore the environment"
    if ($changedEnvironmentNames.Count -gt 0) {
        $environmentMessage += ": " + ($changedEnvironmentNames -join ", ")
    }
    Assert-Harness -Condition (Test-EnvironmentSnapshot -Expected $developmentEnvironment `
        -Actual $developmentEnvironmentAfter) -Message $environmentMessage
    Write-Output "DEVELOPMENT_PREFLIGHT_ENVIRONMENT_RESTORED=True"
    Write-Output "ATTEMPT_1_CREATED=False"
    Write-Output "DEVELOPMENT_PREFLIGHT_DATABASE_ACCESS=NONE"
    if ($SelfTestForceFailure) {
        throw "SelfTestForceFailure"
    }
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
    Write-Host "HARNESS=FAIL"
    Write-Output "FAILURE=$harnessFailure"
    $global:LASTEXITCODE = 1
}
else {
    Write-Output "HARNESS=PASS"
    $global:LASTEXITCODE = 0
}
if ($null -ne $harnessFailure) {
    throw $harnessFailure
}
