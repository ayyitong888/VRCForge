from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_restart_manager_add_type_ignores_nsis_system_dll_collision(tmp_path: Path) -> None:
    helper = (ROOT / "installer/VRCForge_WebPayload.ps1").read_text(encoding="utf-8")

    assert "$restartManagerReferences = @([object].Assembly.Location)" in helper
    assert (
        "Add-Type -Language CSharp -ReferencedAssemblies $restartManagerReferences -TypeDefinition"
        in helper
    )
    assert "$restartManagerCompileRoot = Assert-NoReparsePath $PSScriptRoot" in helper
    assert 'contains an unexpected System.dll' in helper
    assert "$restartManagerPreviousDirectory = [Environment]::CurrentDirectory" in helper
    assert "[Environment]::CurrentDirectory = $restartManagerCompileRoot" in helper
    assert "[Environment]::CurrentDirectory = $restartManagerPreviousDirectory" in helper

    collision_dir = tmp_path / "nsis-plugin-dir"
    collision_dir.mkdir()
    nsis_plugins = list(
        (Path(os.environ["LOCALAPPDATA"]) / "Programs/NSIS").glob(
            "nsis-*/Plugins/x86-unicode/System.dll"
        )
    )
    if not nsis_plugins:
        pytest.skip("NSIS x86-unicode System.dll is unavailable")
    shutil.copyfile(nsis_plugins[-1], collision_dir / "System.dll")
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        r'''
$ErrorActionPreference = "Stop"
[Environment]::CurrentDirectory = (Resolve-Path -LiteralPath $args[0]).Path
$restartManagerReferences = @([object].Assembly.Location)
$restartManagerCompileRoot = $PSScriptRoot
$restartManagerPreviousDirectory = [Environment]::CurrentDirectory
[Environment]::CurrentDirectory = $restartManagerCompileRoot
try {
    Add-Type -Language CSharp -ReferencedAssemblies $restartManagerReferences -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class VrcForgeRestartManagerReferenceProbe {
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    public static extern int RmStartSession(out uint sessionHandle, uint sessionFlags, StringBuilder sessionKey);
    [DllImport("rstrtmgr.dll")]
    public static extern int RmEndSession(uint sessionHandle);
}
"@
} finally {
    [Environment]::CurrentDirectory = $restartManagerPreviousDirectory
}
[uint32]$sessionHandle = 0
$sessionKey = New-Object Text.StringBuilder 33
[void]$sessionKey.Append([Guid]::NewGuid().ToString("N"))
$result = [VrcForgeRestartManagerReferenceProbe]::RmStartSession([ref]$sessionHandle, 0, $sessionKey)
if ($result -ne 0) { throw "RmStartSession failed: $result" }
[void][VrcForgeRestartManagerReferenceProbe]::RmEndSession($sessionHandle)
''',
        encoding="utf-8",
    )
    powershell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
            str(collision_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
