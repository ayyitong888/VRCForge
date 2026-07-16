param(
    [string]$Version = "",
    [string]$ReleaseDir = "dist\release",
    [switch]$AllowDirty,
    [switch]$AllowUnpushed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseOperationMutexName = "Local\VRCForge.Release.BuildPublish.v1"

function Enter-ReleaseOperationMutex {
    $mutex = New-Object System.Threading.Mutex($false, $releaseOperationMutexName)
    $acquired = $false
    try {
        try {
            $acquired = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $acquired = $true
            Write-Warning "Taking ownership of an abandoned VRCForge release-operation mutex."
        }

        if (-not $acquired) {
            throw "Another VRCForge build or publish operation is already running."
        }
        return $mutex
    } catch {
        if (-not $acquired) {
            $mutex.Dispose()
        }
        throw
    }
}

function Get-RequiredProperty {
    param(
        [object]$InputObject,
        [string]$Name,
        [string]$Context
    )

    if ($null -eq $InputObject -or -not ($InputObject.PSObject.Properties.Name -contains $Name)) {
        throw "$Context is missing required property '$Name'."
    }
    return $InputObject.PSObject.Properties[$Name].Value
}

function Get-StreamSha256 {
    param(
        [System.IO.Stream]$Stream
    )

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha256.ComputeHash($Stream)
        return ([System.BitConverter]::ToString($hashBytes)).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Get-GitHubReleaseSnapshot {
    param(
        [string]$Tag = "",
        [long]$ReleaseId = 0,
        [switch]$AllowMissing
    )

    if ($ReleaseId -le 0 -and [string]::IsNullOrWhiteSpace($Tag)) {
        throw "GitHub Release snapshot requires a tag or positive release id."
    }
    $releaseLocator = if ($ReleaseId -gt 0) { "id $ReleaseId" } else { "tag $Tag" }
    $endpoint = if ($ReleaseId -gt 0) {
        "repos/{owner}/{repo}/releases/$ReleaseId"
    } else {
        "repos/{owner}/{repo}/releases/tags/$Tag"
    }
    $apiOutput = @(& gh api $endpoint 2>$null)
    $apiExitCode = $LASTEXITCODE
    $apiJson = $apiOutput -join [System.Environment]::NewLine
    if ($apiExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($apiJson)) {
        if ($AllowMissing) {
            return $null
        }
        throw "Unable to read back GitHub Release $releaseLocator through the GitHub REST API."
    }
    $apiRelease = $apiJson | ConvertFrom-Json
    return [PSCustomObject]@{
        id = Get-RequiredProperty -InputObject $apiRelease -Name "id" -Context "GitHub REST Release readback"
        tagName = Get-RequiredProperty -InputObject $apiRelease -Name "tag_name" -Context "GitHub REST Release readback"
        targetCommitish = Get-RequiredProperty -InputObject $apiRelease -Name "target_commitish" -Context "GitHub REST Release readback"
        name = Get-RequiredProperty -InputObject $apiRelease -Name "name" -Context "GitHub REST Release readback"
        body = Get-RequiredProperty -InputObject $apiRelease -Name "body" -Context "GitHub REST Release readback"
        isDraft = Get-RequiredProperty -InputObject $apiRelease -Name "draft" -Context "GitHub REST Release readback"
        isPrerelease = Get-RequiredProperty -InputObject $apiRelease -Name "prerelease" -Context "GitHub REST Release readback"
        assets = @(Get-RequiredProperty -InputObject $apiRelease -Name "assets" -Context "GitHub REST Release readback")
    }
}

function Assert-RemoteTagTarget {
    param(
        [string]$Tag,
        [string]$Target
    )

    $directRef = "refs/tags/$Tag"
    $peeledRef = "$directRef^{}"
    $remoteLines = @(& git ls-remote --tags origin $directRef $peeledRef)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read the remote release tag $Tag."
    }
    $remoteRefs = @{}
    foreach ($line in $remoteLines) {
        $parts = @($line -split "`t", 2)
        if ($parts.Count -ne 2 -or $parts[0] -notmatch "^[0-9a-fA-F]{40}$") {
            throw "Remote tag readback returned an invalid ref record for $Tag."
        }
        if ($remoteRefs.ContainsKey($parts[1])) {
            throw "Remote tag readback returned duplicate ref records for $Tag."
        }
        $remoteRefs[$parts[1]] = $parts[0].ToLowerInvariant()
    }
    if (-not $remoteRefs.ContainsKey($directRef)) {
        throw "Remote tag $Tag does not exist. Create and push the intended release tag before publishing."
    }
    $resolvedTarget = if ($remoteRefs.ContainsKey($peeledRef)) {
        [string]$remoteRefs[$peeledRef]
    } else {
        [string]$remoteRefs[$directRef]
    }
    if ($resolvedTarget -ne $Target.ToLowerInvariant()) {
        throw "Remote tag $Tag resolves to $resolvedTarget, but the manifest is bound to $Target."
    }
}

function Assert-GitHubReleaseSnapshot {
    param(
        [object]$Release,
        [string]$Tag,
        [string]$Target,
        [long]$ExpectedReleaseId = 0,
        [string]$ExpectedName,
        [string]$ExpectedBody,
        [bool]$ExpectedDraft,
        [bool]$ExpectedPrerelease,
        [string[]]$RequiredArtifactNames,
        [object[]]$ManifestArtifacts
    )

    $remoteId = [long](Get-RequiredProperty -InputObject $Release -Name "id" -Context "GitHub Release readback")
    $remoteTag = [string](Get-RequiredProperty -InputObject $Release -Name "tagName" -Context "GitHub Release readback")
    $remoteTarget = [string](Get-RequiredProperty -InputObject $Release -Name "targetCommitish" -Context "GitHub Release readback")
    $remoteName = [string](Get-RequiredProperty -InputObject $Release -Name "name" -Context "GitHub Release readback")
    $remoteBody = [string](Get-RequiredProperty -InputObject $Release -Name "body" -Context "GitHub Release readback")
    $remoteIsDraft = Get-RequiredProperty -InputObject $Release -Name "isDraft" -Context "GitHub Release readback"
    $remoteIsPrerelease = Get-RequiredProperty -InputObject $Release -Name "isPrerelease" -Context "GitHub Release readback"
    if (
        $remoteId -le 0 -or
        ($ExpectedReleaseId -gt 0 -and $remoteId -ne $ExpectedReleaseId) -or
        $remoteTag -cne $Tag -or
        $remoteTarget.ToLowerInvariant() -cne $Target.ToLowerInvariant() -or
        $remoteName -cne $ExpectedName -or
        $remoteBody -cne $ExpectedBody -or
        ([bool]$remoteIsDraft) -ne $ExpectedDraft -or
        ([bool]$remoteIsPrerelease) -ne $ExpectedPrerelease
    ) {
        throw "GitHub Release metadata does not match the manifest-bound release state."
    }

    $remoteAssets = @(Get-RequiredProperty -InputObject $Release -Name "assets" -Context "GitHub Release readback")
    $remoteAssetNames = @($remoteAssets | ForEach-Object { [string]$_.name })
    if (
        $remoteAssetNames.Count -ne $RequiredArtifactNames.Count -or
        @($remoteAssetNames | Sort-Object -Unique -CaseSensitive).Count -ne $RequiredArtifactNames.Count -or
        @(Compare-Object -ReferenceObject $RequiredArtifactNames -DifferenceObject $remoteAssetNames -CaseSensitive).Count -ne 0
    ) {
        throw "GitHub Release must contain exactly the four manifest-bound assets after upload."
    }
    foreach ($artifactName in $RequiredArtifactNames) {
        $manifestEntry = @($ManifestArtifacts | Where-Object { [string]$_.name -ceq $artifactName })
        if ($manifestEntry.Count -ne 1) {
            throw "Release manifest readback did not return exactly one asset named $artifactName."
        }
        $remoteEntries = @($remoteAssets | Where-Object { [string]$_.name -ceq $artifactName })
        if ($remoteEntries.Count -ne 1) {
            throw "GitHub Release readback did not return exactly one asset named $artifactName."
        }
        $remoteDigest = [string](Get-RequiredProperty -InputObject $remoteEntries[0] -Name "digest" -Context "GitHub Release asset $artifactName")
        $expectedRemoteDigest = "sha256:$(([string]$manifestEntry[0].sha256).ToLowerInvariant())"
        if ($remoteDigest.ToLowerInvariant() -ne $expectedRemoteDigest) {
            throw "GitHub Release asset digest does not match the manifest: $artifactName"
        }
    }
}

$releaseOperationMutex = $null
$releaseOperationMutexOwned = $false
$locationPushed = $false
$stagingRoot = $null
$artifactGuardStreams = @()
try {
    $releaseOperationMutex = Enter-ReleaseOperationMutex
    $releaseOperationMutexOwned = $true
    Push-Location $repoRoot
    $locationPushed = $true

    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    }

    git fetch origin --tags --prune | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Publishing requires a successful git fetch of origin."
    }

    $status = git status --short
    if ($status -and -not $AllowDirty) {
        throw "Working tree has uncommitted changes. Commit before publishing release.`n$status"
    }

    $unpushed = git log origin/main..HEAD --oneline
    if ($unpushed -and -not $AllowUnpushed) {
        throw "Local HEAD contains commits not on origin/main. Push first before publishing release.`n$unpushed"
    }

    $remoteVersion = (git show origin/main:VERSION).Trim()
    if ($remoteVersion -ne $Version) {
        throw "Release version must match origin/main VERSION. local=$Version origin/main=$remoteVersion"
    }

    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) {
        throw "GitHub CLI gh is required to upload release artifacts."
    }

    $resolvedReleaseDir = if ([System.IO.Path]::IsPathRooted($ReleaseDir)) {
        $ReleaseDir
    } else {
        Join-Path $repoRoot $ReleaseDir
    }

    $webInstaller = Join-Path $resolvedReleaseDir "VRCForge_Web_Installer_x64.exe"
    $offlineInstaller = Join-Path $resolvedReleaseDir "VRCForge_Offline_Installer_x64.exe"
    $payloadZip = Join-Path $resolvedReleaseDir "VRCForge_Windows_x64_$Version.zip"
    $unityPackage = Join-Path $resolvedReleaseDir "VRCForge.unitypackage"
    $sourceArtifacts = @($unityPackage, $webInstaller, $offlineInstaller, $payloadZip)
    foreach ($artifact in $sourceArtifacts) {
        if (-not (Test-Path -LiteralPath $artifact)) {
            throw "Missing release artifact: $artifact"
        }
    }

    $manifestPath = Join-Path $resolvedReleaseDir "release-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Missing release manifest: $manifestPath"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $target = (git rev-parse origin/main).Trim()
    if ([string]$manifest.version -ne $Version -or [string]$manifest.commit -ne $target) {
        throw "Release manifest must match the requested version and origin/main commit."
    }
    if (-not ($manifest.PSObject.Properties.Name -contains "buildPolicy")) {
        throw "Release manifest is missing strict build provenance. Rebuild with packaging/build_release.ps1."
    }
    $buildPolicy = $manifest.buildPolicy
    if (
        [string]$buildPolicy.mode -ne "strict" -or
        $buildPolicy.releaseEligible -ne $true -or
        $buildPolicy.allowDirty -ne $false -or
        $buildPolicy.allowUnpushed -ne $false -or
        $buildPolicy.allowVersionMismatch -ne $false
    ) {
        throw "Local-acceptance artifacts are not publishable. Run a clean, pushed strict release rebuild."
    }
    $manifestUvDownloadUrl = [string](Get-RequiredProperty -InputObject $manifest -Name "uvDownloadUrl" -Context "Release manifest")
    $manifestUvDownloadSha256 = [string](Get-RequiredProperty -InputObject $manifest -Name "uvDownloadSha256" -Context "Release manifest")
    if ($manifestUvDownloadSha256 -notmatch "^[0-9a-fA-F]{64}$") {
        throw "Release manifest is missing a pinned uv download SHA-256."
    }
    $uvRuntime = Get-RequiredProperty -InputObject $manifest -Name "uvRuntime" -Context "Release manifest"
    $uvRuntimeSource = [string](Get-RequiredProperty -InputObject $uvRuntime -Name "source" -Context "Release manifest uvRuntime")
    $uvRuntimeDownloadUrl = [string](Get-RequiredProperty -InputObject $uvRuntime -Name "downloadUrl" -Context "Release manifest uvRuntime")
    $uvRuntimeArchiveSha256 = [string](Get-RequiredProperty -InputObject $uvRuntime -Name "archiveSha256" -Context "Release manifest uvRuntime")
    $uvRuntimeArchiveDigestVerified = Get-RequiredProperty -InputObject $uvRuntime -Name "archiveDigestVerified" -Context "Release manifest uvRuntime"
    if (
        $uvRuntimeSource -ne "download" -or
        [string]::IsNullOrWhiteSpace($uvRuntimeDownloadUrl) -or
        $uvRuntimeDownloadUrl -ne $manifestUvDownloadUrl -or
        $uvRuntimeArchiveDigestVerified -ne $true -or
        $uvRuntimeArchiveSha256 -notmatch "^[0-9a-fA-F]{64}$" -or
        $uvRuntimeArchiveSha256.ToLowerInvariant() -ne $manifestUvDownloadSha256.ToLowerInvariant()
    ) {
        throw "Release manifest uv runtime provenance is not a verified strict download."
    }
    $uvRuntimeFiles = @(Get-RequiredProperty -InputObject $uvRuntime -Name "files" -Context "Release manifest uvRuntime")
    $expectedUvRuntimeDigests = @{}
    foreach ($requiredUvFileName in @("uv.exe", "uvx.exe")) {
        $uvFileEntries = @($uvRuntimeFiles | Where-Object { [string]$_.name -ceq $requiredUvFileName })
        if ($uvFileEntries.Count -ne 1 -or [string]$uvFileEntries[0].sha256 -notmatch "^[0-9a-fA-F]{64}$") {
            throw "Release manifest uv runtime provenance requires one valid digest for $requiredUvFileName."
        }
        $expectedUvRuntimeDigests[$requiredUvFileName] = ([string]$uvFileEntries[0].sha256).ToLowerInvariant()
    }
    if ($uvRuntimeFiles.Count -ne 2) {
        throw "Release manifest uv runtime provenance must contain only uv.exe and uvx.exe digests."
    }

    $stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcforge_release_publish_" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
    $artifacts = @()
    foreach ($sourceArtifact in $sourceArtifacts) {
        $stagedArtifact = Join-Path $stagingRoot ([System.IO.Path]::GetFileName($sourceArtifact))
        Copy-Item -LiteralPath $sourceArtifact -Destination $stagedArtifact
        $artifacts += $stagedArtifact
    }

    $manifestArtifacts = @($manifest.artifacts)
    $requiredArtifactNames = @($sourceArtifacts | ForEach-Object { [System.IO.Path]::GetFileName($_) })
    $manifestArtifactNames = @($manifestArtifacts | ForEach-Object { [string]$_.name })
    if (
        $manifestArtifactNames.Count -ne $requiredArtifactNames.Count -or
        @($manifestArtifactNames | Sort-Object -Unique -CaseSensitive).Count -ne $requiredArtifactNames.Count -or
        @(Compare-Object -ReferenceObject $requiredArtifactNames -DifferenceObject $manifestArtifactNames -CaseSensitive).Count -ne 0
    ) {
        throw "Release manifest must contain exactly the four publishable artifact names."
    }
    foreach ($artifact in $artifacts) {
        $artifactName = [System.IO.Path]::GetFileName($artifact)
        $manifestEntry = @($manifestArtifacts | Where-Object { [string]$_.name -ceq $artifactName })
        if ($manifestEntry.Count -ne 1 -or [string]$manifestEntry[0].sha256 -notmatch "^[0-9a-fA-F]{64}$") {
            throw "Release manifest is missing one valid digest for $artifactName."
        }
        $guardStream = [System.IO.File]::Open(
            $artifact,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $artifactGuardStreams += $guardStream
        $actualSha256 = Get-StreamSha256 -Stream $guardStream
        $guardStream.Position = 0
        if ($actualSha256 -ne ([string]$manifestEntry[0].sha256).ToLowerInvariant()) {
            throw "Release artifact digest does not match the manifest: $artifactName"
        }
    }

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stagedPayloadZip = Join-Path $stagingRoot ([System.IO.Path]::GetFileName($payloadZip))
    $payloadArchive = [System.IO.Compression.ZipFile]::OpenRead($stagedPayloadZip)
    try {
        $payloadUvFiles = @($payloadArchive.Entries | Where-Object {
            if ([string]::IsNullOrEmpty($_.Name)) {
                $false
            } else {
                $normalizedEntryPath = ($_.FullName -replace "\\", "/").TrimStart("/")
                $normalizedEntryPath.StartsWith("tools/uv/", [System.StringComparison]::OrdinalIgnoreCase)
            }
        })
        $payloadUvFileNames = @($payloadUvFiles | ForEach-Object {
            ($_.FullName -replace "\\", "/").TrimStart("/")
        })
        $expectedPayloadUvFiles = @("tools/uv/uv.exe", "tools/uv/uvx.exe")
        if (
            $payloadUvFileNames.Count -ne $expectedPayloadUvFiles.Count -or
            @($payloadUvFileNames | Sort-Object -Unique).Count -ne $expectedPayloadUvFiles.Count -or
            @(Compare-Object -ReferenceObject $expectedPayloadUvFiles -DifferenceObject $payloadUvFileNames -CaseSensitive).Count -ne 0
        ) {
            throw "Portable payload tools/uv subtree must contain exactly uv.exe and uvx.exe."
        }
        foreach ($requiredUvFileName in @("uv.exe", "uvx.exe")) {
            $expectedEntryPath = "tools/uv/$requiredUvFileName"
            $payloadEntries = @($payloadUvFiles | Where-Object {
                ($_.FullName -replace "\\", "/").TrimStart("/") -ceq $expectedEntryPath
            })
            if ($payloadEntries.Count -ne 1) {
                throw "Portable payload must contain exactly one $expectedEntryPath entry."
            }
            $entryStream = $payloadEntries[0].Open()
            try {
                $payloadUvSha256 = Get-StreamSha256 -Stream $entryStream
            } finally {
                $entryStream.Dispose()
            }
            if ($payloadUvSha256 -ne $expectedUvRuntimeDigests[$requiredUvFileName]) {
                throw "Portable payload $expectedEntryPath digest does not match strict uv provenance."
            }
        }
    } finally {
        $payloadArchive.Dispose()
    }

    $tag = "v$Version"
    $expectedPrerelease = $Version -match "(?i)(alpha|beta|rc)"
    $releaseTitle = "VRCForge $Version"
    $releaseNotes = "Windows x64 installer release for VRCForge $Version."
    Assert-RemoteTagTarget -Tag $tag -Target $target
    $releaseExists = $false
    $existingTarget = ""
    $existingIsDraft = $false
    $existingIsPrerelease = $false
    $existingRelease = Get-GitHubReleaseSnapshot -Tag $tag -AllowMissing
    if ($null -ne $existingRelease) {
        $releaseExists = $true
        $existingTarget = [string]$existingRelease.targetCommitish
        $existingIsDraft = [bool]$existingRelease.isDraft
        $existingIsPrerelease = [bool]$existingRelease.isPrerelease
    }

    if (-not $releaseExists) {
        $createArgs = @(
            "release", "create", $tag
        ) + $artifacts + @(
            "--target", $target,
            "--verify-tag",
            "--title", $releaseTitle,
            "--notes", $releaseNotes,
            "--draft"
        )
        if ($expectedPrerelease) {
            $createArgs += "--prerelease"
        }
        & gh @createArgs
    } else {
        $existingState = if ($existingIsDraft) { "draft" } else { "published" }
        throw "GitHub Release $tag already exists as $existingState (target=$existingTarget prerelease=$existingIsPrerelease). Refusing remote asset mutation; delete an incomplete draft explicitly before retrying."
    }

    if ($LASTEXITCODE -ne 0) {
        throw "GitHub release upload failed."
    }

    Assert-RemoteTagTarget -Tag $tag -Target $target
    $draftRelease = Get-GitHubReleaseSnapshot -Tag $tag
    Assert-GitHubReleaseSnapshot `
        -Release $draftRelease `
        -Tag $tag `
        -Target $target `
        -ExpectedName $releaseTitle `
        -ExpectedBody $releaseNotes `
        -ExpectedDraft $true `
        -ExpectedPrerelease $expectedPrerelease `
        -RequiredArtifactNames $requiredArtifactNames `
        -ManifestArtifacts $manifestArtifacts
    $draftReleaseId = [long]$draftRelease.id

    # A failed upload or digest readback leaves a non-public draft. Publish only
    # after the complete four-asset snapshot is manifest-bound, then read it back
    # once more so a remote race cannot turn an unchecked state into success.
    $publishOutput = @(& gh api --method PATCH "repos/{owner}/{repo}/releases/$draftReleaseId" -F draft=false 2>$null)
    if ($LASTEXITCODE -ne 0 -or $publishOutput.Count -eq 0) {
        throw "GitHub Release assets were verified, but publishing the draft failed."
    }
    $publishedRelease = Get-GitHubReleaseSnapshot -ReleaseId $draftReleaseId
    Assert-GitHubReleaseSnapshot `
        -Release $publishedRelease `
        -Tag $tag `
        -Target $target `
        -ExpectedReleaseId $draftReleaseId `
        -ExpectedName $releaseTitle `
        -ExpectedBody $releaseNotes `
        -ExpectedDraft $false `
        -ExpectedPrerelease $expectedPrerelease `
        -RequiredArtifactNames $requiredArtifactNames `
        -ManifestArtifacts $manifestArtifacts
    $publishedTagRelease = Get-GitHubReleaseSnapshot -Tag $tag
    if ([long]$publishedTagRelease.id -ne $draftReleaseId) {
        throw "Published GitHub Release tag no longer resolves to the manifest-bound release id."
    }
    Assert-RemoteTagTarget -Tag $tag -Target $target

    Write-Host "Uploaded, verified, and published Unity package, installers, and payload on GitHub Release $tag."
} finally {
    foreach ($guardStream in $artifactGuardStreams) {
        if ($null -ne $guardStream) {
            $guardStream.Dispose()
        }
    }
    if ($null -ne $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($locationPushed) {
        Pop-Location
    }
    if ($releaseOperationMutexOwned) {
        try {
            $releaseOperationMutex.ReleaseMutex()
        } finally {
            $releaseOperationMutexOwned = $false
        }
    }
    if ($null -ne $releaseOperationMutex) {
        $releaseOperationMutex.Dispose()
    }
}
