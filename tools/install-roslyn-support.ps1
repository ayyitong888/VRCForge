param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [string]$CodeAnalysisVersion = "4.12.0",
    [string]$SystemPackageVersion = "8.0.0",
    [string]$SourceRoslynPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$targetFolder = Join-Path $resolvedProjectPath "Assets\Plugins\Roslyn"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vrcautorig-roslyn-" + [System.Guid]::NewGuid().ToString("N"))

$requiredDlls = @(
    "Microsoft.CodeAnalysis.dll",
    "Microsoft.CodeAnalysis.CSharp.dll",
    "Microsoft.CodeAnalysis.Scripting.dll",
    "Microsoft.CodeAnalysis.CSharp.Scripting.dll",
    "System.Collections.Immutable.dll",
    "System.Reflection.Metadata.dll"
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

Write-Host "Installed Roslyn fallback DLLs into: $targetFolder"
Write-Host "Enable Unity scripting define symbol VRCFORGE_ENABLE_ROSLYN to compile and register the legacy vrc_execute_roslyn tool."
Write-Host "No global USE_ROSLYN define is used; VRCForge only enables this fallback through VRCFORGE_ENABLE_ROSLYN."
