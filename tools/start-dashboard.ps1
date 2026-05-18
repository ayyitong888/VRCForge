param(
    [string]$BindHost = "127.0.0.1",
    [int]$BindPort = 8757,
    [string]$ProxyUrl,
    [switch]$Detached = $true,
    [switch]$OpenBrowser = $true,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardScript = Join-Path $repoRoot "dashboard_server.py"
$requirementsPath = Join-Path $repoRoot "requirements.txt"
$settingsPath = Join-Path $repoRoot ".gemini\settings.json"

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
    Write-Host "Created default settings file: $settingsPath"
    Write-Host "Set GEMINI_API_KEY or save provider settings in the dashboard before using AI features."
}

function Configure-Proxy {
    if ([string]::IsNullOrWhiteSpace($ProxyUrl)) {
        return
    }

    $env:HTTP_PROXY = $ProxyUrl
    $env:HTTPS_PROXY = $ProxyUrl
    $env:ALL_PROXY = $ProxyUrl
    $env:NO_PROXY = "127.0.0.1,localhost"
    Write-Host "Using outbound proxy for provider requests: $ProxyUrl"
}

function Get-PythonExecutable {
    $candidates = @(
        (Join-Path $repoRoot ".venv\Scripts\python.exe"),
        (Get-Command "python.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Get-Command "python" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Could not find python.exe. Install Python or create a .venv first."
}

function Ensure-DashboardDependencies {
    param(
        [string]$PythonExe
    )

    $importCheck = 'import anthropic, fastapi, google.genai, openai, pydantic, uvicorn'
    $quotedPython = '"' + $PythonExe + '"'
    cmd.exe /c "$quotedPython -c ""$importCheck"" >nul 2>nul"
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Host "Installing dashboard dependencies from requirements.txt..."
    & $PythonExe -m pip install -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python dependencies from $requirementsPath"
    }
}

function Wait-ForDashboard {
    param(
        [string]$Url
    )

    $healthUrl = "$Url/api/health"
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2 | Out-Null
            return
        }
        catch {
            Start-Sleep -Milliseconds 400
        }
    }
}

Ensure-DefaultSettings
Configure-Proxy

$pythonExe = Get-PythonExecutable
Ensure-DashboardDependencies -PythonExe $pythonExe

$dashboardUrl = "http://$BindHost`:$BindPort"

if ($CheckOnly) {
    Write-Host "Dashboard startup check passed."
    Write-Host "Repo root: $repoRoot"
    Write-Host "Python: $pythonExe"
    Write-Host "Dashboard script: $dashboardScript"
    Write-Host "Settings file: $settingsPath"
    Write-Host "Dashboard URL: $dashboardUrl"
    exit 0
}

if ($Detached) {
    $command = "Set-Location -LiteralPath '$repoRoot'; & '$pythonExe' '$dashboardScript' --host '$BindHost' --port $BindPort"
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $command
    ) | Out-Null
}
else {
    if ($OpenBrowser) {
        Start-Job -ScriptBlock {
            param($Url)
            Start-Sleep -Seconds 2
            Start-Process $Url
        } -ArgumentList $dashboardUrl | Out-Null
    }

    & $pythonExe $dashboardScript --host $BindHost --port $BindPort
    exit $LASTEXITCODE
}

Wait-ForDashboard -Url $dashboardUrl

if ($OpenBrowser) {
    Start-Process $dashboardUrl
}

Write-Host "Dashboard is starting at $dashboardUrl"
