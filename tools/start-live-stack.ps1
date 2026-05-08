param(
    [string]$ProjectPath = "E:\unity\Projects\manuka FT2",
    [string]$UnityEditorPath = "E:\unity\Unity 2022.3.22f1\Editor\Unity.exe",
    [string]$HostAddress = "127.0.0.1",
    [int]$McpPort = 8080,
    [int]$DashboardPort = 8757,
    [int]$UnityWaitSeconds = 360,
    [switch]$NoOpenUnity,
    [switch]$NoOpenBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardScript = Join-Path $repoRoot "dashboard_server.py"
$dashboardCheckScript = Join-Path $repoRoot "tools\start-dashboard.ps1"
$dashboardUrl = "http://$HostAddress`:$DashboardPort"
$mcpUrl = "http://$HostAddress`:$McpPort"

function Get-PythonExecutable {
    $candidates = @(
        (Join-Path $repoRoot ".venv\Scripts\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
        (Get-Command "python.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Get-Command "python" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Could not find python.exe."
}

function Get-McpForUnityExecutable {
    $candidates = @(
        (Join-Path $env:APPDATA "Python\Python314\Scripts\mcp-for-unity.exe"),
        (Get-Command "mcp-for-unity.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Could not find mcp-for-unity.exe."
}

function Test-HttpOk {
    param([string]$Url)

    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec 3 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [string]$Name,
        [string]$Url,
        [int]$Seconds
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk -Url $Url) {
            Write-Host "$Name is ready: $Url"
            return
        }
        Start-Sleep -Seconds 2
    }

    throw "$Name did not become ready: $Url"
}

function Invoke-DashboardJson {
    param(
        [string]$Path,
        [hashtable]$Body,
        [int]$TimeoutSec = 30
    )

    $json = $Body | ConvertTo-Json -Depth 12
    return Invoke-RestMethod -Uri "$dashboardUrl$Path" -Method Post -ContentType "application/json" -Body $json -TimeoutSec $TimeoutSec
}

function Get-UnityInstances {
    try {
        $response = Invoke-DashboardJson -Path "/api/unity/instances" -Body @{
            unity_host = $HostAddress
            unity_port = $McpPort
            unity_instance = ""
        } -TimeoutSec 20
        if ($response.parsed -and $response.parsed.instances) {
            return @($response.parsed.instances)
        }
    }
    catch {
        return @()
    }

    return @()
}

function Wait-UnityInstance {
    param(
        [string]$ProjectName,
        [int]$Seconds
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $instances = Get-UnityInstances
        $match = $instances | Where-Object { $_.project -eq $ProjectName } | Select-Object -First 1
        if ($match) {
            Write-Host "Unity Bridge is ready: $($match.project) / $($match.unity_version)"
            return $match
        }
        Start-Sleep -Seconds 5
    }

    throw "Unity Bridge did not register project '$ProjectName' within $Seconds seconds."
}

function Ensure-McpServer {
    if (Test-HttpOk -Url "$mcpUrl/health") {
        Write-Host "MCP server already running: $mcpUrl"
        return
    }

    $mcpExe = Get-McpForUnityExecutable
    Write-Host "Starting MCP server: $mcpUrl"
    Start-Process -WindowStyle Hidden -FilePath $mcpExe -ArgumentList @(
        "--transport",
        "http",
        "--http-url",
        $mcpUrl,
        "--project-scoped-tools"
    ) | Out-Null
    Wait-HttpOk -Name "MCP server" -Url "$mcpUrl/health" -Seconds 45
}

function Ensure-Dashboard {
    if (Test-HttpOk -Url "$dashboardUrl/api/health") {
        Write-Host "Dashboard already running: $dashboardUrl"
        return
    }

    $pythonExe = Get-PythonExecutable
    & powershell -NoProfile -ExecutionPolicy Bypass -File $dashboardCheckScript -CheckOnly | Out-Host

    Write-Host "Starting dashboard: $dashboardUrl"
    Start-Process -WindowStyle Hidden -FilePath $pythonExe -ArgumentList @(
        $dashboardScript,
        "--host",
        $HostAddress,
        "--port",
        "$DashboardPort"
    ) -WorkingDirectory $repoRoot | Out-Null
    Wait-HttpOk -Name "Dashboard" -Url "$dashboardUrl/api/health" -Seconds 60
}

function Ensure-UnityProject {
    $projectName = Split-Path -Leaf $ProjectPath
    $existing = Get-UnityInstances | Where-Object { $_.project -eq $projectName } | Select-Object -First 1
    if ($existing) {
        Write-Host "Unity project already connected: $projectName"
        return $existing
    }

    if ($NoOpenUnity) {
        Write-Host "Unity launch skipped. Waiting for an existing Unity Bridge..."
        return Wait-UnityInstance -ProjectName $projectName -Seconds $UnityWaitSeconds
    }

    if (-not (Test-Path -LiteralPath $UnityEditorPath)) {
        throw "Unity editor not found: $UnityEditorPath"
    }
    if (-not (Test-Path -LiteralPath $ProjectPath)) {
        throw "Unity project not found: $ProjectPath"
    }

    Write-Host "Opening Unity project: $ProjectPath"
    Invoke-DashboardJson -Path "/api/projects/open" -Body @{
        project_path = $ProjectPath
    } -TimeoutSec 20 | Out-Null

    return Wait-UnityInstance -ProjectName $projectName -Seconds $UnityWaitSeconds
}

Set-Location -LiteralPath $repoRoot
Ensure-McpServer
Ensure-Dashboard
$unityInstance = Ensure-UnityProject

if (-not $NoOpenBrowser) {
    Start-Process $dashboardUrl
}

Write-Host ""
Write-Host "Live stack is ready."
Write-Host "Dashboard: $dashboardUrl"
Write-Host "MCP server: $mcpUrl"
Write-Host "Unity project: $($unityInstance.project)"
