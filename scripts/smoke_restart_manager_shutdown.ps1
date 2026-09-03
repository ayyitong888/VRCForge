param(
    [string]$PackageRoot = (Join-Path $PSScriptRoot "..\dist\VRCForge_Windows_x64")
)

$ErrorActionPreference = "Stop"

function Get-PackageProcesses([string]$Root) {
    $prefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try {
            $path = [IO.Path]::GetFullPath($_.Path)
            $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
        } catch {
            $false
        }
    })
}

function ConvertTo-ProcessEvidence([object[]]$Processes) {
    return @($Processes | ForEach-Object {
        try {
            [pscustomobject]@{
                processId = $_.Id
                processName = $_.ProcessName
                executablePath = [IO.Path]::GetFullPath($_.MainModule.FileName)
                startTimeFileTimeUtc = $_.StartTime.ToFileTimeUtc()
            }
        } catch {
            [pscustomobject]@{
                processId = $_.Id
                processName = $_.ProcessName
                executablePath = "unavailable-after-exit"
                startTimeFileTimeUtc = $null
            }
        }
    })
}

$packageRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $PackageRoot).Path)
$desktopPath = Join-Path $packageRoot "VRCForge.exe"
$backendPath = Join-Path $packageRoot "backend\vrcforge_backend.exe"
foreach ($path in @($desktopPath, $backendPath)) {
    if (-not [IO.File]::Exists($path)) { throw "Packaged executable not found: $path" }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("vrcforge-rm-smoke-" + [Guid]::NewGuid().ToString("N"))
[void](New-Item -ItemType Directory -Path $testRoot)

try {
    # Scope and lifetime: the launched build inherits only this process-local
    # test profile, which is removed after the bounded smoke test. No production
    # VRCForge profile or installed executable is read or mutated.
    $env:VRCFORGE_USER_DATA_DIR = Join-Path $testRoot "userdata"
    $env:VRCFORGE_CONFIG_DIR = Join-Path $testRoot "config"
    $env:VRCFORGE_CONFIG_PATH = Join-Path $env:VRCFORGE_CONFIG_DIR "config.json"
    $env:VRCFORGE_SETTINGS_PATH = Join-Path $env:VRCFORGE_CONFIG_DIR "settings.json"
    $env:VRCFORGE_LOG_DIR = Join-Path $env:VRCFORGE_USER_DATA_DIR "logs"
    $env:VRCFORGE_ARTIFACTS_DIR = Join-Path $env:VRCFORGE_USER_DATA_DIR "artifacts"
    $env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $testRoot "webview2"

    $desktop = Start-Process -FilePath $desktopPath -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $before = @(Get-PackageProcesses $packageRoot)
    } until ($before.Count -ge 2 -or [DateTime]::UtcNow -ge $deadline)
    if ($before.Count -lt 2) {
        throw "Packaged desktop/backend pair did not start within the bounded wait."
    }
    $beforeEvidence = @(ConvertTo-ProcessEvidence $before)

    if ($null -eq ("VrcForgeRmSmoke" -as [type])) {
        Add-Type -Language CSharp -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class VrcForgeRmSmoke {
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    public static extern int RmStartSession(out uint handle, uint flags, StringBuilder key);
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    public static extern int RmRegisterResources(uint handle, uint fileCount, string[] fileNames,
        uint applicationCount, IntPtr applications, uint serviceCount, string[] serviceNames);
    [DllImport("rstrtmgr.dll")]
    public static extern int RmShutdown(uint handle, uint flags, IntPtr callback);
    [DllImport("rstrtmgr.dll")]
    public static extern int RmEndSession(uint handle);
}
"@
    }

    [uint32]$sessionHandle = 0
    $sessionKey = New-Object Text.StringBuilder 33
    [void]$sessionKey.Append([Guid]::NewGuid().ToString("N"))
    $startResult = [VrcForgeRmSmoke]::RmStartSession([ref]$sessionHandle, 0, $sessionKey)
    if ($startResult -ne 0) { throw "RmStartSession failed: $startResult" }
    try {
        # The desktop owns the backend lifetime. Register the desktop resource
        # here so this smoke isolates the cooperative window hook; the installer
        # separately registers both files and uses its identity-checked legacy
        # fallback when the console backend makes Restart Manager return 351.
        [string[]]$resources = @($desktopPath)
        $registerResult = [VrcForgeRmSmoke]::RmRegisterResources(
            $sessionHandle, [uint32]$resources.Length, $resources,
            0, [IntPtr]::Zero, 0, $null)
        if ($registerResult -ne 0) { throw "RmRegisterResources failed: $registerResult" }
        # Authentication boundary: Windows Restart Manager addresses only the
        # two exact packaged resources. Zero flags prohibit forced shutdown.
        $shutdownResult = [VrcForgeRmSmoke]::RmShutdown($sessionHandle, 0, [IntPtr]::Zero)
        if ($shutdownResult -ne 0) { throw "RmShutdown failed: $shutdownResult" }
    } finally {
        [void][VrcForgeRmSmoke]::RmEndSession($sessionHandle)
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        $after = @(Get-PackageProcesses $packageRoot)
    } until ($after.Count -eq 0 -or [DateTime]::UtcNow -ge $deadline)

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8757 -ErrorAction SilentlyContinue)
    [pscustomobject]@{
        ok = $after.Count -eq 0
        packageRoot = $packageRoot
        before = $beforeEvidence
        after = @(ConvertTo-ProcessEvidence $after)
        port8757Listeners = @($listeners | Select-Object OwningProcess)
    } | ConvertTo-Json -Depth 5

    if ($after.Count -ne 0) {
        throw "Restart Manager shutdown left packaged processes running."
    }
} finally {
    foreach ($process in @(Get-PackageProcesses $packageRoot)) {
        try { $process.Kill(); [void]$process.WaitForExit(5000) } catch { }
        $process.Dispose()
    }
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $leaf = Split-Path -Leaf $resolvedTestRoot
    if ($resolvedTestRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf.StartsWith("vrcforge-rm-smoke-", [StringComparison]::Ordinal)) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
