[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$helperPath = Join-Path $PSScriptRoot "..\scripts\integrated_state.ps1"
. $helperPath
$originalLocation = Get-Location
$temporaryRoot = Join-Path $env:TEMP ("int2-integrated-state-" + [guid]::NewGuid().ToString("N"))
$results = [System.Collections.Generic.List[string]]::new()
$failure = $null

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = @(& git -C $Repository @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw ("git {0} failed: {1}" -f ($Arguments -join " "), ($output -join " "))
    }
    return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function New-ScratchSeed {
    param([Parameter(Mandatory = $true)][string]$Repository)

    [System.IO.Directory]::CreateDirectory($Repository) | Out-Null
    [void](Invoke-Git -Repository $Repository -Arguments @("init", "--quiet"))
    [void](Invoke-Git -Repository $Repository -Arguments @("config", "user.name", "INT-2 State Test"))
    [void](Invoke-Git -Repository $Repository -Arguments @("config", "user.email", "int2-state@example.invalid"))
    [System.IO.File]::WriteAllText((Join-Path $Repository "tracked.txt"), "seed`n", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-Git -Repository $Repository -Arguments @("add", "tracked.txt"))
    [void](Invoke-Git -Repository $Repository -Arguments @("commit", "--quiet", "-m", "seed"))
    $common = Invoke-Git -Repository $Repository -Arguments @("rev-parse", "HEAD")
    $tree = Invoke-Git -Repository $Repository -Arguments @("rev-parse", "$common^{tree}")
    return [PSCustomObject]@{ Common = $common; Tree = $tree }
}

function New-ScratchLine {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [string]$Branch = "pktb/int-r3-tt1"
    )

    $seed = New-ScratchSeed -Repository $Repository
    $parentOne = Invoke-Git -Repository $Repository -Arguments @(
        "commit-tree", $seed.Tree, "-p", $seed.Common, "-m", "integration parent one"
    )
    $parentTwo = Invoke-Git -Repository $Repository -Arguments @(
        "commit-tree", $seed.Tree, "-p", $seed.Common, "-m", "integration parent two"
    )
    $merge = Invoke-Git -Repository $Repository -Arguments @(
        "commit-tree", $seed.Tree, "-p", $parentOne, "-p", $parentTwo, "-m", "integration merge"
    )
    $integratedRoot = Invoke-Git -Repository $Repository -Arguments @(
        "commit-tree", $seed.Tree, "-p", $merge, "-m", "integrated root"
    )
    [void](Invoke-Git -Repository $Repository -Arguments @("checkout", "--quiet", "-b", $Branch, $integratedRoot))
    return [PSCustomObject]@{
        Common = $seed.Common
        Tree = $seed.Tree
        ParentOne = $parentOne
        ParentTwo = $parentTwo
        Merge = $merge
        IntegratedRoot = $integratedRoot
        Branch = $Branch
    }
}

function Set-CandidateChange {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [string]$Path = "allowed.txt"
    )

    [System.IO.File]::WriteAllText((Join-Path $Repository $Path), "candidate`n", [System.Text.UTF8Encoding]::new($false))
}

function Add-AllowedCommit {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [string]$Path = "allowed.txt",
        [string]$Message = "authorized change"
    )

    Set-CandidateChange -Repository $Repository -Path $Path
    [void](Invoke-Git -Repository $Repository -Arguments @("add", $Path))
    [void](Invoke-Git -Repository $Repository -Arguments @("commit", "--quiet", "-m", $Message))
    return (Invoke-Git -Repository $Repository -Arguments @("rev-parse", "HEAD"))
}

function New-StateDocument {
    param(
        [Parameter(Mandatory = $true)]$Line,
        [Parameter(Mandatory = $true)][string]$Branch,
        [Parameter(Mandatory = $true)][string]$BaseCommit,
        [string[]]$AllowedPaths = @("allowed.txt"),
        [bool]$CommittedChildAllowed = $true,
        [string]$EvidenceRoot = "PKTB_TEST",
        [string]$DatabaseTag = "test"
    )

    return [ordered]@{
        state_id = "INT2_SCRATCH_TEST"
        branch = $Branch
        evidence_root = $EvidenceRoot
        database_tag = $DatabaseTag
        base_commit = $BaseCommit
        integrated_root = $Line.IntegratedRoot
        merge = [ordered]@{
            commit = $Line.Merge
            parents = @($Line.ParentOne, $Line.ParentTwo)
        }
        allowed_paths = $AllowedPaths
        committed_child_allowed = $CommittedChildAllowed
    }
}

function Write-StateFile {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$State
    )

    $path = Join-Path $temporaryRoot "$Name-state.json"
    $json = $State | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($path, $json + "`n", [System.Text.UTF8Encoding]::new($false))
    return [PSCustomObject]@{
        Path = $path
        Hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Assert-PassCase {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$ExpectedSource
    )

    $stateFile = Write-StateFile -Name $Name -State $State
    Push-Location -LiteralPath $Repository
    try {
        $actual = Get-IntegratedState -StateFile $stateFile.Path -StateSha256 $stateFile.Hash
        if ($actual.SourceKind -cne $ExpectedSource) {
            throw "$Name expected $ExpectedSource but received $($actual.SourceKind)"
        }
        $results.Add("$Name=PASS ($($actual.SourceKind) accepted)")
    }
    finally {
        Pop-Location
    }
}

function Assert-RefuseCase {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$ExpectedReason,
        [string]$SuppliedHash = ""
    )

    $stateFile = Write-StateFile -Name $Name -State $State
    $hashToUse = if ([string]::IsNullOrEmpty($SuppliedHash)) { $stateFile.Hash } else { $SuppliedHash }
    Push-Location -LiteralPath $Repository
    try {
        $refusal = $null
        try {
            [void](Get-IntegratedState -StateFile $stateFile.Path -StateSha256 $hashToUse)
        }
        catch {
            $refusal = $_.Exception.Message
        }
        if ([string]::IsNullOrWhiteSpace($refusal)) {
            throw "$Name unexpectedly accepted the source"
        }
        if ($refusal -notmatch $ExpectedReason) {
            throw "$Name refused for an unexpected reason: $refusal"
        }
        $results.Add("$Name=REFUSE ($refusal)")
    }
    finally {
        Pop-Location
    }
}

function Get-CommitPrefixCollision {
    param(
        [Parameter(Mandatory = $true)][string]$Tree,
        [Parameter(Mandatory = $true)][string]$Parent
    )

    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    $seen = @{}
    try {
        for ($index = 0; $index -lt 400000; $index++) {
            $body = "tree $Tree`nparent $Parent`nauthor INT-2 Test <int2-state@example.invalid> 0 +0000`ncommitter INT-2 Test <int2-state@example.invalid> 0 +0000`n`ncollision-$index`n"
            $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
            $objectBytes = [System.Text.Encoding]::UTF8.GetBytes(("commit {0}`0{1}" -f $bodyBytes.Length, $body))
            $hash = -join ($sha1.ComputeHash($objectBytes) | ForEach-Object { $_.ToString("x2") })
            $prefix = $hash.Substring(0, 8)
            if ($seen.ContainsKey($prefix)) {
                $prior = $seen[$prefix]
                if ($prior.Hash -cne $hash) {
                    return [PSCustomObject]@{
                        FirstHash = $prior.Hash
                        FirstBody = $prior.Body
                        SecondHash = $hash
                        SecondBody = $body
                    }
                }
            }
            else {
                $seen[$prefix] = [PSCustomObject]@{ Hash = $hash; Body = $body }
            }
        }
    }
    finally {
        $sha1.Dispose()
    }
    throw "could not construct the required eight-character SHA prefix collision"
}

try {
    [System.IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null

    $repoA = Join-Path $temporaryRoot "case-a"
    $lineA = New-ScratchLine -Repository $repoA
    Set-CandidateChange -Repository $repoA
    Assert-PassCase -Name "CASE_a_INT2_CANDIDATE" -Repository $repoA `
        -State (New-StateDocument -Line $lineA -Branch $lineA.Branch -BaseCommit $lineA.IntegratedRoot) `
        -ExpectedSource "CANDIDATE"

    $repoB = Join-Path $temporaryRoot "case-b"
    $lineB = New-ScratchLine -Repository $repoB
    [void](Add-AllowedCommit -Repository $repoB)
    Assert-PassCase -Name "CASE_b_INT2_COMMITTED" -Repository $repoB `
        -State (New-StateDocument -Line $lineB -Branch $lineB.Branch -BaseCommit $lineB.IntegratedRoot) `
        -ExpectedSource "COMMITTED"

    $repoC = Join-Path $temporaryRoot "case-c"
    $lineC = New-ScratchLine -Repository $repoC
    [void](Add-AllowedCommit -Repository $repoC -Path "lineage.txt" -Message "R4 declared base")
    $r4Base = Invoke-Git -Repository $repoC -Arguments @("rev-parse", "HEAD")
    [void](Invoke-Git -Repository $repoC -Arguments @("checkout", "--quiet", "-b", "pktb/r4-scratch"))
    Set-CandidateChange -Repository $repoC
    Assert-PassCase -Name "CASE_c_R4_CANDIDATE" -Repository $repoC `
        -State (New-StateDocument -Line $lineC -Branch "pktb/r4-scratch" -BaseCommit $r4Base) `
        -ExpectedSource "CANDIDATE"

    $repoD = Join-Path $temporaryRoot "case-d"
    $lineD = New-ScratchLine -Repository $repoD -Branch "wrong-branch"
    Set-CandidateChange -Repository $repoD
    Assert-RefuseCase -Name "CASE_d_WRONG_BRANCH" -Repository $repoD `
        -State (New-StateDocument -Line $lineD -Branch "pktb/int-r3-tt1" -BaseCommit $lineD.IntegratedRoot) `
        -ExpectedReason "current branch"

    $repoE = Join-Path $temporaryRoot "case-e"
    $lineE = New-ScratchLine -Repository $repoE
    [void](Invoke-Git -Repository $repoE -Arguments @("checkout", "--quiet", "-b", "pktb/off-integrated-line", $lineE.Common))
    [System.IO.File]::WriteAllText((Join-Path $repoE "side.txt"), "side`n", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-Git -Repository $repoE -Arguments @("add", "side.txt"))
    [void](Invoke-Git -Repository $repoE -Arguments @("commit", "--quiet", "-m", "off-line base"))
    $offLineBase = Invoke-Git -Repository $repoE -Arguments @("rev-parse", "HEAD")
    Set-CandidateChange -Repository $repoE
    Assert-RefuseCase -Name "CASE_e_BASE_NOT_ON_INTEGRATED_LINE" -Repository $repoE `
        -State (New-StateDocument -Line $lineE -Branch "pktb/off-integrated-line" -BaseCommit $offLineBase) `
        -ExpectedReason "not on the integrated line"

    $repoF = Join-Path $temporaryRoot "case-f"
    $lineF = New-ScratchLine -Repository $repoF
    [void](Add-AllowedCommit -Repository $repoF)
    Assert-RefuseCase -Name "CASE_f_ONE_COMMIT_AHEAD_NOT_ALLOWED" -Repository $repoF `
        -State (New-StateDocument -Line $lineF -Branch $lineF.Branch -BaseCommit $lineF.IntegratedRoot `
            -CommittedChildAllowed $false) -ExpectedReason "neither the declared candidate"

    $repoG = Join-Path $temporaryRoot "case-g"
    $lineG = New-ScratchLine -Repository $repoG
    [void](Add-AllowedCommit -Repository $repoG -Path "allowed.txt" -Message "first child")
    [void](Add-AllowedCommit -Repository $repoG -Path "second.txt" -Message "second child")
    Assert-RefuseCase -Name "CASE_g_TWO_COMMITS_AHEAD" -Repository $repoG `
        -State (New-StateDocument -Line $lineG -Branch $lineG.Branch -BaseCommit $lineG.IntegratedRoot) `
        -ExpectedReason "neither the declared candidate"

    $repoHTracked = Join-Path $temporaryRoot "case-h-tracked"
    $lineHTracked = New-ScratchLine -Repository $repoHTracked
    [System.IO.File]::WriteAllText((Join-Path $repoHTracked "tracked.txt"), "out of scope`n", [System.Text.UTF8Encoding]::new($false))
    Assert-RefuseCase -Name "CASE_h_TRACKED_OUTSIDE_ALLOWED_PATHS" -Repository $repoHTracked `
        -State (New-StateDocument -Line $lineHTracked -Branch $lineHTracked.Branch `
            -BaseCommit $lineHTracked.IntegratedRoot -AllowedPaths @("allowed.txt")) `
        -ExpectedReason "outside allowed_paths"

    $repoHUntracked = Join-Path $temporaryRoot "case-h-untracked"
    $lineHUntracked = New-ScratchLine -Repository $repoHUntracked
    [System.IO.File]::WriteAllText((Join-Path $repoHUntracked "outside.txt"), "untracked out of scope`n", [System.Text.UTF8Encoding]::new($false))
    Assert-RefuseCase -Name "CASE_h_UNTRACKED_OUTSIDE_ALLOWED_PATHS" -Repository $repoHUntracked `
        -State (New-StateDocument -Line $lineHUntracked -Branch $lineHUntracked.Branch `
            -BaseCommit $lineHUntracked.IntegratedRoot -AllowedPaths @("allowed.txt")) `
        -ExpectedReason "outside allowed_paths"

    $repoI = Join-Path $temporaryRoot "case-i"
    $lineI = New-ScratchLine -Repository $repoI
    Set-CandidateChange -Repository $repoI
    Assert-RefuseCase -Name "CASE_i_STATE_HASH_MISMATCH" -Repository $repoI `
        -State (New-StateDocument -Line $lineI -Branch $lineI.Branch -BaseCommit $lineI.IntegratedRoot) `
        -ExpectedReason "SHA-256 does not match" `
        -SuppliedHash ("0" * 64)

    $repoJ = Join-Path $temporaryRoot "case-j"
    $seedJ = New-ScratchSeed -Repository $repoJ
    $collision = Get-CommitPrefixCollision -Tree $seedJ.Tree -Parent $seedJ.Common
    if ($collision.FirstHash.Substring(0, 8) -cne $collision.SecondHash.Substring(0, 8) -or
        $collision.FirstHash -ceq $collision.SecondHash) {
        throw "constructed parent pair does not share an eight-character prefix with distinct full hashes"
    }
    $firstBodyPath = Join-Path $temporaryRoot "collision-first.commit"
    $secondBodyPath = Join-Path $temporaryRoot "collision-second.commit"
    [System.IO.File]::WriteAllText($firstBodyPath, $collision.FirstBody, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($secondBodyPath, $collision.SecondBody, [System.Text.UTF8Encoding]::new($false))
    $firstInserted = Invoke-Git -Repository $repoJ -Arguments @("hash-object", "-t", "commit", "-w", $firstBodyPath)
    $secondInserted = Invoke-Git -Repository $repoJ -Arguments @("hash-object", "-t", "commit", "-w", $secondBodyPath)
    if ($firstInserted -cne $collision.FirstHash -or $secondInserted -cne $collision.SecondHash) {
        throw "inserted collision commits did not retain their calculated full SHAs"
    }
    $mergeJ = Invoke-Git -Repository $repoJ -Arguments @(
        "commit-tree", $seedJ.Tree, "-p", $collision.SecondHash, "-p", $collision.FirstHash, "-m", "reversed collision parents"
    )
    $rootJ = Invoke-Git -Repository $repoJ -Arguments @("commit-tree", $seedJ.Tree, "-p", $mergeJ, "-m", "integrated root")
    [void](Invoke-Git -Repository $repoJ -Arguments @("checkout", "--quiet", "-b", "pktb/int-r3-tt1", $rootJ))
    Set-CandidateChange -Repository $repoJ
    $lineJ = [PSCustomObject]@{
        Merge = $mergeJ
        IntegratedRoot = $rootJ
        ParentOne = $collision.FirstHash
        ParentTwo = $collision.SecondHash
        Branch = "pktb/int-r3-tt1"
    }
    Assert-RefuseCase -Name "CASE_j_FULL_PARENT_SHA_COLLISION" -Repository $repoJ `
        -State (New-StateDocument -Line $lineJ -Branch $lineJ.Branch -BaseCommit $lineJ.IntegratedRoot) `
        -ExpectedReason "exact full parent SHAs"

    $repoK = Join-Path $temporaryRoot "case-k"
    $lineK = New-ScratchLine -Repository $repoK
    [void](Add-AllowedCommit -Repository $repoK)
    [System.IO.File]::WriteAllText((Join-Path $repoK "allowed.txt"), "dirty after commit`n", [System.Text.UTF8Encoding]::new($false))
    Assert-RefuseCase -Name "CASE_k_COMMITTED_DIRTY_TREE" -Repository $repoK `
        -State (New-StateDocument -Line $lineK -Branch $lineK.Branch -BaseCommit $lineK.IntegratedRoot) `
        -ExpectedReason "worktree is not clean"

    $repoL = Join-Path $temporaryRoot "case-l"
    $lineL = New-ScratchLine -Repository $repoL
    Set-CandidateChange -Repository $repoL
    $missingEvidenceRoot = New-StateDocument -Line $lineL -Branch $lineL.Branch -BaseCommit $lineL.IntegratedRoot
    $missingEvidenceRoot.Remove("evidence_root")
    Assert-RefuseCase -Name "CASE_l_MISSING_EVIDENCE_ROOT" -Repository $repoL `
        -State $missingEvidenceRoot -ExpectedReason "missing evidence_root"
    Assert-RefuseCase -Name "CASE_l_INVALID_EVIDENCE_ROOT" -Repository $repoL `
        -State (New-StateDocument -Line $lineL -Branch $lineL.Branch -BaseCommit $lineL.IntegratedRoot `
            -EvidenceRoot "PKTB_invalid") -ExpectedReason "evidence_root must match"
    $missingDatabaseTag = New-StateDocument -Line $lineL -Branch $lineL.Branch -BaseCommit $lineL.IntegratedRoot
    $missingDatabaseTag.Remove("database_tag")
    Assert-RefuseCase -Name "CASE_l_MISSING_DATABASE_TAG" -Repository $repoL `
        -State $missingDatabaseTag -ExpectedReason "missing database_tag"
    Assert-RefuseCase -Name "CASE_l_INVALID_DATABASE_TAG" -Repository $repoL `
        -State (New-StateDocument -Line $lineL -Branch $lineL.Branch -BaseCommit $lineL.IntegratedRoot `
            -DatabaseTag "BadTag") -ExpectedReason "database_tag must match"

    $routeHome = Join-Path $temporaryRoot "route-preflight-home"
    $routeAFile = Write-StateFile -Name "CASE_m_ROUTE_A" -State (
        New-StateDocument -Line $lineL -Branch $lineL.Branch -BaseCommit $lineL.IntegratedRoot `
            -EvidenceRoot "PKTB_ROUTE_A" -DatabaseTag "routea"
    )
    $routeBFile = Write-StateFile -Name "CASE_m_ROUTE_B" -State (
        New-StateDocument -Line $lineL -Branch $lineL.Branch -BaseCommit $lineL.IntegratedRoot `
            -EvidenceRoot "PKTB_ROUTE_B" -DatabaseTag "routeb"
    )
    Push-Location -LiteralPath $repoL
    try {
        $routeAState = Get-IntegratedState -StateFile $routeAFile.Path -StateSha256 $routeAFile.Hash
        $routeBState = Get-IntegratedState -StateFile $routeBFile.Path -StateSha256 $routeBFile.Hash
    }
    finally {
        Pop-Location
    }
    $routeA = Get-IntegratedEvidenceRoutes -AuthorizedState $routeAState -HomeDirectory $routeHome
    $routeB = Get-IntegratedEvidenceRoutes -AuthorizedState $routeBState -HomeDirectory $routeHome
    $routeADatabaseME = "$($routeA.DevelopmentDatabasePrefix)3"
    $routeBDatabaseME = "$($routeB.DevelopmentDatabasePrefix)3"
    $routeADatabaseVR = "$($routeA.VerificationDatabasePrefix)3"
    $routeBDatabaseVR = "$($routeB.VerificationDatabasePrefix)3"
    if ($routeA.DevelopmentRoot -ceq $routeB.DevelopmentRoot -or
        $routeA.VerificationRoot -ceq $routeB.VerificationRoot -or
        $routeADatabaseME -ceq $routeBDatabaseME -or
        $routeADatabaseVR -ceq $routeBDatabaseVR) {
        throw "different validated states did not produce distinct evidence routes"
    }
    if (Test-Path -LiteralPath $routeHome -PathType Container) {
        throw "route preflight created a folder"
    }
    $results.Add("CASE_m_DYNAMIC_ROUTE_PREFLIGHT=PASS (database-free; no folders created)")
    Write-Output "CASE_m_STATE_A_ME_DEV=$($routeA.DevelopmentRoot)"
    Write-Output "CASE_m_STATE_B_ME_DEV=$($routeB.DevelopmentRoot)"
    Write-Output "CASE_m_STATE_A_VERIFICATION=$($routeA.VerificationRoot)"
    Write-Output "CASE_m_STATE_B_VERIFICATION=$($routeB.VerificationRoot)"
    Write-Output "CASE_m_STATE_A_DATABASE_ME=$routeADatabaseME"
    Write-Output "CASE_m_STATE_B_DATABASE_ME=$routeBDatabaseME"
    Write-Output "CASE_m_STATE_A_DATABASE_VR=$routeADatabaseVR"
    Write-Output "CASE_m_STATE_B_DATABASE_VR=$routeBDatabaseVR"
    Write-Output "CASE_m_DATABASE_ACCESS=NONE"
    Write-Output "CASE_m_FOLDERS_CREATED=False"

}
catch {
    $failure = $_.Exception.Message
}
finally {
    Set-Location -LiteralPath $originalLocation
    if (Test-Path -LiteralPath $temporaryRoot -PathType Container) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}

Write-Output ("SCRATCH_REPOSITORIES_REMOVED={0}" -f (-not (Test-Path -LiteralPath $temporaryRoot)))
foreach ($result in $results) {
    Write-Output $result
}
if (Test-Path -LiteralPath $temporaryRoot -PathType Container) {
    $failure = "scratch repository cleanup did not remove the temporary root"
}
if ($null -ne $failure) {
    Write-Output "STATE_TEST=FAIL"
    Write-Output "FAILURE=$failure"
    $global:LASTEXITCODE = 1
    [Environment]::ExitCode = 1
}
else {
    Write-Output "STATE_TEST=PASS"
    Write-Output "CASE_COUNT=$($results.Count)"
    $global:LASTEXITCODE = 0
    [Environment]::ExitCode = 0
}
