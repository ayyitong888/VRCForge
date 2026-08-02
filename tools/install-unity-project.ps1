param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [string]$UnityEditorPath,
    [string]$SourceAssetsPath,
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
        Remove-DirectoryWithMeta $Destination
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
    $sourceMeta = "$Source.meta"
    $destinationMeta = "$Destination.meta"
    if ((Test-Path -LiteralPath $sourceMeta) -and -not (Test-Path -LiteralPath $destinationMeta)) {
        Copy-Item -LiteralPath $sourceMeta -Destination $destinationMeta -Force
    }
}

function Move-DirectoryWithMeta([string]$Source, [string]$Destination) {
    Move-Item -LiteralPath $Source -Destination $Destination -Force
    $sourceMeta = "$Source.meta"
    if (Test-Path -LiteralPath $sourceMeta) {
        Move-Item -LiteralPath $sourceMeta -Destination "$Destination.meta" -Force
    }
}

function Remove-DirectoryWithMeta([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    $metaPath = "$Path.meta"
    if (Test-Path -LiteralPath $metaPath) {
        Remove-Item -LiteralPath $metaPath -Force
    }
}

function Restore-DirectoryBackup([string]$BackupPath, [string]$TargetPath) {
    if ([string]::IsNullOrWhiteSpace($BackupPath) -or -not (Test-Path -LiteralPath $BackupPath)) {
        return
    }
    if (Test-Path -LiteralPath $TargetPath) {
        Remove-DirectoryWithMeta $TargetPath
    }
    Move-Item -LiteralPath $BackupPath -Destination $TargetPath -Force
    $backupMetaPath = "$BackupPath.meta"
    if (Test-Path -LiteralPath $backupMetaPath) {
        Move-Item -LiteralPath $backupMetaPath -Destination "$TargetPath.meta" -Force
    }
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
$installedVrcForge = $false

try {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

    if (Test-Path -LiteralPath $legacyTargetToolFolder) {
        Write-Host "Detected legacy VRCForge/VRCAutoRig Unity plugin folder. Migrating to Assets/VRCForge."
        $legacyBackupPath = New-BackupPath $backupRoot "VRCAutoRig"
        Move-DirectoryWithMeta $legacyTargetToolFolder $legacyBackupPath
        if (Test-Path -LiteralPath $legacyTargetToolFolder) {
            throw "Legacy Assets/VRCAutoRig still exists after migration attempt. Stop before installing new plugin."
        }
        if (Test-Path -LiteralPath "$legacyTargetToolFolder.meta") {
            throw "Legacy Assets/VRCAutoRig.meta still exists after migration attempt. Stop before installing new plugin."
        }
        Write-Host "Moved legacy Unity tool folder to: $legacyBackupPath"
    }

    if (Test-Path -LiteralPath $targetVrcForge) {
        $vrcForgeBackupPath = New-BackupPath $backupRoot "VRCForge"
        Move-DirectoryWithMeta $targetVrcForge $vrcForgeBackupPath
        Write-Host "Backed up existing Assets/VRCForge to: $vrcForgeBackupPath"
    }

    try {
        Copy-DirectoryClean $sourceAssets $targetVrcForge
        $installedVrcForge = $true
    } catch {
        Restore-DirectoryBackup $vrcForgeBackupPath $targetVrcForge
        throw
    }

} catch {
    if (-not $installedVrcForge) {
        Restore-DirectoryBackup $vrcForgeBackupPath $targetVrcForge
    }
    throw
}

Write-Host "Installed Assets/VRCForge into: $resolvedProjectPath"
Write-Host "Project backups are under: $backupRoot"
Write-Host ""
Write-Host "Next steps inside Unity:"
Write-Host "1. Open the project and wait for VRCForge scripts to compile."
Write-Host "2. Confirm the Console has no compiler errors and VRCForge MCP Core reports Ready."
Write-Host "3. Open VRCForge App and select this project; the packaged Core connects automatically."
Write-Host "4. Optional: use the VRCForge App Doctor if the 64 Unity tools do not appear."

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
