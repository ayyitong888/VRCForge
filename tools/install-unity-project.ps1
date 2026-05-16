param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [string]$UnityEditorPath,
    [switch]$LaunchUnity
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceAssets = Join-Path $repoRoot "Assets\VRCAutoRig"
$resolvedProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$targetAssetsRoot = Join-Path $resolvedProjectPath "Assets"
$targetPackageManifest = Join-Path $resolvedProjectPath "Packages\manifest.json"
$targetProjectSettings = Join-Path $resolvedProjectPath "ProjectSettings\ProjectVersion.txt"
$targetVrcAutoRig = Join-Path $targetAssetsRoot "VRCAutoRig"
$mcpPackageName = "com.coplaydev.unity-mcp"
$mcpPackageValue = "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"

if (-not (Test-Path -LiteralPath $sourceAssets)) {
    throw "Source folder does not exist: $sourceAssets"
}

if (-not (Test-Path -LiteralPath $targetAssetsRoot)) {
    throw "Target Unity project is missing Assets/: $targetAssetsRoot"
}

if (-not (Test-Path -LiteralPath $targetPackageManifest)) {
    throw "Target Unity project is missing Packages/manifest.json: $targetPackageManifest"
}

if (-not (Test-Path -LiteralPath $targetProjectSettings)) {
    throw "Target Unity project is missing ProjectSettings/ProjectVersion.txt: $targetProjectSettings"
}

New-Item -ItemType Directory -Force -Path $targetVrcAutoRig | Out-Null
Copy-Item -Path (Join-Path $sourceAssets "*") -Destination $targetVrcAutoRig -Recurse -Force

$manifest = Get-Content -LiteralPath $targetPackageManifest -Raw | ConvertFrom-Json
if (-not $manifest.PSObject.Properties["dependencies"]) {
    $manifest | Add-Member -NotePropertyName "dependencies" -NotePropertyValue ([pscustomobject]@{})
}

$dependencies = $manifest.dependencies
if ($null -eq $dependencies) {
    $dependencies = [pscustomobject]@{}
    $manifest.dependencies = $dependencies
}

$manifestChanged = $false
$existingDependency = $dependencies.PSObject.Properties[$mcpPackageName]
if ($null -eq $existingDependency) {
    $dependencies | Add-Member -NotePropertyName $mcpPackageName -NotePropertyValue $mcpPackageValue
    $manifestChanged = $true
} elseif ($existingDependency.Value -ne $mcpPackageValue) {
    $existingDependency.Value = $mcpPackageValue
    $manifestChanged = $true
}

if ($manifestChanged) {
    $manifestJson = $manifest | ConvertTo-Json -Depth 20
    Set-Content -LiteralPath $targetPackageManifest -Value $manifestJson -Encoding UTF8
}

Write-Host "Installed Assets/VRCAutoRig into: $resolvedProjectPath"
if ($manifestChanged) {
    Write-Host "Added Unity MCP package dependency to Packages/manifest.json"
} else {
    Write-Host "Unity MCP package dependency already present in Packages/manifest.json"
}

Write-Host ""
Write-Host "Next steps inside Unity:"
Write-Host "1. Open the project and wait for package resolution."
Write-Host "2. Confirm MCP for Unity finished importing."
Write-Host "3. Optional: define VRCFORGE_ENABLE_ROSLYN and run tools/install-roslyn-support.ps1 for the legacy Roslyn fallback. It is not required for core features."
Write-Host "4. Start the Unity MCP server."
Write-Host "5. Re-run python vrchat_blendshape_agent.py --unity-status"

if ($LaunchUnity) {
    if ([string]::IsNullOrWhiteSpace($UnityEditorPath)) {
        throw "LaunchUnity was requested but UnityEditorPath is empty."
    }

    if (-not (Test-Path -LiteralPath $UnityEditorPath)) {
        throw "Unity editor executable was not found: $UnityEditorPath"
    }

    Start-Process -FilePath $UnityEditorPath -ArgumentList @("-projectPath", $resolvedProjectPath)
    Write-Host "Launched Unity: $UnityEditorPath"
}
