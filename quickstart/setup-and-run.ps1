param(
    [string]$ProjectPath,
    [string]$UnityEditorPath,
    [string]$BindHost = "127.0.0.1",
    [int]$BindPort = 8757,
    [string]$ProxyUrl,
    [switch]$SkipUnityInstall,
    [switch]$LaunchUnity,
    [switch]$NoDashboard,
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$quickstartRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $quickstartRoot
$requirementsPath = Join-Path $repoRoot "requirements.txt"
$dashboardScript = Join-Path $repoRoot "dashboard_server.py"
$installUnityScript = Join-Path $repoRoot "tools\install-unity-project.ps1"
$startDashboardScript = Join-Path $repoRoot "tools\start-dashboard.ps1"
$settingsPath = Join-Path $repoRoot ".gemini\settings.json"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Warning $Message
}

function Write-Utf8NoBomFile {
    param(
        [string]$Path,
        [string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Ensure-DefaultSettings {
    $settingsDir = Split-Path -Parent $settingsPath
    if (-not (Test-Path -LiteralPath $settingsDir)) {
        New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
    }

    if (Test-Path -LiteralPath $settingsPath) {
        Write-Ok "Settings file found: $settingsPath"
        return
    }

    $settingsJson = @'
{
  "gemini": {
    "api_key_env": "GEMINI_API_KEY",
    "model": "gemini-2.5-flash",
    "thinking_level": ""
  },
  "unity_mcp": {
    "command": [
      "powershell",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      "tools/unity-mcp-cli.ps1"
    ],
    "host": "127.0.0.1",
    "port": 8080,
    "instance": "",
    "retries": 3,
    "retry_backoff_seconds": 2.0,
    "timeout_seconds": 30,
    "export_tool_name": "vrc_export_blendshapes",
    "execute_tool_name": "vrc_apply_blendshapes"
  },
  "paths": {
    "blendshape_export": "Assets/VRCForge/blendshapes_export.json"
  },
  "planning": {
    "min_confidence": 0.65
  },
  "dashboard": {
    "project_roots": [],
    "unity_editor_path": "",
    "status_push_interval_seconds": 2.5
  }
}
'@

    Write-Utf8NoBomFile -Path $settingsPath -Content $settingsJson
    Write-Ok "Created default settings file without UTF-8 BOM: $settingsPath"
}

function Configure-Proxy {
    if ([string]::IsNullOrWhiteSpace($ProxyUrl)) {
        return
    }

    $env:HTTP_PROXY = $ProxyUrl
    $env:HTTPS_PROXY = $ProxyUrl
    $env:ALL_PROXY = $ProxyUrl
    $env:NO_PROXY = "127.0.0.1,localhost"
    Write-Ok "Provider requests will use proxy: $ProxyUrl"
}

function Resolve-RequiredPath {
    param(
        [string]$Path,
        [string]$Label
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Label is empty."
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label does not exist: $Path"
    }

    return (Resolve-Path -LiteralPath $Path).Path
}

function Test-CommandAvailable {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-PythonExecutable {
    $candidates = @(
        (Join-Path $repoRoot ".venv\Scripts\python.exe"),
        (Get-Command "python.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Get-Command "python" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Get-Command "py.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Python was not found. Install Python 3.10+ and make sure python is on PATH."
}

function Get-PythonVersion {
    param([string]$PythonExe)

    $versionText = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) {
        throw "Could not execute Python: $PythonExe"
    }

    return [version]$versionText.Trim()
}

function Ensure-Pip {
    param([string]$PythonExe)

    & $PythonExe -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "pip was not found; trying ensurepip."
        & $PythonExe -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) {
            throw "pip is required but could not be installed."
        }
    }
}

function Ensure-PythonDependencies {
    param([string]$PythonExe)

    $importCheck = "import anthropic, fastapi, google.genai, openai, pydantic, uvicorn"
    & $PythonExe -c $importCheck 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Python dependencies are already installed."
        return
    }

    Write-Step "Installing Python dependencies"
    & $PythonExe -m pip install -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from $requirementsPath"
    }
    Write-Ok "Python dependencies installed."
}

function Test-PortAvailable {
    param(
        [string]$HostName,
        [int]$Port
    )

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse($HostName), $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Read-JsonFile {
    param([string]$Path)

    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        throw "Invalid JSON file: $Path"
    }
}

function Test-UnityProject {
    param([string]$Path)

    $resolved = Resolve-RequiredPath -Path $Path -Label "Unity project path"
    $assetsPath = Join-Path $resolved "Assets"
    $manifestPath = Join-Path $resolved "Packages\manifest.json"
    $projectVersionPath = Join-Path $resolved "ProjectSettings\ProjectVersion.txt"

    if (-not (Test-Path -LiteralPath $assetsPath)) {
        throw "Unity project is missing Assets/: $assetsPath"
    }
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Unity project is missing Packages/manifest.json: $manifestPath"
    }
    if (-not (Test-Path -LiteralPath $projectVersionPath)) {
        throw "Unity project is missing ProjectSettings/ProjectVersion.txt: $projectVersionPath"
    }

    $manifest = Read-JsonFile -Path $manifestPath
    $dependencies = $manifest.dependencies
    if ($null -eq $dependencies) {
        Write-Warn "Packages/manifest.json has no dependencies object."
    }
    else {
        $dependencyNames = @($dependencies.PSObject.Properties.Name)
        $hasVrcSdk = $dependencyNames | Where-Object {
            $_ -like "*vrchat*" -or $_ -like "*vrcsdk*" -or $_ -like "*avatars*"
        }
        if ($hasVrcSdk) {
            Write-Ok "VRChat SDK-like package reference found: $($hasVrcSdk -join ', ')"
        }
        else {
            Write-Warn "VRChat SDK package was not obvious in Packages/manifest.json. Make sure this is a VRChat Avatar SDK project."
        }

        if ($dependencyNames -contains "com.unity.nuget.newtonsoft-json") {
            Write-Ok "Unity Newtonsoft Json dependency found."
        }
        else {
            Write-Warn "Unity Newtonsoft Json is not listed. Add com.unity.nuget.newtonsoft-json if Unity reports JSON compile errors."
        }
    }

    $versionText = Get-Content -LiteralPath $projectVersionPath -Raw
    if ($versionText -match "m_EditorVersion:\s*(.+)") {
        Write-Ok "Unity project editor version: $($Matches[1].Trim())"
    }

    return $resolved
}

function Ensure-UnityInstall {
    param(
        [string]$ResolvedProjectPath,
        [string]$EditorPath,
        [switch]$Launch
    )

    $args = @("-ExecutionPolicy", "Bypass", "-File", $installUnityScript, "-ProjectPath", $ResolvedProjectPath)
    $sourceMcpPackage = Join-Path $repoRoot "third_party\com.coplaydev.unity-mcp"
    if (Test-Path -LiteralPath $sourceMcpPackage) {
        $args += @("-SourceMcpPackagePath", $sourceMcpPackage)
    }
    if ($Launch) {
        if ([string]::IsNullOrWhiteSpace($EditorPath)) {
            throw "LaunchUnity was requested but UnityEditorPath is empty."
        }
        $resolvedEditor = Resolve-RequiredPath -Path $EditorPath -Label "Unity editor path"
        $args += @("-UnityEditorPath", $resolvedEditor, "-LaunchUnity")
    }

    & powershell @args
    if ($LASTEXITCODE -ne 0) {
        throw "Unity-side install failed."
    }

    $targetVrcForge = Join-Path $ResolvedProjectPath "Assets\VRCForge"
    if (-not (Test-Path -LiteralPath $targetVrcForge)) {
        throw "Unity-side install did not create Assets/VRCForge."
    }

    $manifest = Read-JsonFile -Path (Join-Path $ResolvedProjectPath "Packages\manifest.json")
    $mcpDependency = $manifest.dependencies.PSObject.Properties["com.coplaydev.unity-mcp"]
    if ($null -eq $mcpDependency) {
        Write-Warn "MCP for Unity dependency was not configured because no local CoplayDev package was bundled. Use the Windows installer release for the no-Git/no-manual-import path."
    }
    else {
        Write-Ok "MCP for Unity dependency: $($mcpDependency.Value)"
    }

    Write-Ok "Unity-side VRCForge tools installed."
}

function Start-Dashboard {
    param(
        [string]$HostName,
        [int]$Port,
        [switch]$OpenBrowser
    )

    $args = @("-ExecutionPolicy", "Bypass", "-File", $startDashboardScript, "-BindHost", $HostName, "-BindPort", $Port)
    if (-not [string]::IsNullOrWhiteSpace($ProxyUrl)) {
        $args += @("-ProxyUrl", $ProxyUrl)
    }
    if (-not $OpenBrowser) {
        $args += "-OpenBrowser:`$false"
    }

    & powershell @args
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard failed to start."
    }
}

Write-Host "VRCForge one-click setup"
Write-Host "Repo root: $repoRoot"

Write-Step "Checking local repository files"
foreach ($requiredFile in @($requirementsPath, $dashboardScript, $installUnityScript, $startDashboardScript)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Required file is missing: $requiredFile"
    }
}
Write-Ok "Required VRCForge files found."

Write-Step "Checking system tools"
if (Test-CommandAvailable -Name "git") {
    $gitVersion = git --version
    Write-Ok $gitVersion
}
else {
    Write-Warn "Git was not found on PATH. That is OK after download, but clone/update commands will not work."
}

if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "PowerShell 5.1+ is required."
}
Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

Write-Step "Checking Python"
$pythonExe = Get-PythonExecutable
$pythonVersion = Get-PythonVersion -PythonExe $pythonExe
if ($pythonVersion -lt [version]"3.10.0") {
    throw "Python 3.10+ is required. Found $pythonVersion at $pythonExe"
}
Write-Ok "Python $pythonVersion at $pythonExe"
Ensure-Pip -PythonExe $pythonExe
Write-Ok "pip is available."
Ensure-PythonDependencies -PythonExe $pythonExe

Write-Step "Checking first-run settings"
Ensure-DefaultSettings
Configure-Proxy

Write-Step "Checking dashboard port"
if (-not (Test-PortAvailable -HostName $BindHost -Port $BindPort)) {
    throw "Port $BindHost`:$BindPort is already in use. Pass -BindPort with another port or close the existing process."
}
Write-Ok "Dashboard port is available: http://$BindHost`:$BindPort"

if (-not $SkipUnityInstall) {
    if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
        $ProjectPath = Read-Host "Enter the full path to your Unity Avatar project, or press Enter to skip Unity install"
    }

    if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
        Write-Warn "Unity install skipped because no ProjectPath was provided."
    }
    else {
        Write-Step "Checking Unity project"
        $resolvedProjectPath = Test-UnityProject -Path $ProjectPath
        Write-Ok "Unity project path: $resolvedProjectPath"

        Write-Step "Installing Unity-side VRCForge tools"
        Ensure-UnityInstall -ResolvedProjectPath $resolvedProjectPath -EditorPath $UnityEditorPath -Launch:$LaunchUnity
    }
}
else {
    Write-Warn "Unity install skipped by -SkipUnityInstall."
}

Write-Host ""
Write-Host "Unity next steps:"
Write-Host "1. Open the Unity Avatar project and wait for package resolution / C# compile."
Write-Host "2. Start the MCP bridge from Unity: VRCForge / MCP / Start Bridge Now."
Write-Host "3. Dashboard should connect to the MCP bridge at http://127.0.0.1:8080."

if ($CheckOnly) {
    Write-Host ""
    Write-Ok "Check-only mode passed."
    exit 0
}

if ($NoDashboard) {
    Write-Host ""
    Write-Ok "Setup checks completed. Dashboard launch skipped by -NoDashboard."
    exit 0
}

Write-Step "Starting Dashboard"
Start-Dashboard -HostName $BindHost -Port $BindPort -OpenBrowser:(!$NoBrowser)
Write-Ok "Dashboard startup requested: http://$BindHost`:$BindPort"
