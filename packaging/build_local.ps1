param(
    [string]$Configuration = "Release",
    [string]$Version = "",
    [string]$PayloadDownloadUrl = "",
    [switch]$StrictDirty
)

# Local packaging wrapper: builds the same installers as build_release.ps1
# without requiring github.com connectivity or a pushed HEAD.
# It pre-seeds the bundled uv runtime so Install-UvRuntime skips its download,
# then delegates to build_release.ps1 with -AllowUnpushed and
# -AllowVersionMismatch (plus -AllowDirty unless -StrictDirty is given). Use
# ONLY for local testing; published releases must go through build_release.ps1
# with a clean, pushed HEAD whose VERSION matches origin/main.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    }

    Write-Host "=== VRCForge LOCAL build (unpushed HEAD allowed) ===" -ForegroundColor Yellow
    $headLine = cmd /c "git log -1 --oneline 2>nul"
    Write-Host "HEAD: $headLine"
    $status = git status --short
    if ($status) {
        Write-Host "Working tree is dirty (allowed in local build):" -ForegroundColor Yellow
        $status | ForEach-Object { Write-Host "  $_" }
    }

    # A local acceptance build may intentionally carry the next target version
    # before its version commit is pushed. The delegated release script allows
    # this only when both local-only switches are present.
    $originVersion = (cmd /c "git show origin/main:VERSION 2>nul" | Out-String).Trim()
    if ($originVersion -and $originVersion -ne $Version) {
        Write-Warning ("LOCAL acceptance build: VERSION ($Version) != cached origin/main VERSION ($originVersion). " +
            "Artifacts remain unpublished test outputs until a clean pushed strict rebuild.")
    }

    # Pre-seed the bundled uv runtime so Install-UvRuntime does not need github.com.
    # build_release.ps1 copies .\tools into the payload BEFORE Install-UvRuntime runs,
    # so seeding tools\uv here makes the download step a no-op.
    $uvSeedDir = Join-Path $repoRoot "tools\uv"
    $uvSeed = Join-Path $uvSeedDir "uv.exe"
    $uvxSeed = Join-Path $uvSeedDir "uvx.exe"
    if ((Test-Path -LiteralPath $uvSeed) -and (Test-Path -LiteralPath $uvxSeed)) {
        Write-Host "Bundled uv runtime already present: $uvSeedDir"
    } else {
        $seeded = $false
        $candidateDirs = @()
        foreach ($name in @("uv", "uvx")) {
            $cmd = Get-Command $name -ErrorAction SilentlyContinue
            if ($cmd -and $cmd.Source) {
                $candidateDirs += (Split-Path -Parent $cmd.Source)
            }
        }
        $candidateDirs += @(
            (Join-Path $env:USERPROFILE ".local\bin"),
            (Join-Path $env:LOCALAPPDATA "Programs\uv"),
            (Join-Path $env:LOCALAPPDATA "VRCForge\tools\uv"),
            "C:\Program Files\VRCForge\tools\uv"
        )
        foreach ($dir in ($candidateDirs | Select-Object -Unique)) {
            if ([string]::IsNullOrWhiteSpace($dir)) { continue }
            $u = Join-Path $dir "uv.exe"
            $x = Join-Path $dir "uvx.exe"
            if ((Test-Path -LiteralPath $u) -and (Test-Path -LiteralPath $x)) {
                New-Item -ItemType Directory -Force -Path $uvSeedDir | Out-Null
                Copy-Item -LiteralPath $u -Destination $uvSeed -Force
                Copy-Item -LiteralPath $x -Destination $uvxSeed -Force
                Write-Host "Seeded bundled uv runtime from: $dir"
                $seeded = $true
                break
            }
        }
        if (-not $seeded) {
            # Last resort: pull uv.exe/uvx.exe out of a previous payload zip.
            $releaseDir = Join-Path $repoRoot "dist\release"
            $zip = Get-ChildItem -LiteralPath $releaseDir -Filter "VRCForge_Windows_x64_*.zip" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($zip) {
                $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcforge_uvseed_" + [System.Guid]::NewGuid().ToString("N"))
                New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
                try {
                    Expand-Archive -LiteralPath $zip.FullName -DestinationPath $tempRoot -Force
                    $u = Get-ChildItem -LiteralPath $tempRoot -Recurse -Filter uv.exe | Select-Object -First 1
                    $x = Get-ChildItem -LiteralPath $tempRoot -Recurse -Filter uvx.exe | Select-Object -First 1
                    if ($u -and $x) {
                        New-Item -ItemType Directory -Force -Path $uvSeedDir | Out-Null
                        Copy-Item -LiteralPath $u.FullName -Destination $uvSeed -Force
                        Copy-Item -LiteralPath $x.FullName -Destination $uvxSeed -Force
                        Write-Host "Seeded bundled uv runtime from previous payload zip: $($zip.Name)"
                        $seeded = $true
                    }
                } finally {
                    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
        if (-not $seeded) {
            Write-Warning ("uv.exe/uvx.exe not found locally. build_release.ps1 will try to download them " +
                "from github.com and may fail offline. Install uv (winget install astral-sh.uv) " +
                "or place uv.exe + uvx.exe in tools\uv, then re-run.")
        }
    }

    if ([string]::IsNullOrWhiteSpace($PayloadDownloadUrl)) {
        $PayloadDownloadUrl = "https://github.com/ayyitong888/VRCForge/releases/download/v$Version/VRCForge_Windows_x64_$Version.zip"
        Write-Host "PayloadDownloadUrl (baked into the web installer only): $PayloadDownloadUrl"
    }

    $buildParams = @{
        Configuration      = $Configuration
        Version            = $Version
        PayloadDownloadUrl = $PayloadDownloadUrl
        AllowUnpushed      = $true
        AllowVersionMismatch = $true
    }
    if (-not $StrictDirty) {
        $buildParams.AllowDirty = $true
    }
    & (Join-Path $PSScriptRoot "build_release.ps1") @buildParams

    Write-Host ""
    Write-Host "=== LOCAL build done. Artifacts: dist\release ===" -ForegroundColor Yellow
    Write-Host "Reminder: this build was made from an unpushed HEAD. Push before publishing it anywhere." -ForegroundColor Yellow
} finally {
    Pop-Location
}
