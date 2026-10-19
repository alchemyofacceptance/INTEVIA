[CmdletBinding()]
param(
    [ValidateSet("fresh", "clone")][string]$Mode = "clone",
    [ValidateSet("DEVELOPMENT", "CLOSURE_EXCEPTION")][string]$RunKind = "DEVELOPMENT",
    [int]$AttemptNumber = 0,
    [ValidatePattern("^[1-9][0-9]*$")][string]$RunSuffix,
    [string[]]$Labels = @(),
    [string]$PlanFile,
    [string]$PlanExpectedSha256,
    [string]$StateFile,
    [string]$StateSha256,
    [switch]$CleanupTemplate,
    [switch]$FailFast,
    [ValidateRange(1, 60)][int]$StallMinutes = 5,
    [switch]$StallGuardSelfTest,
    [switch]$LaunchSelfTest,
    [string]$FreshAuthorityFile,
    [string]$FreshAuthoritySha256,
    [string[]]$OrderedGroups = @()
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "integrated_guards.ps1")
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

function Get-TemplatePreflightEvidence {
    param([Parameter(Mandatory = $true)]$Invocation)

    $actionLines = @()
    $buildLines = @()
    if ($Invocation.Label -ceq "Clone template preflight") {
        $actionLines = @($Invocation.OutputLines | Where-Object {
            $_ -match "^TEMPLATE_ACTION=(BUILT|REUSED)$"
        })
        $buildLines = @($Invocation.OutputLines | Where-Object {
            $_ -match "^TEMPLATE_BUILD_SECONDS=(?:[0-9]+(?:\.[0-9]+)?|NOT_APPLICABLE)$"
        })
    }

    $templateAction = $null
    $templateBuildSeconds = $null
    $consistent = $false
    if ($actionLines.Count -eq 1 -and $buildLines.Count -eq 1) {
        $templateAction = $actionLines[0].Substring("TEMPLATE_ACTION=".Length)
        if ($templateAction -eq "BUILT" -and
            $buildLines[0] -match "^TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $templateBuildSeconds = [double]::Parse(
                $Matches[1], [System.Globalization.CultureInfo]::InvariantCulture
            )
            $consistent = $true
        }
        elseif ($templateAction -eq "REUSED" -and
            $buildLines[0] -ceq "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE") {
            $consistent = $true
        }
    }

    return [PSCustomObject]@{
        ActionLineCount = $actionLines.Count
        BuildLineCount = $buildLines.Count
        TemplateAction = $templateAction
        TemplateBuildSeconds = $templateBuildSeconds
        IsConsistent = $consistent
    }
}

function Get-CommandLogOutput {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $logLines = @(Get-Content -LiteralPath $LogPath -ErrorAction Stop)
    $marker = "COMMAND=$Label"
    $markerIndexes = [System.Collections.Generic.List[int]]::new()
    for ($index = 0; $index -lt $logLines.Count; $index++) {
        if ($logLines[$index] -ceq $marker) {
            $markerIndexes.Add($index)
        }
    }
    if ($markerIndexes.Count -gt 1) {
        throw "R9: log contains ambiguous command sections for '$Label'"
    }
    if ($markerIndexes.Count -eq 0 -or $markerIndexes[0] -ge ($logLines.Count - 1)) {
        return @()
    }
    $startIndex = $markerIndexes[0] + 1

    $endIndex = $logLines.Count
    for ($index = $startIndex; $index -lt $logLines.Count; $index++) {
        if ($logLines[$index] -match "^COMMAND=") {
            $endIndex = $index
            break
        }
    }
    if ($endIndex -le $startIndex) {
        return @()
    }
    return @($logLines[$startIndex..($endIndex - 1)])
}

function Write-AuthorizedRunSpecDigest {
    param([Parameter(Mandatory = $true)]$AuthorizedState)

    Write-Output "RUN_SPEC_SHA256=$($AuthorizedState.State.run_spec_sha256)"
}

function Set-AuthorizedRunSpecDigestForChild {
    param([Parameter(Mandatory = $true)]$AuthorizedState)

    $env:INTEVIA_RUN_SPEC_SHA256 = [string]$AuthorizedState.State.run_spec_sha256
}

function Get-LockTimeoutObservationProblems {
    param(
        [Parameter(Mandatory = $true)][string]$Line,
        [Parameter(Mandatory = $true)][int]$ExpectedMilliseconds
    )

    $problems = [System.Collections.Generic.List[string]]::new()
    if ($Line -notmatch "^LOCK_TIMEOUT_EFFECTIVE alias=(?<alias>[^ ]+) value_ms=(?<value>[0-9]+) unit=(?<unit>[^ ]+)$") {
        $problems.Add("CP-0 lock-timeout observation was malformed")
        return $problems.ToArray()
    }
    $alias = $Matches.alias
    $value = $Matches.value
    $unit = $Matches.unit

    $observedTimeout = 0
    if (-not [int]::TryParse($value, [ref]$observedTimeout)) {
        $problems.Add("CP-0 lock-timeout value was not an integer")
    }
    elseif ($observedTimeout -ne $ExpectedMilliseconds) {
        $problems.Add(
            "lock timeout on alias '$alias' was $observedTimeout ms; expected $ExpectedMilliseconds ms"
        )
    }
    if ($unit -cne "ms") {
        $problems.Add("lock timeout on alias '$alias' was not reported in milliseconds")
    }
    return $problems.ToArray()
}

function Get-VerificationKindLine {
    param(
        [ValidateSet("DEVELOPMENT", "CLOSURE_EXCEPTION")][string]$RunKind = "DEVELOPMENT"
    )

    return "VERIFICATION_KIND=$RunKind"
}

function New-IntegratedRunRecordData {
    param(
        [ValidateSet("DEVELOPMENT", "CLOSURE_EXCEPTION")][string]$RunKind = "DEVELOPMENT",
        [Parameter(Mandatory = $true)][string]$PlanHash,
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][int]$TestsPlanned
    )

    return [ordered]@{
        classification = "TEST_ATTEMPT"
        result = "FAIL"
        plan_sha256 = $PlanHash
        failing_test = $null
        candidate = $Candidate
        tests_planned = $TestsPlanned
        tests_executed = 0
        tests_not_reached = $TestsPlanned
        executed_matches_plan = "NOT_STARTED"
        pyright_pre_run = "pyright: removed by Human decision, 16 Oct"
        run_kind = $RunKind
    }
}

function Get-PermittedSkipIds {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("clone", "fresh")][string]$Mode,
        [string]$PolicyPath = (Join-Path $repoRoot "verification\permitted_skips.json")
    )

    if (-not (Test-Path -LiteralPath $PolicyPath -PathType Leaf)) {
        throw "R9: permitted-skips policy is missing: $PolicyPath"
    }
    try {
        $policy = Get-Content -LiteralPath $PolicyPath -Raw -ErrorAction Stop |
            ConvertFrom-Json -AsHashtable -ErrorAction Stop
        if ($policy -isnot [System.Collections.IDictionary] -or $policy.Keys.Count -ne 2 -or
            $policy.version -ne 1 -or $policy.entries -isnot [System.Collections.IEnumerable] -or
            $policy.entries -is [string]) { throw "invalid schema" }
        $ids = @()
        $seen = [System.Collections.Generic.HashSet[string]]::new()
        foreach ($entry in $policy.entries) {
            if ($entry -isnot [System.Collections.IDictionary] -or $entry.Keys.Count -ne 3 -or
                $entry.id -isnot [string] -or $entry.id -notmatch '^tests\.[A-Za-z_]\w*(\.[A-Za-z_]\w*){2,}$' -or
                $entry.condition -cnotin @("always", "fresh-mode") -or
                $entry.reason -isnot [string] -or -not $entry.reason.Trim() -or
                -not $seen.Add($entry.id)) { throw "invalid entry" }
            if ($entry.condition -eq "always" -or ($Mode -eq "fresh" -and $entry.condition -eq "fresh-mode")) {
                $ids += $entry.id
            }
        }
        return $ids
    }
    catch {
        throw "R9: permitted-skips policy is malformed or invalid: $($_.Exception.Message)"
    }
}

function Test-ClosureSkipAccounting {
    param(
        [AllowNull()][object]$ReportedSkipCount,
        [AllowEmptyCollection()][string[]]$SkipTestIds = @(),
        [AllowEmptyCollection()][string[]]$PermittedSkipIds = @()
    )

    $problems = [System.Collections.Generic.List[string]]::new()
    $reportedCount = 0
    if ($null -eq $ReportedSkipCount -or
        -not [int]::TryParse([string]$ReportedSkipCount, [ref]$reportedCount) -or
        $reportedCount -lt 0) {
        $problems.Add("reported skip count is missing or invalid")
    }
    elseif ($reportedCount -ne $SkipTestIds.Count) {
        $problems.Add("reported skip count does not equal the identified skip count")
    }
    if (@($SkipTestIds | Select-Object -Unique).Count -ne $SkipTestIds.Count) {
        $problems.Add("skipped test identifiers are not unique")
    }
    foreach ($skipId in @($SkipTestIds | Where-Object { $_ -notin $PermittedSkipIds })) {
        $problems.Add("unpermitted skipped test: $skipId")
    }
    return $problems.ToArray()
}

function Set-PrintedSummaryFromLine {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)

    if ($Line -match "^test_[^\s]+\s+\((?<testId>tests\.[^)]+)\)\s+\.\.\.\s+skipped(?:\s|$)") {
        $script:printedSummary.skip_ids.Add($Matches.testId)
    }
}

if ($LaunchSelfTest) {
    Set-Location -LiteralPath $repoRoot
    $earlierTestOutput = [PSCustomObject]@{
        Label = "R14 changed-test no-database pre-check"
        OutputLines = @(
            "TEMPLATE_ACTION=BUILT",
            "TEMPLATE_BUILD_SECONDS=12.500000"
        )
    }
    $currentPreflightOutput = [PSCustomObject]@{
        Label = "Clone template preflight"
        OutputLines = @(
            "TEMPLATE_ACTION=REUSED",
            "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE"
        )
    }
    $earlierEvidence = Get-TemplatePreflightEvidence -Invocation $earlierTestOutput
    $currentEvidence = Get-TemplatePreflightEvidence -Invocation $currentPreflightOutput
    if ($earlierEvidence.ActionLineCount -ne 0 -or
        $earlierEvidence.BuildLineCount -ne 0 -or
        $earlierEvidence.IsConsistent -or
        $currentEvidence.ActionLineCount -ne 1 -or
        $currentEvidence.BuildLineCount -ne 1 -or
        -not $currentEvidence.IsConsistent -or
        $currentEvidence.TemplateAction -cne "REUSED") {
        throw "R8: clone preflight invocation-scope self-test failed"
    }
    Write-Output "R8 clone preflight invocation-scope self-test=PASS"

    $commandScopeTestPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "intevia-command-scope-" + [guid]::NewGuid().ToString("N") + ".log"
    )
    try {
        @(
            "COMMAND=Explicit-label development run",
            "test_control_output (tests.sample.ControlCase.test_control_output) ... ok",
            "COMMAND=Explicit-label development run",
            "Destroying test database for alias 'default' ('decoy')..."
        ) | Set-Content -LiteralPath $commandScopeTestPath -Encoding utf8
        $ambiguousCommandSectionMessage = $null
        try {
            [void](Get-CommandLogOutput -LogPath $commandScopeTestPath -Label "Explicit-label development run")
        }
        catch {
            if ($_.Exception.Message -notmatch "ambiguous command sections") {
                throw
            }
            $ambiguousCommandSectionMessage = $_.Exception.Message
        }
        if ($null -eq $ambiguousCommandSectionMessage) {
            throw "R9: duplicate command-section self-test was not refused"
        }
    }
    finally {
        if (Test-Path -LiteralPath $commandScopeTestPath -PathType Leaf) {
            Remove-Item -LiteralPath $commandScopeTestPath -Force -ErrorAction Stop
        }
    }
    Write-Output "R9 command-section uniqueness self-test=PASS"

    $syntheticRunSpecDigest = "c" * 64
    $syntheticAuthorizedState = [PSCustomObject]@{
        State = [PSCustomObject]@{
            run_spec_sha256 = $syntheticRunSpecDigest
        }
    }
    $previousRunSpecBinding = Get-Item -LiteralPath "Env:INTEVIA_RUN_SPEC_SHA256" `
        -ErrorAction SilentlyContinue
    try {
        Write-AuthorizedRunSpecDigest -AuthorizedState $syntheticAuthorizedState
        Set-AuthorizedRunSpecDigestForChild -AuthorizedState $syntheticAuthorizedState
        $childPowerShell = [System.Environment]::ProcessPath
        if ([string]::IsNullOrWhiteSpace($childPowerShell) -or
            -not (Test-Path -LiteralPath $childPowerShell -PathType Leaf)) {
            throw "R11: PowerShell child executable is unavailable"
        }
        $childOutput = @(& $childPowerShell `
            -NoLogo -NoProfile -NonInteractive `
            -Command 'Write-Output ("RUN_SPEC_BINDING_CHILD=" + $env:INTEVIA_RUN_SPEC_SHA256)' 2>&1)
        $childExitCode = $LASTEXITCODE
        $childBindingLine = @($childOutput | Where-Object {
            [string]$_ -ceq "RUN_SPEC_BINDING_CHILD=$syntheticRunSpecDigest"
        })
        if ($childExitCode -ne 0 -or $childBindingLine.Count -ne 1) {
            throw "R11: authorized run-spec digest did not reach the child process"
        }
        Write-Output $childBindingLine[0]
    }
    finally {
        if ($null -ne $previousRunSpecBinding) {
            Set-Item -LiteralPath "Env:INTEVIA_RUN_SPEC_SHA256" `
                -Value ([string]$previousRunSpecBinding.Value) -ErrorAction Stop
        }
        else {
            Remove-Item -LiteralPath "Env:INTEVIA_RUN_SPEC_SHA256" `
                -ErrorAction SilentlyContinue
        }
    }
    Write-Output "RUN_SPEC_BINDING_EXECUTABLE_WITNESS=PASS"

    $correctTimeoutProblems = @(Get-LockTimeoutObservationProblems `
        -Line "LOCK_TIMEOUT_EFFECTIVE alias=default value_ms=120000 unit=ms" `
        -ExpectedMilliseconds 120000)
    $wrongTimeoutUnitProblems = @(Get-LockTimeoutObservationProblems `
        -Line "LOCK_TIMEOUT_EFFECTIVE alias=default value_ms=120000 unit=s" `
        -ExpectedMilliseconds 120000)
    $wrongTimeoutValueProblems = @(Get-LockTimeoutObservationProblems `
        -Line "LOCK_TIMEOUT_EFFECTIVE alias=default value_ms=120001 unit=ms" `
        -ExpectedMilliseconds 120000)
    if ($correctTimeoutProblems.Count -ne 0 -or
        $wrongTimeoutUnitProblems -notcontains "lock timeout on alias 'default' was not reported in milliseconds" -or
        $wrongTimeoutValueProblems -notcontains "lock timeout on alias 'default' was 120001 ms; expected 120000 ms") {
        throw "C CP-0 lock-timeout unit and numeric comparison self-test failed"
    }
    Write-Output "CP-0 lock-timeout unit=ms comparison=PASS REFUSED=unit=s and mismatched value"

    $closureRecord = New-IntegratedRunRecordData `
        -RunKind "CLOSURE_EXCEPTION" `
        -PlanHash ("a" * 64) `
        -Candidate "candidate" `
        -TestsPlanned 1
    if ($closureRecord.run_kind -cne "CLOSURE_EXCEPTION" -or
        (Get-VerificationKindLine -RunKind "CLOSURE_EXCEPTION") -cne "VERIFICATION_KIND=CLOSURE_EXCEPTION") {
        throw "D RunKind closure-exception self-test failed"
    }
    Write-Output "D RunKind closure-exception record and verification-kind self-test=PASS"
    $developmentRecord = New-IntegratedRunRecordData `
        -PlanHash ("b" * 64) `
        -Candidate "candidate" `
        -TestsPlanned 1
    if ($developmentRecord.run_kind -cne "DEVELOPMENT" -or
        (Get-VerificationKindLine) -cne "VERIFICATION_KIND=DEVELOPMENT") {
        throw "D RunKind development-default self-test failed"
    }
    Write-Output "D RunKind development default self-test=PASS"

    $clonePermittedSkipIds = @(Get-PermittedSkipIds -Mode "clone")
    if ($clonePermittedSkipIds.Count -eq 0) {
        throw "E2 committed clone skip policy has no permitted always entry"
    }
    $permittedSkipProblems = @(Test-ClosureSkipAccounting `
        -ReportedSkipCount 1 `
        -SkipTestIds @($clonePermittedSkipIds[0]) `
        -PermittedSkipIds $clonePermittedSkipIds)
    if ($permittedSkipProblems.Count -ne 0) {
        throw "E2 permitted-skip accounting self-test failed"
    }
    Write-Output "E2 permitted skip accounting self-test=PASS"
    $unpermittedSkipProblems = @(Test-ClosureSkipAccounting `
        -ReportedSkipCount 1 `
        -SkipTestIds @("tests.sample.SkipChecks.test_unpermitted") `
        -PermittedSkipIds $clonePermittedSkipIds)
    if (@($unpermittedSkipProblems | Where-Object { $_ -like "unpermitted skipped test:*" }).Count -ne 1) {
        throw "E2 unpermitted-skip refusal self-test failed"
    }
    Write-Output "E2 unpermitted skip refusal self-test=PASS"
    $incompleteSkipProblems = @(Test-ClosureSkipAccounting `
        -ReportedSkipCount 2 `
        -SkipTestIds @($clonePermittedSkipIds[0]) `
        -PermittedSkipIds $clonePermittedSkipIds)
    $unidentifiedSkipProblems = @(Test-ClosureSkipAccounting `
        -ReportedSkipCount 1 `
        -SkipTestIds @() `
        -PermittedSkipIds $clonePermittedSkipIds)
    if ($incompleteSkipProblems.Count -eq 0 -or $unidentifiedSkipProblems.Count -eq 0) {
        throw "E2 incomplete skip accounting self-test failed"
    }
    Write-Output "E2 incomplete skip accounting self-test=PASS"

    $skipPolicyTestDirectory = Join-Path ([System.IO.Path]::GetTempPath()) (
        "intevia-skip-policy-" + [guid]::NewGuid().ToString("N")
    )
    $missingSkipPolicyPath = Join-Path $skipPolicyTestDirectory "missing.json"
    $malformedSkipPolicyPath = Join-Path $skipPolicyTestDirectory "malformed.json"
    try {
        [System.IO.Directory]::CreateDirectory($skipPolicyTestDirectory) | Out-Null
        [System.IO.File]::WriteAllText(
            $malformedSkipPolicyPath,
            "{",
            [System.Text.UTF8Encoding]::new($false)
        )
        foreach ($policyPath in @($missingSkipPolicyPath, $malformedSkipPolicyPath)) {
            $policyRefused = $false
            try {
                [void](Get-PermittedSkipIds -Mode "clone" -PolicyPath $policyPath)
            }
            catch {
                if ($_.Exception.Message -notmatch "R9: permitted-skips policy") { throw }
                $policyRefused = $true
            }
            if (-not $policyRefused) {
                throw "E2 missing or malformed skip policy was not refused"
            }
        }
    }
    finally {
        if (Test-Path -LiteralPath $skipPolicyTestDirectory -PathType Container) {
            Remove-Item -LiteralPath $skipPolicyTestDirectory -Recurse -Force -ErrorAction Stop
        }
    }
    Write-Output "E2 missing and malformed skip policy refusal self-test=PASS"
    $script:printedSummary = [PSCustomObject]@{
        skip_ids = [System.Collections.Generic.List[string]]::new()
    }
    Set-PrintedSummaryFromLine `
        -Line "test_skip (tests.sample.SkipChecks.test_identified) ... skipped by policy"
    if ($script:printedSummary.skip_ids.Count -ne 1 -or
        $script:printedSummary.skip_ids[0] -cne "tests.sample.SkipChecks.test_identified") {
        throw "E2 printed skip identifier self-test failed"
    }
    Write-Output "E2 skipped-test identification self-test=PASS"

    $watchTimestamp = [datetime]::new(2026, 10, 17, 12, 0, 0, [DateTimeKind]::Utc)
    $watchThreshold = [TimeSpan]::FromMinutes(5)
    $forwardTimestamp = Update-WatchdogInactivity `
        -LastSeenWrite $watchTimestamp `
        -CurrentWrite $watchTimestamp.AddSeconds(1) `
        -LastChangeElapsed ([TimeSpan]::FromMinutes(4)) `
        -NowElapsed ([TimeSpan]::FromMinutes(4).Add([TimeSpan]::FromSeconds(1))) `
        -Threshold $watchThreshold
    if ($forwardTimestamp.IsStalled -or
        $forwardTimestamp.LastSeenWrite -ne $watchTimestamp.AddSeconds(1) -or
        $forwardTimestamp.LastChangeElapsed -ne ([TimeSpan]::FromMinutes(4).Add([TimeSpan]::FromSeconds(1)))) {
        throw "G watchdog forward timestamp change self-test failed"
    }
    Write-Output "G watchdog forward timestamp change resets inactivity=PASS"
    $backwardTimestamp = Update-WatchdogInactivity `
        -LastSeenWrite $watchTimestamp `
        -CurrentWrite $watchTimestamp.AddSeconds(-1) `
        -LastChangeElapsed ([TimeSpan]::FromMinutes(4)) `
        -NowElapsed ([TimeSpan]::FromMinutes(4).Add([TimeSpan]::FromSeconds(1))) `
        -Threshold $watchThreshold
    if ($backwardTimestamp.IsStalled -or
        $backwardTimestamp.LastSeenWrite -ne $watchTimestamp.AddSeconds(-1) -or
        $backwardTimestamp.LastChangeElapsed -ne ([TimeSpan]::FromMinutes(4).Add([TimeSpan]::FromSeconds(1)))) {
        throw "G watchdog backward timestamp change self-test failed"
    }
    Write-Output "G watchdog backward timestamp change resets inactivity=PASS"
    $unchangedTimestamp = Update-WatchdogInactivity `
        -LastSeenWrite $watchTimestamp `
        -CurrentWrite $watchTimestamp `
        -LastChangeElapsed ([TimeSpan]::Zero) `
        -NowElapsed $watchThreshold `
        -Threshold $watchThreshold
    if (-not $unchangedTimestamp.IsStalled -or
        $unchangedTimestamp.LastChangeElapsed -ne [TimeSpan]::Zero) {
        throw "G watchdog monotonic threshold self-test failed"
    }
    Write-Output "G watchdog unchanged timestamp reaches monotonic threshold=PASS"

    $selfTestEnvironment = @{
        DJANGO_SETTINGS_MODULE = "intevia.test_settings"
        INTEVIA_S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
        INTEVIA_POSTGRES_TEST_DB = "test_intevia_living_organism_tu1_me_selftest"
    }
    if ($PlanExpectedSha256 -and (
        $PlanExpectedSha256 -notmatch "^[0-9a-fA-F]{64}$" -or
        [string]::IsNullOrWhiteSpace($PlanFile)
    )) {
        throw "R7: expected plan SHA-256 requires a plan file and 64 hexadecimal characters"
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
if ($RunKind -eq "CLOSURE_EXCEPTION" -and
    [string]::IsNullOrWhiteSpace($PlanFile)) {
    throw "R3/R10: a closure exception requires its resolved -PlanFile"
}
if ((-not $Labels -or $Labels.Count -eq 0) -and
    [string]::IsNullOrWhiteSpace($PlanFile)) {
    throw "R3: at least one test label is required when no plan file is supplied"
}
if ([string]::IsNullOrWhiteSpace($StateFile) -or
    $StateSha256 -cnotmatch "^[0-9a-fA-F]{64}$") {
    throw "R9: authorized state file and digest are required"
}
Write-IntegratedGuardSummary -FailFast:$FailFast -StallMinutes $StallMinutes
. (Join-Path $PSScriptRoot "integrated_state.ps1")
$originalLocation = Get-Location
$environmentNames = @(
    "INTEVIA_DATABASE_ENGINE",
    "INTEVIA_S015_TEST_ROUTE",
    "DJANGO_SETTINGS_MODULE",
    "INTEVIA_POSTGRES_TEST_DB",
    "VERIFICATION_RUN_NONCE",
    "VERIFICATION_DB_RECEIPT",
    "VERIFICATION_ISOLATION_JSON",
    "VERIFICATION_CLONE_PROBE_DATABASE_NAME",
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
    "INTEVIA_FRESH_AUTHORITY_FILE",
    "INTEVIA_FRESH_AUTHORITY_SHA256",
    "VERIFICATION_TEST_DATABASE_NAME_MAX_LENGTH",
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
$counts = $null
$summary = $null
$script:runRecordData = $null
$setupSeconds = $null
$templateBuildSeconds = $null
$templateAction = $null
$clonePreflightOutputLines = @()
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
    # P1-F5: only the bounded launcher probe and fixed-length TT1 test witness are exempt.
    param(
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$LogLines,
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

    $logLines = @($LogLines)

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
    $testCreatedWitnessPattern = '^test_intevia_living_organism_(?:w_[0-9a-f]{12}|tt1_witness_[0-9a-f]{12})$'
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
    $capturedLines = [System.Collections.Generic.List[string]]::new()
    $status = 1
    try {
        & python @Arguments 2>&1 | ForEach-Object {
            $lineText = Protect-OutputText -Text ([string]$_)
            $capturedLines.Add($lineText)
            Write-Host $lineText
            if ($Label -cin @("Clone template preflight", "Explicit-label development run") -and
                $lineText -match "Destroying test database for alias") {
                $script:teardownLines++
            }
        }
        $status = $LASTEXITCODE
    }
    finally {
        if ($capturedLines.Count -gt 0) {
            $outputText = ($capturedLines -join [Environment]::NewLine) + [Environment]::NewLine
            [System.IO.File]::AppendAllText(
                $LogPath,
                $outputText,
                [System.Text.UTF8Encoding]::new($false)
            )
        }
    }
    return [PSCustomObject]@{
        Label = $Label
        ExitCode = [int]$status
        OutputLines = [string[]]$capturedLines.ToArray()
    }
}

function Write-PreRunRefusalRecord {
    param(
        [Parameter(Mandatory = $true)][string]$RecordDirectory,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$RefusalStage,
        [Parameter(Mandatory = $true)][string]$PlanHash,
        [Parameter(Mandatory = $true)]$PlanData,
        [Parameter(Mandatory = $true)][string]$CandidateBinding
    )

    $refusalRecord = [ordered]@{
        classification = "PRE_RUN_REFUSAL"
        result = "REFUSED"
        refusal_stage = $RefusalStage
        plan_sha256 = $PlanHash
        candidate = $CandidateBinding
        tests_planned = [int]$PlanData.tests.Count
        tests_executed = 0
        tests_not_reached = [int]$PlanData.tests.Count
        executed_matches_plan = "NOT_STARTED"
        database_route = [string]$PlanData.database_route
    }
    if (-not (Test-Path -LiteralPath $RecordDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $RecordDirectory -Force | Out-Null
    }
    $runRecordPath = Join-Path $RecordDirectory "run-record.json"
    [System.IO.File]::WriteAllText(
        $runRecordPath,
        (($refusalRecord | ConvertTo-Json -Depth 8) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    $recordLine = "RUN_RECORD_CLASSIFICATION=PRE_RUN_REFUSAL"
    Write-Output $recordLine
    Write-Output "RUN_RECORD_FILE=$runRecordPath"
    Add-Content -LiteralPath $LogPath -Value $recordLine -Encoding utf8
    Add-Content -LiteralPath $LogPath -Value "RUN_RECORD_FILE=$runRecordPath" -Encoding utf8
}












try {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    Set-Location -LiteralPath $repoRoot

    if ($PlanFile) {
        if ($Labels.Count -gt 0) {
            throw "R3: specify either -PlanFile or -Labels, not both"
        }
        if (-not (Test-Path -LiteralPath $PlanFile -PathType Leaf)) {
            throw "R4: resolved plan file does not exist"
        }
    }
    else {
        if ($Labels.Count -gt 20) {
            throw "R3: at most twenty labels are allowed; use -PlanFile for larger selections"
        }
        foreach ($label in $Labels) {
            if ($label -notmatch "^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$") {
                throw "test label must be module.Class or module.Class.method"
            }
        }
    }
    if (@($Labels | Sort-Object -Unique).Count -ne $Labels.Count) {
        throw "duplicate test labels are refused"
    }
    if ($RunKind -eq "CLOSURE_EXCEPTION") {
        $script:permittedSkipIds = @(Get-PermittedSkipIds -Mode "clone")
    }
    if ($CleanupTemplate -and $Mode -ne "clone") {
        throw "template cleanup is only valid for a clone-mode run"
    }
    $authorizedState = Get-IntegratedState -StateFile $StateFile -StateSha256 $StateSha256
    $routes = Get-IntegratedEvidenceRoutes -AuthorizedState $authorizedState -HomeDirectory $HOME
    $stageRoot = $routes.DevelopmentRoot
    $env:INTEVIA_DATABASE_TAG = [string]$authorizedState.DatabaseTag
    $env:INTEVIA_EVIDENCE_ROOT = [string]$authorizedState.EvidenceRoot
    $env:INTEVIA_EVIDENCE_ROOT_PATH = [System.IO.Path]::GetFullPath((Split-Path -Parent $stageRoot))
    if ($AttemptNumber -lt 1 -and $RunSuffix) {
        if ($RunSuffix -notmatch "^([1-9][0-9]*)$") {
            throw "R9: run suffix is invalid"
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
    if ($runSuffix -cnotmatch ("^{0}$" -f [regex]::Escape([string]$AttemptNumber))) {
        throw "R9: run suffix must match the attempt number"
    }
    $attemptDatabase = Assert-IntegratedTestDatabaseName `
        -Name "$($routes.DevelopmentDatabasePrefix)$runSuffix" `
        -Purpose "development attempt"
    Write-Output "STATE_FILE=$($authorizedState.StateFile)"
    Write-Output "STATE_ID=$($authorizedState.StateId)"
    Write-Output "STATE_SHA256=$($authorizedState.StateSha256)"
    Write-Output "SOURCE_KIND=$($authorizedState.SourceKind)"
    Write-Output "HEAD=$($authorizedState.Head)"
    Write-Output "EVIDENCE_ROOT=$($authorizedState.EvidenceRoot)"
    Write-Output "DATABASE_TAG=$($authorizedState.DatabaseTag)"
    Write-Output "ME_DEV_ROOT=$($routes.DevelopmentRoot)"
    Write-Output "ME_DATABASE=$attemptDatabase"

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
    Write-AuthorizedRunSpecDigest -AuthorizedState $authorizedState
    Write-Output (Get-VerificationKindLine -RunKind $RunKind)
    if ($StallGuardSelfTest) {
        $selfCheckLogPath = Join-Path $stageRoot ("STALL_GUARD_SELF_CHECK_{0}.log" -f $runSuffix)
        $selfCheckNumbering = "sc1"
        $selfCheckDatabase = Assert-IntegratedTestDatabaseName `
            -Name "$($routes.DevelopmentDatabasePrefix)$selfCheckNumbering" `
            -Purpose "stall-guard self-check"
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
            -Numbering $selfCheckNumbering)
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
            -Numbering $selfCheckNumbering)
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
            -Numbering $selfCheckNumbering `
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
    if ($attemptDatabase -in @("intevia", "intevia_dev")) {
        throw "protected or overlength test database name refused"
    }

    $templateStore = Join-Path $stageRoot "integrated-template-store"
    $preRunRoot = Join-Path $stageRoot "pre-run-refusals"
    New-Item -ItemType Directory -Path $preRunRoot -Force | Out-Null
    $preflightDirectory = Join-Path $preRunRoot (
        "attempt_{0}_{1}" -f $runSuffix, [guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $preflightDirectory -Force | Out-Null
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $preflightDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $preflightDirectory "isolation.jsonl"
    $logPath = Join-Path $preflightDirectory "integrated-development.log"
    $resolvedRunPlanFile = Join-Path $preflightDirectory "run-plan.json"
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
        (Get-VerificationKindLine -RunKind $RunKind)
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
    $env:VERIFICATION_TEST_DATABASE_NAME_MAX_LENGTH = [string]$routes.DatabaseNameMaxLength
    $env:VERIFICATION_CLONE_PROBE_DATABASE_NAME = Get-IntegratedCloneProbeDatabaseName `
        -RunNonce $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = "0049_h100_command_intake"
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
    Set-AuthorizedRunSpecDigestForChild -AuthorizedState $authorizedState
    $bindingRun = Invoke-PythonAndLog `
        -Label "Candidate source binding" `
        -LogPath $logPath `
        -Arguments @(
            "-m", "verification.isolation.run_plan", "candidate-binding",
            "--root", $repoRoot, "--state", $authorizedState.StateFile
        )
    if ($bindingRun.ExitCode -ne 0) { throw "R9: candidate binding could not be calculated" }
    $bindingLine = @($bindingRun.OutputLines |
        Where-Object { $_ -match "^CANDIDATE_BINDING_SHA256=[0-9a-f]{64}$" })
    if ($bindingLine.Count -ne 1) { throw "R9: candidate binding output is missing" }
    $candidateBinding = ($bindingLine[0] -replace "^CANDIDATE_BINDING_SHA256=", "")
    $env:INTEVIA_CANDIDATE_BINDING_SHA256 = $candidateBinding
    Write-Output $bindingLine[0]
    if ($PlanFile) {
        $resolvedPlanSource = (Resolve-Path -LiteralPath $PlanFile -ErrorAction Stop).Path
        Copy-Item -LiteralPath $resolvedPlanSource -Destination $resolvedRunPlanFile -ErrorAction Stop
        $sourcePlanHash = (Get-FileHash -LiteralPath $resolvedPlanSource -Algorithm SHA256).Hash.ToLowerInvariant()
        $planHash = (Get-FileHash -LiteralPath $resolvedRunPlanFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($sourcePlanHash -cne $planHash) {
            throw "R4: copied plan bytes differ from the supplied resolved plan"
        }
        $planArguments = @(
            "-m", "verification.isolation.run_plan", "validate-plan",
            "--plan", $resolvedRunPlanFile
        )
    }
    else {
        $planArguments = @(
            "-m", "verification.isolation.run_plan", "plan",
            "--output", $resolvedRunPlanFile
        )
        foreach ($label in $Labels) { $planArguments += @("--label", $label) }
        foreach ($group in $OrderedGroups) { $planArguments += @("--group", $group) }
    }
    $planRun = Invoke-PythonAndLog -Label "R3/R4 no-database plan" -LogPath $logPath -Arguments $planArguments
    if ($planRun.ExitCode -ne 0) {
        throw "R3/R4: labels did not resolve to a valid loader-built plan"
    }
    $planLine = @($planRun.OutputLines | Where-Object { $_ -match "^PLAN_SHA256=[0-9a-f]{64}$" })
    if ($planLine.Count -ne 1 -or $planLine[0] -notmatch "^PLAN_SHA256=([0-9a-f]{64})$") {
        throw "R4: plan digest was not recorded"
    }
    $planHash = $Matches[1]
    $planData = Get-Content -LiteralPath $resolvedRunPlanFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($PlanExpectedSha256 -and
        $PlanExpectedSha256.ToLowerInvariant() -cne $planHash.ToLowerInvariant()) {
        Add-Content -LiteralPath $logPath -Value "PLAN_DIGEST_REFUSAL=R7 expected digest differs from plan bytes" -Encoding utf8
        Write-PreRunRefusalRecord `
            -RecordDirectory $preflightDirectory `
            -LogPath $logPath `
            -RefusalStage "R7" `
            -PlanHash $planHash `
            -PlanData $planData `
            -CandidateBinding $candidateBinding
        throw "R7: execution plan digest differs from validated plan"
    }
    $env:INTEVIA_RUN_PLAN_FILE = $resolvedRunPlanFile
    $env:INTEVIA_RUN_PLAN_SHA256 = $planHash
    $env:INTEVIA_RUN_SUFFIX = $runSuffix
    $precheckRun = Invoke-PythonAndLog `
        -Label "R14 changed-test no-database pre-check" `
        -LogPath $logPath `
        -Arguments @(
            "-m", "verification.isolation.run_plan", "precheck",
            "--root", $repoRoot, "--state", $authorizedState.StateFile
        )
        if ($precheckRun.ExitCode -ne 0) {
            Write-PreRunRefusalRecord `
                -RecordDirectory $preflightDirectory `
                -LogPath $logPath `
                -RefusalStage "R14" `
                -PlanHash $planHash `
                -PlanData $planData `
                -CandidateBinding $candidateBinding
            throw "R14: changed-test no-database pre-check refused the launch"
        }

        $pyrightDisposition = "pyright: removed by Human decision, 16 Oct"
        Add-Content -LiteralPath $logPath -Value $pyrightDisposition -Encoding utf8
        Write-Output $pyrightDisposition

        if ($planData.database_route -notin @("DATABASE", "NO_DATABASE")) {
            Write-PreRunRefusalRecord `
                -RecordDirectory $preflightDirectory `
                -LogPath $logPath `
                -RefusalStage "T6_DATABASE_ROUTE_UNAVAILABLE" `
                -PlanHash $planHash `
                -PlanData $planData `
                -CandidateBinding $candidateBinding
            throw "T6: plan does not establish a database route; database launch refused"
        }
        if ($planData.database_route -eq "NO_DATABASE") {
            Write-PreRunRefusalRecord `
                -RecordDirectory $preflightDirectory `
                -LogPath $logPath `
                -RefusalStage "T6_NO_DATABASE_ROUTE" `
                -PlanHash $planHash `
                -PlanData $planData `
                -CandidateBinding $candidateBinding
            throw "T6: plan requires no database; CP-0 and database launch refused"
        }

        $tagRegisterPath = Join-Path $HOME "INTEVIA_AGENT_STAGING\database-tag-register.jsonl"
        $tagRegisterCode = @'
import json
import sys

from verification.isolation.template import register_database_tag

result = register_database_tag(sys.argv[1], sys.argv[2], sys.argv[3])
print("DATABASE_TAG_REGISTER=" + json.dumps(result, sort_keys=True))
'@
        $tagRegisterRun = Invoke-PythonAndLog `
            -Label "TU-1b database tag register" `
            -LogPath $logPath `
            -Arguments @(
                "-c", $tagRegisterCode, $tagRegisterPath,
                $env:INTEVIA_DATABASE_TAG, $env:INTEVIA_EVIDENCE_ROOT
            )
        if ($tagRegisterRun.ExitCode -ne 0) {
            throw "TU-1b database tag register refused the launch"
        }

        Move-Item -LiteralPath $preflightDirectory -Destination $runDirectory
        $preflightDirectory = $null
        $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
        $isolationPath = Join-Path $runDirectory "isolation.jsonl"
        $logPath = Join-Path $runDirectory "integrated-development.log"
        $resolvedRunPlanFile = Join-Path $runDirectory "run-plan.json"
        $env:VERIFICATION_DB_RECEIPT = $receiptPath
        $env:VERIFICATION_ISOLATION_JSON = $isolationPath
        $env:INTEVIA_RUN_PLAN_FILE = $resolvedRunPlanFile

        $script:runRecordData = New-IntegratedRunRecordData `
            -RunKind $RunKind `
            -PlanHash $planHash `
            -Candidate $candidateBinding `
            -TestsPlanned ([int]$planData.tests.Count)

        Write-Output "ATTEMPT=$AttemptNumber"
    Write-Output "RUN_SUFFIX=$runSuffix"
    Write-Output "DATABASE=$attemptDatabase"
    Write-Output "RUN_DIRECTORY=$runDirectory"
    Write-Output "TEMPLATE_STORE=$effectiveTemplateStore"
    Write-Output "TEMPLATE_CLEANUP=$($env:VERIFICATION_TEMPLATE_CLEANUP)"
    Write-Output "LABELS=$($Labels -join ',')"

    $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
    $checkRun = Invoke-PythonAndLog -Label "Django system check" -LogPath $logPath `
        -Arguments @("manage.py", "check", "--settings=intevia.test_settings")
    if ($checkRun.ExitCode -ne 0) {
        $runProblems.Add("Django system check returned status $($checkRun.ExitCode)")
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
probe_name = os.environ["VERIFICATION_CLONE_PROBE_DATABASE_NAME"]
name_limit = int(os.environ["VERIFICATION_TEST_DATABASE_NAME_MAX_LENGTH"])
if len(probe_name.encode("utf-8")) > name_limit:
    raise RuntimeError("clone preflight database name exceeds the primary-name bound")
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
                $preflightRun = Invoke-PythonAndLog `
                    -Label "Clone template preflight" `
                    -LogPath $logPath `
                    -Arguments @("-c", $preflightCodeSource)
                $clonePreflightOutputLines = @($preflightRun.OutputLines)
            }
            finally {
                $env:PGOPTIONS = $previousPgOptions
            }
            $preflightCode = $preflightRun.ExitCode
            if ($preflightCode -ne 0) {
                $runProblems.Add("clone template preflight returned status $preflightCode; tests were not started")
            }
            else {
                $preflightEvidence = Get-TemplatePreflightEvidence -Invocation $preflightRun
                if ($preflightEvidence.ActionLineCount -ne 1 -or
                    $preflightEvidence.BuildLineCount -ne 1) {
                    $runProblems.Add("R8: clone preflight omitted template action or build timing")
                }
                else {
                    $templateAction = $preflightEvidence.TemplateAction
                    if ($preflightEvidence.IsConsistent) {
                        $templateBuildSeconds = $preflightEvidence.TemplateBuildSeconds
                    }
                    else {
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
            if ($RunKind -eq "CLOSURE_EXCEPTION") {
                $script:printedSummary = [PSCustomObject]@{
                    skip_ids = [System.Collections.Generic.List[string]]::new()
                }
            }
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
            if ($RunKind -eq "CLOSURE_EXCEPTION") {
                $closureRunLogLines = @(Get-CommandLogOutput `
                    -LogPath $logPath -Label "Explicit-label development run")
                foreach ($line in $closureRunLogLines) {
                    Set-PrintedSummaryFromLine -Line $line
                }
                $script:skipTestIds = @($script:printedSummary.skip_ids.ToArray())
                Write-Output "TEST_SKIP_IDS=$($script:skipTestIds -join ',')"
                if ($null -ne $script:runRecordData) {
                    $script:runRecordData["skip_test_ids"] = @($script:skipTestIds)
                }
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
                if ($RunKind -eq "CLOSURE_EXCEPTION") {
                    $skipAccountingProblems = @(Test-ClosureSkipAccounting `
                        -ReportedSkipCount $counts.skipped `
                        -SkipTestIds $script:skipTestIds `
                        -PermittedSkipIds $script:permittedSkipIds)
                    foreach ($problem in $skipAccountingProblems) {
                        $runProblems.Add($problem)
                    }
                }
                if ($Mode -eq "clone" -and $cp0Records.Count -eq 1 -and
                    ($cp0Records[0].template_identity -eq $null -or
                     $cp0Records[0].template_cp0_digest -ne $cp0Records[0].clone_cp0_digest)) {
                    $runProblems.Add("clone CP-0 did not match the integrated template digest")
                }
                if ($null -ne $script:runRecordData) {
                    $script:runRecordData["plan_sha256"] = [string]$summary.plan_sha256
                    $script:runRecordData["failing_test"] = [string]$summary.failing_test
                    $script:runRecordData["tests_planned"] = [int]$summary.tests_planned
                    $script:runRecordData["tests_executed"] = [int]$summary.tests_executed
                    $script:runRecordData["tests_not_reached"] = [int]$summary.tests_not_reached
                    $script:runRecordData["executed_matches_plan"] = [string]$summary.executed_matches_plan
                }
            }
        }
        catch {
            $runProblems.Add("isolation evidence could not be read: $($_.Exception.Message)")
        }
    }
    else { $runProblems.Add("isolation evidence file is absent") }

    try {
        $reconciliationLogLines = @($clonePreflightOutputLines |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $developmentRunLogLines = @(Get-CommandLogOutput `
            -LogPath $logPath -Label "Explicit-label development run" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $timeoutEvidenceLines = @($developmentRunLogLines | Where-Object {
            $_ -match "^LOCK_TIMEOUT_EFFECTIVE(?:\s|$)"
        })
        if ($timeoutEvidenceLines.Count -eq 0) {
            $runProblems.Add("CP-0 did not report the effective lock timeout on an open test connection")
        }
        foreach ($line in $timeoutEvidenceLines) {
            $timeoutProblems = @(Get-LockTimeoutObservationProblems `
                -Line $line `
                -ExpectedMilliseconds ([int]$script:lockTimeoutMilliseconds))
            foreach ($problem in $timeoutProblems) {
                $runProblems.Add($problem)
            }
        }
        $reconciliationLogLines += $developmentRunLogLines
        $reconciliation = Get-RunDatabaseReconciliation `
            -ReceiptPath $receiptPath -LogLines $reconciliationLogLines -RunNonce $runNonce `
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

$finalDevVerdict = if ($script:watchdogStalled) {
    "STALL"
}
elseif ($null -eq $failure -and $runProblems.Count -eq 0) {
    "PASS"
}
else {
    "FAIL"
}
# HG_DIRECTION_TU1B_SCOPE_AND_RC1_TEXTS_20261015 (15 Oct 2026); commission section 1: a refused required control or failed isolation cannot yield an overall PASS.
$requiredControlsPassed = $finalDevVerdict -eq "PASS" -and
    $instrumentIsolationVerdict -eq "PASS" -and
    $null -ne $summary -and
    $summary.cp0_requirements_met -eq $true -and
    $null -ne $counts -and
    $counts.was_successful -eq $true

if ($null -ne $script:runRecordData) {
    $ordinaryRecord = $null
    if ($null -ne $counts) {
        $ordinaryRecord = [ordered]@{
            tests_run = [int]$counts.tests_run
            errors = [int]$counts.errors
            failures = [int]$counts.failures
            skipped = [int]$counts.skipped
            was_successful = [bool]$counts.was_successful
        }
    }
    $runResult = if ($null -ne $counts -and [int]$counts.errors -gt 0) {
        "ERROR"
    }
    elseif ($null -ne $counts -and [int]$counts.failures -gt 0) {
        "FAIL"
    }
    elseif ($requiredControlsPassed) {
        "PASS"
    }
    else {
        "FAIL"
    }
    $script:runRecordData["result"] = $runResult
    $script:runRecordData["isolation_verdict"] = $instrumentIsolationVerdict
    $script:runRecordData["dev_run_verdict"] = $finalDevVerdict
    $script:runRecordData["cp0_requirements_met"] = (
        $null -ne $summary -and $summary.cp0_requirements_met -eq $true
    )
    $script:runRecordData["required_controls_passed"] = $requiredControlsPassed
    $script:runRecordData["ordinary_test_verdict"] = $ordinaryRecord
    $runRecordPath = Join-Path $runDirectory "run-record.json"
    $temporaryRecordPath = "$runRecordPath.tmp"
    try {
        if (Test-Path -LiteralPath $runRecordPath) {
            throw "run record already exists; refusing to overwrite"
        }
        [System.IO.File]::WriteAllText(
            $temporaryRecordPath,
            (($script:runRecordData | ConvertTo-Json -Depth 8) + "`n"),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Move($temporaryRecordPath, $runRecordPath)
        Write-Output "RUN_RECORD_CLASSIFICATION=TEST_ATTEMPT"
        Write-Output "RUN_RECORD_FILE=$runRecordPath"
    }
    catch {
        $runProblems.Add("final run record could not be written: $($_.Exception.Message)")
        if (-not $script:watchdogStalled) {
            $finalDevVerdict = "FAIL"
        }
    }
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
if ($finalDevVerdict -eq "STALL") {
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
    if ($runProblems.Count -eq 0 -and $finalDevVerdict -eq "PASS") {
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
exit $global:LASTEXITCODE
