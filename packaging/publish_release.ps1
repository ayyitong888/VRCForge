param(
    [string]$Version = "",
    [string]$ReleaseDir = "dist\release",
    [switch]$AllowDirty,
    [switch]$AllowUnpushed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    }

    git fetch origin --tags --prune | Out-Null

    $status = git status --short
    if ($status -and -not $AllowDirty) {
        Write-Error "Working tree has uncommitted changes. Commit before publishing release.`n$status"
        exit 1
    }

    $unpushed = git log origin/main..HEAD --oneline
    if ($unpushed -and -not $AllowUnpushed) {
        Write-Error "Local HEAD contains commits not on origin/main. Push first before publishing release.`n$unpushed"
        exit 1
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
    $artifacts = @($unityPackage, $webInstaller, $offlineInstaller, $payloadZip)
    foreach ($artifact in $artifacts) {
        if (-not (Test-Path -LiteralPath $artifact)) {
            throw "Missing release artifact: $artifact"
        }
    }

    $tag = "v$Version"
    $target = (git rev-parse origin/main).Trim()
    $releaseExists = $false
    $existingTarget = ""
    try {
        $existingRelease = & gh release view $tag --json tagName,targetCommitish,isDraft,isPrerelease 2>$null
        $releaseExists = $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($existingRelease)
        if ($releaseExists) {
            $existingPayload = $existingRelease | ConvertFrom-Json
            $existingTarget = [string]$existingPayload.targetCommitish
        }
    } catch {
        $releaseExists = $false
    }

    if (-not $releaseExists) {
        $createArgs = @(
            "release", "create", $tag
        ) + $artifacts + @(
            "--target", $target,
            "--title", "VRCForge $Version",
            "--notes", "Windows x64 installer release for VRCForge $Version."
        )
        if ($Version -match "(?i)(alpha|beta|rc)") {
            $createArgs += "--prerelease"
        }
        & gh @createArgs
    } else {
        if ($existingTarget -and $existingTarget -ne $target) {
            throw "Existing GitHub Release $tag targets $existingTarget, but origin/main is $target. Move or delete the tag/release before re-uploading assets."
        }
        & gh release upload $tag @artifacts --clobber
    }

    if ($LASTEXITCODE -ne 0) {
        throw "GitHub release upload failed."
    }

    Write-Host "Uploaded Unity package, installers, and payload to GitHub Release $tag."
} finally {
    Pop-Location
}
