from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "installer" / "VRCForge_WebPayload.ps1"
SHELL = shutil.which("powershell") or shutil.which("pwsh")
VERSION = "1.4.0"
SMOKE_ID = "b" * 32
SMOKE_LEAF = f"VRCForge-Smoke-{SMOKE_ID}"
OFFICIAL_URL = (
    "https://github.com/ayyitong888/VRCForge/releases/download/v1.4.0/"
    "VRCForge_Windows_x64_1.4.0.zip"
)


def _is_admin() -> bool:
    if not SHELL:
        return False
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().lower() == "true"


def _write_zip(path: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            item = zipfile.ZipInfo("placeholder")
            # ZipInfo normalizes os.sep in its constructor on Windows. Restore
            # the requested raw member spelling so the helper sees the same
            # backslash namespace emitted by Compress-Archive.
            item.filename = name
            item.orig_filename = name
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, value)
        if symlink is not None:
            item = zipfile.ZipInfo(symlink)
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, "target")


def _make_secure_stage(tmp_path: Path, payload: Path) -> Path:
    """Create the exact protected nonce leaf expected by the production helper.

    This requires an elevated token because production staging is intentionally
    owned and ACLed for SYSTEM/Administrators only.  The caller gates it below;
    non-elevated CI still runs all static contract assertions.
    """
    program_files = tmp_path / "ProgramFiles"
    staging_parent = program_files / f"VRCForge Installer Staging-{SMOKE_LEAF}"
    root = staging_parent / uuid.uuid4().hex
    root.mkdir(parents=True)
    destination = root / "payload.zip"
    shutil.copyfile(payload, destination)
    bootstrap = r"""
$ErrorActionPreference = 'Stop'
$system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$admins = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
foreach ($sid in @($system, $admins)) {
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, [System.Security.AccessControl.FileSystemRights]::FullControl, $inheritance, [System.Security.AccessControl.PropagationFlags]::None, [System.Security.AccessControl.AccessControlType]::Allow)
  [void]$acl.AddAccessRule($rule)
}
$acl.SetOwner($admins)
foreach ($path in @($env:VRCFORGE_TEST_PROGRAM_FILES, $env:VRCFORGE_TEST_STAGING_PARENT, $env:VRCFORGE_TEST_STAGE_ROOT)) {
  [IO.Directory]::SetAccessControl($path, $acl)
}
"""
    environment = os.environ.copy()
    environment["VRCFORGE_TEST_PROGRAM_FILES"] = str(program_files)
    environment["VRCFORGE_TEST_STAGING_PARENT"] = str(staging_parent)
    environment["VRCFORGE_TEST_STAGE_ROOT"] = str(root)
    subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", bootstrap],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )
    return root


def _verified_payload_entries() -> dict[str, bytes]:
    desktop = b"desktop"
    backend = b"backend"
    version = VERSION.encode("utf-8")
    notification_icon = b"notification-icon"
    manifest = {
        "schema": "vrcforge.payload-integrity.v1",
        "version": VERSION,
        "files": {
            "desktop": {"relativePath": "VRCForge.exe", "sha256": hashlib.sha256(desktop).hexdigest()},
            "backend": {"relativePath": "backend/vrcforge_backend.exe", "sha256": hashlib.sha256(backend).hexdigest()},
            "version": {"relativePath": "VERSION", "sha256": hashlib.sha256(version).hexdigest()},
            "notificationIcon": {"relativePath": "VRCForge.png", "sha256": hashlib.sha256(notification_icon).hexdigest()},
        },
    }
    return {
        "VRCForge.exe": desktop,
        "VRCForge.png": notification_icon,
        r"backend\vrcforge_backend.exe": backend,
        "VERSION": version,
        "payload-integrity.json": json.dumps(manifest).encode("utf-8"),
    }


def _run_extract(tmp_path: Path, payload: Path, *, expected_hash: str | None = None, expected_length: int | None = None) -> subprocess.CompletedProcess[str]:
    root = _make_secure_stage(tmp_path, payload)
    environment = os.environ.copy()
    environment["ProgramFiles"] = str(tmp_path / "ProgramFiles")
    environment["ProgramW6432"] = str(tmp_path / "ProgramFiles")
    return subprocess.run(
        [
            SHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HELPER),
            "-Action",
            "Extract",
            "-Version",
            VERSION,
            "-PayloadUrl",
            OFFICIAL_URL,
            "-ExpectedSha256",
            expected_hash or hashlib.sha256(payload.read_bytes()).hexdigest(),
            "-ExpectedLength",
            str(expected_length if expected_length is not None else payload.stat().st_size),
            "-ProgramFilesRoot",
            str(tmp_path / "ProgramFiles"),
            "-StageRoot",
            str(root),
            "-DestinationRoot",
            str(tmp_path / "ProgramFiles" / SMOKE_LEAF),
            "-ExpectedInstallLeaf",
            SMOKE_LEAF,
            "-StateTag",
            SMOKE_LEAF,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )


def _run_layout_validation(payload: Path) -> subprocess.CompletedProcess[str]:
    command = r"""
& {
  $source = [IO.File]::ReadAllText($env:VRCFORGE_TEST_HELPER_PATH)
  $cut = $source.LastIndexOf('Assert-Inputs')
  if ($cut -lt 0) { throw 'helper dispatch marker missing' }
  $body = $source.Substring(0, $cut)
  $contract = [scriptblock]::Create($body)
  . $contract -Action Extract -Version '1.4.0' -PayloadUrl 'https://github.com/ayyitong888/VRCForge/releases/download/v1.4.0/VRCForge_Windows_x64_1.4.0.zip' -ExpectedSha256 ('0' * 64) -ExpectedLength 1
  Add-Type -AssemblyName System.IO.Compression
  $stream = [IO.File]::OpenRead($env:VRCFORGE_TEST_ZIP_PATH)
  try {
    $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
    try { Test-ZipLayout $archive } finally { $archive.Dispose() }
  } finally { $stream.Dispose() }
}
"""
    environment = os.environ.copy()
    environment["VRCFORGE_TEST_HELPER_PATH"] = str(HELPER)
    environment["VRCFORGE_TEST_ZIP_PATH"] = str(payload)
    return subprocess.run(
        [
            SHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )


def test_web_payload_helper_has_closed_bootstrap_contract() -> None:
    source = HELPER.read_text(encoding="utf-8")

    assert "https://github.com/ayyitong888/VRCForge/releases/download/v$Version/" in source
    assert "CreateNew" in source
    assert "FileShare]::None" in source
    assert "SetAccessRuleProtection($true, $false)" in source
    assert "[IO.Directory]::CreateDirectory($Path, $acl)" in source
    assert "$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value" in source
    assert "Assert-NoReparsePath" in source
    assert "Assert-SafeProgramFilesDestination" in source
    assert "^VRCForge-Smoke-[a-f0-9]{32}$" in source
    assert "$ExpectedInstallLeaf -cne $StateTag" in source
    assert "does not match the exact scoped identity" in source
    assert "Assert-NoUntrustedWriteAcl" in source
    assert "$script:TrustedInstallerSid" in source
    assert "$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -notin $trustedOwners" in source
    assert "$rule.IdentityReference.Value -notin $trustedWriters" in source
    assert "Assert-PrivateStageAcl" in source
    assert "$seen.ContainsKey($rule.IdentityReference.Value)" in source
    assert "$seen.ContainsKey($script:SystemSid.Value)" in source
    assert "Open-VerifiedArchive" in source
    assert "ZipArchive" in source
    assert "Normalize([Text.NormalizationForm]::FormC)" in source
    assert "Remove-ValidatedStageRoot" in source
    extract = source[source.index("function Invoke-Extract") :]
    assert extract.index("$stream = Open-VerifiedArchive $payloadPath") < extract.index(
        "$destination = Assert-SafeProgramFilesDestination $DestinationRoot"
    )
    assert '[Text.Encoding]::UTF8.GetBytes((Get-FullPath $Root))' in source
    assert 'release-assets.githubusercontent.com' in source
    assert '$request.AllowAutoRedirect = $false' in source
    assert 'if ([int]$response.StatusCode -notin @(301, 302, 303, 307, 308))' in source
    assert "[System.Security.AccessControl.FileSystemRights]::Modify" not in source
    assert "[System.Security.AccessControl.FileSystemRights]::FullControl\n" not in source
    for forbidden in ("certutil", "tar.exe", "cmd /", "taskkill", "Invoke-Expression", "Start-Process"):
        assert forbidden not in source


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_web_payload_layout_accepts_windows_separators_and_root_files(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"
    _write_zip(payload, {"VRCForge.exe": b"desktop", r"backend\vrcforge_backend.exe": b"backend"})

    result = _run_layout_validation(payload)

    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_web_payload_layout_rejects_mixed_separator_collision(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"
    _write_zip(payload, {"nested/file.txt": b"one", r"nested\file.txt": b"two"})

    result = _run_layout_validation(payload)

    assert result.returncode != 0
    assert "duplicate normalized entry name" in (result.stderr + result.stdout)


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_web_payload_helper_rejects_non_official_url_before_any_staging(tmp_path: Path) -> None:
    native_program_files = os.environ.get("ProgramW6432") or os.environ["ProgramFiles"]
    result = subprocess.run(
        [
            SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER),
            "-Action", "Extract", "-Version", VERSION,
            "-PayloadUrl", "https://example.invalid/payload.zip",
            "-ExpectedSha256", "0" * 64, "-ExpectedLength", "1",
            "-ProgramFilesRoot", native_program_files,
            "-StageRoot", str(tmp_path / "not-a-stage"),
            "-DestinationRoot", str(tmp_path / "out"),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    output = " ".join((result.stderr + result.stdout).split())
    assert "exact version-bound official release URL" in output


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_destination_validation_rejects_other_program_files_leaf_before_creation() -> None:
    native_program_files = Path(os.environ.get("ProgramW6432") or os.environ["ProgramFiles"])
    destination = native_program_files / f"Not-VRCForge-{uuid.uuid4().hex}"
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), "-Action", "ValidateDestination", "-Version", VERSION, "-ProgramFilesRoot", str(native_program_files), "-DestinationRoot", str(destination)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "exact scoped identity" in (result.stderr + result.stdout)
    assert not destination.exists()


@pytest.mark.skipif(not SHELL, reason="PowerShell is required")
def test_destination_validation_rejects_nested_allowed_leaf_before_creation() -> None:
    native_program_files = Path(os.environ.get("ProgramW6432") or os.environ["ProgramFiles"])
    destination = native_program_files / f"Other-{uuid.uuid4().hex}" / "VRCForge-Smoke"
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), "-Action", "ValidateDestination", "-Version", VERSION, "-ProgramFilesRoot", str(native_program_files), "-DestinationRoot", str(destination)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "direct child of Program Files" in (result.stderr + result.stdout)
    assert not destination.parent.exists()


@pytest.mark.skipif(not _is_admin(), reason="destination ACL fixture requires an elevated Windows token")
def test_destination_validation_rejects_specific_user_owner_and_writer(tmp_path: Path) -> None:
    program_files = tmp_path / "ProgramFiles"
    destination = program_files / SMOKE_LEAF
    destination.mkdir(parents=True)
    script = r"""
$admins = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$user = [Security.Principal.WindowsIdentity]::GetCurrent().User
foreach ($item in @($env:VRCFORGE_TEST_PROGRAM_FILES, $env:VRCFORGE_TEST_DESTINATION)) {
  $acl = New-Object System.Security.AccessControl.DirectorySecurity
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($sid in @($admins, $system)) {
    [void]$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
  }
  $acl.SetOwner($admins)
  [IO.Directory]::SetAccessControl($item, $acl)
}
$destinationAcl = [IO.Directory]::GetAccessControl($env:VRCFORGE_TEST_DESTINATION)
[void]$destinationAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($user, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
$destinationAcl.SetOwner($user)
[IO.Directory]::SetAccessControl($env:VRCFORGE_TEST_DESTINATION, $destinationAcl)
"""
    fixture_environment = os.environ.copy()
    fixture_environment["VRCFORGE_TEST_PROGRAM_FILES"] = str(program_files)
    fixture_environment["VRCFORGE_TEST_DESTINATION"] = str(destination)
    subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        text=True,
        capture_output=True,
        env=fixture_environment,
    )
    environment = os.environ.copy()
    environment["ProgramFiles"] = str(program_files)
    environment["ProgramW6432"] = str(program_files)
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), "-Action", "ValidateDestination", "-Version", VERSION, "-ProgramFilesRoot", str(program_files), "-DestinationRoot", str(destination), "-ExpectedInstallLeaf", SMOKE_LEAF, "-StateTag", SMOKE_LEAF],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )
    assert result.returncode != 0
    assert "untrusted owner" in (result.stderr + result.stdout).lower()


@pytest.mark.skipif(not _is_admin(), reason="production ACL fixture requires an elevated Windows token")
def test_web_payload_helper_extracts_verified_regular_archive(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"
    _write_zip(payload, _verified_payload_entries())

    result = _run_extract(tmp_path, payload)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ProgramFiles" / SMOKE_LEAF / "VRCForge.exe").read_bytes() == b"desktop"
    assert (tmp_path / "ProgramFiles" / SMOKE_LEAF / "VRCForge.png").read_bytes() == b"notification-icon"
    assert (tmp_path / "ProgramFiles" / SMOKE_LEAF / "backend" / "vrcforge_backend.exe").read_bytes() == b"backend"


@pytest.mark.skipif(not _is_admin(), reason="production ACL fixture requires an elevated Windows token")
@pytest.mark.parametrize(
    ("entries", "symlink", "hash_delta", "length_delta", "needle"),
    [
        ({"safe.txt": b"ok"}, None, True, 0, "SHA-256"),
        ({"safe.txt": b"ok"}, None, False, 1, "length"),
        ({"../escape.txt": b"bad"}, None, False, 0, "unsafe path segment"),
        ({"file:stream": b"bad"}, None, False, 0, "ADS-like"),
        ({"CON.txt": b"bad"}, None, False, 0, "reserved device"),
        ({"Readme.txt": b"one", "README.txt": b"two"}, None, False, 0, "collision"),
        ({"nested/file.txt": b"one", r"nested\file.txt": b"two"}, None, False, 0, "duplicate"),
        ({"safe.txt": b"ok"}, "link", False, 0, "non-regular"),
    ],
)
def test_web_payload_helper_rejects_hostile_archives(
    tmp_path: Path,
    entries: dict[str, bytes],
    symlink: str | None,
    hash_delta: bool,
    length_delta: int,
    needle: str,
) -> None:
    payload = tmp_path / "payload.zip"
    _write_zip(payload, entries, symlink=symlink)
    expected_hash = hashlib.sha256(payload.read_bytes()).hexdigest()
    if hash_delta:
        expected_hash = "0" * 64

    result = _run_extract(
        tmp_path,
        payload,
        expected_hash=expected_hash,
        expected_length=payload.stat().st_size + length_delta,
    )

    assert result.returncode != 0
    assert needle.lower() in (result.stderr + result.stdout).lower()
    assert not (tmp_path / "ProgramFiles" / SMOKE_LEAF).exists()


@pytest.mark.skipif(not _is_admin(), reason="production ACL fixture requires an elevated Windows token")
def test_web_payload_helper_rejects_archive_entry_count_over_limit(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"
    _write_zip(payload, {f"items/{index}.txt": b"x" for index in range(4097)})

    result = _run_extract(tmp_path, payload)

    assert result.returncode != 0
    assert "too many entries" in (result.stderr + result.stdout).lower()
