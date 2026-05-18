param(
    [string]$SourceAssetsPath = "Assets\VRCForge",
    [string]$OutputPath = "dist\release\VRCForge.unitypackage"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedSource = if ([System.IO.Path]::IsPathRooted($SourceAssetsPath)) {
    $SourceAssetsPath
} else {
    Join-Path $repoRoot $SourceAssetsPath
}
$resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath
} else {
    Join-Path $repoRoot $OutputPath
}

if (-not (Test-Path -LiteralPath $resolvedSource)) {
    throw "Unity package source path does not exist: $resolvedSource"
}

$tar = Get-Command tar -ErrorAction SilentlyContinue
if (-not $tar) {
    throw "tar.exe is required to create VRCForge.unitypackage."
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcforge-unitypackage-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

function New-UnityGuid {
    return [guid]::NewGuid().ToString("N")
}

function Write-Utf8NoBom([string]$Path, [string]$Value) {
    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
}

function Write-UnityPackageEntry {
    param(
        [string]$EntryRoot,
        [string]$PathName,
        [string]$SourcePath,
        [bool]$IsDirectory
    )

    $entryDir = Join-Path $EntryRoot (New-UnityGuid)
    New-Item -ItemType Directory -Force -Path $entryDir | Out-Null
    Write-Utf8NoBom (Join-Path $entryDir "pathname") $PathName

    if ($IsDirectory) {
        New-Item -ItemType File -Force -Path (Join-Path $entryDir "asset") | Out-Null
        $meta = @"
fileFormatVersion: 2
guid: $(New-UnityGuid)
folderAsset: yes
DefaultImporter:
  externalObjects: {}
  userData:
  assetBundleName:
  assetBundleVariant:
"@
    } else {
        Copy-Item -LiteralPath $SourcePath -Destination (Join-Path $entryDir "asset") -Force
        $meta = @"
fileFormatVersion: 2
guid: $(New-UnityGuid)
MonoImporter:
  externalObjects: {}
  serializedVersion: 2
  defaultReferences: []
  executionOrder: 0
  icon: {instanceID: 0}
  userData:
  assetBundleName:
  assetBundleVariant:
"@
    }

    Write-Utf8NoBom (Join-Path $entryDir "asset.meta") $meta
}

function Get-RelativePackagePath([string]$RootPath, [string]$ItemPath) {
    $root = [System.IO.Path]::GetFullPath($RootPath)
    if (-not $root.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $root += [System.IO.Path]::DirectorySeparatorChar
    }
    $item = [System.IO.Path]::GetFullPath($ItemPath)
    $rootUri = [System.Uri]::new($root)
    $itemUri = [System.Uri]::new($item)
    return [System.Uri]::UnescapeDataString($rootUri.MakeRelativeUri($itemUri).ToString()).Replace("/", "/")
}

try {
    $sourceRoot = Resolve-Path -LiteralPath $resolvedSource
    $repoRootResolved = Resolve-Path -LiteralPath $repoRoot
    $items = @($sourceRoot.Path) + @(
        Get-ChildItem -LiteralPath $sourceRoot.Path -Recurse -Force |
            Sort-Object FullName |
            ForEach-Object { $_.FullName }
    )

    foreach ($item in $items) {
        $relative = (Get-RelativePackagePath $repoRootResolved.Path $item).Replace("\", "/")
        $isDirectory = (Get-Item -LiteralPath $item).PSIsContainer
        Write-UnityPackageEntry -EntryRoot $tempRoot -PathName $relative -SourcePath $item -IsDirectory:$isDirectory
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resolvedOutput) | Out-Null
    if (Test-Path -LiteralPath $resolvedOutput) {
        Remove-Item -LiteralPath $resolvedOutput -Force
    }

    & $tar.Source -czf $resolvedOutput -C $tempRoot .
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE."
    }
    Write-Host "Unity package created: $resolvedOutput"
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
