"""Exercise the production installer activation/complete functions on a temp filesystem."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "installer" / "VRCForge_WebPayload.ps1"
WEB_INSTALLER = ROOT / "installer" / "VRCForge_Web_Installer_x64.nsi"
OFFLINE_INSTALLER = ROOT / "installer" / "VRCForge_Offline_Installer_x64.nsi"
FINALIZATION = ROOT / "installer" / "VRCForge_InstallFinalization.nsh"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
VERSION = "1.4.0"
INSTALL_LEAF = f"VRCForge-Smoke-{uuid.uuid4().hex}"
PAYLOAD_URL = (
    "https://github.com/ayyitong888/VRCForge/releases/download/v1.4.0/"
    "VRCForge_Windows_x64_1.4.0.zip"
)


def _payload(path: Path) -> None:
    files = {
        "VRCForge.exe": b"new desktop",
        "backend/vrcforge_backend.exe": b"new backend",
        "VERSION": VERSION.encode(),
        "VRCForge.png": b"icon",
    }
    manifest = {
        "schema": "vrcforge.payload-integrity.v1",
        "version": VERSION,
        "files": {
            "desktop": {"relativePath": "VRCForge.exe", "sha256": hashlib.sha256(files["VRCForge.exe"]).hexdigest()},
            "backend": {"relativePath": "backend/vrcforge_backend.exe", "sha256": hashlib.sha256(files["backend/vrcforge_backend.exe"]).hexdigest()},
            "version": {"relativePath": "VERSION", "sha256": hashlib.sha256(files["VERSION"]).hexdigest()},
            "notificationIcon": {"relativePath": "VRCForge.png", "sha256": hashlib.sha256(files["VRCForge.png"]).hexdigest()},
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr("payload-integrity.json", json.dumps(manifest).encode())


_POWERSHELL_RUNNER = r'''
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText($env:VRCFORGE_TEST_HELPER)
$cut = $source.LastIndexOf('Assert-Inputs')
if ($cut -lt 0) { throw 'helper dispatch marker missing' }
$contract = [scriptblock]::Create($source.Substring(0, $cut))
Add-Type -AssemblyName System.IO.Compression
. $contract -Action Extract -Version $env:VRCFORGE_TEST_VERSION `
  -PayloadUrl $env:VRCFORGE_TEST_URL `
  -ExpectedSha256 $env:VRCFORGE_TEST_HASH `
  -ExpectedLength ([UInt64]$env:VRCFORGE_TEST_LENGTH) `
  -ProgramFilesRoot $env:VRCFORGE_TEST_PROGRAM_FILES `
  -PayloadPath $env:VRCFORGE_TEST_PAYLOAD `
  -DestinationRoot $env:VRCFORGE_TEST_DESTINATION `
  -ExpectedInstallLeaf $env:VRCFORGE_TEST_INSTALL_LEAF `
  -StateTag $env:VRCFORGE_TEST_INSTALL_LEAF

# Replace only host/ACL-boundary operations. All moves, extraction, manifest
# verification, activation state I/O, rollback, and backup deletion use the
# production functions against this isolated temporary directory.
function Get-TrustedProgramFilesRoot { return [IO.Path]::GetFullPath($env:VRCFORGE_TEST_PROGRAM_FILES) }
function Get-ActivationStatePath { return $env:VRCFORGE_TEST_ACTIVATION_STATE }
function Assert-NoReparsePath([string]$Path, [switch]$AllowMissingLeaf) { return [IO.Path]::GetFullPath($Path) }
function Assert-NoUntrustedWriteAcl([string]$Path) { }
function Assert-NoReparseTree([string]$Root) { }
function Assert-SafeProgramFilesDestination([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path)
  $base = [IO.Path]::GetFullPath($env:VRCFORGE_TEST_PROGRAM_FILES)
  if ([IO.Path]::GetDirectoryName($full) -ine $base -or [IO.Path]::GetFileName($full) -cne $env:VRCFORGE_TEST_INSTALL_LEAF) { throw 'fixture destination escaped its temporary Program Files root' }
  if ($env:VRCFORGE_TEST_FAIL_RESTORE -eq '1' -and -not [IO.Directory]::Exists($full)) { throw 'fixture restore destination failure' }
  return $full
}
function New-InstallSiblingDirectory([string]$Path, [string]$Kind) {
  [void][IO.Directory]::CreateDirectory($Path)
  Assert-InstallSibling $Path $Kind | Out-Null
}

$script:OriginalAssertInstalledPayload = (Get-Command Assert-InstalledPayload).ScriptBlock
function Assert-InstalledPayload([string]$Root) {
  $full = [IO.Path]::GetFullPath($Root)
  $destination = [IO.Path]::GetFullPath($env:VRCFORGE_TEST_DESTINATION)
  if ($env:VRCFORGE_TEST_FAIL_ACTIVATION -eq '1' -and $full -ieq $destination) { throw 'fixture activation readback failure' }
  & $script:OriginalAssertInstalledPayload $Root
}

$script:OriginalRemoveInstallSibling = (Get-Command Remove-InstallSibling).ScriptBlock
function Remove-InstallSibling([string]$Path, [string]$Kind) {
  if ($env:VRCFORGE_TEST_FAIL_CLEANUP -eq '1' -and $Kind -ceq 'Backup') { throw 'fixture backup cleanup failure' }
  & $script:OriginalRemoveInstallSibling $Path $Kind
}
function Request-InstalledAppExit([string]$Root) { }
function Assert-InstallNotRunning([string]$Root) { }

function Invoke-TestExtract {
  Assert-Inputs
  Invoke-Extract
}
function Invoke-TestComplete {
  $Action = 'Complete'
  Assert-Inputs
  Invoke-Complete
}

try {
  if ($env:VRCFORGE_TEST_ACTION -eq 'Extract') {
    Invoke-TestExtract
  } else {
    Invoke-TestComplete
  }
} catch {
  [Console]::Error.WriteLine($_.Exception.ToString())
  exit 7
}
'''


def _run_helper(
    tmp_path: Path,
    action: str,
    *,
    existing_install=True,
    fail_activation=False,
    fail_restore=False,
    fail_cleanup=False,
):
    program_files = tmp_path / "ProgramFiles"
    program_files.mkdir(exist_ok=True)
    destination = program_files / INSTALL_LEAF
    if action == "Extract" and existing_install:
        destination.mkdir()
        (destination / "old-install.txt").write_text("old install")
    payload = tmp_path / "payload.zip"
    _payload(payload)
    activation_state = tmp_path / "protected-helper" / "activation-backup.txt"
    activation_state.parent.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "VRCFORGE_TEST_HELPER": str(HELPER),
            "VRCFORGE_TEST_ACTION": action,
            "VRCFORGE_TEST_VERSION": VERSION,
            "VRCFORGE_TEST_URL": PAYLOAD_URL,
            "VRCFORGE_TEST_HASH": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "VRCFORGE_TEST_LENGTH": str(payload.stat().st_size),
            "VRCFORGE_TEST_PROGRAM_FILES": str(program_files),
            "VRCFORGE_TEST_PAYLOAD": str(payload),
            "VRCFORGE_TEST_DESTINATION": str(destination),
            "VRCFORGE_TEST_INSTALL_LEAF": INSTALL_LEAF,
            "VRCFORGE_TEST_ACTIVATION_STATE": str(activation_state),
            "VRCFORGE_TEST_FAIL_ACTIVATION": "1" if fail_activation else "0",
            "VRCFORGE_TEST_FAIL_RESTORE": "1" if fail_restore else "0",
            "VRCFORGE_TEST_FAIL_CLEANUP": "1" if fail_cleanup else "0",
        }
    )
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _POWERSHELL_RUNNER],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
        timeout=60,
    ), program_files, destination, activation_state


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_extract_keeps_exact_prior_backup_until_complete_and_complete_only_removes_it(tmp_path: Path):
    result, program_files, destination, state = _run_helper(tmp_path, "Extract")
    assert result.returncode == 0, result.stderr + result.stdout
    record = Path(state.read_text(encoding="utf-16-le"))
    assert record.is_dir()
    assert (record / "old-install.txt").read_text() == "old install"

    unrelated = program_files / f"{INSTALL_LEAF}-Backup-{uuid.uuid4().hex}"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("unrelated backup")
    completed, _, _, _ = _run_helper(tmp_path, "Complete")
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert not record.exists()
    assert (destination / "VRCForge.exe").read_bytes() == b"new desktop"
    assert (unrelated / "keep.txt").read_text() == "unrelated backup"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_complete_cleanup_failure_is_warning_and_preserves_backup(tmp_path: Path):
    result, _, _, state = _run_helper(tmp_path, "Extract")
    assert result.returncode == 0, result.stderr + result.stdout
    record = Path(state.read_text(encoding="utf-16-le"))
    completed, _, _, _ = _run_helper(tmp_path, "Complete", fail_cleanup=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert record.is_dir()
    assert "fixture backup cleanup failure" in completed.stderr + completed.stdout


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_extract_without_prior_install_writes_fresh_empty_state_and_complete_keeps_other_backups(tmp_path: Path):
    result, program_files, _, state = _run_helper(tmp_path, "Extract", existing_install=False)
    assert result.returncode == 0, result.stderr + result.stdout
    assert state.read_bytes() == b""
    unrelated = program_files / f"{INSTALL_LEAF}-Backup-{uuid.uuid4().hex}"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("unrelated backup")
    completed, _, _, _ = _run_helper(tmp_path, "Complete", existing_install=False)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert unrelated.is_dir()
    assert (unrelated / "keep.txt").read_text() == "unrelated backup"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_complete_without_state_fails_closed_and_does_not_delete_backups(tmp_path: Path):
    result, program_files, _, state = _run_helper(tmp_path, "Extract", existing_install=False)
    assert result.returncode == 0, result.stderr + result.stdout
    state.unlink()
    unrelated = program_files / f"{INSTALL_LEAF}-Backup-{uuid.uuid4().hex}"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("unrelated backup")
    completed, _, _, _ = _run_helper(tmp_path, "Complete", existing_install=False)
    assert completed.returncode != 0
    assert "completion state is missing" in completed.stderr + completed.stdout
    assert unrelated.is_dir()
    assert (unrelated / "keep.txt").read_text() == "unrelated backup"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_activation_readback_failure_restores_old_install_and_preserves_original_error(tmp_path: Path):
    result, _, destination, state = _run_helper(tmp_path, "Extract", fail_activation=True)
    assert result.returncode != 0
    assert "fixture activation readback failure" in result.stderr + result.stdout
    assert (destination / "old-install.txt").read_text() == "old install"
    assert list(destination.parent.glob(f"{INSTALL_LEAF}-Backup-*")) == []


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_restore_failure_reports_original_rollback_and_exact_backup_path(tmp_path: Path):
    result, _, destination, state = _run_helper(tmp_path, "Extract", fail_activation=True, fail_restore=True)
    output = result.stderr + result.stdout
    assert result.returncode != 0
    assert "fixture activation readback failure" in output
    assert "fixture restore destination failure" in output
    backup = Path(state.read_text(encoding="utf-16-le"))
    assert str(backup) in output
    assert not destination.exists()
    assert (backup / "old-install.txt").read_text() == "old install"


def test_both_nsis_installers_complete_only_after_checked_post_configuration():
    finalization = FINALIZATION.read_text(encoding="utf-8-sig")
    complete = finalization.index("-Action Complete")
    failure = finalization.index("install_configuration_failed:")
    assert complete < failure
    for step in ("InstallDataStep", "InstallShortcutStep", "InstallUninstallerStep", "InstallRegistrationStep"):
        assert finalization.index(f'VrcForgeRequireInstallStep "$({step})"') < complete
    assert finalization.index('WriteRegDWORD HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${UNINSTALL_KEY}" "NoRepair" 1') < complete
    assert "Shortcuts or registration may be partially updated" in finalization
    assert "Prior program files remain at:" in finalization
    assert "SetErrorLevel 2" in finalization[failure:]
    assert "Abort" in finalization[failure:]
    for installer in (WEB_INSTALLER, OFFLINE_INSTALLER):
        source = installer.read_text(encoding="utf-8-sig")
        assert '!include "VRCForge_InstallFinalization.nsh"' in source
        section = source.split('Section "Install"', 1)[1].split("SectionEnd", 1)[0]
        assert "!insertmacro VrcForgeFinishInstallation" in section
        finish = section.index("!insertmacro VrcForgeFinishInstallation")
        extract = section.rindex("-Action Extract")
        assert extract < finish
        failure = section.index('MessageBox MB_ICONSTOP|MB_RETRYCANCEL "$(ActivationFailedText)', extract)
        assert section.index('Call CleanupProtectedHelper', extract) < failure < finish
        assert section.index("SetErrorLevel 2", failure) < finish
        assert section.index("Abort", failure) < finish
