[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("fresh", "clone")][string]$Mode,
    [Parameter(Mandatory = $true)][ValidateRange(1, 99)][int]$AttemptNumber,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string[]]$Labels,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$StateFile,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9a-fA-F]{64}$")][string]$StateSha256,
    [switch]$CleanupTemplate,
    [switch]$FullSuiteParity
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "integrated_state.ps1")
$originalLocation = Get-Location
$fullSuiteParityLabels = @(
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

$failure = $null
$runProblems = [System.Collections.Generic.List[string]]::new()
$setupSeconds = $null
$templateBuildSeconds = $null
$wholeRun = $null
$script:teardownLines = 0

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

function Invoke-PythonAndLog {
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
        if ($lineText -match "^TT1_(?:FRESH|CLONE)_SETUP_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $script:setupSeconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
        }
        elseif ($lineText -match "^TT1_TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
            $script:templateBuildSeconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
        }
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

    if ($FullSuiteParity) {
        if ($Mode -ne "clone" -or -not $CleanupTemplate) {
            throw "full-suite parity mode requires clone mode with template cleanup"
        }
        if ($Labels.Count -ne $fullSuiteParityLabels.Count -or
            @($Labels | Where-Object { $_ -cnotin $fullSuiteParityLabels }).Count -gt 0 -or
            @($fullSuiteParityLabels | Where-Object { $_ -cnotin $Labels }).Count -gt 0) {
            throw "full-suite parity mode requires exactly the commissioned 23 module labels"
        }
    }
    else {
        foreach ($label in $Labels) {
            if ($label -notmatch "^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){2,3}$") {
                throw "test label must be module.Class or module.Class.method"
            }
        }
    }
    if (@($Labels | Sort-Object -Unique).Count -ne $Labels.Count) {
        throw "duplicate test labels are refused"
    }
    if ($CleanupTemplate -and $Mode -ne "clone") {
        throw "template cleanup is only valid for a clone-mode run"
    }

    $authorizedState = Get-IntegratedState -StateFile $StateFile -StateSha256 $StateSha256
    $routes = Get-IntegratedEvidenceRoutes -AuthorizedState $authorizedState -HomeDirectory $HOME
    Write-Output "STATE_FILE=$($authorizedState.StateFile)"
    Write-Output "STATE_ID=$($authorizedState.StateId)"
    Write-Output "STATE_SHA256=$($authorizedState.StateSha256)"
    Write-Output "SOURCE_KIND=$($authorizedState.SourceKind)"
    Write-Output "HEAD=$($authorizedState.Head)"
    Write-Output "EVIDENCE_ROOT=$($authorizedState.EvidenceRoot)"
    Write-Output "DATABASE_TAG=$($authorizedState.DatabaseTag)"
    Write-Output "ME_DEV_ROOT=$($routes.DevelopmentRoot)"
    Write-Output "ME_DATABASE=$($routes.DevelopmentDatabasePrefix)$AttemptNumber"

    $missingCredentials = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @(
        "INTEVIA_DATABASE_ENGINE",
        "INTEVIA_POSTGRES_DB",
        "INTEVIA_POSTGRES_USER",
        "INTEVIA_POSTGRES_PASSWORD",
        "DJANGO_SECRET_KEY"
    )) {
        $present = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))
        Write-Output ("CREDENTIAL_{0}={1}" -f $name, $(if ($present) { "set" } else { "UNSET" }))
        if (-not $present) {
            $missingCredentials.Add($name)
        }
    }
    if ($missingCredentials.Count -gt 0) {
        throw ("Required credentials are UNSET; no attempt folder was created: " + ($missingCredentials -join ","))
    }
    if ($env:INTEVIA_POSTGRES_DB -in @("intevia", "intevia_dev")) {
        throw "protected administrative database is refused"
    }

    $stageRoot = $routes.DevelopmentRoot
    $runDirectory = Join-Path $stageRoot ("attempt_{0}" -f $AttemptNumber)
    if (Test-Path -LiteralPath $runDirectory) {
        throw "development attempt directory already exists; refusing reuse"
    }
    $attemptDatabase = "$($routes.DevelopmentDatabasePrefix)$AttemptNumber"
    if ($attemptDatabase.Length -gt 63 -or $attemptDatabase -in @("intevia", "intevia_dev", $env:INTEVIA_POSTGRES_DB)) {
        throw "protected or overlength test database name refused"
    }

    $templateStore = Join-Path $stageRoot "integrated-template-store"

    New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
    $runNonce = [guid]::NewGuid().ToString("N")
    $receiptPath = Join-Path $runDirectory "database-receipts.jsonl"
    $isolationPath = Join-Path $runDirectory "isolation.jsonl"
    $logPath = Join-Path $runDirectory "integrated-development.log"
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
    $env:VERIFICATION_DB_RECEIPT = $receiptPath
    $env:VERIFICATION_ISOLATION_JSON = $isolationPath
    $env:VERIFICATION_EXPECTED_CORE_HEAD = "0041_r4s2_assign_current_membership"
    $env:VERIFICATION_STEP = "S015"
    $env:VERIFICATION_TEMPLATE_MODE = $Mode
    $env:VERIFICATION_TEMPLATE_STORE = $effectiveTemplateStore
    $env:VERIFICATION_TEMPLATE_CLEANUP = if ($CleanupTemplate) { "1" } else { "0" }
    $env:DJANGO_SETTINGS_MODULE = "intevia.test_settings"
    $env:PYTHONUNBUFFERED = "1"
    $testPythonPath = @($repoRoot, (Join-Path $repoRoot "tests"))
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $testPythonPath += $env:PYTHONPATH
    }
    $env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $testPythonPath)

    Write-Output "MODE=$Mode"
    Write-Output "ATTEMPT=$AttemptNumber"
    Write-Output "DATABASE=$attemptDatabase"
    Write-Output "RUN_DIRECTORY=$runDirectory"
    Write-Output "TEMPLATE_STORE=$effectiveTemplateStore"
    Write-Output "TEMPLATE_CLEANUP=$($env:VERIFICATION_TEMPLATE_CLEANUP)"
    Write-Output "LABELS=$($Labels -join ',')"

    $wholeRun = [System.Diagnostics.Stopwatch]::StartNew()
    $checkCode = Invoke-PythonAndLog -Label "Django system check" -LogPath $logPath `
        -Arguments @("manage.py", "check", "--settings=intevia.test_settings")
    if ($checkCode -ne 0) {
        $runProblems.Add("Django system check returned status $checkCode")
    }
    else {
        $testArguments = @(
            "manage.py", "test", "--settings=intevia.test_settings",
            "--testrunner=verification.isolation.runner.IsolationRunner",
            "--parallel", "1", "--noinput", "--verbosity=2"
        ) + $Labels
        $testCode = Invoke-PythonAndLog -Label "Explicit-label development run" `
            -LogPath $logPath -Arguments $testArguments
        if ($testCode -ne 0) {
            $runProblems.Add("development test batch returned status $testCode")
        }
    }
    $wholeRun.Stop()

    if ($null -eq $setupSeconds) {
        $runProblems.Add("isolation runner did not report setup time")
    }
    if ($Mode -eq "clone" -and $null -eq $templateBuildSeconds) {
        $runProblems.Add("isolation runner did not report template build time")
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
                Write-Output "TEST_FAILURES=$($counts.failures)"
                Write-Output "TEST_ERRORS=$($counts.errors)"
                Write-Output "TEST_SKIPPED=$($counts.skipped)"
                if ($Mode -eq "clone" -and $cp0Records.Count -eq 1 -and
                    ($cp0Records[0].template_identity -eq $null -or
                     $cp0Records[0].template_cp0_digest -ne $cp0Records[0].clone_cp0_digest)) {
                    $runProblems.Add("clone CP-0 did not match the integrated template digest")
                }
            }
        }
        catch {
            $runProblems.Add("isolation evidence could not be read: $($_.Exception.Message)")
        }
    }
    else { $runProblems.Add("isolation evidence file is absent") }

    if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
        try {
            $receipts = @(Get-Content -LiteralPath $receiptPath |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
            $created = @($receipts | Where-Object {
                $_.event -eq "created" -and $_.name -eq $attemptDatabase -and $_.run_nonce -eq $runNonce
            })
            $destroyed = @($receipts | Where-Object {
                $_.event -eq "destroyed" -and $_.name -eq $attemptDatabase -and
                $_.run_nonce -eq $runNonce -and $_.confirmed_absent -eq $true
            })
            $oidsMatch = $false
            $confirmedAbsent = $false
            if ($created.Count -eq 1 -and $destroyed.Count -eq 1) {
                $oidsMatch = $created[0].oid -eq $destroyed[0].oid
                $confirmedAbsent = $destroyed[0].confirmed_absent -eq $true
            }
            Write-Output "DATABASE_CREATED_COUNT=$($created.Count)"
            Write-Output "DATABASE_DESTROYED_COUNT=$($destroyed.Count)"
            Write-Output "DATABASE_OIDS_MATCH=$oidsMatch"
            Write-Output "DATABASE_CONFIRMED_ABSENT=$confirmedAbsent"
            if ($created.Count -ne 1 -or $destroyed.Count -ne 1 -or
                -not $oidsMatch -or -not $confirmedAbsent) {
                $runProblems.Add("test database creation and confirmed teardown receipts do not match")
            }
            if ($script:teardownLines -ne 1) {
                $runProblems.Add("expected one streamed test database teardown line; observed $script:teardownLines")
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
            $runProblems.Add("database receipts could not be read: $($_.Exception.Message)")
        }
    }
    else { $runProblems.Add("database receipt file is absent") }

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

if ($null -eq $setupSeconds) {
    Write-Output "SETUP_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("SETUP_SECONDS={0:F6}" -f $setupSeconds)
}
if ($Mode -eq "fresh") {
    Write-Output "TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE"
}
elseif ($null -eq $templateBuildSeconds) {
    Write-Output "TEMPLATE_BUILD_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("TEMPLATE_BUILD_SECONDS={0:F6}" -f $templateBuildSeconds)
}
if ($null -eq $wholeRun) {
    Write-Output "WHOLE_RUN_SECONDS=UNAVAILABLE"
}
else {
    Write-Output ("WHOLE_RUN_SECONDS={0:F6}" -f $wholeRun.Elapsed.TotalSeconds)
}
if ($null -eq $failure) {
    foreach ($problem in $runProblems) {
        Write-Output "FAILURE=$problem"
    }
    if ($runProblems.Count -eq 0) {
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
