param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [string]$CodeAnalysisVersion = "4.12.0",
    [string]$SystemPackageVersion = "8.0.0",
    [string]$SourceRoslynPath,
    [switch]$SkipEnableDefine
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$targetFolder = Join-Path $resolvedProjectPath "Assets\Plugins\Roslyn"
$projectBackupFolder = Join-Path $resolvedProjectPath ".vrcforge\backups"
$cscRspPath = Join-Path $resolvedProjectPath "Assets\csc.rsp"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcforge-roslyn-" + [System.Guid]::NewGuid().ToString("N"))

$requiredDlls = @(
    "Microsoft.CodeAnalysis.dll",
    "Microsoft.CodeAnalysis.CSharp.dll",
    "Microsoft.CodeAnalysis.Scripting.dll",
    "Microsoft.CodeAnalysis.CSharp.Scripting.dll",
    "System.Collections.Immutable.dll",
    "System.Reflection.Metadata.dll",
    "System.Memory.dll",
    "System.Runtime.CompilerServices.Unsafe.dll",
    "System.Buffers.dll",
    "System.Threading.Tasks.Extensions.dll"
)

function Copy-RequiredDllsFromFolder {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceFolder
    )

    foreach ($dll in $requiredDlls) {
        $source = Join-Path $SourceFolder $dll
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Required Roslyn DLL is missing: $source"
        }

        Copy-Item -LiteralPath $source -Destination (Join-Path $targetFolder $dll) -Force
    }
}

function Install-NuGetDll {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageName,
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [string]$DllName
    )

    $packageLower = $PackageName.ToLowerInvariant()
    $packageUrl = "https://www.nuget.org/api/v2/package/$PackageName/$Version"
    $packageFile = Join-Path $tempRoot "$packageLower.$Version.zip"
    $extractFolder = Join-Path $tempRoot "$packageLower.$Version"

    Invoke-WebRequest -Uri $packageUrl -OutFile $packageFile
    New-Item -ItemType Directory -Force -Path $extractFolder | Out-Null
    Expand-Archive -LiteralPath $packageFile -DestinationPath $extractFolder -Force

    $candidates = @(
        (Join-Path $extractFolder "lib\netstandard2.0\$DllName"),
        (Join-Path $extractFolder "lib\netstandard2.1\$DllName"),
        (Join-Path $extractFolder "lib\net472\$DllName"),
        (Join-Path $extractFolder "lib\net462\$DllName")
    )

    $sourceDll = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $sourceDll) {
        throw "Could not find $DllName inside $PackageName $Version."
    }

    Copy-Item -LiteralPath $sourceDll -Destination (Join-Path $targetFolder $DllName) -Force
}

function Enable-RoslynScriptingDefine {
    if ($SkipEnableDefine) {
        return
    }

    New-Item -ItemType Directory -Force -Path $projectBackupFolder | Out-Null
    $existingContent = ""
    if (Test-Path -LiteralPath $cscRspPath) {
        $existingContent = Get-Content -LiteralPath $cscRspPath -Raw -Encoding UTF8
        if ($existingContent -match 'VRCFORGE_ENABLE_ROSLYN') {
            return
        }

        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        Copy-Item -LiteralPath $cscRspPath -Destination (Join-Path $projectBackupFolder "csc_rsp_$timestamp.rsp") -Force
    }

    $line = "-define:VRCFORGE_ENABLE_ROSLYN"
    if ([string]::IsNullOrWhiteSpace($existingContent)) {
        Set-Content -LiteralPath $cscRspPath -Value $line -Encoding UTF8
        return
    }

    Add-Content -LiteralPath $cscRspPath -Value $line -Encoding UTF8
}

if (-not (Test-Path -LiteralPath (Join-Path $resolvedProjectPath "Assets"))) {
    throw "Target Unity project is missing Assets/: $resolvedProjectPath"
}

New-Item -ItemType Directory -Force -Path $targetFolder | Out-Null

try {
    if (-not [string]::IsNullOrWhiteSpace($SourceRoslynPath)) {
        $resolvedSource = (Resolve-Path -LiteralPath $SourceRoslynPath).Path
        Copy-RequiredDllsFromFolder -SourceFolder $resolvedSource
    } else {
        New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
        Install-NuGetDll -PackageName "Microsoft.CodeAnalysis.Common" -Version $CodeAnalysisVersion -DllName "Microsoft.CodeAnalysis.dll"
        Install-NuGetDll -PackageName "Microsoft.CodeAnalysis.CSharp" -Version $CodeAnalysisVersion -DllName "Microsoft.CodeAnalysis.CSharp.dll"
        Install-NuGetDll -PackageName "Microsoft.CodeAnalysis.Scripting.Common" -Version $CodeAnalysisVersion -DllName "Microsoft.CodeAnalysis.Scripting.dll"
        Install-NuGetDll -PackageName "Microsoft.CodeAnalysis.CSharp.Scripting" -Version $CodeAnalysisVersion -DllName "Microsoft.CodeAnalysis.CSharp.Scripting.dll"
        Install-NuGetDll -PackageName "System.Collections.Immutable" -Version $SystemPackageVersion -DllName "System.Collections.Immutable.dll"
        Install-NuGetDll -PackageName "System.Reflection.Metadata" -Version $SystemPackageVersion -DllName "System.Reflection.Metadata.dll"
        Install-NuGetDll -PackageName "System.Memory" -Version "4.5.5" -DllName "System.Memory.dll"
        Install-NuGetDll -PackageName "System.Runtime.CompilerServices.Unsafe" -Version "6.0.0" -DllName "System.Runtime.CompilerServices.Unsafe.dll"
        Install-NuGetDll -PackageName "System.Buffers" -Version "4.5.1" -DllName "System.Buffers.dll"
        Install-NuGetDll -PackageName "System.Threading.Tasks.Extensions" -Version "4.5.4" -DllName "System.Threading.Tasks.Extensions.dll"
    }
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

$missing = @($requiredDlls | Where-Object { -not (Test-Path -LiteralPath (Join-Path $targetFolder $_)) })
if ($missing.Count -gt 0) {
    throw "Roslyn installation incomplete. Missing: $($missing -join ', ')"
}

Enable-RoslynScriptingDefine

Write-Host "Installed Roslyn Advanced Power Mode DLLs into: $targetFolder"
if ($SkipEnableDefine) {
    Write-Host "Skipped automatic VRCFORGE_ENABLE_ROSLYN define update. Enable it manually before using vrc_execute_roslyn."
} else {
    Write-Host "Enabled VRCFORGE_ENABLE_ROSLYN through Assets\csc.rsp. Unity must recompile before vrc_execute_roslyn appears."
}
Write-Host "Every call must pass confirmAdvancedPowerMode=true and approve the Unity warning dialog before code executes."
Write-Host "No global USE_ROSLYN define is used; VRCForge only enables this advanced mode through VRCFORGE_ENABLE_ROSLYN."
