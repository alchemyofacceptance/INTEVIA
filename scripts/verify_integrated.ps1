[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$originalLocation = Get-Location
$expectedBranch = "pktb/int-r3-tt1"
$expectedR3Parent = "a9dadb5e"
$expectedTt1Parent = "76291565"
$expectedRoute = "LIVING_ORGANISM_TEST_LIFECYCLE"
$expectedCoreHead = "0040_s2b_role_actor_nullable_state"
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

$failureReasons = [System.Collections.Generic.List[string]]::new()
$script:printedSummary = [ordered]@{
    tests_run = $null
    failures = $null
    errors = $null
    skipped = $null
    run_lines = 0
    result_seen = $false
    status = $null
}
$script:teardownLines = 0
$sourceKind = $null
$head = $null
$mergeCommit = $null
$bindingHash = $null
$scriptHash = $null
$runDirectory = $null
$runNonce = $null
$testDatabase = $null
$receiptPath = $null
$isolationPath = $null
$initialBinding = $null
$counts = [ordered]@{ tests_run = $null; failures = $null; errors = $null; skipped = $null }
$verdict = "FAIL"
$isolationVerdict = "NOT ESTABLISHED"
$cp0Met = $false
$ordinary = $null
$teardown = [ordered]@{
    database = $null
    created = 0
    destroyed = 0
    created_oid = $null
    destroyed_oid = $null
    oids_match = $false
    confirmed_absent = $false
}

function Add-Failure {
    param([Parameter(Mandatory = $true)][string]$Message)

    if (-not [string]::IsNullOrWhiteSpace($Message)) {
        $failureReasons.Add($Message)
    }
}

function Protect-OutputText {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)

    $protected = $Text
    foreach ($name in @("INTEVIA_POSTGRES_PASSWORD", "DJANGO_SECRET_KEY")) {
        $secret = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrEmpty($secret)) {
            $protected = $protected.Replace($secret, "[REDACTED]")
        }
    }
    return $protected
}

function Get-Sha256Bytes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return -join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
    }
}

function Get-IntegratedMergeCommit {
    $history = @(& git rev-list --all --parents)
    if ($LASTEXITCODE -ne 0) {
        throw "merge-commit discovery failed"
    }

    $matches = @()
    foreach ($line in $history) {
        $fields = @($line -split "\s+")
        if ($fields.Count -eq 3 -and
            $fields[1].StartsWith($expectedR3Parent, [System.StringComparison]::OrdinalIgnoreCase) -and
            $fields[2].StartsWith($expectedTt1Parent, [System.StringComparison]::OrdinalIgnoreCase)) {
            $matches += $fields[0]
        }
    }
    if ($matches.Count -ne 1) {
        throw "expected one merge commit with the commissioned parent pair; found $($matches.Count)"
    }
    return [string]$matches[0]
}

function Assert-AuthorizedChangePaths {
    param([Parameter(Mandatory = $true)][string[]]$Paths)

    $authorized = @(
        "scripts/run_integrated_dev.ps1",
        "scripts/verify_integrated.ps1",
        "tests/test_verify_integrated.ps1"
    )
    $normalized = @($Paths | ForEach-Object { $_.Replace("\", "/") } | Sort-Object -Unique)
    if ($normalized.Count -ne $authorized.Count -or
        @($normalized | Where-Object { $_ -notin $authorized }).Count -ne 0) {
        throw "source changes are outside the three authorized INT-1 files"
    }
}

function Get-SourceBinding {
    param([Parameter(Mandatory = $true)][string]$IntegratedMerge)

    $currentBranch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $currentBranch -ne $expectedBranch) {
        throw "source branch is not the commissioned integration branch"
    }
    $currentHead = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "HEAD lookup failed"
    }
    $parentsText = (& git show -s --format=%P $currentHead).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "HEAD parent lookup failed"
    }
    $parents = @($parentsText -split "\s+" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

    if ($currentHead -eq $IntegratedMerge) {
        $trackedPaths = @(& git diff --name-only $IntegratedMerge)
        if ($LASTEXITCODE -ne 0) {
            throw "candidate changed-path inventory failed"
        }
        $diffPath = [System.IO.Path]::GetTempFileName()
        try {
            & git diff --binary $IntegratedMerge "--output=$diffPath"
            if ($LASTEXITCODE -ne 0) {
                throw "candidate diff calculation failed"
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
            throw "candidate untracked-file inventory failed"
        }
        Assert-AuthorizedChangePaths -Paths (@($trackedPaths) + @($untracked))
        $rows = @()
        foreach ($path in $untracked) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                throw "candidate untracked path is not a regular file: $path"
            }
            $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            $rows += "$hash`:$path"
        }
        $rows = @($rows | Sort-Object)
        if ($diffHash -eq "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" -and
            $rows.Count -eq 0) {
            throw "candidate source contains no uncommitted INT-1 bytes"
        }
        $parts = @("baseline=$IntegratedMerge", "diff=$diffHash") + $rows
        $binding = Get-Sha256Bytes -Bytes ([System.Text.UTF8Encoding]::new($false).GetBytes(($parts -join "`n")))
        return [PSCustomObject]@{
            SourceKind = "CANDIDATE"
            Head = $currentHead
            MergeCommit = $IntegratedMerge
            Binding = $binding
            DiffHash = $diffHash
            Tree = $null
            Parent = $null
        }
    }

    if ($parents.Count -eq 1 -and $parents[0] -eq $IntegratedMerge) {
        $committedPaths = @(& git diff-tree --no-commit-id --name-only --no-renames -r $currentHead)
        if ($LASTEXITCODE -ne 0) {
            throw "committed changed-path inventory failed"
        }
        Assert-AuthorizedChangePaths -Paths $committedPaths
        $status = @(& git status --porcelain --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            throw "committed source worktree check failed"
        }
        if ($status.Count -ne 0) {
            throw "committed source worktree is not clean"
        }
        $tree = (& git rev-parse "${currentHead}^{tree}").Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "committed source tree lookup failed"
        }
        $parts = @("commit=$currentHead", "tree=$tree", "parent=$IntegratedMerge")
        $binding = Get-Sha256Bytes -Bytes ([System.Text.UTF8Encoding]::new($false).GetBytes(($parts -join "`n")))
        return [PSCustomObject]@{
            SourceKind = "COMMITTED"
            Head = $currentHead
            MergeCommit = $IntegratedMerge
            Binding = $binding
            DiffHash = $null
            Tree = $tree
            Parent = $IntegratedMerge
        }
    }

    throw "HEAD is neither the integrated candidate nor its clean single-parent commit"
}

function Get-NextVerificationRunNumber {
    param([Parameter(Mandatory = $true)][string]$VerificationRoot)

    $existing = @(Get-ChildItem -LiteralPath $VerificationRoot -Directory -Filter "run_*" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.Name -match "^run_(\d+)$") { [int]$Matches[1] } })
    $number = 1
    while ($existing -contains $number) { $number++ }
    return $number
}

function Set-PrintedSummaryFromLine {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)

    if ($Line -match "^Ran\s+(\d+)\s+tests?\s+in\b") {
        $script:printedSummary.tests_run = [int]$Matches[1]
        $script:printedSummary.run_lines++
    }
    if ($Line -match "^OK(?:\s+\((.*)\))?$") {
        $script:printedSummary.status = "PASS"
        $script:printedSummary.result_seen = $true
        $script:printedSummary.failures = 0
        $script:printedSummary.errors = 0
        $script:printedSummary.skipped = 0
        if ($Matches[1] -match "(?:^|,\s*)skipped=(\d+)") {
            $script:printedSummary.skipped = [int]$Matches[1]
        }
    }
    elseif ($Line -match "^FAILED(?:\s+\((.*)\))?$") {
        $script:printedSummary.status = "FAIL"
        $script:printedSummary.result_seen = $true
        foreach ($key in @("failures", "errors", "skipped")) {
            $script:printedSummary[$key] = 0
            if ($Matches[1] -match "(?:^|,\s*)$key=(\d+)") {
                $script:printedSummary[$key] = [int]$Matches[1]
            }
        }
    }
}

function Invoke-PythonAndCapture {
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
        Set-PrintedSummaryFromLine -Line $lineText
        if ($lineText -match "Destroying test database for alias") {
            $script:teardownLines++
        }
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
    if ($parseErrors.Count -gt 0) {
        throw "PowerShell syntax preflight failed: $($parseErrors[0].Message)"
    }

    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne $expectedBranch) {
        throw "branch mismatch"
    }
    $mergeCommit = Get-IntegratedMergeCommit
    $initialBinding = Get-SourceBinding -IntegratedMerge $mergeCommit
    $sourceKind = $initialBinding.SourceKind
    $head = $initialBinding.Head
    $bindingHash = $initialBinding.Binding
    $scriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "SOURCE_KIND=$sourceKind"
    Write-Output "HEAD=$head"
    Write-Output "MERGE_COMMIT=$mergeCommit"
    Write-Output "BINDING_SHA256=$bindingHash"
    Write-Output "SCRIPT_SHA256=$scriptHash"

    $inheritedVerification = @(Get-ChildItem Env: | Where-Object { $_.Name -like "VERIFICATION_*" })
    if ($inheritedVerification.Count -gt 0) {
        $names = @($inheritedVerification | ForEach-Object { $_.Name } | Sort-Object -Unique)
        throw "inherited VERIFICATION_* settings are refused: $($names -join ',')"
    }
    if ($env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected administrative database is refused"
    }
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name is required"
        }
    }

    $verificationRoot = Join-Path (Join-Path $HOME "INTEVIA_AGENT_STAGING") "PKTB_INT1\VERIFICATION_OF_RECORD"
    New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
    $runNumber = Get-NextVerificationRunNumber -VerificationRoot $verificationRoot
    $runDirectory = Join-Path $verificationRoot ("run_{0}" -f $runNumber)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "verification run directory already exists; refusing reuse"
    }
    $testDatabase = "test_intevia_living_organism_int1_vr_$runNumber"
    if ($testDatabase.Length -gt 63 -or $testDatabase -in @("intevia", "intevia_dev", $env:INTEVIA_POSTGRES_DB)) {
        throw "protected or overlength test database name refused"
    }
    New-Item -ItemType Directory -Path $runDirectory | Out-Null
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "integrated-fresh-suite.log"
    Write-Output "RUN_DIRECTORY=$runDirectory"

    $env:INTEVIA_DATABASE_ENGINE = "postgresql"
    $env:INTEVIA_POSTGRES_TEST_DB = $testDatabase
    $env:INTEVIA_S015_TEST_ROUTE = $expectedRoute
    $env:VERIFICATION_RUN_NONCE = $runNonce
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = $expectedCoreHead
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = "fresh"
    $env:VERIFICATION_TEMPLATE_STORE = Join-Path $runDirectory "tooling-template-store"
    $env:VERIFICATION_TEMPLATE_CLEANUP = "0"
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    $env:PYTHONUNBUFFERED = "1"
    $pythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $pythonPath += $env:PYTHONPATH
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $pythonPath)

    Write-Output "TEST_DATABASE=$testDatabase"
    Write-Output "EXPECTED_CORE_HEAD=$expectedCoreHead"
    Write-Output "DATABASE_CREDENTIALS=present (values withheld)"
    $suite = @(
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

    $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
    $checkCode = Invoke-PythonAndCapture -Label "Django system check" -LogPath $logPath `
        -Arguments @("manage.py", "check", "--settings=intevia.test_settings")
    if ($checkCode -ne 0) {
        Add-Failure "Django system check returned status $checkCode"
    }
    else {
        $testArguments = @(
            "manage.py", "test", "--settings=intevia.test_settings",
            "--testrunner=verification.isolation.runner.IsolationRunner",
            "--parallel", "1", "--noinput", "--verbosity=2"
        ) + $suite
        $testCode = Invoke-PythonAndCapture -Label "R1/R2/S1-S3/R3 plus TT-1 full suite" `
            -LogPath $logPath -Arguments $testArguments
        if ($testCode -ne 0) {
            Add-Failure "full suite returned status $testCode"
        }
    }
    $wholeRun.Stop()
    Write-Output ("WHOLE_RUN_SECONDS={0:F6}" -f $wholeRun.Elapsed.TotalSeconds)
    Write-Output "LOG_PATH=$logPath"
}
catch {
    Add-Failure (Protect-OutputText -Text $_.Exception.Message)
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
    Set-Location -LiteralPath $originalLocation.Path
}

if ($null -ne $runDirectory -and (Test-Path -LiteralPath $runDirectory -PathType Container)) {
    if (-not [string]::IsNullOrWhiteSpace($isolationPath) -and
        (Test-Path -LiteralPath $isolationPath -PathType Leaf)) {
        try {
            $isolationRecords = @(Get-Content -LiteralPath $isolationPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
            $cp0Records = @($isolationRecords | Where-Object { $_.checkpoint -eq "CP-0" })
            $summaryRecords = @($isolationRecords | Where-Object { $_.checkpoint -eq "SUMMARY" })
            if ($cp0Records.Count -eq 1) {
                $cp0Met = $cp0Records[0].requirements_met -eq $true
            }
            else {
                Add-Failure "isolation evidence must contain exactly one CP-0 record"
            }
            if ($summaryRecords.Count -eq 1) {
                $summary = $summaryRecords[0]
                $isolationVerdict = [string]$summary.isolation_verdict
                $ordinary = $summary.ordinary_test_verdict
                if ($isolationVerdict -ne "PASS") {
                    Add-Failure "isolation summary did not PASS"
                }
                if ($summary.cp0_requirements_met -ne $true -or -not $cp0Met) {
                    Add-Failure "CP-0 was not met"
                }
                if ($null -eq $ordinary -or $ordinary.was_successful -ne $true) {
                    Add-Failure "ordinary test summary did not report success"
                }
                else {
                    foreach ($key in @("tests_run", "failures", "errors", "skipped")) {
                        if ($null -eq $script:printedSummary[$key] -or
                            [long]$script:printedSummary[$key] -ne [long]$ordinary.$key) {
                            Add-Failure "printed $key count does not match the isolation summary"
                        }
                    }
                    if (-not $script:printedSummary.result_seen -or
                        $script:printedSummary.status -ne "PASS" -or $script:printedSummary.run_lines -ne 1) {
                        Add-Failure "printed test result summary is absent or unsuccessful"
                    }
                }
            }
            else {
                Add-Failure "isolation evidence must contain exactly one SUMMARY record"
            }
        }
        catch {
            Add-Failure ("isolation evidence could not be read: " + (Protect-OutputText -Text $_.Exception.Message))
        }
    }
    else {
        Add-Failure "isolation evidence file is absent"
    }

    if (-not [string]::IsNullOrWhiteSpace($receiptPath) -and
        (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        try {
            $receiptRecords = @(Get-Content -LiteralPath $receiptPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
            $created = @($receiptRecords | Where-Object {
                $_.event -eq "created" -and $_.name -eq $testDatabase -and $_.run_nonce -eq $runNonce
            })
            $destroyed = @($receiptRecords | Where-Object {
                $_.event -eq "destroyed" -and $_.name -eq $testDatabase -and
                $_.run_nonce -eq $runNonce -and $_.confirmed_absent -eq $true
            })
            $teardown.created = $created.Count
            $teardown.destroyed = $destroyed.Count
            if ($created.Count -eq 1) { $teardown.created_oid = $created[0].oid }
            if ($destroyed.Count -eq 1) { $teardown.destroyed_oid = $destroyed[0].oid }
            $teardown.database = $testDatabase
            $teardown.oids_match = $created.Count -eq 1 -and $destroyed.Count -eq 1 -and
                $created[0].oid -eq $destroyed[0].oid
            $teardown.confirmed_absent = $destroyed.Count -eq 1 -and $destroyed[0].confirmed_absent -eq $true
            if ($created.Count -ne 1 -or $destroyed.Count -ne 1 -or
                -not $teardown.oids_match -or -not $teardown.confirmed_absent) {
                Add-Failure "test database creation and confirmed teardown receipts do not match"
            }
            if ($script:teardownLines -ne 1) {
                Add-Failure "expected one streamed test database teardown line; observed $script:teardownLines"
            }
        }
        catch {
            Add-Failure ("database receipts could not be read: " + (Protect-OutputText -Text $_.Exception.Message))
        }
    }
    else {
        Add-Failure "database receipt file is absent"
    }

    if ($null -eq $ordinary) {
        $counts = [ordered]@{ tests_run = $null; failures = $null; errors = $null; skipped = $null }
    }
    else {
        $counts = [ordered]@{
            tests_run = $ordinary.tests_run
            failures = $ordinary.failures
            errors = $ordinary.errors
            skipped = $ordinary.skipped
        }
    }

    try {
        $finalBinding = Get-SourceBinding -IntegratedMerge $mergeCommit
        if ($null -eq $initialBinding -or $finalBinding.SourceKind -ne $initialBinding.SourceKind -or
            $finalBinding.Head -ne $initialBinding.Head -or
            $finalBinding.MergeCommit -ne $initialBinding.MergeCommit -or
            $finalBinding.Binding -ne $bindingHash) {
            Add-Failure "reviewed source binding changed during verification"
        }
    }
    catch {
        Add-Failure ("source binding could not be re-checked: " + (Protect-OutputText -Text $_.Exception.Message))
    }
    try {
        $finalScriptHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($finalScriptHash -ne $scriptHash) {
            Add-Failure "verifier script bytes changed during verification"
        }
    }
    catch {
        Add-Failure ("verifier script hash could not be re-checked: " + $_.Exception.Message)
    }

    $verdict = if ($failureReasons.Count -eq 0 -and $sourceKind -in @("CANDIDATE", "COMMITTED") -and
        $isolationVerdict -eq "PASS" -and $cp0Met -and $teardown.created -eq 1 -and
        $teardown.destroyed -eq 1 -and $teardown.oids_match -and $teardown.confirmed_absent) {
        "PASS"
    }
    else {
        "FAIL"
    }
    $verdictPath = Join-Path $runDirectory "VERDICT.json"
    $verdictDocument = [ordered]@{
        verdict = $verdict
        source_kind = $sourceKind
        head = $head
        merge_commit = $mergeCommit
        binding_sha256 = $bindingHash
        script_sha256 = $scriptHash
        tests_run = $counts.tests_run
        failures = $counts.failures
        errors = $counts.errors
        skipped = $counts.skipped
        isolation_verdict = $isolationVerdict
        cp0_met = $cp0Met
        teardown = $teardown
        failure_reasons = @($failureReasons)
    }
    try {
        $json = $verdictDocument | ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText($verdictPath, $json + "`n", [System.Text.UTF8Encoding]::new($false))
        Write-Output "VERDICT_FILE=$verdictPath"
    }
    catch {
        $verdict = "FAIL"
        Write-Output ("VERDICT_WRITE_FAILURE=" + (Protect-OutputText -Text $_.Exception.Message))
    }
}

Write-Output "ISOLATION_VERDICT=$isolationVerdict"
Write-Output "CP0_MET=$cp0Met"
Write-Output "TESTS_RUN=$($counts.tests_run)"
Write-Output "TEST_FAILURES=$($counts.failures)"
Write-Output "TEST_ERRORS=$($counts.errors)"
Write-Output "TEST_SKIPPED=$($counts.skipped)"
Write-Output "TEARDOWN_CREATED=$($teardown.created)"
Write-Output "TEARDOWN_DESTROYED=$($teardown.destroyed)"
Write-Output "TEARDOWN_OIDS_MATCH=$($teardown.oids_match)"
Write-Output "TEARDOWN_CONFIRMED_ABSENT=$($teardown.confirmed_absent)"
if ($failureReasons.Count -gt 0) {
    foreach ($reason in $failureReasons) {
        Write-Output "FAILURE=$reason"
    }
}
Write-Output "VERDICT: $verdict"
$global:LASTEXITCODE = if ($verdict -eq "PASS") { 0 } else { 1 }
