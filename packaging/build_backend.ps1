param(
    [string]$OutputDir = "dist\backend"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir
} else {
    Join-Path $repoRoot $OutputDir
}

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    throw "PyInstaller is required to build backend/vrcforge_backend.exe."
}

New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null
$tempDist = Join-Path $repoRoot "build\pyinstaller_dist"
Remove-Item -LiteralPath $tempDist -Recurse -Force -ErrorAction SilentlyContinue
$excludeModules = @(
    "IPython",
    "boto3",
    "botocore",
    "matplotlib",
    "numba",
    "onnxruntime",
    "pandas",
    "pytest",
    "scipy",
    "sqlalchemy",
    "torch",
    "torchaudio",
    "torchvision"
)
$excludeArgs = @()
foreach ($module in $excludeModules) {
    $excludeArgs += @("--exclude-module", $module)
}

& $pyinstaller.Source `
    --noconfirm `
    --clean `
    --onedir `
    --name vrcforge_backend `
    @excludeArgs `
    --distpath $tempDist `
    --specpath (Join-Path $repoRoot "build\pyinstaller") `
    --workpath (Join-Path $repoRoot "build\pyinstaller") `
    (Join-Path $repoRoot "dashboard_server.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$sourceOutputDir = Join-Path $tempDist "vrcforge_backend"
if (-not (Test-Path -LiteralPath (Join-Path $sourceOutputDir "vrcforge_backend.exe"))) {
    throw "PyInstaller did not produce vrcforge_backend.exe."
}

Remove-Item -LiteralPath $resolvedOutputDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null
Copy-Item -Path (Join-Path $sourceOutputDir "*") -Destination $resolvedOutputDir -Recurse -Force

Write-Host "Backend built: $(Join-Path $resolvedOutputDir 'vrcforge_backend.exe')"
