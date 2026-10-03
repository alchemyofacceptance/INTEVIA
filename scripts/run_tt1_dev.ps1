[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("fresh", "clone")][string]$Mode,
    [Parameter(Mandatory = $true)][ValidateRange(1, 13)][int]$AttemptNumber,
    [string[]]$TestLabels = @(
        "tests.test_tt1_template_lifecycle",
        "tests.test_tt1_identity",
        "tests.test_tt1_ownership"
    )
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
$expectedBranch = "pktb/tt1-template-clones"
$expectedBaseline = "4fee6699d3e59febec60dfc2d6ef11338e21a16d"

function Invoke-PythonAndLog {
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
    }
    $status = $LASTEXITCODE
    return [int]$status
}

try {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    Set-Location -LiteralPath $repoRoot

    $parseTokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { throw "PowerShell syntax preflight failed: $($parseErrors[0].Message)" }

    $branch = (git branch --show-current).Trim()
    $head = (git rev-parse HEAD).Trim()
    if ($branch -ne $expectedBranch -or $head -ne $expectedBaseline) {
        throw "branch or baseline mismatch"
    }

    $credentialNames = @("INTEVIA_DATABASE_ENGINE", "INTEVIA_POSTGRES_DB", "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD", "DJANGO_SECRET_KEY")
    foreach ($name in $credentialNames) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name is required"
        }
    }
    if ($env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected administrative database is refused"
    }

    $changedPython = @(& git diff --name-only --diff-filter=ACMRTUXB $expectedBaseline -- "*.py")
    if ($LASTEXITCODE -ne 0) { throw "changed Python-file inventory failed" }
    $untracked = @(& git ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "untracked-file inventory failed" }
    $untrackedPython = @($untracked | Where-Object { $_ -like "*.py" })
    $pythonFiles = @($changedPython + $untrackedPython | Sort-Object -Unique |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($pythonFiles.Count -gt 0) {
        & python -m py_compile @pythonFiles
        if ($LASTEXITCODE -ne 0) { throw "Python byte-compilation failed" }
    }

    $stageRoot = Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") "PKTB_TT1\ME_DEV"
    $runDirectory = Join-Path $stageRoot ("attempt_{0}" -f $AttemptNumber)
    if (Test-Path -LiteralPath $runDirectory) { throw "attempt directory already exists; refusing reuse" }
    New-Item -ItemType Directory -Path $runDirectory | Out-Null

    $attemptDatabase = "test_intevia_living_organism_tt1_me_$AttemptNumber"
    if ($attemptDatabase.Length -gt 63) { throw "test database name exceeds PostgreSQL's identifier limit" }
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "tt1-development.log"

    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_POSTGRES_TEST_DB = $attemptDatabase
    $env:INTEVIA_S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
    $env:VERIFICATION_EXPECTED_CORE_HEAD = "0040_s2b_role_actor_nullable_state"
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_RUN_NONCE = $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_TEMPLATE_MODE = $Mode
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    $env:PYTHONUNBUFFERED = "1"
    if ($Mode -eq "clone") {
        $env:VERIFICATION_TEMPLATE_STORE = Join-Path $stageRoot "templates"
        $env:VERIFICATION_TEMPLATE_CLEANUP = "0"
    }
    else {
        Remove-Item Env:VERIFICATION_TEMPLATE_STORE, Env:VERIFICATION_TEMPLATE_CLEANUP -ErrorAction SilentlyContinue
    }
    $testPythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $testPythonPath += $env:PYTHONPATH }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $testPythonPath)

    Write-Output "MODE=$Mode"
    Write-Output "ATTEMPT=$AttemptNumber"
    Write-Output "DATABASE=$attemptDatabase"
    Write-Output "DATABASE_CREDENTIALS=present (values withheld)"
    $checkCode = Invoke-PythonAndLog -Arguments @("manage.py", "check", "--settings=intevia.test_settings") -LogPath $logPath
    if ($checkCode -ne 0) { throw "Django system check failed with status $checkCode" }

    $arguments = @("manage.py", "test", "--settings=intevia.test_settings",
        "--testrunner=verification.isolation.runner.IsolationRunner", "--parallel", "1", "--noinput", "--verbosity=2") + $TestLabels
    $testCode = Invoke-PythonAndLog -Arguments $arguments -LogPath $logPath
    if ($testCode -ne 0) { throw "development test batch failed with status $testCode" }
    Write-Output "RUN_LOG=$logPath"
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
    Write-Output "DEV_RUN_VERDICT=PASS"
    $global:LASTEXITCODE = 0
}
else {
    Write-Output "DEV_RUN_VERDICT=FAIL"
    $global:LASTEXITCODE = 1
}