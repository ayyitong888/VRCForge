param(
    [string]$OutputDir = "dist\bridge_target",
    [string]$ManifestPath = "dist\bridge-target-manifest.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$repositoryPath = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd([char[]]"\/")
$repositoryPrefix = $repositoryPath + [System.IO.Path]::DirectorySeparatorChar
$fixedConnectorModuleSha256 = "e8effb923d0fbd1427f1d89ea6f1d6a69914658b1ba18cd86a52f37ccd269fa4"
$fixedConnectorModuleBytes = 39869

function Resolve-BridgeBuildPath {
    param(
        [string]$Path,
        [switch]$AllowFile
    )

    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
    }
    if (
        [string]::Equals($candidate, $repositoryPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $candidate.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Bridge build paths must stay below the repository root."
    }

    $current = if ($AllowFile) { [System.IO.Path]::GetDirectoryName($candidate) } else { $candidate }
    while ($current.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if (
            $null -ne $item -and
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Bridge build paths cannot traverse a reparse point."
        }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
    return $candidate
}

function ConvertFrom-SingleJsonResult {
    param(
        [object[]]$Lines,
        [string]$Label
    )

    try {
        $value = ($Lines -join [Environment]::NewLine) | ConvertFrom-Json
    } catch {
        throw "$Label did not return valid JSON."
    }
    if ($null -eq $value -or $value.ok -ne $true) {
        throw "$Label was rejected."
    }
    return $value
}

$resolvedOutputDir = Resolve-BridgeBuildPath -Path $OutputDir
$resolvedManifestPath = Resolve-BridgeBuildPath -Path $ManifestPath -AllowFile
$outputPrefix = $resolvedOutputDir.TrimEnd([char[]]"\/") + [System.IO.Path]::DirectorySeparatorChar
if (
    [string]::Equals(
        $resolvedManifestPath,
        $resolvedOutputDir,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $resolvedManifestPath.StartsWith(
        $outputPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "ManifestPath must stay outside OutputDir to prevent self-hashing."
}

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $pythonCommand) {
    throw "Python is required to build the fixed bridge target."
}
$pythonExe = $pythonCommand.Source
$packagerProbe = Join-Path $PSScriptRoot "bridge_target_packager_probe.py"
$manifestTool = Join-Path $PSScriptRoot "bridge_target_manifest.py"
$entrySource = Join-Path $repoRoot "primitive_bridge_target_entry.py"
foreach ($requiredSource in @($packagerProbe, $manifestTool, $entrySource)) {
    $item = Get-Item -LiteralPath $requiredSource -Force -ErrorAction Stop
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "A fixed bridge build source is invalid."
    }
}

$buildRoot = Resolve-BridgeBuildPath -Path "build\bridge-target"
$tempDist = Resolve-BridgeBuildPath -Path (Join-Path $buildRoot "dist")
$tempWork = Resolve-BridgeBuildPath -Path (Join-Path $buildRoot "work")
$buildPrefix = $buildRoot.TrimEnd([char[]]"\/") + [System.IO.Path]::DirectorySeparatorChar
if (
    [string]::Equals(
        $resolvedOutputDir,
        $buildRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $resolvedOutputDir.StartsWith(
        $buildPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    [string]::Equals(
        $resolvedManifestPath,
        $buildRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $resolvedManifestPath.StartsWith(
        $buildPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "Bridge outputs must stay outside the disposable packager workspace."
}
try {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $tempDist -Force | Out-Null
    New-Item -ItemType Directory -Path $tempWork -Force | Out-Null

    $packagerConfigLines = @(& $pythonExe $packagerProbe 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The fixed bridge target packager input probe failed."
    }
    $packagerConfig = ConvertFrom-SingleJsonResult `
        -Lines $packagerConfigLines `
        -Label "The fixed bridge target packager input probe"
    if (
        $packagerConfig.schema -cne "vrcforge.bridge_target_packager_probe.v1" -or
        $packagerConfig.packagerVersion -cne "6.19.0" -or
        $packagerConfig.distribution -cne "mcpforunityserver" -or
        $packagerConfig.connectorVersion -cne "9.6.8" -or
        $packagerConfig.module -cne "main" -or
        [string]::IsNullOrWhiteSpace([string]$packagerConfig.connectorSource) -or
        -not [System.IO.Path]::IsPathRooted([string]$packagerConfig.connectorSource) -or
        ([string]$packagerConfig.connectorSource).Contains(";")
    ) {
        throw "The fixed bridge target packager input receipt is invalid."
    }
    $connectorSource = [System.IO.Path]::GetFullPath(
        [string]$packagerConfig.connectorSource
    )
    $connectorSourceItem = Get-Item -LiteralPath $connectorSource -Force -ErrorAction Stop
    if (
        $connectorSourceItem.PSIsContainer -or
        ($connectorSourceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $connectorSourceItem.Length -ne $fixedConnectorModuleBytes -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $connectorSource).Hash.ToLowerInvariant() `
            -cne $fixedConnectorModuleSha256
    ) {
        throw "The fixed bridge target connector source changed after inspection."
    }
    $connectorDataArgument = "$connectorSource;."

    $packagerLines = @(& $pythonExe `
        -m PyInstaller `
        "--noconfirm" `
        "--clean" `
        "--onedir" `
        "--name" "vrcforge_bridge_target" `
        "--console" `
        "--noupx" `
        "--contents-directory" "_internal" `
        "--paths" $repoRoot `
        "--hidden-import" ([string]$packagerConfig.module) `
        "--copy-metadata" ([string]$packagerConfig.distribution) `
        "--add-data" $connectorDataArgument `
        "--distpath" $tempDist `
        "--workpath" $tempWork `
        "--specpath" $tempWork `
        $entrySource 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The fixed bridge target packager failed."
    }

    $sourceTree = Join-Path $tempDist "vrcforge_bridge_target"
    $sourceExecutable = Join-Path $sourceTree "vrcforge_bridge_target.exe"
    if (-not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
        throw "The fixed bridge target executable was not produced."
    }

    Remove-Item -LiteralPath $resolvedOutputDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $resolvedOutputDir -Force | Out-Null
    Copy-Item -Path (Join-Path $sourceTree "*") -Destination $resolvedOutputDir -Recurse -Force
    $bridgeTargetExecutable = Join-Path $resolvedOutputDir "vrcforge_bridge_target.exe"
    if (-not (Test-Path -LiteralPath $bridgeTargetExecutable -PathType Leaf)) {
        throw "The copied fixed bridge target executable is unavailable."
    }

    $buildLines = @(& $pythonExe `
        $manifestTool `
        "--tree" $resolvedOutputDir `
        "--manifest" $resolvedManifestPath `
        "--build" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The fixed bridge target manifest build failed."
    }
    $buildReceipt = ConvertFrom-SingleJsonResult `
        -Lines $buildLines `
        -Label "The fixed bridge target manifest build"
    try {
        $treeDocument = Get-Content -LiteralPath $resolvedManifestPath -Raw | ConvertFrom-Json
    } catch {
        throw "The fixed bridge target manifest was not valid JSON."
    }
    $connectorRecords = @(
        $treeDocument.files | Where-Object {
            [string]$_.path -ceq "_internal/main.py"
        }
    )
    $executableRecords = @(
        $treeDocument.files | Where-Object {
            [string]$_.path -ceq "vrcforge_bridge_target.exe"
        }
    )
    $executableSha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $bridgeTargetExecutable
    ).Hash.ToLowerInvariant()
    if (
        $treeDocument.schema -cne "vrcforge.bridge_target_tree_manifest.v1" -or
        $treeDocument.treeDigest -cne $buildReceipt.treeDigest -or
        $treeDocument.directoryCount -ne $buildReceipt.directoryCount -or
        $treeDocument.entryCount -ne $buildReceipt.entryCount -or
        $treeDocument.byteCount -ne $buildReceipt.byteCount -or
        $connectorRecords.Count -ne 1 -or
        [string]$connectorRecords[0].sha256 -cne $fixedConnectorModuleSha256 -or
        [uint64]$connectorRecords[0].length -ne $fixedConnectorModuleBytes -or
        $executableRecords.Count -ne 1 -or
        [string]$executableRecords[0].sha256 -cne $executableSha256
    ) {
        throw "The fixed bridge target tree is missing a required pinned leaf."
    }

    $verifyLines = @(& $pythonExe `
        $manifestTool `
        "--tree" $resolvedOutputDir `
        "--manifest" $resolvedManifestPath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The fixed bridge target manifest verification failed."
    }
    $verifyReceipt = ConvertFrom-SingleJsonResult `
        -Lines $verifyLines `
        -Label "The fixed bridge target manifest verification"
    if (
        $buildReceipt.mode -ne "build" -or
        $verifyReceipt.mode -ne "verify" -or
        $buildReceipt.schema -cne "vrcforge.bridge_target_tree_manifest.v1" -or
        $verifyReceipt.schema -cne $buildReceipt.schema -or
        $verifyReceipt.treeDigest -cne $buildReceipt.treeDigest -or
        $verifyReceipt.directoryCount -ne $buildReceipt.directoryCount -or
        $verifyReceipt.entryCount -ne $buildReceipt.entryCount -or
        $verifyReceipt.byteCount -ne $buildReceipt.byteCount
    ) {
        throw "The fixed bridge target manifest readback did not match its build receipt."
    }

    $manifestSha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedManifestPath
    ).Hash.ToLowerInvariant()
    [ordered]@{
        ok = $true
        schema = "vrcforge.bridge_target_build_receipt.v1"
        manifestSchema = [string]$verifyReceipt.schema
        manifestSha256 = $manifestSha256
        treeDigest = [string]$verifyReceipt.treeDigest
        directoryCount = [uint64]$verifyReceipt.directoryCount
        entryCount = [uint64]$verifyReceipt.entryCount
        byteCount = [uint64]$verifyReceipt.byteCount
        verifiedAfterBuild = $true
    } | ConvertTo-Json -Compress
} finally {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
}
