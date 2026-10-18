$script:MaxTestDatabaseNameLength = 47
$script:MaxAttemptNumberDigits = 6
$script:MaxDatabaseTagLength = $script:MaxTestDatabaseNameLength -
    ("test_intevia_living_organism__me_".Length + $script:MaxAttemptNumberDigits)

function Assert-IntegratedDatabaseTag {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$DatabaseTag)

    $tagPattern = '^[a-z0-9]{1,' + $script:MaxDatabaseTagLength + '}$'
    if ($DatabaseTag -cnotmatch $tagPattern) {
        throw "database_tag must match $tagPattern for a six-digit attempt and the $($script:MaxTestDatabaseNameLength)-byte primary-name bound"
    }
    return $DatabaseTag
}

function Assert-IntegratedTestDatabaseName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Name,
        [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Purpose
    )

    $nameBytes = [System.Text.Encoding]::UTF8.GetByteCount($Name)
    if ($nameBytes -gt $script:MaxTestDatabaseNameLength) {
        throw "$Purpose database name exceeds the $($script:MaxTestDatabaseNameLength)-byte primary-name bound"
    }
    return $Name
}

function Get-IntegratedCloneProbeDatabaseName {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$RunNonce)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($RunNonce))
    }
    finally {
        $algorithm.Dispose()
    }
    $token = [System.Convert]::ToHexString($hash).ToLowerInvariant().Substring(0, 12)
    return Assert-IntegratedTestDatabaseName `
        -Name "test_intevia_living_organism_w_$token" `
        -Purpose "clone preflight probe"
}

function Get-IntegratedState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$StateFile,
        [Parameter(Mandatory = $true)][ValidatePattern("^[0-9a-fA-F]{64}$")][string]$StateSha256
    )

    if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) {
        throw "authorized state file does not exist"
    }
    $actualStateHash = (Get-FileHash -LiteralPath $StateFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualStateHash -ne $StateSha256.ToLowerInvariant()) {
        throw "authorized state file SHA-256 does not match"
    }

    $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json -ErrorAction Stop
    foreach ($name in @("state_id", "branch", "base_commit", "integrated_root", "merge", "allowed_paths", "committed_child_allowed", "evidence_root", "database_tag")) {
        if ($null -eq $state.PSObject.Properties[$name]) {
            throw "authorized state file is missing $name"
        }
    }
    if ([string]::IsNullOrWhiteSpace([string]$state.state_id) -or
        [string]::IsNullOrWhiteSpace([string]$state.branch)) {
        throw "authorized state id and branch must be non-empty"
    }
    if ($state.evidence_root -isnot [string] -or
        $state.evidence_root -cnotmatch "^PKTB_[A-Z0-9_]{1,24}$") {
        throw "evidence_root must match ^PKTB_[A-Z0-9_]{1,24}$"
    }
    if ($state.database_tag -isnot [string]) {
        throw "database_tag must be a string"
    }
    $null = Assert-IntegratedDatabaseTag -DatabaseTag $state.database_tag
    if ($null -eq $state.merge -or
        $null -eq $state.merge.PSObject.Properties["commit"] -or
        $null -eq $state.merge.PSObject.Properties["parents"]) {
        throw "authorized state merge declaration is incomplete"
    }

    $fullHashPattern = "^[0-9a-fA-F]{40}$"
    foreach ($hash in @($state.merge.commit, $state.base_commit, $state.integrated_root) + @($state.merge.parents)) {
        if ([string]$hash -notmatch $fullHashPattern) {
            throw "authorized state commit and parent hashes must be full 40-character SHAs"
        }
    }
    $mergeParents = @($state.merge.parents)
    if ($mergeParents.Count -ne 2) {
        throw "authorized state must declare exactly two merge parents"
    }
    if ($state.committed_child_allowed -isnot [bool]) {
        throw "committed_child_allowed must be a JSON boolean"
    }
    if ($state.allowed_paths -isnot [System.Array] -or $state.allowed_paths.Count -eq 0) {
        throw "allowed_paths must be a non-empty JSON array"
    }

    $allowedPaths = @()
    foreach ($path in $state.allowed_paths) {
        if ($path -isnot [string] -or [string]::IsNullOrWhiteSpace($path)) {
            throw "allowed_paths must contain non-empty strings"
        }
        $normalized = $path.Replace("\", "/")
        if ($normalized.StartsWith("/") -or $normalized -match "^[A-Za-z]:" -or
            $normalized -match "(^|/)\.\.?(/|$)") {
            throw "allowed_paths must contain repository-relative paths"
        }
        if ($normalized -cnotin $allowedPaths) {
            $allowedPaths += $normalized
        }
    }
    if ($allowedPaths.Count -ne $state.allowed_paths.Count) {
        throw "allowed_paths must not contain duplicates"
    }
    if ($null -eq $state.PSObject.Properties["run_spec_version"] -or
        $null -eq $state.PSObject.Properties["run_spec_sha256"] -or
        $state.run_spec_version -cne "v1.38" -or
        [string]$state.run_spec_sha256 -notmatch "^[0-9a-fA-F]{64}$") {
        throw "R11: authorized state lacks a valid v1.38 run specification binding"
    }
    $specificationPath = Join-Path (Split-Path -Parent $PSScriptRoot) "docs\operations\RUN_REQUIREMENTS.md"
    if (-not (Test-Path -LiteralPath $specificationPath -PathType Leaf)) {
        throw "R11: repository run specification is missing"
    }
    $specificationHash = (Get-FileHash -LiteralPath $specificationPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($specificationHash -cne ([string]$state.run_spec_sha256).ToLowerInvariant()) {
        throw "R11: repository run specification digest differs from the authorized state"
    }

    $declaredMerge = ([string]$state.merge.commit).ToLowerInvariant()
    $declaredRoot = ([string]$state.integrated_root).ToLowerInvariant()
    $declaredBase = ([string]$state.base_commit).ToLowerInvariant()
    $declaredParents = @($mergeParents | ForEach-Object { ([string]$_).ToLowerInvariant() })
    $mergeParentsText = (& git show -s --format=%P $declaredMerge)
    if ($LASTEXITCODE -ne 0) {
        throw "declared merge commit cannot be resolved"
    }
    $actualMergeParents = @("$mergeParentsText".Trim() -split "\s+" | Where-Object { $_ })
    if ($actualMergeParents.Count -ne 2 -or
        $actualMergeParents[0] -cne $declaredParents[0] -or
        $actualMergeParents[1] -cne $declaredParents[1]) {
        throw "declared merge commit does not have the exact full parent SHAs in the state file"
    }

    $rootParentsText = (& git show -s --format=%P $declaredRoot)
    if ($LASTEXITCODE -ne 0) {
        throw "declared integrated root cannot be resolved"
    }
    $rootParents = @("$rootParentsText".Trim() -split "\s+" | Where-Object { $_ })
    if ($rootParents.Count -ne 1 -or $rootParents[0] -cne $declaredMerge) {
        throw "integrated root must have the declared merge as its only parent"
    }

    if ($declaredBase -ne $declaredRoot) {
        & git merge-base --is-ancestor $declaredRoot $declaredBase
        if ($LASTEXITCODE -eq 1) {
            throw "declared base is not on the integrated line"
        }
        if ($LASTEXITCODE -ne 0) {
            throw "declared base ancestry could not be verified"
        }
    }

    $currentBranch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $currentBranch -cne [string]$state.branch) {
        throw "current branch does not match the authorized state"
    }
    $head = (& git rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "HEAD could not be resolved"
    }
    $sourceKind = $null
    $changedPaths = @()

    if ($head -ceq $declaredBase) {
        $trackedPaths = @(& git diff --name-only --no-renames $declaredBase)
        if ($LASTEXITCODE -ne 0) {
            throw "candidate changed-path inventory failed"
        }
        $untrackedPaths = @(& git ls-files --others --exclude-standard)
        if ($LASTEXITCODE -ne 0) {
            throw "candidate untracked-path inventory failed"
        }
        $changedPaths = @(@($trackedPaths) + @($untrackedPaths) |
            ForEach-Object { ([string]$_).Replace("\", "/") } |
            Sort-Object -CaseSensitive -Unique)
        $unauthorized = @($changedPaths | Where-Object { $_ -cnotin $allowedPaths })
        if ($unauthorized.Count -gt 0) {
            throw ("candidate contains changed paths outside allowed_paths: " + ($unauthorized -join ","))
        }
        foreach ($path in $untrackedPaths) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                throw "candidate untracked path is not a regular file"
            }
        }
        $sourceKind = "CANDIDATE"
    }
    else {
        $headParentsText = (& git show -s --format=%P $head)
        if ($LASTEXITCODE -ne 0) {
            throw "HEAD parent lookup failed"
        }
        $headParents = @("$headParentsText".Trim() -split "\s+" | Where-Object { $_ })
        if (-not $state.committed_child_allowed -or $headParents.Count -ne 1 -or
            $headParents[0] -cne $declaredBase) {
            throw "HEAD is neither the declared candidate nor an authorized direct committed child"
        }
        $status = @(& git status --porcelain --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            throw "committed source worktree check failed"
        }
        if ($status.Count -ne 0) {
            throw "committed source worktree is not clean"
        }
        $committedPaths = @(& git diff-tree --no-commit-id --name-only --no-renames -r $head)
        if ($LASTEXITCODE -ne 0) {
            throw "committed changed-path inventory failed"
        }
        $changedPaths = @($committedPaths | ForEach-Object { ([string]$_).Replace("\", "/") } |
            Sort-Object -CaseSensitive -Unique)
        $unauthorized = @($changedPaths | Where-Object { $_ -cnotin $allowedPaths })
        if ($unauthorized.Count -gt 0) {
            throw ("committed child contains paths outside allowed_paths: " + ($unauthorized -join ","))
        }
        $sourceKind = "COMMITTED"
    }

    return [PSCustomObject]@{
        State = $state
        StateFile = (Resolve-Path -LiteralPath $StateFile).Path
        StateId = [string]$state.state_id
        StateSha256 = $actualStateHash
        Branch = [string]$state.branch
        EvidenceRoot = [string]$state.evidence_root
        DatabaseTag = [string]$state.database_tag
        BaseCommit = $declaredBase
        IntegratedRoot = $declaredRoot
        MergeCommit = $declaredMerge
        Head = $head
        SourceKind = $sourceKind
        ChangedPaths = $changedPaths
    }
}

function Get-IntegratedEvidenceRoutes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$AuthorizedState,
        [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$HomeDirectory
    )

    $evidenceRoot = $AuthorizedState.EvidenceRoot
    $databaseTag = $AuthorizedState.DatabaseTag
    if ($evidenceRoot -isnot [string] -or
        $evidenceRoot -cnotmatch "^PKTB_[A-Z0-9_]{1,24}$" -or
        $databaseTag -isnot [string]) {
        throw "evidence routes require a validated state"
    }
    $databaseTag = Assert-IntegratedDatabaseTag -DatabaseTag $databaseTag

    $stageRoot = Join-Path (Join-Path $HomeDirectory "INTEVIA_AGENT_STAGING") $evidenceRoot
    return [PSCustomObject]@{
        DevelopmentRoot = Join-Path $stageRoot "ME_DEV"
        DevelopmentDatabasePrefix = "test_intevia_living_organism_${databaseTag}_me_"
        DatabaseNameMaxLength = $script:MaxTestDatabaseNameLength
        DatabaseTagMaxLength = $script:MaxDatabaseTagLength
    }
}
