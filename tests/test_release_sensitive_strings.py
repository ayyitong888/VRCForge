from pathlib import Path
import subprocess
import sys
import hashlib
import importlib.util
import io
import tarfile
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "packaging" / "scan_release_sensitive_strings.py"
SPEC = importlib.util.spec_from_file_location("release_sensitive_scan", SCANNER)
SCAN_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SCAN_MODULE)


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True, capture_output=True)


def _scan(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--repo-root", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )


def _artifact_scan(*artifacts: Path) -> subprocess.CompletedProcess[str]:
    arguments = [sys.executable, str(SCANNER)]
    for artifact in artifacts:
        arguments.extend(("--artifact", str(artifact)))
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def _repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "clean.txt").write_text("release input\n", encoding="utf-8")
    _git(tmp_path, "add", "dashboard/clean.txt")
    return tmp_path


def test_release_scan_rejects_tracked_secret_without_echoing_value(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    secret = "ghp_" + "abcdefghijklmnopqrstuvwxyz1234567890"
    (repo / "dashboard" / "tracked.txt").write_text(f"token = {secret}\n", encoding="utf-8")
    _git(repo, "add", "dashboard/tracked.txt")

    result = _scan(repo)

    assert result.returncode == 1
    assert "dashboard/tracked.txt:1: github_token" in result.stderr
    assert secret not in result.stderr


def test_release_scan_rejects_ignored_untracked_machine_path(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    ignored_root = repo / "dashboard" / "cache"
    ignored_root.mkdir()
    (repo / ".gitignore").write_text("dashboard/cache/\n", encoding="utf-8")
    machine_path = "C:" + r"\Users\release-user\AppData\Roaming\VRCForge"
    (ignored_root / "local.txt").write_text(machine_path + "\n", encoding="utf-8")

    result = _scan(repo)

    assert result.returncode == 1
    assert "dashboard/cache/local.txt:1: windows_machine_path" in result.stderr
    assert machine_path not in result.stderr


def test_release_scan_covers_frontend_source_and_build_config_without_echoing_values(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    frontend = repo / "src" / "cache"
    frontend.mkdir(parents=True)
    secret = "ghp_" + "frontendreleasegate1234567890abcdef"
    machine_path = "C:" + r"\Users\release-user\AppData\Local\VRCForge"
    (frontend / "local.ts").write_text(f'export const token = "{secret}";\n', encoding="utf-8")
    (repo / "vite.config.ts").write_text(f'const localPath = "{machine_path}";\n', encoding="utf-8")

    result = _scan(repo)

    assert result.returncode == 1
    assert "src/cache/local.ts:1: github_token" in result.stderr
    assert "vite.config.ts:1: windows_machine_path" in result.stderr
    assert secret not in result.stderr
    assert machine_path not in result.stderr


def test_release_scan_skips_binary_data(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "dashboard" / "binary.dat").write_bytes(b"\0ghp_" + b"abcdefghijklmnopqrstuvwxyz1234567890")
    _git(repo, "add", "dashboard/binary.dat")

    result = _scan(repo)

    assert result.returncode == 0, result.stderr


def test_release_scan_rejects_utf16_unquoted_credential_assignment_without_echoing_value(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    secret = "x" * 32
    (repo / "dashboard" / "local.env").write_text(f"API_KEY={secret}\n", encoding="utf-16")

    result = _scan(repo)

    assert result.returncode == 1
    assert "dashboard/local.env:1: credential_assignment" in result.stderr
    assert secret not in result.stderr


def _write_unitypackage(path: Path, logical_path: str, content: bytes) -> None:
    with tarfile.open(path, "w:gz") as archive:
        pathname = logical_path.encode("utf-8")
        pathname_info = tarfile.TarInfo("a" * 32 + "/pathname")
        pathname_info.size = len(pathname)
        archive.addfile(pathname_info, io.BytesIO(pathname))
        asset_info = tarfile.TarInfo("a" * 32 + "/asset")
        asset_info.size = len(content)
        archive.addfile(asset_info, io.BytesIO(content))


def test_artifact_scan_rejects_zip_and_unitypackage_secrets_without_echoing_value(tmp_path: Path) -> None:
    secret = "ghp_" + "a" * 36
    portable = tmp_path / "portable.zip"
    with zipfile.ZipFile(portable, "w") as archive:
        archive.writestr("dashboard/config.env", f"TOKEN={secret}\n")
    unitypackage = tmp_path / "VRCForge.unitypackage"
    _write_unitypackage(unitypackage, "Assets/VRCForge/Editor/secret.txt", secret.encode("utf-8"))

    result = _artifact_scan(portable, unitypackage)

    assert result.returncode == 1
    assert "dashboard/config.env:1: github_token" in result.stderr
    assert "Assets/VRCForge/Editor/secret.txt:1: github_token" in result.stderr
    assert secret not in result.stderr


def test_artifact_scan_allows_only_certifi_exact_public_ca_bundle_path(tmp_path: Path) -> None:
    certificate = b"-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----\n"
    portable = tmp_path / "portable.zip"
    with zipfile.ZipFile(portable, "w") as archive:
        archive.writestr("backend/_internal/certifi/cacert.pem", certificate)
    allowed = _artifact_scan(portable)
    assert allowed.returncode == 0, allowed.stderr

    unexpected = tmp_path / "unexpected.zip"
    with zipfile.ZipFile(unexpected, "w") as archive:
        archive.writestr("backend/other/cert.pem", certificate)
    rejected = _artifact_scan(unexpected)
    assert rejected.returncode == 1
    assert "backend/other/cert.pem:1: certificate" in rejected.stderr


def test_artifact_scan_checks_installer_raw_stream_only_for_high_confidence_tokens(tmp_path: Path) -> None:
    secret = "sk-" + "a" * 32
    installer = tmp_path / "installer.exe"
    installer.write_bytes(b"MZ" + secret.encode("ascii") + b"\0")

    result = _artifact_scan(installer)

    assert result.returncode == 1
    assert "installer.exe: openai_api_key" in result.stderr
    assert secret not in result.stderr


def test_content_baseline_survives_line_shift_but_rejects_changed_content(tmp_path: Path) -> None:
    line = "token = " + "ghp_" + "abcdefghijklmnopqrstuvwxyz1234567890"
    baseline = frozenset({("candidate.txt", "github_token", hashlib.sha256(line.encode()).hexdigest())})
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("\n" + line + "\n", encoding="utf-8")

    assert SCAN_MODULE.findings_for_file(tmp_path, Path("candidate.txt"), baseline) == []

    candidate.write_text("\n" + line + "x\n", encoding="utf-8")
    assert SCAN_MODULE.findings_for_file(tmp_path, Path("candidate.txt"), baseline) == [
        ("candidate.txt", 2, "github_token")
    ]


def test_release_build_runs_sensitive_scan_before_building_artifacts() -> None:
    build_script = (REPO_ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8")

    scan_call = build_script.index(
        '& $pythonExe .\\packaging\\scan_release_sensitive_strings.py --repo-root $repoRoot'
    )
    desktop_build = build_script.index("Build-TauriDesktopApp -DestinationExe")
    assert scan_call < desktop_build
    artifact_scan = build_script.index("Release artifact sensitive-string scan failed.")
    installer_build = build_script.index("Web NSIS build failed.")
    assert artifact_scan > installer_build
