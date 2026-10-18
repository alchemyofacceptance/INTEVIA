$script:lockTimeoutMilliseconds = 120000

function Write-IntegratedGuardSummary {
    param(
        [Parameter(Mandatory = $true)][switch]$FailFast,
        [Parameter(Mandatory = $true)][ValidateRange(1, 60)][int]$StallMinutes
    )

    Write-Output "FAILFAST=$(if ($FailFast) { 'ON' } else { 'OFF' })"
    Write-Output "LOCK_TIMEOUT_MS=$script:lockTimeoutMilliseconds"
    Write-Output "STALL_GUARD_MINUTES=$StallMinutes"
}

function Write-WatchdogLogLine {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$LineText,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    $LineText = Protect-OutputText -Text $LineText
    Add-Content -LiteralPath $LogPath -Value $LineText -Encoding utf8
    Write-Host $LineText
    if ($LineText -match "^TT1_(?:FRESH|CLONE)_SETUP_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
        $script:setupSeconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
    }
    elseif ($LineText -match "^TT1_TEMPLATE_BUILD_SECONDS=([0-9]+(?:\.[0-9]+)?)$") {
        $script:templateBuildSeconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($LineText -match "^Found ([0-9]+) test\(s\)\.?$") {
        $script:discoveredTestCount = [int]$Matches[1]
    }
    if ($LineText -match "Destroying test database for alias") {
        $script:teardownLines++
    }
    if ($LineText -match "^test_.*\(tests\.") {
        $script:testStartsBeforeStall++
        $script:lastTestLine = $LineText
    }
    if ($LineText -match "^test_.*\(tests\..*\)\s+\.\.\.\s+ok(?:\s|$)") {
        $script:testPassesBeforeStall++
    }
}

function Invoke-RunnerDatabasePython {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("snapshot", "drop", "check-self-check", "create-self-check")]
        [string]$Action,
        [Parameter(Mandatory = $true)][string]$DatabaseName,
        [Parameter(Mandatory = $true)][string]$DatabaseOwnershipRoot,
        [Parameter(Mandatory = $true)][string]$Numbering,
        [long]$ExpectedOid = 0
    )

    $pythonCode = @'
import json
import re
import sys

import django

try:
    action, database_name, expected_oid_text, allowed_prefix, expected_suffix = sys.argv[1:6]
    if not database_name.startswith(allowed_prefix):
        raise ValueError("database name is outside the attempt-owned prefix")
    suffix = database_name[len(allowed_prefix):]
    if suffix != expected_suffix:
        raise ValueError("database name does not match the authorized run numbering")

    django.setup()
    from django.db import connections

    connection = connections["default"]
    maintenance_database = connection.settings_dict["NAME"]
    if database_name in (maintenance_database, "intevia", "intevia_dev"):
        raise ValueError("protected database name refused")
    expected_oid = int(expected_oid_text)
    quote_name = connection.ops.quote_name

    if action == "snapshot":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pid, state, wait_event_type, wait_event, "
                "pg_blocking_pids(pid), "
                "CASE WHEN query_start IS NULL THEN NULL "
                "ELSE clock_timestamp() - query_start END, query_start "
                "FROM pg_stat_activity WHERE datname = %s ORDER BY pid",
                [database_name],
            )
            for row in cursor.fetchall():
                print(json.dumps({
                    "pid": row[0],
                    "state": row[1],
                    "wait_event_type": row[2],
                    "wait_event": row[3],
                    "pg_blocking_pids": row[4],
                    "running_time": str(row[5]) if row[5] is not None else None,
                    "query_start": row[6].isoformat() if row[6] is not None else None,
                }, separators=(",", ":")))
    elif action == "check-self-check":
        with connection.cursor() as cursor:
            cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", [database_name])
            print("SELF_CHECK_DATABASE_EXISTS=" + str(cursor.fetchone() is not None))
    elif action == "create-self-check":
        with connection.cursor() as cursor:
            cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", [database_name])
            if cursor.fetchone() is not None:
                raise ValueError("self-check database name is already occupied")
            cursor.execute("CREATE DATABASE " + quote_name(database_name))
            cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", [database_name])
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("self-check database creation was not confirmed")
            print("CREATED_OID=" + str(row[0]))
    elif action == "drop":
        if expected_oid <= 0:
            raise ValueError("database ownership OID is required")
        with connection.cursor() as cursor:
            cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", [database_name])
            row = cursor.fetchone()
            if row is None or int(row[0]) != expected_oid:
                raise ValueError("current database OID does not match attempt ownership")
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                [database_name],
            )
            cursor.fetchall()
            cursor.execute("DROP DATABASE " + quote_name(database_name))
            cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", [database_name])
            if cursor.fetchone() is not None:
                raise RuntimeError("database absence was not confirmed after drop")
            print("DROP_OID=" + str(expected_oid))
            print("DROP_CONFIRMED_ABSENT=True")
    else:
        raise ValueError("unsupported watchdog database operation")
except Exception as error:
    print("DATABASE_HELPER_ERROR=" + type(error).__name__, file=sys.stderr)
    raise SystemExit(1)
'@
    $python = Get-Command python -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $python.Source
    $startInfo.WorkingDirectory = (Get-Location).Path
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.Environment["DJANGO_SETTINGS_MODULE"] = "intevia.settings"
    foreach ($argument in @(
        "-c",
        $pythonCode,
        $Action,
        $DatabaseName,
        [string]$ExpectedOid,
        $DatabaseOwnershipRoot,
        $Numbering
    )) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "runner database helper process could not be started"
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $errorType = [regex]::Match(
                $stderr,
                "DATABASE_HELPER_ERROR=([A-Za-z_][A-Za-z0-9_]*)"
            ).Groups[1].Value
            if ([string]::IsNullOrWhiteSpace($errorType)) {
                $errorType = "unclassified"
            }
            throw "runner database helper $Action failed with status $($process.ExitCode) ($errorType)"
        }
        return @($stdout -split "\r?\n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    finally {
        $process.Dispose()
    }
}

function Get-OwnedDatabaseOid {
    param(
        [Parameter(Mandatory = $true)][string]$DatabaseName,
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$RunNonce
    )

    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        throw "database creation receipt is absent at watchdog teardown"
    }
    $records = @(Get-Content -LiteralPath $ReceiptPath -ErrorAction Stop |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
    $created = @($records | Where-Object {
        $_.event -eq "created" -and $_.run_nonce -ceq $RunNonce -and
        $_.name -ceq $DatabaseName
    })
    if ($created.Count -ne 1 -or $created[0].oid -notmatch "^[0-9]+$") {
        throw "watchdog refused to drop a database without one matching creation receipt"
    }
    return [long]$created[0].oid
}

function Remove-WatchdogDatabase {
    param(
        [Parameter(Mandatory = $true)][string]$DatabaseName,
        [Parameter(Mandatory = $true)][long]$ExpectedOid,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$DatabaseOwnershipRoot,
        [Parameter(Mandatory = $true)][string]$Numbering,
        [string]$ReceiptPath,
        [string]$RunNonce,
        [switch]$SelfCheck
    )

    $prefix = $DatabaseOwnershipRoot
    if (-not $DatabaseName.StartsWith($prefix, [StringComparison]::Ordinal)) {
        throw "watchdog refused a database outside the attempt-owned prefix"
    }
    $suffix = $DatabaseName.Substring($prefix.Length)
    if ($suffix -cne $Numbering) {
        throw "watchdog refused a database outside the authorized run numbering"
    }
    if ($DatabaseName -in @("intevia", "intevia_dev", $env:INTEVIA_POSTGRES_DB)) {
        throw "watchdog refused a protected database name"
    }
    $dropLines = @(Invoke-RunnerDatabasePython `
        -Action "drop" `
        -DatabaseName $DatabaseName `
        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
        -Numbering $Numbering `
        -ExpectedOid $ExpectedOid)
    if ("DROP_OID=$ExpectedOid" -notin $dropLines -or
        "DROP_CONFIRMED_ABSENT=True" -notin $dropLines) {
        throw "watchdog database teardown did not confirm absence"
    }

    if (-not $SelfCheck) {
        if ([string]::IsNullOrWhiteSpace($ReceiptPath) -or
            [string]::IsNullOrWhiteSpace($RunNonce)) {
            throw "watchdog teardown lacks its run ownership context"
        }
        $record = [ordered]@{
            event = "destroyed"
            run_nonce = $RunNonce
            at = [DateTime]::UtcNow.ToString("o", [Globalization.CultureInfo]::InvariantCulture)
            pid = $PID
            name = $DatabaseName
            oid = $ExpectedOid
            confirmed_absent = $true
        }
        Add-Content -LiteralPath $ReceiptPath -Value (
            $record | ConvertTo-Json -Compress
        ) -Encoding utf8
        Write-WatchdogLogLine `
            -LineText "Destroying test database for alias 'default' ('$DatabaseName')..." `
            -LogPath $LogPath
    }
    foreach ($line in $dropLines) {
        Write-WatchdogLogLine -LineText $line -LogPath $LogPath
    }
    Write-WatchdogLogLine -LineText "WATCHDOG_DATABASE_DROPPED=$DatabaseName" -LogPath $LogPath
    Write-WatchdogLogLine -LineText "WATCHDOG_DATABASE_CONFIRMED_ABSENT=True" -LogPath $LogPath
    return $true
}

function Write-StallActivitySnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$DatabaseName,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$DatabaseOwnershipRoot,
        [Parameter(Mandatory = $true)][string]$Numbering
    )

    Write-WatchdogLogLine -LineText "STALL_PG_STAT_ACTIVITY_BEGIN" -LogPath $LogPath
    foreach ($line in Invoke-RunnerDatabasePython `
        -Action "snapshot" `
        -DatabaseName $DatabaseName `
        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
        -Numbering $Numbering) {
        Write-WatchdogLogLine -LineText $line -LogPath $LogPath
    }
    Write-WatchdogLogLine -LineText "STALL_PG_STAT_ACTIVITY_END" -LogPath $LogPath
}

function Update-WatchdogInactivity {
    param(
        [Parameter(Mandatory = $true)][datetime]$LastSeenWrite,
        [Parameter(Mandatory = $true)][datetime]$CurrentWrite,
        [Parameter(Mandatory = $true)][timespan]$LastChangeElapsed,
        [Parameter(Mandatory = $true)][timespan]$NowElapsed,
        [Parameter(Mandatory = $true)][timespan]$Threshold
    )

    if ($CurrentWrite -ne $LastSeenWrite) {
        return [PSCustomObject]@{
            LastSeenWrite = $CurrentWrite
            LastChangeElapsed = $NowElapsed
            IsStalled = $false
        }
    }
    return [PSCustomObject]@{
        LastSeenWrite = $LastSeenWrite
        LastChangeElapsed = $LastChangeElapsed
        IsStalled = ($NowElapsed - $LastChangeElapsed) -ge $Threshold
    }
}

function Invoke-WatchedPythonAndLog {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$RunRoot,
        [Parameter(Mandatory = $true)][string]$DatabaseOwnershipRoot,
        [Parameter(Mandatory = $true)][string]$Numbering,
        [Parameter(Mandatory = $true)][int]$StallThresholdMinutes,
        [Parameter(Mandatory = $true)][string]$DatabaseName,
        [long]$DatabaseOid,
        [string]$ReceiptPath,
        [string]$RunNonce,
        [switch]$SelfCheck
    )

    Write-Host "COMMAND=$Label"
    Add-Content -LiteralPath $LogPath -Value "COMMAND=$Label" -Encoding utf8
    $script:testStartsBeforeStall = 0
    $script:testPassesBeforeStall = 0
    $script:lastTestLine = "UNAVAILABLE"

    $python = Get-Command python -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $python.Source
    $startInfo.WorkingDirectory = (Get-Location).Path
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }
    $existingPgOptions = $startInfo.Environment["PGOPTIONS"]
    $lockTimeoutOption = "-c lock_timeout=$($script:lockTimeoutMilliseconds / 1000)s"
    $startInfo.Environment["PGOPTIONS"] = if (
        [string]::IsNullOrWhiteSpace($existingPgOptions)
    ) {
        $lockTimeoutOption
    }
    else {
        "$existingPgOptions $lockTimeoutOption"
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "test process could not be started"
    }
    $stdoutTask = $process.StandardOutput.ReadLineAsync()
    $stderrTask = $process.StandardError.ReadLineAsync()
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastLogWrite = (Get-Item -LiteralPath $LogPath -ErrorAction Stop).LastWriteTimeUtc
    $lastChangeElapsed = $watch.Elapsed
    $stalled = $false
    $processEnded = $false
    $databaseAbsent = $false
    $processFailures = [System.Collections.Generic.List[string]]::new()
    $stallDetectedSeconds = $null

    while ($null -ne $stdoutTask -or $null -ne $stderrTask -or -not $process.HasExited) {
        if ($null -ne $stdoutTask -and $stdoutTask.IsCompleted) {
            $line = $stdoutTask.GetAwaiter().GetResult()
            if ($null -eq $line) {
                $stdoutTask = $null
            }
            else {
                Write-WatchdogLogLine -LineText $line -LogPath $LogPath
                $stdoutTask = $process.StandardOutput.ReadLineAsync()
            }
        }
        if ($null -ne $stderrTask -and $stderrTask.IsCompleted) {
            $line = $stderrTask.GetAwaiter().GetResult()
            if ($null -eq $line) {
                $stderrTask = $null
            }
            else {
                Write-WatchdogLogLine -LineText $line -LogPath $LogPath
                $stderrTask = $process.StandardError.ReadLineAsync()
            }
        }

        if (-not $process.HasExited) {
            $currentLogWrite = (Get-Item -LiteralPath $LogPath -ErrorAction Stop).LastWriteTimeUtc
            $inactivity = Update-WatchdogInactivity `
                -LastSeenWrite $lastLogWrite `
                -CurrentWrite $currentLogWrite `
                -LastChangeElapsed $lastChangeElapsed `
                -NowElapsed $watch.Elapsed `
                -Threshold ([TimeSpan]::FromMinutes($StallThresholdMinutes))
            $lastLogWrite = $inactivity.LastSeenWrite
            $lastChangeElapsed = $inactivity.LastChangeElapsed
            if ($inactivity.IsStalled) {
                $stalled = $true
                $script:watchdogStalled = $true
                $stallDetectedSeconds = $watch.Elapsed.TotalSeconds
                Write-WatchdogLogLine -LineText "STALL_LAST_TEST=$($script:lastTestLine)" -LogPath $LogPath
                Write-WatchdogLogLine -LineText (
                    "STALL_TESTS_STARTED=$($script:testStartsBeforeStall)"
                ) -LogPath $LogPath
                Write-WatchdogLogLine -LineText (
                    "STALL_TESTS_PASSED=$($script:testPassesBeforeStall)"
                ) -LogPath $LogPath
                if ($null -ne $script:discoveredTestCount) {
                    $notReached = [Math]::Max(
                        0,
                        $script:discoveredTestCount - $script:testStartsBeforeStall
                    )
                    Write-WatchdogLogLine -LineText (
                        "STALL_TESTS_NOT_REACHED=$notReached"
                    ) -LogPath $LogPath
                }
                Write-WatchdogLogLine -LineText (
                    "STALL_THRESHOLD_MINUTES=$StallThresholdMinutes"
                ) -LogPath $LogPath

                try {
                    Write-StallActivitySnapshot `
                        -DatabaseName $DatabaseName `
                        -LogPath $LogPath `
                        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
                        -Numbering $Numbering
                }
                catch {
                    $processFailures.Add("activity snapshot failed: $(
                        Protect-OutputText -Text $_.Exception.Message
                    )")
                }
                try {
                    if (-not $process.HasExited) {
                        try {
                            $process.Kill($true)
                        }
                        catch {
                            if (-not $process.HasExited) {
                                throw
                            }
                        }
                    }
                    $process.WaitForExit()
                    $processEnded = $process.HasExited
                    Write-WatchdogLogLine -LineText (
                        "WATCHDOG_TEST_PROCESS_TREE_ENDED=$processEnded"
                    ) -LogPath $LogPath
                    while ($processEnded -and
                        ($null -ne $stdoutTask -or $null -ne $stderrTask)) {
                        if ($null -ne $stdoutTask -and $stdoutTask.IsCompleted) {
                            $line = $stdoutTask.GetAwaiter().GetResult()
                            if ($null -eq $line) {
                                $stdoutTask = $null
                            }
                            else {
                                Write-WatchdogLogLine -LineText $line -LogPath $LogPath
                                $stdoutTask = $process.StandardOutput.ReadLineAsync()
                            }
                        }
                        if ($null -ne $stderrTask -and $stderrTask.IsCompleted) {
                            $line = $stderrTask.GetAwaiter().GetResult()
                            if ($null -eq $line) {
                                $stderrTask = $null
                            }
                            else {
                                Write-WatchdogLogLine -LineText $line -LogPath $LogPath
                                $stderrTask = $process.StandardError.ReadLineAsync()
                            }
                        }
                        Start-Sleep -Milliseconds 100
                    }
                }
                catch {
                    $processFailures.Add("test process termination failed: $(
                        Protect-OutputText -Text $_.Exception.Message
                    )")
                }
                try {
                    if ($SelfCheck) {
                        $ownedOid = $DatabaseOid
                    }
                    else {
                        $ownedOid = Get-OwnedDatabaseOid `
                            -DatabaseName $DatabaseName `
                            -ReceiptPath $ReceiptPath `
                            -RunNonce $RunNonce
                    }
                    $databaseAbsent = Remove-WatchdogDatabase `
                        -DatabaseName $DatabaseName `
                        -ExpectedOid $ownedOid `
                        -LogPath $LogPath `
                        -DatabaseOwnershipRoot $DatabaseOwnershipRoot `
                        -Numbering $Numbering `
                        -ReceiptPath $ReceiptPath `
                        -RunNonce $RunNonce `
                        -SelfCheck:$SelfCheck
                }
                catch {
                    $processFailures.Add("attempt database teardown failed: $(
                        Protect-OutputText -Text $_.Exception.Message
                    )")
                }
                foreach ($processFailure in $processFailures) {
                    Write-WatchdogLogLine `
                        -LineText "WATCHDOG_FAILURE=$processFailure" `
                        -LogPath $LogPath
                    $runProblems.Add($processFailure)
                }
                $watch.Stop()
                $process.Dispose()
                return [PSCustomObject]@{
                    ExitCode = 124
                    Stalled = $stalled
                    ProcessEnded = $processEnded
                    DatabaseAbsent = $databaseAbsent
                    StallDetectedSeconds = $stallDetectedSeconds
                    ElapsedSeconds = $watch.Elapsed.TotalSeconds
                    Failures = @($processFailures.ToArray())
                }
            }
        }
        Start-Sleep -Milliseconds 100
    }

    $process.WaitForExit()
    $status = [int]$process.ExitCode
    $process.Dispose()
    $watch.Stop()
    return [PSCustomObject]@{
        ExitCode = $status
        Stalled = $false
        ProcessEnded = $true
        DatabaseAbsent = $false
        StallDetectedSeconds = $null
        ElapsedSeconds = $watch.Elapsed.TotalSeconds
        Failures = @()
    }
}
