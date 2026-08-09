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
$retiredGuids = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
@(
    "1d2ac338c0b461cafc0ca7b6871e6304",
    "e3ea79e5b45092c05901a8e6a0230cf6",
    "9ef91b08379b1e2120da076139d37484",
    "65a86f7265c22863a08d7def00521c50",
    "8b2e3c74998c4021f894bb52f364203e",
    "38d1e11cad40830a19c7b4b3e8f0d418",
    "fe99b2166dd28b6ee9efae0066c039cf"
) | ForEach-Object { [void]$retiredGuids.Add($_) }
$publishedGuidByPath = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)

function New-StableUnityGuid([string]$PathName) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("vrcforge.unitypackage.v1/$PathName")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    return ([System.BitConverter]::ToString($hash).Replace("-", "").Substring(0, 32)).ToLowerInvariant()
}

function Get-PackageAssetGuid([string]$PathName) {
    if ($publishedGuidByPath.ContainsKey($PathName)) {
        return $publishedGuidByPath[$PathName]
    }
    return New-StableUnityGuid $PathName
}

function Write-Utf8NoBom([string]$Path, [string]$Value) {
    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
}

function Write-UnityPackageEntry {
    param(
        [string]$EntryRoot,
        [string]$PathName,
        [string]$SourcePath,
        [bool]$IsDirectory,
        [bool]$UseSourceMeta = $true
    )

    # Unity's package importer treats the archive directory name as the asset
    # identity during overwrite imports. It must therefore be the same GUID as
    # asset.meta; a separate stable entry id causes a false "new GUID" warning
    # and can invalidate references during an upgrade.
    $entryDir = Join-Path $EntryRoot (Get-PackageAssetGuid $PathName)
    New-Item -ItemType Directory -Force -Path $entryDir | Out-Null
    Write-Utf8NoBom (Join-Path $entryDir "pathname") $PathName

    if ($IsDirectory) {
        $meta = @"
fileFormatVersion: 2
guid: $(Get-PackageAssetGuid $PathName)
folderAsset: yes
DefaultImporter:
  externalObjects: {}
  userData:
  assetBundleName:
  assetBundleVariant:
"@
    } elseif ([System.IO.Path]::GetExtension($PathName).Equals(".txt", [System.StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $SourcePath -Destination (Join-Path $entryDir "asset") -Force
        $meta = @"
fileFormatVersion: 2
guid: $(Get-PackageAssetGuid $PathName)
TextScriptImporter:
  externalObjects: {}
  userData:
  assetBundleName:
  assetBundleVariant:
"@
    } else {
        Copy-Item -LiteralPath $SourcePath -Destination (Join-Path $entryDir "asset") -Force
        $meta = @"
fileFormatVersion: 2
guid: $(Get-PackageAssetGuid $PathName)
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
    $sourceMetaPath = "$SourcePath.meta"
    if ($UseSourceMeta -and (Test-Path -LiteralPath $sourceMetaPath -PathType Leaf)) {
        Copy-Item -LiteralPath $sourceMetaPath -Destination (Join-Path $entryDir "asset.meta") -Force
    } else {
        # Unity's YAML reader rejects a final empty scalar when the generated
        # .meta ends immediately after the colon. Keep the required terminal
        # LF even though pathnames intentionally stay byte-exact without one.
        Write-Utf8NoBom (Join-Path $entryDir "asset.meta") ($meta + "`n")
    }
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
    $guidManifestPath = Join-Path $PSScriptRoot "unitypackage_guid_manifest.json"
    if (-not (Test-Path -LiteralPath $guidManifestPath -PathType Leaf)) {
        throw "Unity package GUID manifest is missing: $guidManifestPath"
    }
    $guidManifest = Get-Content -LiteralPath $guidManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($null -eq $guidManifest.PSObject.Properties["schema"] -or
        [string]$guidManifest.schema -cne "vrcforge.unitypackage-guid-manifest.v1" -or
        $null -eq $guidManifest.PSObject.Properties["entries"]) {
        throw "Unity package GUID manifest schema is invalid."
    }
    $manifestGuidOwner = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in @($guidManifest.entries)) {
        $pathProperty = $entry.PSObject.Properties["path"]
        $guidProperty = $entry.PSObject.Properties["guid"]
        if ($null -eq $pathProperty -or $null -eq $guidProperty) {
            throw "Unity package GUID manifest entry is missing path or guid."
        }
        $manifestPath = [string]$pathProperty.Value
        $manifestGuid = ([string]$guidProperty.Value).ToLowerInvariant()
        $pathParts = @($manifestPath.Split('/'))
        if ($manifestPath -cne $manifestPath.Trim() -or
            ($manifestPath -cne "Assets/VRCForge" -and -not $manifestPath.StartsWith("Assets/VRCForge/", [System.StringComparison]::Ordinal)) -or
            $manifestPath.Contains("\") -or
            $manifestPath.EndsWith("/", [System.StringComparison]::Ordinal) -or
            $manifestPath.EndsWith(".meta", [System.StringComparison]::OrdinalIgnoreCase) -or
            $pathParts -contains "." -or $pathParts -contains "..") {
            throw "Unity package GUID manifest contains an invalid asset path: $manifestPath"
        }
        if ($manifestGuid -notmatch '^[0-9a-f]{32}$') {
            throw "Unity package GUID manifest contains a missing or malformed guid for $manifestPath"
        }
        if ($publishedGuidByPath.ContainsKey($manifestPath)) {
            throw "Unity package GUID manifest contains a duplicate path: $manifestPath"
        }
        if ($manifestGuidOwner.ContainsKey($manifestGuid)) {
            throw "Unity package GUID manifest contains a duplicate guid $manifestGuid for $manifestPath and $($manifestGuidOwner[$manifestGuid])"
        }
        if ($retiredGuids.Contains($manifestGuid)) {
            throw "Unity package GUID manifest contains a retired guid $manifestGuid for $manifestPath"
        }
        $publishedGuidByPath.Add($manifestPath, $manifestGuid)
        $manifestGuidOwner.Add($manifestGuid, $manifestPath)
    }
    if ($publishedGuidByPath.Count -eq 0) {
        throw "Unity package GUID manifest has no published asset mappings."
    }

    $sourceRoot = Resolve-Path -LiteralPath $resolvedSource
    $documentationRoot = "Assets/VRCForge/Documentation"
    $documentationEntries = [ordered]@{
        "$documentationRoot/README.txt" = (Join-Path $repoRoot "README.md")
        "$documentationRoot/LICENSE-GPL-3.0.txt" = (Join-Path $repoRoot "LICENSE")
        "$documentationRoot/NOTICE.txt" = (Join-Path $repoRoot "NOTICE")
        "$documentationRoot/USER_MANUAL.txt" = (Join-Path $repoRoot "USER_MANUAL.md")
        "$documentationRoot/DEPENDENCIES.txt" = (Join-Path $repoRoot "DEPENDENCIES.md")
    }
    foreach ($documentationSource in $documentationEntries.Values) {
        if (-not (Test-Path -LiteralPath $documentationSource -PathType Leaf)) {
            throw "Unity package documentation source is missing: $documentationSource"
        }
    }
    $generatedSourcePath = Join-Path $sourceRoot.Path "Generated"
    if (Test-Path -LiteralPath $generatedSourcePath) {
        $generatedSourceItem = Get-Item -LiteralPath $generatedSourcePath
        $generatedPoison = -not $generatedSourceItem.PSIsContainer
        if (-not $generatedPoison) {
            $generatedPoison = @(
                Get-ChildItem -LiteralPath $generatedSourcePath -Recurse -Force -File |
                    Where-Object { $_.Extension -ne ".meta" }
            ).Count -gt 0
        }
        if ($generatedPoison) {
            throw "Unity package content assertion failed: Assets/VRCForge/Generated is runtime output and must not contain package content."
        }
    }
    $sourceFiles = @(
        Get-ChildItem -LiteralPath $sourceRoot.Path -Recurse -Force -File |
            Where-Object { $_.Extension -ne ".meta" } |
            Sort-Object FullName
    )
    $packableDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $sourceRootFullPath = [System.IO.Path]::GetFullPath($sourceRoot.Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar)
    foreach ($sourceFile in $sourceFiles) {
        $parent = [System.IO.Directory]::GetParent($sourceFile.FullName)
        while ($null -ne $parent) {
            $parentPath = [System.IO.Path]::GetFullPath($parent.FullName).TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar)
            if ([string]::Equals($parentPath, $sourceRootFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                break
            }
            $sourcePrefix = $sourceRootFullPath + [System.IO.Path]::DirectorySeparatorChar
            if (-not $parentPath.StartsWith($sourcePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Unity package source file escaped its VRCForge Assets root: $($sourceFile.FullName)"
            }
            [void]$packableDirectories.Add($parentPath)
            $parent = $parent.Parent
        }
    }
    $items = @($sourceRoot.Path) + @($packableDirectories | Sort-Object) + @($sourceFiles | ForEach-Object { $_.FullName })

    $emittedPathNames = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($item in $items) {
        # The source may be a release-staging directory outside this checkout.
        # Unity package pathnames are an artifact contract, not a reflection of
        # the builder's physical input path.
        if ([string]::Equals($item, $sourceRoot.Path, [System.StringComparison]::OrdinalIgnoreCase)) {
            $relative = "Assets/VRCForge"
        } else {
            $sourceRelative = (Get-RelativePackagePath $sourceRoot.Path $item).Replace("\", "/")
            if ([string]::IsNullOrWhiteSpace($sourceRelative) -or $sourceRelative.StartsWith("../", [System.StringComparison]::Ordinal)) {
                throw "Unity package source item escaped its VRCForge Assets root: $item"
            }
            $relative = "Assets/VRCForge/$sourceRelative"
        }
        if (-not $emittedPathNames.Add($relative)) {
            throw "Unity package content assertion failed: duplicate pathname $relative"
        }
        $isDirectory = (Get-Item -LiteralPath $item).PSIsContainer
        Write-UnityPackageEntry -EntryRoot $tempRoot -PathName $relative -SourcePath $item -IsDirectory:$isDirectory
    }

    if (-not $emittedPathNames.Add($documentationRoot)) {
        throw "Unity package content assertion failed: reserved documentation path already exists: $documentationRoot"
    }
    Write-UnityPackageEntry `
        -EntryRoot $tempRoot `
        -PathName $documentationRoot `
        -SourcePath $repoRoot `
        -IsDirectory:$true `
        -UseSourceMeta:$false
    foreach ($documentationPath in $documentationEntries.Keys) {
        if (-not $emittedPathNames.Add($documentationPath)) {
            throw "Unity package content assertion failed: reserved documentation path already exists: $documentationPath"
        }
        Write-UnityPackageEntry `
            -EntryRoot $tempRoot `
            -PathName $documentationPath `
            -SourcePath $documentationEntries[$documentationPath] `
            -IsDirectory:$false `
            -UseSourceMeta:$false
    }

    $seenGuids = @{}
    foreach ($entryRoot in Get-ChildItem -LiteralPath $tempRoot -Directory) {
        $entryPathName = (Get-Content -LiteralPath (Join-Path $entryRoot.FullName "pathname") -Raw).Trim()
        $metaPath = Join-Path $entryRoot.FullName "asset.meta"
        $metaText = Get-Content -LiteralPath $metaPath -Raw -Encoding utf8
        $matches = [regex]::Matches($metaText, '(?m)^guid:\s*([0-9a-fA-F]{32})\s*$')
        if ($matches.Count -ne 1) {
            throw "Unity package GUID assertion failed: missing or malformed guid for $entryPathName"
        }
        $guid = $matches[0].Groups[1].Value.ToLowerInvariant()
        if ($seenGuids.ContainsKey($guid)) {
            throw "Unity package GUID assertion failed: duplicate guid $guid for $entryPathName and $($seenGuids[$guid])"
        }
        if ($retiredGuids.Contains($guid)) {
            throw "Unity package GUID assertion failed: retired guid $guid is present at $entryPathName"
        }
        if ($publishedGuidByPath.ContainsKey($entryPathName) -and
            $guid -cne $publishedGuidByPath[$entryPathName]) {
            throw "Unity package GUID assertion failed: published guid drift for $entryPathName"
        }
        $seenGuids[$guid] = $entryPathName
    }

    $packagePathNames = @(
        Get-ChildItem -LiteralPath $tempRoot -Directory | ForEach-Object {
            (Get-Content -LiteralPath (Join-Path $_.FullName "pathname") -Raw).Trim()
        }
    )
    if ($packagePathNames | Where-Object { $_.EndsWith(".meta", [System.StringComparison]::OrdinalIgnoreCase) }) {
        throw "Unity package content assertion failed: .meta files must be entry metadata, not standalone assets."
    }
    if ($packagePathNames | Where-Object {
        $_ -ceq "Assets/VRCForge/Generated" -or
        $_.StartsWith("Assets/VRCForge/Generated/", [System.StringComparison]::Ordinal)
    }) {
        throw "Unity package content assertion failed: Assets/VRCForge/Generated and its descendants are forbidden."
    }
    $packagePathSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($packagePathName in $packagePathNames) {
        [void]$packagePathSet.Add($packagePathName)
    }
    foreach ($manifestPath in $publishedGuidByPath.Keys) {
        if (-not $packagePathSet.Contains($manifestPath)) {
            throw "Unity package content assertion failed: GUID manifest pathname was not emitted: $manifestPath"
        }
    }
    foreach ($packagePathName in $packagePathNames) {
        if (-not $publishedGuidByPath.ContainsKey($packagePathName)) {
            throw "Unity package content assertion failed: emitted pathname is absent from GUID manifest: $packagePathName"
        }
    }
    if ($packagePathNames | Where-Object {
        $_ -match "(?i)coplay|gamelovers|mcpforunity|2025-11-25|tcp-length-prefixed-jsonrpc|VRCForgeToolAttribute|VRCForgeParameterAttribute|VRCForgeResponse|ThirdPartyNotices"
    }) {
        throw "Unity package content assertion failed: retired or third-party MCP provenance path is forbidden."
    }
    if ($packagePathNames | Where-Object { $_ -match "(^|/)Packages/(com\\.(coplaydev|gamelovers)\\.unity-mcp)(/|$)" }) {
        throw "Unity package content assertion failed: third-party MCP package content is forbidden."
    }
    foreach ($documentationPath in @($documentationRoot) + @($documentationEntries.Keys)) {
        if (@($packagePathNames | Where-Object { $_ -ceq $documentationPath }).Count -ne 1) {
            throw "Unity package content assertion failed: missing or duplicate documentation path $documentationPath"
        }
    }
    foreach ($documentationPath in $documentationEntries.Keys) {
        $documentationEntryRoot = Join-Path $tempRoot (Get-PackageAssetGuid $documentationPath)
        $packagedDocumentationPath = Join-Path $documentationEntryRoot "asset"
        $packagedDocumentationMetaPath = Join-Path $documentationEntryRoot "asset.meta"
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $packagedDocumentationPath).Hash -cne
            (Get-FileHash -Algorithm SHA256 -LiteralPath $documentationEntries[$documentationPath]).Hash) {
            throw "Unity package content assertion failed: documentation bytes changed for $documentationPath"
        }
        if ((Get-Content -LiteralPath $packagedDocumentationMetaPath -Raw) -notmatch '(?m)^TextScriptImporter:$') {
            throw "Unity package content assertion failed: documentation does not use TextScriptImporter: $documentationPath"
        }
    }
    $firstPartyCoreMarker = Join-Path $sourceRoot.Path "Core\MCP\VRCForgeCommandAttribute.cs"
    if (Test-Path -LiteralPath $firstPartyCoreMarker -PathType Leaf) {
        $requiredFirstPartyPaths = @(
            "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
            "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
            "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
            "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
            "Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs",
            "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
            "Assets/VRCForge/Editor/MCP/VRCForgeMcpSourceMigration.cs",
            "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs",
            "Assets/VRCForge/Editor/McpBridgeBootstrap.cs",
            "Assets/VRCForge/Editor/VRCForgeUninstaller.cs"
        )
        foreach ($requiredPath in $requiredFirstPartyPaths) {
            if ($packagePathNames -notcontains $requiredPath) {
                throw "Unity package content assertion failed: missing first-party MCP Core path $requiredPath"
            }
        }
        $forbiddenAnnotation = Get-ChildItem -LiteralPath $sourceRoot.Path -Recurse -File -Filter "*.cs" |
            Select-String -Pattern "McpForUnityTool(Attribute)?|McpForUnityParameter(Attribute)?|MCPForUnity" -CaseSensitive
        if ($forbiddenAnnotation) {
            throw "Unity package content assertion failed: legacy external MCP annotation remains in first-party source."
        }
        $conflictDetectorPath = [System.IO.Path]::GetFullPath(
            (Join-Path $sourceRoot.Path "Editor\McpBridgeBootstrap.cs"))
        $forbiddenSourceResidue = Get-ChildItem -LiteralPath $sourceRoot.Path -Recurse -File |
            Where-Object {
                $_.Extension -in @(".cs", ".txt", ".json", ".asmdef") -and
                [System.IO.Path]::GetFullPath($_.FullName) -cne $conflictDetectorPath
            } |
            Select-String -Pattern "CoplayDev|McpForUnity|MCPForUnity|2025-11-25|tcp-length-prefixed-jsonrpc|VRCForgeToolAttribute|VRCForgeParameterAttribute|VRCForgeResponse|ThirdPartyNotices"
        if ($forbiddenSourceResidue) {
            throw "Unity package content assertion failed: third-party or old MCP source residue remains."
        }
        $conflictDetectorSource = Get-Content -LiteralPath $conflictDetectorPath -Raw
        $allowedConflictPackageIds = @(
            "com.coplaydev.unity-mcp",
            "com.gamelovers.unity-mcp"
        )
        foreach ($allowedPackageId in $allowedConflictPackageIds) {
            if ([regex]::Matches($conflictDetectorSource, [regex]::Escape($allowedPackageId)).Count -ne 1) {
                throw "Unity package content assertion failed: third-party conflict detector allowlist drifted."
            }
            $conflictDetectorSource = $conflictDetectorSource.Replace($allowedPackageId, "")
        }
        if ($conflictDetectorSource -match "coplay|gamelovers|McpForUnity|MCPForUnity|2025-11-25|tcp-length-prefixed-jsonrpc|VRCForgeToolAttribute|VRCForgeParameterAttribute|VRCForgeResponse|ThirdPartyNotices") {
            throw "Unity package content assertion failed: conflict detector contains non-allowlisted MCP residue."
        }
    }

    $packageBytePattern = "coplay|gamelovers|mcpforunity|2025-11-25|tcp-length-prefixed-jsonrpc|VRCForgeToolAttribute|VRCForgeParameterAttribute|VRCForgeResponse|ThirdPartyNotices"
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)
    foreach ($entryRoot in Get-ChildItem -LiteralPath $tempRoot -Directory) {
        $entryPathName = (Get-Content -LiteralPath (Join-Path $entryRoot.FullName "pathname") -Raw).Trim()
        foreach ($entryFileName in @("asset", "asset.meta")) {
            $entryFile = Join-Path $entryRoot.FullName $entryFileName
            if (-not (Test-Path -LiteralPath $entryFile -PathType Leaf)) {
                continue
            }
            $entryText = $latin1.GetString([System.IO.File]::ReadAllBytes($entryFile))
            if ($entryFileName -ceq "asset" -and $entryPathName -ceq "Assets/VRCForge/Editor/McpBridgeBootstrap.cs") {
                foreach ($allowedPackageId in @("com.coplaydev.unity-mcp", "com.gamelovers.unity-mcp")) {
                    if ([regex]::Matches($entryText, [regex]::Escape($allowedPackageId)).Count -ne 1) {
                        throw "Unity package byte assertion failed: conflict detector package ID drifted."
                    }
                    $entryText = $entryText.Replace($allowedPackageId, "")
                }
            }
            if ($entryText -match $packageBytePattern) {
                throw "Unity package byte assertion failed: forbidden MCP residue in $entryPathName/$entryFileName"
            }
        }
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
