param(
    [Parameter(Mandatory = $true)][string]$AppDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$Version = "",
    [string]$PayloadDownloadUrl = "",
    [string]$PythonExe = "python",
    [string]$MetadataPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Version) { $Version = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim() }
if (-not $PayloadDownloadUrl) { $PayloadDownloadUrl = "https://github.com/ayyitong888/VRCForge/releases/download/v$Version/VRCForge_Windows_x64_$Version.tar.zst" }
$appRoot = [IO.Path]::GetFullPath($AppDirectory)
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$manifestPath = Join-Path $PSScriptRoot "native\Cargo.toml"
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

Push-Location $repoRoot
try {
    & npx.cmd tsc --project setup/tsconfig.json
    if ($LASTEXITCODE -ne 0) { throw "Setup UI type check failed." }
    & npx.cmd vite build --config setup/vite.config.ts
    if ($LASTEXITCODE -ne 0) { throw "Setup UI build failed." }
    # The carousel uses runtime image paths, so Vite cannot discover these files.
    $assetDestination = Join-Path $PSScriptRoot "dist\setup\ui\assets"
    New-Item -ItemType Directory -Force -Path $assetDestination | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "ui\assets") -Filter *.png -File |
        Copy-Item -Destination $assetDestination -Force
    & cargo.exe build --release --locked --manifest-path $manifestPath
    if ($LASTEXITCODE -ne 0) { throw "Native setup build failed." }

    # Preserve licenses from the actual Windows dependency graph in the payload.
    $cargoMetadata = & cargo.exe metadata --locked --format-version 1 --filter-platform x86_64-pc-windows-msvc --manifest-path $manifestPath
    if ($LASTEXITCODE -ne 0) { throw "Unable to read setup dependency licenses." }
    $dependencies = $cargoMetadata | ConvertFrom-Json
    $licenseRoot = Join-Path $appRoot "licenses\setup"
    New-Item -ItemType Directory -Force -Path $licenseRoot | Out-Null
    $notices = @("# VRCForge Windows Setup dependencies", "", "These components are compiled into the setup and uninstaller. Their upstream license texts are retained alongside this file.", "")
    foreach ($package in $dependencies.packages | Where-Object { $_.source }) {
        $notices += "- $($package.name) $($package.version): $($package.license)"
        $packageRoot = Split-Path -Parent $package.manifest_path
        $texts = @(Get-ChildItem -LiteralPath $packageRoot -File | Where-Object { $_.Name -match '^(LICENSE|LICENCE|COPYING|NOTICE)(\.|-|$)' })
        if ($texts.Count -gt 0) {
            $destination = Join-Path $licenseRoot "$($package.name)-$($package.version)"
            New-Item -ItemType Directory -Force -Path $destination | Out-Null
            $texts | Copy-Item -Destination $destination -Force
        }
    }
    [IO.File]::WriteAllText((Join-Path $licenseRoot "THIRD_PARTY_NOTICES.md"), ($notices -join "`n"), [Text.UTF8Encoding]::new($false))

    $offline = Join-Path $outputRoot "VRCForge_Offline_Installer_x64.exe"
    $web = Join-Path $outputRoot "VRCForge_Web_Installer_x64.exe"
    $payload = Join-Path $outputRoot "VRCForge_Windows_x64_$Version.tar.zst"
    foreach ($output in @($offline, $web, $payload)) {
        if (Test-Path -LiteralPath $output -PathType Leaf) { Remove-Item -LiteralPath $output -Force }
    }
    $metadata = & $PythonExe (Join-Path $PSScriptRoot "build_payload.py") `
        --app-dir $appRoot --setup-exe (Join-Path $PSScriptRoot "native\target\release\vrcforge-setup.exe") `
        --output $offline --payload-output $payload --web-output $web --web-url $PayloadDownloadUrl
    if ($LASTEXITCODE -ne 0) { throw "Setup payload packaging failed." }
    if ($MetadataPath) { [IO.File]::WriteAllText([IO.Path]::GetFullPath($MetadataPath), ($metadata -join "`n"), [Text.UTF8Encoding]::new($false)) }
    Write-Host "Built self-drawn offline/web setup and zstd payload for VRCForge $Version."
} finally {
    Pop-Location
}
