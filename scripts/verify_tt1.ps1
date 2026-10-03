[CmdletBinding()]
param(
    [switch]$Template,
    [switch]$Clone
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

$ErrorActionPreference = "Stop"
$failure = $null
$runDirectory = $null
$runNonce = $null
$teardownCount = 0
$expectedBranch = "pktb/tt1-template-clones"
$expectedBaseline = "4fee6699d3e59febec60dfc2d6ef11338e21a16d"
$expectedRoute = "LIVING_ORGANISM_TEST_LIFECYCLE"
$expectedCoreHead = "0040_s2b_role_actor_nullable_state"
$pythonSuite = @(
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
    "tests.test_tt1_template_lifecycle",
    "tests.test_tt1_identity",
    "tests.test_tt1_ownership"
)

function Get-ReviewedByteBinding {
    param([Parameter(Mandatory = $true)][string]$Baseline)

    $diffPath = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString("N") + ".diff")
    try {
        & git diff --binary $Baseline "--output=$diffPath"
        if ($LASTEXITCODE -ne 0) { throw "git diff against baseline failed" }
        $diffHash = (Get-FileHash -LiteralPath $diffPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    finally {
        if (Test-Path -LiteralPath $diffPath) { Remove-Item -LiteralPath $diffPath -Force }
    }

    $untracked = @(& git ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "git untracked-file inventory failed" }
    $rows = @()
    foreach ($path in $untracked) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "untracked path is not a regular file: $path"
        }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        $rows += "$hash`:$path"
    }
    $parts = @("baseline=$Baseline", "diff=$diffHash") + @($rows | Sort-Object)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))
        $binding = -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
    }
    return [PSCustomObject]@{ DiffHash = $diffHash; Rows = @($rows | Sort-Object); Binding = $binding }
}

function Invoke-PythonAndCapture {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    & python @Arguments 2>&1 | ForEach-Object {
        $line = [string]$_
        $password = [Environment]::GetEnvironmentVariable("INTEVIA_POSTGRES_PASSWORD")
        if (-not [string]::IsNullOrEmpty($password)) { $line = $line.Replace($password, "[REDACTED]") }
        Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
        Write-Host $line
        if ($line -match "Destroying test database for alias") {
            $script:teardownCount = $script:teardownCount + 1
        }
    }
    $status = $LASTEXITCODE
    return [int]$status
}

function Get-NextVerificationRunNumber {
    param([Parameter(Mandatory = $true)][string]$VerificationRoot)

    $existingRuns = @(Get-ChildItem -LiteralPath $VerificationRoot -Directory -Filter "run_*" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.Name -match "^run_(\d+)$") { [int]$Matches[1] } })
    $runNumber = 1
    while ($existingRuns -contains $runNumber) { $runNumber++ }
    return $runNumber
}

try {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    Set-Location -LiteralPath $repoRoot

    $parseTokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { throw "PowerShell syntax preflight failed: $($parseErrors[0].Message)" }

    $scriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "SCRIPT_SHA256=$scriptHash"

    $changedPython = @(& git diff --name-only --diff-filter=ACMRTUXB $expectedBaseline -- "*.py")
    if ($LASTEXITCODE -ne 0) { throw "changed Python-file inventory failed" }
    $untracked = @(& git ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "untracked-file inventory failed" }
    $untrackedPython = @($untracked | Where-Object { $_ -like "*.py" })
    $pythonFiles = @($changedPython + $untrackedPython | Sort-Object -Unique |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    $syntaxCheck = @'
import ast
import sys

failed = False
for path in sys.argv[1:]:
    try:
        with open(path, "rb") as source:
            ast.parse(source.read(), filename=path)
    except (OSError, SyntaxError, UnicodeError) as error:
        print(f"SYNTAX_FAIL={path}: {error}", flush=True)
        failed = True
    else:
        print(f"SYNTAX_OK={path}", flush=True)
raise SystemExit(1 if failed else 0)
'@
    & python -c $syntaxCheck @pythonFiles
    if ($LASTEXITCODE -ne 0) { throw "Python syntax preflight failed" }

    if ($Template -or $Clone) { throw "template and clone options are refused by the fresh-build verifier" }
    if ($env:VERIFICATION_TEMPLATE_MODE -and $env:VERIFICATION_TEMPLATE_MODE -ne "fresh") {
        throw "inherited template/clone mode is refused by the fresh-build verifier"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:VERIFICATION_TEMPLATE_STORE)) {
        throw "inherited template store is refused by the fresh-build verifier"
    }

    $branch = (git branch --show-current).Trim()
    $head = (git rev-parse HEAD).Trim()
    Write-Output "BRANCH=$branch"
    Write-Output "BASELINE_SHA=$head"
    if ($branch -ne $expectedBranch -or $head -ne $expectedBaseline) {
        throw "branch or baseline mismatch"
    }
    $reviewedBytes = Get-ReviewedByteBinding -Baseline $expectedBaseline
    Write-Output "BASELINE_DIFF_SHA256=$($reviewedBytes.DiffHash)"
    Write-Output "REVIEWED_BYTES_BINDING_SHA256=$($reviewedBytes.Binding)"

    if ($Template -or $Clone -or $env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected database or template/clone option is refused"
    }
    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_S015_TEST_ROUTE = $expectedRoute
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    foreach ($name in @("INTEVIA_POSTGRES_DB", "INTEVIA_POSTGRES_USER", "INTEVIA_POSTGRES_PASSWORD")) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name is required"
        }
    }

    $stageRoot = Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") "PKTB_TT1\VERIFICATION_OF_RECORD"
    $attemptNumber = Get-NextVerificationRunNumber -VerificationRoot $stageRoot
    $runDirectory = Join-Path $stageRoot ("run_{0}" -f $attemptNumber)
    if (Test-Path -LiteralPath $runDirectory) { throw "attempt directory already exists; refusing reuse" }
    New-Item -ItemType Directory -Path $runDirectory | Out-Null

    $attemptDatabase = "test_intevia_living_organism_tt1_vr_$attemptNumber"
    if ($attemptDatabase -in @("intevia", "intevia_dev") -or $attemptDatabase.Length -gt 63) {
        throw "protected or overlength test database name refused"
    }
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "r2-plus-tt1-fresh.log"
    $env:INTEVIA_POSTGRES_TEST_DB = $attemptDatabase
    $env:VERIFICATION_RUN_NONCE = $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = $expectedCoreHead
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = "fresh"
    $env:VERIFICATION_TEMPLATE_STORE = Join-Path $runDirectory "tooling-template-store"
    $env:VERIFICATION_TEMPLATE_CLEANUP = "0"
    $testPythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $testPythonPath += $env:PYTHONPATH }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $testPythonPath)
    $env:PYTHONUNBUFFERED = "1"

    Write-Output "ATTEMPT=$attemptNumber"
    Write-Output "TEST_DATABASE=$attemptDatabase"
    Write-Output "DATABASE_CREDENTIALS=present (values withheld)"
    $checkCode = Invoke-PythonAndCapture -Arguments @("manage.py", "check", "--settings=intevia.test_settings") -LogPath $logPath
    if ($checkCode -ne 0) { throw "Django system check failed with status $checkCode" }

    $arguments = @("manage.py", "test", "--settings=intevia.test_settings",
        "--testrunner=verification.isolation.runner.IsolationRunner", "--parallel", "1", "--noinput", "--verbosity=2") + $pythonSuite
    $testCode = Invoke-PythonAndCapture -Arguments $arguments -LogPath $logPath
    if ($testCode -ne 0) { throw "fresh R2 plus TT1 suite failed with status $testCode" }

    if ($teardownCount -ne 1) { throw "expected one test database teardown; observed $teardownCount" }
    $isolationRecords = @(Get-Content -LiteralPath $isolationPath | ForEach-Object { $_ | ConvertFrom-Json })
    $cp0 = @($isolationRecords | Where-Object { $_.checkpoint -eq "CP-0" })
    $summary = @($isolationRecords | Where-Object { $_.checkpoint -eq "SUMMARY" })
    if ($cp0.Count -ne 1 -or $cp0[0].requirements_met -ne $true) { throw "CP-0 was not established exactly once" }
    if ($summary.Count -ne 1 -or $summary[0].isolation_verdict -ne "PASS" -or
        $summary[0].ordinary_test_verdict.was_successful -ne $true) { throw "fresh isolation summary did not PASS" }

    $receipts = @(Get-Content -LiteralPath $receiptPath | ForEach-Object { $_ | ConvertFrom-Json })
    $created = @($receipts | Where-Object {
        $_.event -eq "created" -and $_.name -eq $attemptDatabase -and $_.run_nonce -eq $runNonce
    })
    $destroyed = @($receipts | Where-Object {
        $_.event -eq "destroyed" -and $_.name -eq $attemptDatabase -and
        $_.run_nonce -eq $runNonce -and $_.confirmed_absent -eq $true
    })
    if ($created.Count -ne 1 -or $destroyed.Count -ne 1 -or $created[0].oid -ne $destroyed[0].oid) {
        throw "fresh test database creation and teardown receipts do not match"
    }

    $finalBytes = Get-ReviewedByteBinding -Baseline $expectedBaseline
    if ($finalBytes.Binding -ne $reviewedBytes.Binding) { throw "reviewed repository bytes changed during the run" }
    if ((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $scriptHash) {
        throw "verifier bytes changed during the run"
    }
    Write-Output "REVIEWED_BYTES_UNCHANGED=True"
    Write-Output "ISOLATION_VERDICT=$($summary[0].isolation_verdict)"
    Write-Output "TESTS_RUN=$($summary[0].ordinary_test_verdict.tests_run)"
    Write-Output "TEST_ERRORS=$($summary[0].ordinary_test_verdict.errors)"
    Write-Output "TEST_FAILURES=$($summary[0].ordinary_test_verdict.failures)"
    Write-Output "TEST_SKIPPED=$($summary[0].ordinary_test_verdict.skipped)"
}
catch {
    $failure = $_.Exception.Message
    $password = [Environment]::GetEnvironmentVariable("INTEVIA_POSTGRES_PASSWORD")
    if (-not [string]::IsNullOrEmpty($password)) { $failure = $failure.Replace($password, "[REDACTED]") }
    Write-Output "FAILURE=$failure"
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
}

if ($null -eq $failure) {
    Write-Output "VERDICT: PASS"
    $global:LASTEXITCODE = 0
}
else {
    Write-Output "VERDICT: FAIL"
    $global:LASTEXITCODE = 1
}