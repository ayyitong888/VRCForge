param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [string]$UnityEditorPath,
    [string]$SourceAssetsPath,
    [string]$SourceMcpPackagePath,
    [switch]$LaunchUnity
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ExistingPath([string]$PathValue, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        throw "$Label is empty."
    }
    if (-not (Test-Path -LiteralPath $PathValue)) {
        throw "$Label does not exist: $PathValue"
    }
    return (Resolve-Path -LiteralPath $PathValue).Path
}

function New-BackupPath([string]$BackupRoot, [string]$Prefix) {
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $candidate = Join-Path $BackupRoot ("{0}_{1}" -f $Prefix, $timestamp)
    $suffix = 1
    while (Test-Path -LiteralPath $candidate) {
        $candidate = Join-Path $BackupRoot ("{0}_{1}_{2}" -f $Prefix, $timestamp, $suffix)
        $suffix += 1
    }
    return $candidate
}

function Copy-DirectoryClean([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

function Restore-DirectoryBackup([string]$BackupPath, [string]$TargetPath) {
    if ([string]::IsNullOrWhiteSpace($BackupPath) -or -not (Test-Path -LiteralPath $BackupPath)) {
        return
    }
    if (Test-Path -LiteralPath $TargetPath) {
        Remove-Item -LiteralPath $TargetPath -Recurse -Force
    }
    Move-Item -LiteralPath $BackupPath -Destination $TargetPath -Force
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SourceAssetsPath)) {
    $SourceAssetsPath = Join-Path $repoRoot "Assets\VRCForge"
}

$sourceAssets = Resolve-ExistingPath $SourceAssetsPath "Source Assets/VRCForge folder"
$resolvedProjectPath = Resolve-ExistingPath $ProjectPath "Unity project path"
$targetAssetsRoot = Join-Path $resolvedProjectPath "Assets"
$targetPackagesRoot = Join-Path $resolvedProjectPath "Packages"
$targetPackageManifest = Join-Path $targetPackagesRoot "manifest.json"
$targetProjectSettings = Join-Path $resolvedProjectPath "ProjectSettings\ProjectVersion.txt"
$targetVrcForge = Join-Path $targetAssetsRoot "VRCForge"
$legacyTargetToolFolder = Join-Path $targetAssetsRoot ("VRC" + "AutoRig")
$projectStateRoot = Join-Path $resolvedProjectPath ".vrcforge"
$backupRoot = Join-Path $projectStateRoot "backups"
$mcpPackageName = "com.coplaydev.unity-mcp"
$mcpPackageValue = "file:Packages/com.coplaydev.unity-mcp"
$targetMcpPackagePath = Join-Path $targetPackagesRoot $mcpPackageName
$sourceMcpPackage = $null
if (-not [string]::IsNullOrWhiteSpace($SourceMcpPackagePath)) {
    $sourceMcpPackage = Resolve-ExistingPath $SourceMcpPackagePath "Source CoplayDev Unity MCP package"
} else {
    $defaultSourceMcpPackage = Join-Path $repoRoot "third_party\com.coplaydev.unity-mcp"
    if (Test-Path -LiteralPath $defaultSourceMcpPackage) {
        $sourceMcpPackage = (Resolve-Path -LiteralPath $defaultSourceMcpPackage).Path
    }
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

$legacyBackupPath = $null
$vrcForgeBackupPath = $null
$mcpBackupPath = $null
$manifestBackupPath = $null
$installedVrcForge = $false
$installedMcp = $false
$shouldConfigureMcp = $false

try {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

    if (Test-Path -LiteralPath $legacyTargetToolFolder) {
        Write-Host "Detected legacy VRCForge/VRCAutoRig Unity plugin folder. Migrating to Assets/VRCForge."
        $legacyBackupPath = New-BackupPath $backupRoot "VRCAutoRig"
        Move-Item -LiteralPath $legacyTargetToolFolder -Destination $legacyBackupPath -Force
        if (Test-Path -LiteralPath $legacyTargetToolFolder) {
            throw "Legacy Assets/VRCAutoRig still exists after migration attempt. Stop before installing new plugin."
        }
        Write-Host "Moved legacy Unity tool folder to: $legacyBackupPath"
    }

    if (Test-Path -LiteralPath $targetVrcForge) {
        $vrcForgeBackupPath = New-BackupPath $backupRoot "VRCForge"
        Move-Item -LiteralPath $targetVrcForge -Destination $vrcForgeBackupPath -Force
        Write-Host "Backed up existing Assets/VRCForge to: $vrcForgeBackupPath"
    }

    try {
        Copy-DirectoryClean $sourceAssets $targetVrcForge
        $installedVrcForge = $true
    } catch {
        Restore-DirectoryBackup $vrcForgeBackupPath $targetVrcForge
        throw
    }

    if ($sourceMcpPackage) {
        if (Test-Path -LiteralPath $targetMcpPackagePath) {
            $mcpBackupPath = New-BackupPath $backupRoot "com.coplaydev.unity-mcp"
            Move-Item -LiteralPath $targetMcpPackagePath -Destination $mcpBackupPath -Force
            Write-Host "Backed up existing Unity MCP package to: $mcpBackupPath"
        }
        try {
            Copy-DirectoryClean $sourceMcpPackage $targetMcpPackagePath
            $installedMcp = $true
        } catch {
            Restore-DirectoryBackup $mcpBackupPath $targetMcpPackagePath
            throw
        }
        $shouldConfigureMcp = $true
    } elseif (Test-Path -LiteralPath $targetMcpPackagePath) {
        $shouldConfigureMcp = $true
        Write-Warning "No local CoplayDev Unity MCP source package was supplied. Keeping existing project package folder and configuring manifest."
    } else {
        Write-Warning "No local CoplayDev Unity MCP package was supplied. Skipping manifest MCP dependency update to avoid a broken file: package reference."
    }

    $manifestBackupPath = New-BackupPath $backupRoot "manifest"
    $manifestBackupPath = "$manifestBackupPath.json"
    Copy-Item -LiteralPath $targetPackageManifest -Destination $manifestBackupPath -Force

    try {
        $manifest = Get-Content -LiteralPath $targetPackageManifest -Raw | ConvertFrom-Json
        if (-not $manifest.PSObject.Properties["dependencies"]) {
            $manifest | Add-Member -NotePropertyName "dependencies" -NotePropertyValue ([pscustomobject]@{})
        }

        $dependencies = $manifest.dependencies
        if ($null -eq $dependencies) {
            $dependencies = [pscustomobject]@{}
            $manifest.dependencies = $dependencies
        }

        if ($shouldConfigureMcp) {
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
                [System.IO.File]::WriteAllText($targetPackageManifest, $manifestJson, [System.Text.UTF8Encoding]::new($false))
                Get-Content -LiteralPath $targetPackageManifest -Raw | ConvertFrom-Json | Out-Null
            }
        }
    } catch {
        Copy-Item -LiteralPath $manifestBackupPath -Destination $targetPackageManifest -Force
        throw "Failed to update Packages/manifest.json. Restored backup from $manifestBackupPath. Error: $($_.Exception.Message)"
    }
} catch {
    if (-not $installedVrcForge) {
        Restore-DirectoryBackup $vrcForgeBackupPath $targetVrcForge
    }
    if (-not $installedMcp) {
        Restore-DirectoryBackup $mcpBackupPath $targetMcpPackagePath
    }
    throw
}

Write-Host "Installed Assets/VRCForge into: $resolvedProjectPath"
Write-Host "Project backups are under: $backupRoot"
if ($shouldConfigureMcp) {
    Write-Host "Unity MCP package dependency uses: $mcpPackageValue"
}
if ($sourceMcpPackage) {
    Write-Host "Copied Unity MCP package into: $targetMcpPackagePath"
}
Write-Host ""
Write-Host "Next steps inside Unity:"
Write-Host "1. Open the project and wait for package resolution."
Write-Host "2. Confirm MCP for Unity finished importing."
Write-Host "3. Start the Unity MCP server."
Write-Host "4. Re-run python vrchat_blendshape_agent.py --unity-status"

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
