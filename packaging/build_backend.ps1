param(
    [string]$OutputDir = "dist\backend",
    [string]$SkillSigningKeyPath = $env:VRCFORGE_SKILL_SIGNING_KEY_PATH
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

# Ship the same complete first-party source used for .vsk export. The backend
# projects it for new profiles without weakening community package trust.
$bundledGuide = Join-Path $repoRoot "examples\skill-packages\vrcforge-first-run-guide"
$bundledManifest = Get-Content -LiteralPath (Join-Path $bundledGuide "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$bundledGuideFiles = @("manifest.json") + @($bundledManifest.entrypoints.PSObject.Properties.Value)
foreach ($relativePath in $bundledGuideFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundledGuide $relativePath) -PathType Leaf)) {
        throw "Bundled setup guide file is missing: $relativePath"
    }
}
$bundledDocs = Join-Path $repoRoot "examples\skill-packages\vrcforge-docs"
$bundledDocsManifest = Get-Content -LiteralPath (Join-Path $bundledDocs "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$bundledDocsFiles = @("manifest.json") + @($bundledDocsManifest.entrypoints.PSObject.Properties.Value)
foreach ($relativePath in $bundledDocsFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundledDocs $relativePath) -PathType Leaf)) {
        throw "Bundled VRCForge Docs file is missing: $relativePath"
    }
}

$bundledRoles = Join-Path $repoRoot "resources\sub-agents"
$bundledRoleFiles = @(Get-ChildItem -LiteralPath $bundledRoles -Filter "*.json" -File)
if ($bundledRoleFiles.Count -eq 0) {
    throw "Built-in sub-agent role declarations are missing."
}

$bundledAvatarSkills = Join-Path $repoRoot "build\bundled-avatar-skills"
if ([string]::IsNullOrWhiteSpace($SkillSigningKeyPath)) {
    throw "The existing Avatar Skill signer is required. Set VRCFORGE_SKILL_SIGNING_KEY_PATH or pass -SkillSigningKeyPath; setup does not replace signed Skills with development packages."
}
& python (Join-Path $repoRoot "packaging\build_avatar_skill_bundles.py") --output $bundledAvatarSkills --signing-key $SkillSigningKeyPath
if ($LASTEXITCODE -ne 0) { throw "Bundled Avatar Skill export failed." }
$bundledAvatarIndex = Get-Content -LiteralPath (Join-Path $bundledAvatarSkills "index.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$avatarSkillDataArgs = @("--add-data", "$(Join-Path $bundledAvatarSkills 'index.json');resources/skill-packages")
foreach ($package in $bundledAvatarIndex.packages) {
    $avatarSkillDataArgs += @("--add-data", "$(Join-Path $bundledAvatarSkills $package.file);resources/skill-packages")
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
    --add-data "$bundledGuide;examples/skill-packages/vrcforge-first-run-guide" `
    --add-data "$bundledDocs;examples/skill-packages/vrcforge-docs" `
    --add-data "$bundledRoles;resources/sub-agents" `
    @avatarSkillDataArgs `
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
# STOPGAP: PyInstaller's bundled CPython 3.14.3 predates gh-93821. Remove this
# build-time backport for 1.10.5 once its builder adopts a release containing c99e750.
& python (Join-Path $repoRoot "packaging\apply_cpython_gh93821.py") --backend $sourceOutputDir
if ($LASTEXITCODE -ne 0) { throw "CPython Windows asyncio accept fix failed." }
$collectedAvatarRoot = Join-Path $sourceOutputDir "_internal\resources\skill-packages"
foreach ($filename in @("index.json") + @($bundledAvatarIndex.packages.file)) {
    if (-not (Test-Path -LiteralPath (Join-Path $collectedAvatarRoot $filename) -PathType Leaf)) {
        throw "PyInstaller did not collect the bundled Avatar Skill resource: $filename"
    }
}
foreach ($relativePath in $bundledGuideFiles) {
    $collectedPath = Join-Path $sourceOutputDir "_internal\examples\skill-packages\vrcforge-first-run-guide\$relativePath"
    if (-not (Test-Path -LiteralPath $collectedPath -PathType Leaf)) {
        throw "PyInstaller did not collect the bundled setup guide: $relativePath"
    }
    if ((Get-FileHash -LiteralPath $collectedPath).Hash -ne (Get-FileHash -LiteralPath (Join-Path $bundledGuide $relativePath)).Hash) {
        throw "Bundled setup guide differs from its source: $relativePath"
    }
}
foreach ($relativePath in $bundledDocsFiles) {
    $collectedPath = Join-Path $sourceOutputDir "_internal\examples\skill-packages\vrcforge-docs\$relativePath"
    if (-not (Test-Path -LiteralPath $collectedPath -PathType Leaf)) {
        throw "PyInstaller did not collect VRCForge Docs: $relativePath"
    }
    if ((Get-FileHash -LiteralPath $collectedPath).Hash -ne (Get-FileHash -LiteralPath (Join-Path $bundledDocs $relativePath)).Hash) {
        throw "Bundled VRCForge Docs differs from its source: $relativePath"
    }
}
foreach ($roleFile in $bundledRoleFiles) {
    $collectedPath = Join-Path $sourceOutputDir "_internal\resources\sub-agents\$($roleFile.Name)"
    if (-not (Test-Path -LiteralPath $collectedPath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $collectedPath).Hash -ne (Get-FileHash -LiteralPath $roleFile.FullName).Hash) {
        throw "Bundled sub-agent role declaration missing or changed: $($roleFile.Name)"
    }
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
