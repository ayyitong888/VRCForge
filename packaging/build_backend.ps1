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
    --debug noarchive `
    --name vrcforge_backend `
    --hidden-import agent_approval_transactions `
    --hidden-import agent_checkpoint_recovery `
    --hidden-import agent_skill_registry `
    --hidden-import agent_shell_pty_worker `
    --hidden-import winpty `
    --collect-data winpty `
    --hidden-import tools.vrcforge_agent_mcp_stdio `
    --hidden-import tools.vrcforge_cli `
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
$requiredWinPtyFiles = @(
    "_internal\winpty\OpenConsole.exe",
    "_internal\winpty\winpty-agent.exe"
)
foreach ($relativePath in $requiredWinPtyFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceOutputDir $relativePath))) {
        throw "PyInstaller did not collect required PTY runtime file: $relativePath"
    }
}

# Keep runtime modules inspectable as ordinary onedir files. PyInstaller's
# noarchive mode does not enable verbose imports or the debug bootloader.
$requiredRuntimeModules = @(
    'agent_approval_transactions', 'agent_checkpoint_recovery',
    'unity_read_input_schemas', 'unity_shared_input_schemas',
    'unity_write_input_schemas', 'runtime_observation'
)
foreach ($module in $requiredRuntimeModules) {
    $relativePath = "_internal\$module.pyc"
    if (-not (Test-Path -LiteralPath (Join-Path $sourceOutputDir $relativePath))) {
        throw "PyInstaller did not collect required runtime module: $relativePath"
    }
}

Remove-Item -LiteralPath $resolvedOutputDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null
Copy-Item -Path (Join-Path $sourceOutputDir "*") -Destination $resolvedOutputDir -Recurse -Force

Write-Host "Backend built: $(Join-Path $resolvedOutputDir 'vrcforge_backend.exe')"
