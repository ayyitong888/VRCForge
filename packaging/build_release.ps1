param(
    [string]$Configuration = "Release",
    [string]$Version = "",
    [string]$CoplayDevPackagePath = "third_party\com.coplaydev.unity-mcp",
    [string]$UvRuntimeLicensePath = "third_party\uv-runtime",
    [string]$UnityPackagePath = "",
    [string]$PayloadDownloadUrl = "",
    [string]$UvDownloadUrl = "https://github.com/astral-sh/uv/releases/download/0.9.17/uv-x86_64-pc-windows-msvc.zip",
    [string]$UvDownloadSha256 = "",
    [switch]$AllowDirty,
    [switch]$AllowUnpushed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$uvDownloadUrl = $UvDownloadUrl

function Resolve-DotNetExe {
    $command = Get-Command dotnet -ErrorAction SilentlyContinue
    $candidates = @()
    if ($command) {
        $candidates += $command.Source
    }
    $candidates += (Join-Path $env:LOCALAPPDATA "Microsoft\dotnet\dotnet.exe")

    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $sdks = & $candidate --list-sdks
        if ($sdks) {
            return $candidate
        }
    }

    throw ".NET SDK 8.0+ is required to build VRCForge.exe. Only the runtime is not enough."
}

function Resolve-NpmExe {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidate = Join-Path $env:ProgramFiles "nodejs\npm.cmd"
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }

    throw "Node.js/npm is required to build the Tauri desktop app."
}

function Resolve-CargoExe {
    $command = Get-Command cargo.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidate = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }

    throw "Rust cargo is required to build the Tauri desktop app."
}

function Resolve-MakeNsisExe {
    $command = Get-Command makensis -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $localNsisRoot = Join-Path $env:LOCALAPPDATA "Programs\NSIS"
    if (Test-Path -LiteralPath $localNsisRoot) {
        $candidate = Get-ChildItem -LiteralPath $localNsisRoot -Recurse -Filter makensis.exe -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }

    throw "NSIS makensis.exe is required to build VRCForge_Web_Installer_x64.exe and VRCForge_Offline_Installer_x64.exe."
}

function Build-TauriDesktopApp {
    param(
        [string]$DestinationExe
    )

    $npmExe = Resolve-NpmExe
    $cargoExe = Resolve-CargoExe
    $cargoDir = Split-Path -Parent $cargoExe
    if ($env:PATH -notlike "*$cargoDir*") {
        $env:PATH = "$cargoDir;$env:PATH"
    }

    Write-Host "Installing Tauri frontend dependencies..."
    & $npmExe ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed."
    }

    Write-Host "Building Tauri desktop app..."
    & $npmExe run tauri:build
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri desktop build failed."
    }

    $tauriExe = Join-Path $repoRoot "src-tauri\target\release\vrcforge-agentic-app.exe"
    if (-not (Test-Path -LiteralPath $tauriExe)) {
        throw "Tauri build did not produce expected exe: $tauriExe"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DestinationExe) | Out-Null
    Copy-Item -LiteralPath $tauriExe -Destination $DestinationExe -Force
}

function Install-UvRuntime {
    param(
        [string]$DestinationDir
    )

    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    $uvPath = Join-Path $DestinationDir "uv.exe"
    $uvxPath = Join-Path $DestinationDir "uvx.exe"
    if ((Test-Path -LiteralPath $uvPath) -and (Test-Path -LiteralPath $uvxPath)) {
        Write-Host "Bundled uv runtime already present: $DestinationDir"
        return
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcforge_uv_" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    try {
        $zipPath = Join-Path $tempRoot "uv-x86_64-pc-windows-msvc.zip"
        Write-Host "Downloading uv Windows x64 runtime: $uvDownloadUrl"
        Invoke-WebRequest -Uri $uvDownloadUrl -OutFile $zipPath
        if (-not [string]::IsNullOrWhiteSpace($UvDownloadSha256)) {
            $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
            if ($actualHash -ne $UvDownloadSha256.ToLowerInvariant()) {
                throw "uv runtime SHA256 mismatch. expected=$UvDownloadSha256 actual=$actualHash"
            }
        } else {
            Write-Warning "UvDownloadSha256 was not provided; uv archive integrity is not pinned."
        }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $tempRoot -Force

        $extractedUv = Get-ChildItem -LiteralPath $tempRoot -Recurse -Filter uv.exe | Select-Object -First 1
        $extractedUvx = Get-ChildItem -LiteralPath $tempRoot -Recurse -Filter uvx.exe | Select-Object -First 1
        if (-not $extractedUv -or -not $extractedUvx) {
            throw "Downloaded uv archive did not contain uv.exe and uvx.exe."
        }

        Copy-Item -LiteralPath $extractedUv.FullName -Destination $uvPath -Force
        Copy-Item -LiteralPath $extractedUvx.FullName -Destination $uvxPath -Force
        Write-Host "Bundled uv runtime copied to: $DestinationDir"
    } finally {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Push-Location $repoRoot
try {
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    }

    git fetch origin --tags --prune | Out-Null

    $status = git status --short
    if ($status -and -not $AllowDirty) {
        Write-Error "Working tree has uncommitted changes. Commit or stash before packaging.`n$status"
        exit 1
    }

    $unpushed = git log origin/main..HEAD --oneline
    if ($unpushed -and -not $AllowUnpushed) {
        Write-Error "Local HEAD contains commits not on origin/main. Push first before packaging.`n$unpushed"
        exit 1
    }

    $remoteVersion = (git show origin/main:VERSION).Trim()
    if ($remoteVersion -ne $Version) {
        throw "Installer version must match GitHub latest VERSION. local=$Version origin/main=$remoteVersion"
    }

    $dotnetExe = Resolve-DotNetExe
    $nsisExe = Resolve-MakeNsisExe

    if ([string]::IsNullOrWhiteSpace($PayloadDownloadUrl)) {
        throw "PayloadDownloadUrl is required for VRCForge_Web_Installer_x64.exe."
    }

    & .\packaging\check_third_party_licenses.ps1
    & .\packaging\check_coplaydev_mcp_license.ps1 -PackagePath $CoplayDevPackagePath

    $payloadRoot = Join-Path $repoRoot "dist\VRCForge_Windows_x64"
    $releaseRoot = Join-Path $repoRoot "dist\release"
    Remove-Item -LiteralPath $payloadRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $payloadRoot | Out-Null
    Build-TauriDesktopApp -DestinationExe (Join-Path $payloadRoot "VRCForge.exe")
    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

    $legacyLauncherBuildRoot = Join-Path $repoRoot "dist\legacy-launcher-build"
    Remove-Item -LiteralPath $legacyLauncherBuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $legacyLauncherBuildRoot | Out-Null

    & $dotnetExe publish .\launcher\VRCForge.Launcher\VRCForge.Launcher.csproj `
        -c $Configuration `
        -r win-x64 `
        -p:Platform=x64 `
        -p:Version=$Version `
        -p:DebugType=none `
        -p:DebugSymbols=false `
        -o $legacyLauncherBuildRoot
    if ($LASTEXITCODE -ne 0) {
        throw "dotnet publish failed."
    }
    Remove-Item -LiteralPath (Join-Path $legacyLauncherBuildRoot "VRCForge.pdb") -Force -ErrorAction SilentlyContinue

    & .\packaging\build_backend.ps1 -OutputDir (Join-Path $payloadRoot "backend")

    Copy-Item -LiteralPath .\VERSION -Destination (Join-Path $payloadRoot "VERSION") -Force
    Copy-Item -LiteralPath .\dashboard -Destination (Join-Path $payloadRoot "dashboard") -Recurse -Force
    Copy-Item -LiteralPath .\tools -Destination (Join-Path $payloadRoot "tools") -Recurse -Force
    Get-ChildItem -LiteralPath (Join-Path $payloadRoot "tools") -Recurse -Filter "*.ps1" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    $legacyLauncherPayloadRoot = Join-Path $payloadRoot "tools\legacy-launcher"
    New-Item -ItemType Directory -Force -Path $legacyLauncherPayloadRoot | Out-Null
    Copy-Item -Path (Join-Path $legacyLauncherBuildRoot "*") -Destination $legacyLauncherPayloadRoot -Recurse -Force
    Copy-Item -LiteralPath .\start_dashboard.cmd -Destination (Join-Path $payloadRoot "start_dashboard.cmd") -Force
    Install-UvRuntime -DestinationDir (Join-Path $payloadRoot "tools\uv")
    New-Item -ItemType Directory -Force -Path (Join-Path $payloadRoot "config"),(Join-Path $payloadRoot "logs"),(Join-Path $payloadRoot "artifacts") | Out-Null

    $unityPluginRoot = Join-Path $payloadRoot "unity_plugin"
    New-Item -ItemType Directory -Force -Path (Join-Path $unityPluginRoot "Assets\VRCForge"),(Join-Path $unityPluginRoot "Packages") | Out-Null
    Copy-Item -LiteralPath .\Assets\VRCForge\Editor -Destination (Join-Path $unityPluginRoot "Assets\VRCForge\Editor") -Recurse -Force
    Copy-Item -LiteralPath $CoplayDevPackagePath -Destination (Join-Path $unityPluginRoot "Packages\com.coplaydev.unity-mcp") -Recurse -Force
    $unityMcpPayloadRoot = Join-Path $unityPluginRoot "Packages\com.coplaydev.unity-mcp"
    $vrcforgeExcludedUnityMcpFiles = @(
        "Editor\Setup\RoslynInstaller.cs",
        "Editor\Tools\ExecuteCode.cs"
    )
    foreach ($relativePath in $vrcforgeExcludedUnityMcpFiles) {
        Remove-Item -LiteralPath (Join-Path $unityMcpPayloadRoot $relativePath) -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $unityMcpPayloadRoot "$relativePath.meta") -Force -ErrorAction SilentlyContinue
    }

    if ([string]::IsNullOrWhiteSpace($UnityPackagePath)) {
        $UnityPackagePath = Join-Path $releaseRoot "VRCForge.unitypackage"
        & .\packaging\build_unitypackage.ps1 -SourceAssetsPath "Assets\VRCForge" -OutputPath $UnityPackagePath
    }
    if (-not (Test-Path -LiteralPath $UnityPackagePath)) {
        throw "VRCForge.unitypackage is required for manual fallback. Provide -UnityPackagePath or let build_unitypackage.ps1 generate it."
    }
    $releaseUnityPackage = Join-Path $releaseRoot "VRCForge.unitypackage"
    if ([System.IO.Path]::GetFullPath($UnityPackagePath) -ne [System.IO.Path]::GetFullPath($releaseUnityPackage)) {
        Copy-Item -LiteralPath $UnityPackagePath -Destination $releaseUnityPackage -Force
        $UnityPackagePath = $releaseUnityPackage
    }
    Copy-Item -LiteralPath $UnityPackagePath -Destination (Join-Path $unityPluginRoot "VRCForge.unitypackage") -Force

    New-Item -ItemType Directory -Force -Path (Join-Path $payloadRoot "licenses") | Out-Null
    Copy-Item -LiteralPath .\LICENSE -Destination (Join-Path $payloadRoot "licenses\VRCForge-GPL-3.0.txt") -Force
    Copy-Item -LiteralPath .\NOTICE -Destination (Join-Path $payloadRoot "licenses\VRCForge-NOTICE.txt") -Force
    $coplayLicense = Get-ChildItem -LiteralPath $CoplayDevPackagePath -File |
        Where-Object { $_.Name -match "^(LICENSE|COPYING)" } |
        Select-Object -First 1
    if ($coplayLicense) {
        Copy-Item -LiteralPath $coplayLicense.FullName -Destination (Join-Path $payloadRoot "licenses\CoplayDev-Unity-MCP-LICENSE.txt") -Force
    }
    $coplayDistributionNotes = Join-Path $CoplayDevPackagePath "VRCFORGE_DISTRIBUTION_NOTES.txt"
    if (Test-Path -LiteralPath $coplayDistributionNotes) {
        Copy-Item -LiteralPath $coplayDistributionNotes -Destination (Join-Path $payloadRoot "licenses\CoplayDev-Unity-MCP-DISTRIBUTION-NOTES.txt") -Force
    }
    $resolvedUvLicensePath = if ([System.IO.Path]::IsPathRooted($UvRuntimeLicensePath)) {
        $UvRuntimeLicensePath
    } else {
        Join-Path $repoRoot $UvRuntimeLicensePath
    }
    Copy-Item -LiteralPath (Join-Path $resolvedUvLicensePath "LICENSE-MIT") -Destination (Join-Path $payloadRoot "licenses\uv-LICENSE-MIT.txt") -Force
    Copy-Item -LiteralPath (Join-Path $resolvedUvLicensePath "LICENSE-APACHE") -Destination (Join-Path $payloadRoot "licenses\uv-LICENSE-APACHE-2.0.txt") -Force
    Copy-Item -LiteralPath (Join-Path $resolvedUvLicensePath "VRCFORGE_DISTRIBUTION_NOTES.txt") -Destination (Join-Path $payloadRoot "licenses\uv-DISTRIBUTION-NOTES.txt") -Force

    $payloadZip = Join-Path $releaseRoot "VRCForge_Windows_x64_$Version.zip"
    Remove-Item -LiteralPath $payloadZip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $payloadRoot "*") -DestinationPath $payloadZip -Force
    $payloadSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $payloadZip).Hash.ToLowerInvariant()

    $offlineInstaller = Join-Path $releaseRoot "VRCForge_Offline_Installer_x64.exe"
    $webInstaller = Join-Path $releaseRoot "VRCForge_Web_Installer_x64.exe"

    & $nsisExe "/DVERSION=$Version" "/DPAYLOAD_DIR=$payloadRoot" "/DOUTFILE=$offlineInstaller" .\installer\VRCForge_Offline_Installer_x64.nsi
    if ($LASTEXITCODE -ne 0) {
        throw "Offline NSIS build failed."
    }

    & $nsisExe "/DVERSION=$Version" "/DDOWNLOAD_URL=$PayloadDownloadUrl" "/DPAYLOAD_SHA256=$payloadSha256" "/DOUTFILE=$webInstaller" .\installer\VRCForge_Web_Installer_x64.nsi
    if ($LASTEXITCODE -ne 0) {
        throw "Web NSIS build failed."
    }

    $manifest = [ordered]@{
        version = $Version
        commit = (git rev-parse HEAD).Trim()
        uvDownloadUrl = $uvDownloadUrl
        uvDownloadSha256 = $UvDownloadSha256
        artifacts = @(
            @{ name = [System.IO.Path]::GetFileName($UnityPackagePath); sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $UnityPackagePath).Hash.ToLowerInvariant() },
            @{ name = [System.IO.Path]::GetFileName($payloadZip); sha256 = $payloadSha256 },
            @{ name = [System.IO.Path]::GetFileName($offlineInstaller); sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $offlineInstaller).Hash.ToLowerInvariant() },
            @{ name = [System.IO.Path]::GetFileName($webInstaller); sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $webInstaller).Hash.ToLowerInvariant() }
        )
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $releaseRoot "release-manifest.json") -Encoding UTF8

    Write-Host "Release payload built: $payloadRoot"
    Write-Host "Release artifacts: $releaseRoot"
} finally {
    Pop-Location
}
