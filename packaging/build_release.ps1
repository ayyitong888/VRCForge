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
    [switch]$AllowUnpushed,
    [switch]$AllowVersionMismatch,
    [switch]$StrictEvidence
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$uvDownloadUrl = $UvDownloadUrl
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

function Resolve-SafeRepositoryPath {
    param(
        [string]$Path
    )

    $repositoryPath = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd([char[]]"\/")
    $candidatePath = [System.IO.Path]::GetFullPath($Path)
    $repositoryPrefix = $repositoryPath + [System.IO.Path]::DirectorySeparatorChar
    $isRepositoryRoot = [string]::Equals(
        $candidatePath,
        $repositoryPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if (
        -not $isRepositoryRoot -and
        -not $candidatePath.StartsWith(
            $repositoryPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Output path must stay inside the repository: $candidatePath"
    }

    $currentPath = $candidatePath
    while ($true) {
        $currentItem = Get-Item -LiteralPath $currentPath -Force -ErrorAction SilentlyContinue
        if (
            $null -ne $currentItem -and
            ($currentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Repository output path cannot traverse a reparse point: $currentPath"
        }
        if ([string]::Equals(
            $currentPath,
            $repositoryPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            break
        }
        $parentPath = [System.IO.Path]::GetDirectoryName($currentPath)
        if ([string]::IsNullOrWhiteSpace($parentPath) -or $parentPath -eq $currentPath) {
            throw "Repository output path ancestry is invalid: $candidatePath"
        }
        $currentPath = $parentPath
    }
    return $candidatePath
}

function New-SafeRepositoryDirectory {
    param(
        [string]$Path
    )

    $resolvedPath = Resolve-SafeRepositoryPath -Path $Path
    if (Get-Item -LiteralPath $resolvedPath -Force -ErrorAction SilentlyContinue) {
        throw "Repository output directory already exists: $resolvedPath"
    }
    $parentPath = [System.IO.Path]::GetDirectoryName($resolvedPath)
    if (-not (Test-Path -LiteralPath $parentPath -PathType Container)) {
        if (Get-Item -LiteralPath $parentPath -Force -ErrorAction SilentlyContinue) {
            throw "Repository output parent is not a directory: $parentPath"
        }
        $null = New-SafeRepositoryDirectory -Path $parentPath
    } else {
        $null = Resolve-SafeRepositoryPath -Path $parentPath
    }
    New-Item -ItemType Directory -Path $resolvedPath -ErrorAction Stop | Out-Null
    $createdItem = Get-Item -LiteralPath $resolvedPath -Force -ErrorAction Stop
    if (
        -not $createdItem.PSIsContainer -or
        ($createdItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Repository output directory is not a regular directory: $resolvedPath"
    }
    $null = Resolve-SafeRepositoryPath -Path $resolvedPath
    return $resolvedPath
}

function Copy-SafeRepositoryFileCreateNew {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    $resolvedSource = Resolve-SafeRepositoryPath -Path $SourcePath
    $sourceItem = Get-Item -LiteralPath $resolvedSource -Force -ErrorAction Stop
    if (
        $sourceItem.PSIsContainer -or
        ($sourceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Repository copy source must be a regular file: $resolvedSource"
    }

    $resolvedDestination = Resolve-SafeRepositoryPath -Path $DestinationPath
    if (Get-Item -LiteralPath $resolvedDestination -Force -ErrorAction SilentlyContinue) {
        throw "Repository output file already exists: $resolvedDestination"
    }
    $destinationParent = [System.IO.Path]::GetDirectoryName($resolvedDestination)
    if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
        $null = New-SafeRepositoryDirectory -Path $destinationParent
    } else {
        $null = Resolve-SafeRepositoryPath -Path $destinationParent
    }

    $sourceStream = $null
    $destinationStream = $null
    try {
        $sourceStream = [System.IO.FileStream]::new(
            $resolvedSource,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $destinationStream = [System.IO.FileStream]::new(
            $resolvedDestination,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $sourceStream.CopyTo($destinationStream)
        $destinationStream.Flush($true)
        $sourceStream.Position = 0
        $sourceDigest = Get-StreamSha256 -Stream $sourceStream
        $destinationStream.Position = 0
        $destinationDigest = Get-StreamSha256 -Stream $destinationStream
        $destinationLength = $destinationStream.Length
        if ($destinationDigest -ne $sourceDigest) {
            throw "Repository output copy digest mismatch: $resolvedDestination"
        }
    } finally {
        if ($null -ne $destinationStream) {
            $destinationStream.Dispose()
        }
        if ($null -ne $sourceStream) {
            $sourceStream.Dispose()
        }
    }

    $null = Resolve-SafeRepositoryPath -Path $resolvedDestination
    $destinationItem = Get-Item -LiteralPath $resolvedDestination -Force -ErrorAction Stop
    if (
        $destinationItem.PSIsContainer -or
        ($destinationItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $destinationItem.Length -ne $destinationLength
    ) {
        throw "Repository output file identity changed after copy: $resolvedDestination"
    }
    $verificationStream = $null
    try {
        $verificationStream = [System.IO.FileStream]::new(
            $resolvedDestination,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $verifiedDigest = Get-StreamSha256 -Stream $verificationStream
        if ($verifiedDigest -ne $destinationDigest) {
            throw "Repository output file changed after copy: $resolvedDestination"
        }
    } finally {
        if ($null -ne $verificationStream) {
            $verificationStream.Dispose()
        }
    }
    return [pscustomobject]@{
        path = $resolvedDestination
        sha256 = $verifiedDigest
        length = $destinationLength
    }
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
    $null = Copy-SafeRepositoryFileCreateNew `
        -SourcePath $tauriExe `
        -DestinationPath $DestinationExe
}

function Install-UvRuntime {
    param(
        [string]$DestinationDir,
        [bool]$RequireVerifiedDownload
    )

    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    $uvPath = Join-Path $DestinationDir "uv.exe"
    $uvxPath = Join-Path $DestinationDir "uvx.exe"
    if (
        -not $RequireVerifiedDownload -and
        (Test-Path -LiteralPath $uvPath) -and
        (Test-Path -LiteralPath $uvxPath)
    ) {
        Write-Host "Bundled uv runtime already present: $DestinationDir"
        return [pscustomobject]@{
            source = "preseed"
            downloadUrl = $null
            archiveSha256 = $null
            archiveDigestVerified = $false
            files = @(
                [pscustomobject]@{ name = "uv.exe"; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $uvPath).Hash.ToLowerInvariant() }
                [pscustomobject]@{ name = "uvx.exe"; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $uvxPath).Hash.ToLowerInvariant() }
            )
        }
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcforge_uv_" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    try {
        $zipPath = Join-Path $tempRoot "uv-x86_64-pc-windows-msvc.zip"
        Write-Host "Downloading uv Windows x64 runtime: $uvDownloadUrl"
        Invoke-WebRequest -UseBasicParsing -Uri $uvDownloadUrl -OutFile $zipPath
        $verifiedUvPath = Join-Path $tempRoot "verified-uv.exe"
        $verifiedUvxPath = Join-Path $tempRoot "verified-uvx.exe"
        $archiveSha256 = ""
        $archiveDigestVerified = $false
        $archiveStream = $null
        $archive = $null
        try {
            # Hold the exact downloaded bytes against writes/deletes from digest
            # verification through entry extraction. Opening the path again after
            # hashing would leave a replacement race in the release supply chain.
            $archiveStream = [System.IO.File]::Open(
                $zipPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $archiveSha256 = Get-StreamSha256 -Stream $archiveStream
            $archiveStream.Position = 0
            if ($UvDownloadSha256 -match "^[0-9a-fA-F]{64}$") {
                if ($archiveSha256 -ne $UvDownloadSha256.ToLowerInvariant()) {
                    throw "uv runtime SHA256 mismatch. expected=$UvDownloadSha256 actual=$archiveSha256"
                }
                $archiveDigestVerified = $true
            } else {
                Write-Warning "UvDownloadSha256 was not provided; uv archive integrity is not pinned."
            }
            if ($RequireVerifiedDownload -and -not $archiveDigestVerified) {
                throw "Strict release build requires a verified uv archive download."
            }

            Add-Type -AssemblyName System.IO.Compression
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [System.IO.Compression.ZipArchive]::new(
                $archiveStream,
                [System.IO.Compression.ZipArchiveMode]::Read,
                $true
            )
            $uvEntries = @($archive.Entries | Where-Object { -not [string]::IsNullOrEmpty($_.Name) -and $_.Name -ieq "uv.exe" })
            $uvxEntries = @($archive.Entries | Where-Object { -not [string]::IsNullOrEmpty($_.Name) -and $_.Name -ieq "uvx.exe" })
            if ($uvEntries.Count -ne 1 -or $uvxEntries.Count -ne 1) {
                throw "Downloaded uv archive must contain exactly one uv.exe and exactly one uvx.exe."
            }
            foreach ($entryTarget in @(
                @{ Entry = $uvEntries[0]; Path = $verifiedUvPath },
                @{ Entry = $uvxEntries[0]; Path = $verifiedUvxPath }
            )) {
                $entryStream = $null
                $outputStream = $null
                try {
                    $entryStream = $entryTarget.Entry.Open()
                    $outputStream = [System.IO.File]::Open(
                        $entryTarget.Path,
                        [System.IO.FileMode]::CreateNew,
                        [System.IO.FileAccess]::Write,
                        [System.IO.FileShare]::None
                    )
                    $entryStream.CopyTo($outputStream)
                } finally {
                    if ($null -ne $outputStream) {
                        $outputStream.Dispose()
                    }
                    if ($null -ne $entryStream) {
                        $entryStream.Dispose()
                    }
                }
            }
        } finally {
            if ($null -ne $archive) {
                $archive.Dispose()
            }
            if ($null -ne $archiveStream) {
                $archiveStream.Dispose()
            }
        }

        $verifiedUvSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $verifiedUvPath).Hash.ToLowerInvariant()
        $verifiedUvxSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $verifiedUvxPath).Hash.ToLowerInvariant()

        if (Test-Path -LiteralPath $DestinationDir) {
            Remove-Item -LiteralPath $DestinationDir -Recurse -Force -ErrorAction Stop
        }
        New-Item -ItemType Directory -Path $DestinationDir -ErrorAction Stop | Out-Null
        Copy-Item -LiteralPath $verifiedUvPath -Destination $uvPath -ErrorAction Stop
        Copy-Item -LiteralPath $verifiedUvxPath -Destination $uvxPath -ErrorAction Stop
        $installedUvEntries = @(Get-ChildItem -LiteralPath $DestinationDir -Force -ErrorAction Stop)
        $installedUvNames = @($installedUvEntries | ForEach-Object { $_.Name })
        if (
            $installedUvEntries.Count -ne 2 -or
            @($installedUvNames | Sort-Object -Unique).Count -ne 2 -or
            @(Compare-Object -ReferenceObject @("uv.exe", "uvx.exe") -DifferenceObject $installedUvNames -CaseSensitive).Count -ne 0 -or
            @($installedUvEntries | Where-Object {
                $_.PSIsContainer -or ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
            }).Count -ne 0
        ) {
            throw "Installed uv runtime directory must contain exactly regular uv.exe and uvx.exe files."
        }
        $installedUvSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $uvPath).Hash.ToLowerInvariant()
        $installedUvxSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $uvxPath).Hash.ToLowerInvariant()
        if ($installedUvSha256 -ne $verifiedUvSha256 -or $installedUvxSha256 -ne $verifiedUvxSha256) {
            throw "Installed uv runtime digests differ from the verified archive entries."
        }
        Write-Host "Bundled uv runtime copied to: $DestinationDir"
        return [pscustomobject]@{
            source = "download"
            downloadUrl = $uvDownloadUrl
            archiveSha256 = $archiveSha256
            archiveDigestVerified = $archiveDigestVerified
            files = @(
                [pscustomobject]@{ name = "uv.exe"; sha256 = $installedUvSha256 }
                [pscustomobject]@{ name = "uvx.exe"; sha256 = $installedUvxSha256 }
            )
        }
    } finally {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$releaseOperationMutex = $null
$releaseOperationMutexOwned = $false
$locationPushed = $false
try {
    $releaseOperationMutex = Enter-ReleaseOperationMutex
    $releaseOperationMutexOwned = $true
    Push-Location $repoRoot
    $locationPushed = $true

    if ($StrictEvidence -and ($AllowDirty -or $AllowUnpushed -or $AllowVersionMismatch)) {
        throw "Strict evidence builds cannot use local-acceptance relaxation switches."
    }
    $strictSourceBuild = -not ($AllowDirty -or $AllowUnpushed -or $AllowVersionMismatch)
    $strictEvidenceBuild = $strictSourceBuild -and [bool]$StrictEvidence
    $strictReleaseBuild = $strictSourceBuild -and -not $strictEvidenceBuild
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    }
    $sourceVersion = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    if ($sourceVersion -ne $Version) {
        throw "Package version must match the source VERSION file. requested=$Version source=$sourceVersion"
    }
    if ($UvDownloadSha256 -notmatch "^[0-9a-fA-F]{64}$") {
        if ($strictSourceBuild) {
            throw "Strict release build requires a pinned 64-hex UvDownloadSha256."
        }
        Write-Warning "LOCAL acceptance build: uv download SHA-256 is not pinned; artifacts remain non-publishable."
    }

    git fetch origin --tags --prune | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ($strictSourceBuild) {
            throw "Strict release build requires a successful git fetch of origin."
        }
        Write-Warning "LOCAL acceptance build: git fetch failed; using the cached origin/main ref."
    }

    $status = git status --short
    if ($status -and -not $AllowDirty) {
        throw "Working tree has uncommitted changes. Commit or stash before packaging.`n$status"
    }

    $unpushed = git log origin/main..HEAD --oneline
    if ($unpushed -and -not $AllowUnpushed) {
        throw "Local HEAD contains commits not on origin/main. Push first before packaging.`n$unpushed"
    }

    $headCommit = (git rev-parse HEAD).Trim()
    $originMainCommit = (git rev-parse origin/main).Trim()
    if ($strictSourceBuild -and $headCommit -ne $originMainCommit) {
        throw "Strict release build requires HEAD to equal origin/main. HEAD=$headCommit origin/main=$originMainCommit"
    }

    $remoteVersion = (git show origin/main:VERSION).Trim()
    if ($remoteVersion -ne $Version) {
        if (-not ($AllowUnpushed -and $AllowVersionMismatch)) {
            throw "Installer version must match GitHub latest VERSION. local=$Version origin/main=$remoteVersion"
        }
        Write-Warning (
            "LOCAL acceptance build only: VERSION ($Version) differs from origin/main ($remoteVersion). " +
            "Do not publish these artifacts; a strict build still requires the version commit on origin/main."
        )
    }

    $dotnetExe = Resolve-DotNetExe
    $nsisExe = Resolve-MakeNsisExe

    if ([string]::IsNullOrWhiteSpace($PayloadDownloadUrl)) {
        throw "PayloadDownloadUrl is required for VRCForge_Web_Installer_x64.exe."
    }

    & .\packaging\check_third_party_licenses.ps1
    & .\packaging\check_coplaydev_mcp_license.ps1 -PackagePath $CoplayDevPackagePath

    if ($strictEvidenceBuild) {
        $evidenceRunId = "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([Guid]::NewGuid().ToString('N'))"
        $evidenceBuildRoot = Join-Path $repoRoot "dist\evidence\$headCommit\$evidenceRunId"
        $evidenceBuildRoot = New-SafeRepositoryDirectory -Path $evidenceBuildRoot
        $payloadRoot = Join-Path $evidenceBuildRoot "VRCForge_Windows_x64"
        $releaseRoot = Join-Path $evidenceBuildRoot "release"
        $payloadRoot = New-SafeRepositoryDirectory -Path $payloadRoot
        $releaseRoot = New-SafeRepositoryDirectory -Path $releaseRoot
    } else {
        $payloadRoot = Join-Path $repoRoot "dist\VRCForge_Windows_x64"
        $releaseRoot = Join-Path $repoRoot "dist\release"
        Remove-Item -LiteralPath $payloadRoot -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $payloadRoot | Out-Null
        New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    }
    Build-TauriDesktopApp -DestinationExe (Join-Path $payloadRoot "VRCForge.exe")
    $evidenceAttestor = $null
    $evidenceAuthority = $null
    if ($strictEvidenceBuild) {
        Write-Host "Building external evidence attestor..."
        $cargoExe = Resolve-CargoExe
        & $cargoExe build `
            --manifest-path (Join-Path $repoRoot "src-tauri\Cargo.toml") `
            --release `
            --bin vrcforge_primitive_attestor
        if ($LASTEXITCODE -ne 0) {
            throw "cargo build failed for the external evidence attestor."
        }
        $attestorBuildExe = Join-Path $repoRoot "src-tauri\target\release\vrcforge_primitive_attestor.exe"
        if (-not (Test-Path -LiteralPath $attestorBuildExe -PathType Leaf)) {
            throw "External evidence attestor build did not produce the expected executable."
        }
        $attestorItem = Get-Item -LiteralPath $attestorBuildExe -Force
        if (($attestorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "External evidence attestor build output must be a regular file."
        }
        $evidenceRelativePath = "artifacts/primitive-origin-tools/$headCommit/$evidenceRunId/vrcforge_primitive_attestor.exe"
        $evidenceAttestorPath = Join-Path $repoRoot ($evidenceRelativePath.Replace("/", "\"))
        $attestorCopy = Copy-SafeRepositoryFileCreateNew `
            -SourcePath $attestorBuildExe `
            -DestinationPath $evidenceAttestorPath
        $evidenceAttestorDigest = $attestorCopy.sha256
        $evidenceAttestor = [ordered]@{
            repositoryRelativePath = $evidenceRelativePath
            sha256 = $evidenceAttestorDigest
            trustedBoundaryReady = $false
        }

        Write-Host "Building the non-installing evidence authority diagnostic bundle..."
        $authorityBinNames = @(
            "vrcforge_primitive_evidence_service",
            "vrcforge_primitive_evidence_controller",
            "vrcforge_primitive_evidence_install_helper"
        )
        $authorityBuildArguments = @(
            "build",
            "--manifest-path",
            (Join-Path $repoRoot "src-tauri\Cargo.toml"),
            "--release"
        )
        foreach ($authorityBinName in $authorityBinNames) {
            $authorityBuildArguments += @("--bin", $authorityBinName)
        }
        & $cargoExe @authorityBuildArguments
        if ($LASTEXITCODE -ne 0) {
            throw "cargo build failed for the evidence authority diagnostic bundle."
        }

        $authorityRelativeRoot = "artifacts/primitive-evidence-authority/$headCommit/$evidenceRunId"
        $authorityFiles = @()
        $authorityCopiedPaths = @{}
        $authorityControllerDigest = $null
        foreach ($authorityBinName in $authorityBinNames) {
            $authorityFileName = "$authorityBinName.exe"
            $authorityBuildExe = Join-Path $repoRoot "src-tauri\target\release\$authorityFileName"
            if (-not (Test-Path -LiteralPath $authorityBuildExe -PathType Leaf)) {
                throw "Evidence authority build did not produce $authorityFileName."
            }
            $authorityBuildItem = Get-Item -LiteralPath $authorityBuildExe -Force
            if (($authorityBuildItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Evidence authority build output must be a regular file: $authorityFileName"
            }
            $authorityRelativePath = "$authorityRelativeRoot/$authorityFileName"
            $authorityDestination = Join-Path $repoRoot ($authorityRelativePath.Replace("/", "\"))
            $authorityCopy = Copy-SafeRepositoryFileCreateNew `
                -SourcePath $authorityBuildExe `
                -DestinationPath $authorityDestination
            $authorityCopiedPaths[$authorityBinName] = $authorityCopy.path
            $authorityFiles += [ordered]@{
                name = $authorityFileName
                sha256 = $authorityCopy.sha256
                length = $authorityCopy.length
            }
            if ($authorityBinName -eq "vrcforge_primitive_evidence_controller") {
                $authorityControllerDigest = [string]$authorityCopy.sha256
            }
        }

        $authorityInstallHelperPath = $authorityCopiedPaths["vrcforge_primitive_evidence_install_helper"]
        $authorityPlanLines = @(& $authorityInstallHelperPath --plan)
        if ($LASTEXITCODE -ne 0) {
            throw "Evidence authority install-helper plan was rejected."
        }
        try {
            $authorityPlan = ($authorityPlanLines -join [Environment]::NewLine) | ConvertFrom-Json
        } catch {
            throw "Evidence authority install-helper plan was not valid JSON."
        }
        if (
            $authorityPlan.schema -ne "vrcforge.primitive_evidence_authority_policy.v1" -or
            $authorityPlan.mutationSupported -ne $false -or
            $authorityPlan.trustedBoundaryReady -ne $false -or
            $authorityPlan.candidatePayloadIncludesAuthority -ne $false -or
            $authorityPlan.controllerPathPolicy -ne "sha256-parent-create-new-never-reuse" -or
            @($authorityPlan.blockers).Count -eq 0
        ) {
            throw "Evidence authority install-helper plan violated the fail-closed source policy."
        }
        if ([string]::IsNullOrWhiteSpace($authorityControllerDigest)) {
            throw "Evidence authority controller digest was not captured."
        }
        $authorityControllerFileName = "vrcforge_primitive_evidence_controller.exe"
        $authorityControllerPattern = Join-Path `
            ([string]$authorityPlan.layout.binaryRoot) `
            (Join-Path "{controller-sha256-lower}" $authorityControllerFileName)
        if ([string]$authorityPlan.layout.controllerExecutablePattern -cne $authorityControllerPattern) {
            throw "Evidence authority controller path pattern was not content addressed."
        }
        $authorityControllerInstalledPath = Join-Path `
            ([string]$authorityPlan.layout.binaryRoot) `
            (Join-Path $authorityControllerDigest $authorityControllerFileName)
        $evidenceAuthority = [ordered]@{
            schema = "vrcforge.primitive_evidence_authority_bundle.v1"
            repositoryRelativeRoot = $authorityRelativeRoot
            files = $authorityFiles
            planSchema = [string]$authorityPlan.schema
            installationSupported = $false
            trustedBoundaryReady = $false
            candidatePayloadIncluded = $false
            controller = [ordered]@{
                sha256 = $authorityControllerDigest
                installedPath = $authorityControllerInstalledPath
                installMode = "create-new-never-reuse"
            }
        }
    }

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
    $uvRuntimeProvenance = Install-UvRuntime `
        -DestinationDir (Join-Path $payloadRoot "tools\uv") `
        -RequireVerifiedDownload $strictSourceBuild
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

    $payloadIntegrityManifest = [ordered]@{
        schema = "vrcforge.payload-integrity.v1"
        version = $Version
        files = [ordered]@{
            desktop = [ordered]@{
                relativePath = "VRCForge.exe"
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $payloadRoot "VRCForge.exe")).Hash.ToLowerInvariant()
            }
            backend = [ordered]@{
                relativePath = "backend/vrcforge_backend.exe"
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $payloadRoot "backend\vrcforge_backend.exe")).Hash.ToLowerInvariant()
            }
            version = [ordered]@{
                relativePath = "VERSION"
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $payloadRoot "VERSION")).Hash.ToLowerInvariant()
            }
        }
    }
    $payloadIntegrityPath = Join-Path $payloadRoot "payload-integrity.json"
    $payloadIntegrityJson = ($payloadIntegrityManifest | ConvertTo-Json -Depth 6) + [Environment]::NewLine
    [System.IO.File]::WriteAllText($payloadIntegrityPath, $payloadIntegrityJson, [System.Text.UTF8Encoding]::new($false))

    $payloadZip = Join-Path $releaseRoot "VRCForge_Windows_x64_$Version.zip"
    Remove-Item -LiteralPath $payloadZip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $payloadRoot "*") -DestinationPath $payloadZip -Force
    $payloadSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $payloadZip).Hash.ToLowerInvariant()

    & python .\scripts\smoke_packaged_backend.py `
        --version $Version `
        --packaged-root $payloadRoot `
        --payload-zip $payloadZip `
        --artifacts-dir (Join-Path $repoRoot "artifacts")
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged Doctor and CLI self-test failed."
    }

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

    $finalSourceVersion = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    $finalHeadCommit = (git rev-parse HEAD).Trim()
    $finalOriginMainCommit = (git rev-parse origin/main).Trim()
    $finalStatus = git status --short
    if ($finalSourceVersion -ne $Version) {
        throw "Source VERSION changed during packaging. requested=$Version source=$finalSourceVersion"
    }
    if ($strictSourceBuild -and (
        $finalStatus -or
        $finalHeadCommit -ne $finalOriginMainCommit -or
        $finalHeadCommit -ne $headCommit -or
        $finalOriginMainCommit -ne $originMainCommit
    )) {
        throw "Strict release source state changed during packaging; rebuild from a clean HEAD equal to origin/main."
    }

    $buildPolicy = [ordered]@{
        mode = if ($strictEvidenceBuild) { "strict-evidence" } elseif ($strictReleaseBuild) { "strict" } else { "local-acceptance" }
        releaseEligible = [bool]$strictReleaseBuild
        allowDirty = [bool]$AllowDirty
        allowUnpushed = [bool]$AllowUnpushed
        allowVersionMismatch = [bool]$AllowVersionMismatch
    }
    if ($strictEvidenceBuild) {
        $buildPolicy.evidenceEligible = $true
    }
    $manifest = [ordered]@{
        version = $Version
        commit = $finalHeadCommit
        buildPolicy = $buildPolicy
        uvDownloadUrl = $uvDownloadUrl
        uvDownloadSha256 = $UvDownloadSha256
        uvRuntime = $uvRuntimeProvenance
        packagedDoctorSelfTest = [ordered]@{
            schema = "vrcforge.packaged_backend_smoke.v2"
            passed = $true
        }
        artifacts = @(
            @{ name = [System.IO.Path]::GetFileName($UnityPackagePath); sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $UnityPackagePath).Hash.ToLowerInvariant() },
            @{ name = [System.IO.Path]::GetFileName($payloadZip); sha256 = $payloadSha256 },
            @{ name = [System.IO.Path]::GetFileName($offlineInstaller); sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $offlineInstaller).Hash.ToLowerInvariant() },
            @{ name = [System.IO.Path]::GetFileName($webInstaller); sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $webInstaller).Hash.ToLowerInvariant() }
        )
    }
    if ($strictEvidenceBuild) {
        $manifest.evidenceAttestor = $evidenceAttestor
        $manifest.evidenceAuthority = $evidenceAuthority
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $releaseRoot "release-manifest.json") -Encoding UTF8

    Write-Host "Release payload built: $payloadRoot"
    Write-Host "Release artifacts: $releaseRoot"
} finally {
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
