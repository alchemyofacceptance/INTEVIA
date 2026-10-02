$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
$scriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "SCRIPT_SHA256=$scriptHash"

$ErrorActionPreference = "Stop"
$expectedBranch = "pktb/aa-r1-application-authority"
$expectedBaseline = "6baf3bf5edb0b1c5ff3af0ad34aab8ae977fea57"
$expectedRoute = "LIVING_ORGANISM_TEST_LIFECYCLE"
$expectedTestDatabasePrefix = "test_intevia_living_organism_"
$expectedCoreHead = "0034_s015_authority_decisions"
$teardownCount = 0
$failure = $null
$runDirectory = $null

function Invoke-RecordedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Output "COMMAND=$Label"
    $output = @(& python @Arguments 2>&1)
    $status = $LASTEXITCODE
    foreach ($line in $output) {
        $lineText = [string]$line
        $databasePassword = [Environment]::GetEnvironmentVariable("INTEVIA_POSTGRES_PASSWORD")
        if (-not [string]::IsNullOrEmpty($databasePassword)) {
            $lineText = $lineText.Replace($databasePassword, "[REDACTED]")
        }
        Write-Output $lineText
        if ($lineText -match "Destroying test database for alias") {
            $script:teardownCount++
        }
    }
    if ($status -ne 0) {
        throw "$Label failed with exit code $status"
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

    $diffPath = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString("N") + ".diff")
    try {
        & git diff --binary $expectedBaseline "--output=$diffPath"
        if ($LASTEXITCODE -ne 0) {
            throw "git diff against baseline failed"
        }
        $diffHash = (Get-FileHash -LiteralPath $diffPath -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Output "BASELINE_DIFF_SHA256=$diffHash"
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
    foreach ($path in $untracked) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "untracked path is not a regular file: $path"
        }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Output "UNTRACKED_SHA256=$hash`:$path"
    }

    $changedPython = @(& git diff --name-only --diff-filter=ACMRTUXB $expectedBaseline -- "*.py")
    if ($LASTEXITCODE -ne 0) {
        throw "changed Python-file inventory failed"
    }
    $pythonFiles = @($changedPython + @($untracked | Where-Object { $_ -like "*.py" }) |
        Sort-Object -Unique | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
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
    $testDatabase = $env:INTEVIA_POSTGRES_TEST_DB
    if ([string]::IsNullOrWhiteSpace($testDatabase)) {
        throw "INTEVIA_POSTGRES_TEST_DB is required"
    }
    if ($testDatabase -cnotmatch "^test_intevia_living_organism_[a-z0-9_]+$") {
        throw "test database is not attempt-owned by the S015 route"
    }
    foreach ($name in @("INTEVIA_POSTGRES_DB", "INTEVIA_POSTGRES_USER", "INTEVIA_POSTGRES_PASSWORD")) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name is required"
        }
    }
    $stagingRoot = Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") "PKTB_AA_R1"
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    $runDirectory = Join-Path $stagingRoot ("run-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $runDirectory | Out-Null
    Write-Output "RUN_DIRECTORY=$runDirectory"
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
    Write-Output "TEST_ROUTE=$expectedRoute"
    Write-Output "DJANGO_SETTINGS_MODULE=intevia.test_settings (--settings=intevia.test_settings)"
    Write-Output "ISOLATION_RUNNER=verification.isolation.runner.IsolationRunner"
    Write-Output "EXPECTED_CORE_HEAD=$expectedCoreHead"
    Write-Output "TEST_DATABASE=$testDatabase"
    Write-Output "DATABASE_CREDENTIALS=present (values withheld)"

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
        "--testrunner=verification.isolation.runner.IsolationRunner", "--parallel", "1", "--noinput"
    ) + $suites
    Invoke-RecordedCommand -Label "S015 application, S1/S2/S3 and guard suites" -Arguments $arguments
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
    exit 0
}
Write-Output "VERDICT: FAIL"
exit 1
