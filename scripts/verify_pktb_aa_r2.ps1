$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
$ErrorActionPreference = "Stop"

$parseTokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {
    foreach ($parseError in $parseErrors) {
        Write-Output "POWERSHELL_SYNTAX_FAIL=$($parseError.Message)"
    }
    throw "PowerShell syntax preflight failed"
}
Write-Output "POWERSHELL_SYNTAX=OK"

$scriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "SCRIPT_SHA256=$scriptHash"

$expectedBranch = "pktb/aa-r2-office-authority"
$expectedBaseline = "5cf628ffca1821c4800ade644dd887b93a429037"
$expectedRoute = "LIVING_ORGANISM_TEST_LIFECYCLE"
$expectedCoreHead = "0040_s2b_role_actor_nullable_state"
$testDatabasePrefix = "test_intevia_living_organism_aa_r2_me_"
$teardownCount = 0
$failure = $null
$runDirectory = $null
$runNonce = $null
$reviewedSnapshot = $null

function Invoke-RecordedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$LogPath
    )

    Write-Output "COMMAND=$Label"
    if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
        Add-Content -LiteralPath $LogPath -Value "COMMAND=$Label" -Encoding utf8
    }
    $output = @(& python @Arguments 2>&1 | ForEach-Object {
        $lineText = [string]$_
        $databasePassword = [Environment]::GetEnvironmentVariable("INTEVIA_POSTGRES_PASSWORD")
        if (-not [string]::IsNullOrEmpty($databasePassword)) {
            $lineText = $lineText.Replace($databasePassword, "[REDACTED]")
        }
        if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
            Add-Content -LiteralPath $LogPath -Value $lineText -Encoding utf8
        }
        Write-Host $lineText
        $lineText
    })
    $status = $LASTEXITCODE
    foreach ($line in $output) {
        $lineText = [string]$line
        if ($lineText -match "Destroying test database for alias") {
            $script:teardownCount++
        }
    }
    if ($status -ne 0) {
        throw "$Label returned status $status"
    }
}

function Get-ReviewedByteSnapshot {
    param([Parameter(Mandatory = $true)][string]$Baseline)

    $diffPath = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString("N") + ".diff")
    try {
        & git diff --binary $Baseline "--output=$diffPath"
        if ($LASTEXITCODE -ne 0) {
            throw "git diff against baseline failed"
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
        throw "git untracked-file inventory failed"
    }
    $untrackedRows = [System.Collections.Generic.List[string]]::new()
    foreach ($path in $untracked) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "untracked path is not a regular file: $path"
        }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        $untrackedRows.Add("$hash`:$path")
    }
    $bindingParts = @("baseline=$Baseline", "diff=$diffHash") + @($untrackedRows | Sort-Object)
    $bindingBytes = [System.Text.Encoding]::UTF8.GetBytes(($bindingParts -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bindingHash = -join ($sha.ComputeHash($bindingBytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
    }
    return [PSCustomObject]@{
        DiffHash = $diffHash
        UntrackedRows = @($untrackedRows | Sort-Object)
        BindingHash = $bindingHash
    }
}

function Write-IsolationEvidence {
    param([Parameter(Mandatory = $true)][string]$Path)

    $cp0Written = $false
    $summaryWritten = $false
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            try {
                $record = $line | ConvertFrom-Json
            }
            catch {
                continue
            }
            if ($record.checkpoint -eq "CP-0") {
                Write-Output "CP-0_RECORD=$line"
                $cp0Written = $true
            }
            elseif ($record.checkpoint -eq "SUMMARY") {
                Write-Output "SUMMARY_RECORD=$line"
                $summaryWritten = $true
            }
        }
    }
    if (-not $cp0Written) {
        Write-Output "CP-0_RECORD=ABSENT"
    }
    if (-not $summaryWritten) {
        Write-Output "SUMMARY_RECORD=ABSENT"
    }
}

try {
    $branch = (git branch --show-current).Trim()
    $head = (git rev-parse HEAD).Trim()
    Write-Output "BRANCH=$branch"
    Write-Output "BASELINE_SHA=$head"
    if ($branch -ne $expectedBranch) {
        throw "branch mismatch"
    }
    if ($head -ne $expectedBaseline) {
        throw "baseline SHA mismatch"
    }

    $reviewedSnapshot = Get-ReviewedByteSnapshot -Baseline $expectedBaseline
    Write-Output "BASELINE_DIFF_SHA256=$($reviewedSnapshot.DiffHash)"
    foreach ($row in $reviewedSnapshot.UntrackedRows) {
        Write-Output "UNTRACKED_SHA256=$row"
    }
    Write-Output "REVIEWED_BYTES_BINDING_SHA256=$($reviewedSnapshot.BindingHash)"

    $changedPython = @(& git diff --name-only --diff-filter=ACMRTUXB $expectedBaseline -- "*.py")
    if ($LASTEXITCODE -ne 0) {
        throw "changed Python-file inventory failed"
    }
    $untrackedPython = @($reviewedSnapshot.UntrackedRows | ForEach-Object {
        $rowPath = $_.Substring($_.IndexOf(":") + 1)
        if ($rowPath -like "*.py") { $rowPath }
    })
    $pythonFiles = @($changedPython + $untrackedPython | Sort-Object -Unique |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    $syntaxCheck = @'
import ast
import sys

failed = False
for path in sys.argv[1:]:
    try:
        with open(path, "rb") as source_file:
            ast.parse(source_file.read(), filename=path)
    except (OSError, SyntaxError, UnicodeError) as error:
        print(f"SYNTAX_FAIL={path}: {error}", flush=True)
        failed = True
    else:
        print(f"SYNTAX_OK={path}", flush=True)
raise SystemExit(1 if failed else 0)
'@
    Invoke-RecordedCommand -Label "Python AST syntax preflight" -Arguments (@("-c", $syntaxCheck) + $pythonFiles)

    if ($env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected administrative database is refused"
    }
    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_S015_TEST_ROUTE = $expectedRoute
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    foreach ($name in @("INTEVIA_POSTGRES_DB", "INTEVIA_POSTGRES_USER", "INTEVIA_POSTGRES_PASSWORD")) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name is required"
        }
    }

    $stagingRoot = Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") "PKTB_AA_R2\ME_DEV"
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    $attemptNumbers = @(Get-ChildItem -LiteralPath $stagingRoot -Directory -Filter "attempt_*" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.Name -match "^attempt_(\d+)$") { [int]$Matches[1] } })
    $attemptNumber = if ($attemptNumbers.Count -eq 0) { 1 } else { ($attemptNumbers | Measure-Object -Maximum).Maximum + 1 }
    $runDirectory = Join-Path $stagingRoot ("attempt_{0}" -f $attemptNumber)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "attempt directory already exists"
    }
    New-Item -ItemType Directory -Path $runDirectory | Out-Null

    $testDatabase = "$testDatabasePrefix$attemptNumber"
    if ($testDatabase -match "^(intevia|intevia_dev)$") {
        throw "protected test database is refused"
    }
    $env:INTEVIA_POSTGRES_TEST_DB = $testDatabase
    $env:VERIFICATION_RUN_NONCE = [guid]::NewGuid().ToString("N")
    $env:VERIFICATION_DB_RECEIPT = Join-Path $runDirectory "database-receipts.jsonl"
    $env:VERIFICATION_ISOLATION_JSON = Join-Path $runDirectory "isolation.jsonl"
    $env:VERIFICATION_EXPECTED_CORE_HEAD = $expectedCoreHead
    $env:VERIFICATION_STEP = "S015"
    $testPythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $testPythonPath += $env:PYTHONPATH
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $testPythonPath)
    $env:PYTHONUNBUFFERED = "1"

    Write-Output "RUN_DIRECTORY=$runDirectory"
    $suiteLogPath = Join-Path $runDirectory "r1-regression-output.log"
    Write-Output "REGRESSION_LOG=$suiteLogPath"
    Write-Output "TEST_ROUTE=$expectedRoute"
    Write-Output "DJANGO_SETTINGS_MODULE=intevia.test_settings (--settings=intevia.test_settings)"
    Write-Output "ISOLATION_RUNNER=verification.isolation.runner.IsolationRunner"
    Write-Output "EXPECTED_CORE_HEAD=$expectedCoreHead"
    Write-Output "TEST_DATABASE=$testDatabase"
    Write-Output "DATABASE_CREDENTIALS=present (values withheld)"
    Write-Output "AP6=NOT RUN (not exercised under a non-superuser role)"

    $suites = @(
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
        "tests.test_s015_test_harness"
    )
    $arguments = @(
        "manage.py", "test", "--settings=intevia.test_settings",
        "--testrunner=verification.isolation.runner.IsolationRunner", "--parallel", "1", "--noinput", "--verbosity=2"
    ) + $suites
    Invoke-RecordedCommand -Label "R1 plus S1/S2/S3 regression suites" -LogPath $suiteLogPath -Arguments $arguments

    if ($teardownCount -ne 1) {
        throw "expected exactly one test database teardown; observed $teardownCount"
    }
    $isolationRecords = @(Get-Content -LiteralPath $env:VERIFICATION_ISOLATION_JSON | ForEach-Object { $_ | ConvertFrom-Json })
    $cp0 = @($isolationRecords | Where-Object { $_.checkpoint -eq "CP-0" })
    $summary = @($isolationRecords | Where-Object { $_.checkpoint -eq "SUMMARY" })
    if ($cp0.Count -ne 1 -or $cp0[0].requirements_met -ne $true) {
        throw "isolation CP-0 was not established exactly once"
    }
    if ($summary.Count -ne 1 -or $summary[0].isolation_verdict -ne "PASS" -or
        $summary[0].ordinary_test_verdict.was_successful -ne $true) {
        throw "isolation runner did not report one successful summary"
    }
    $receipts = @(Get-Content -LiteralPath $env:VERIFICATION_DB_RECEIPT | ForEach-Object { $_ | ConvertFrom-Json })
    $created = @($receipts | Where-Object { $_.event -eq "created" -and $_.run_nonce -eq $env:VERIFICATION_RUN_NONCE })
    $destroyed = @($receipts | Where-Object {
        $_.event -eq "destroyed" -and $_.run_nonce -eq $env:VERIFICATION_RUN_NONCE -and
        $_.confirmed_absent -eq $true
    })
    if ($created.Count -ne 1 -or $destroyed.Count -ne 1) {
        throw "owned test database did not have exactly one creation and confirmed teardown receipt"
    }

    $finalSnapshot = Get-ReviewedByteSnapshot -Baseline $expectedBaseline
    if ($finalSnapshot.BindingHash -ne $reviewedSnapshot.BindingHash) {
        throw "reviewed repository bytes changed during the run"
    }
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
    Write-Output "FAILURE=$failure"
}

if (-not [string]::IsNullOrWhiteSpace($runDirectory)) {
    Write-IsolationEvidence -Path (Join-Path $runDirectory "isolation.jsonl")
}

Write-Output "TEST_DATABASE_TEARDOWN_COUNT=$teardownCount"
if ($null -eq $failure) {
    Write-Output "VERDICT: PASS"
    Write-Output "SCRIPT_STATUS=COMPLETED"
    return
}
Write-Output "VERDICT: FAIL"
Write-Output "SCRIPT_STATUS=FAILED; see FAILURE above"
return