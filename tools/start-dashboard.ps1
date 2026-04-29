param(
    [string]$BindHost = "127.0.0.1",
    [int]$BindPort = 8757,
    [switch]$Detached = $true,
    [switch]$OpenBrowser = $true,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardScript = Join-Path $repoRoot "dashboard_server.py"
$requirementsPath = Join-Path $repoRoot "requirements.txt"

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

    & $PythonExe -c "import fastapi, uvicorn, google.genai, pydantic" 2>$null
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

$pythonExe = Get-PythonExecutable
Ensure-DashboardDependencies -PythonExe $pythonExe

$dashboardUrl = "http://$BindHost`:$BindPort"

if ($CheckOnly) {
    Write-Host "Dashboard startup check passed."
    Write-Host "Repo root: $repoRoot"
    Write-Host "Python: $pythonExe"
    Write-Host "Dashboard script: $dashboardScript"
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
