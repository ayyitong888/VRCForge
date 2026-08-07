from __future__ import annotations

"""Exercise packaged project cache loading and selected-project recovery.

The probe owns an isolated fake user profile and one disposable Unity-shaped
fixture.  It never starts Unity, opens a project, installs packages, calls a
provider, triggers external project discovery, or indexes files outside that
fixture.  Refresh behavior is covered by source tests; this packaged probe
preseeds the documented cache format and verifies packaged load/restart.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from diagnose_packaged_provider_config import (  # noqa: E402 - shared bounded probe helpers.
    contains_secret,
    logs_exclude_secrets,
    redact_text,
    start_backend,
    write_summary,
)
from smoke_packaged_backend import (  # noqa: E402 - sibling shared probe helpers.
    ORIGIN,
    choose_port,
    port_is_open,
    request_json,
    scan_support_bundle_privacy,
    sha256_file,
    stop_process,
    validate_support_bundle,
    wait_for_bootstrap,
)


SCHEMA = "vrcforge.packaged_project_snapshot_probe.v1"
PROJECT_CACHE_SCHEMA = "vrcforge.project_snapshot_cache.v1"
PROJECT_SELECTION_SCHEMA = "vrcforge.selected_project.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify packaged Project snapshot cache and selected-project restart recovery."
    )
    parser.add_argument("--version", default="", help="Expected packaged version. Defaults to VERSION.")
    parser.add_argument(
        "--packaged-root",
        default="dist/VRCForge_Windows_x64",
        help="Unpacked portable payload containing backend/vrcforge_backend.exe.",
    )
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Artifact root. A packaged-project-snapshot-* run directory is created below it.",
    )
    parser.add_argument(
        "--payload-zip",
        default="",
        help="Portable ZIP whose SHA-256 binds this runtime proof. Defaults to the versioned local artifact.",
    )
    parser.add_argument("--port", type=int, default=0, help="Loopback port. Zero selects a free ephemeral port.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Backend startup timeout in seconds.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def read_expected_version(value: str) -> str:
    return str(value or "").strip() or (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def current_head_commit() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def package_binding(version: str, payload_zip: Path) -> dict[str, Any]:
    """Bind this probe to current HEAD, release manifest, and portable ZIP."""

    manifest_path = REPO_ROOT / "dist" / "release" / "release-manifest.json"
    result: dict[str, Any] = {
        "ok": False,
        "headCommit": "",
        "manifestCommit": "",
        "manifestPath": str(manifest_path),
        "manifestVersion": "",
        "payloadZip": str(payload_zip),
        "payloadZipSha256": "",
        "manifestZipSha256": "",
    }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        head = current_head_commit()
        if not isinstance(manifest, dict):
            return result
        artifacts = manifest.get("artifacts")
        matching = [
            item
            for item in artifacts if isinstance(item, dict) and item.get("name") == payload_zip.name
        ] if isinstance(artifacts, list) else []
        if len(matching) != 1:
            return result
        actual_digest = sha256_file(payload_zip)
        manifest_digest = str(matching[0].get("sha256") or "").lower()
        manifest_commit = str(manifest.get("commit") or "").lower()
        manifest_version = str(manifest.get("version") or "")
        result.update(
            {
                "headCommit": head,
                "manifestCommit": manifest_commit,
                "manifestVersion": manifest_version,
                "payloadZipSha256": actual_digest,
                "manifestZipSha256": manifest_digest,
                "ok": bool(
                    manifest_version == version
                    and manifest_commit == head
                    and actual_digest == manifest_digest
                ),
            }
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return result


def packaged_root_matches_zip(packaged_root: Path, payload_zip: Path) -> bool:
    """Require every unpacked payload file to match the bound portable ZIP."""

    try:
        root = packaged_root.resolve(strict=True)
        actual_files = {
            path.relative_to(root).as_posix(): path
            for path in root.rglob("*")
            if path.is_file()
        }
        with zipfile.ZipFile(payload_zip) as archive:
            members = {info.filename: info for info in archive.infolist() if not info.is_dir()}
            if set(actual_files) != set(members):
                return False
            for name, info in members.items():
                target = actual_files[name]
                digest = hashlib.sha256()
                with archive.open(info) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != sha256_file(target):
                    return False
        return True
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def create_disposable_unity_project(runtime_root: Path) -> Path:
    """Make the smallest Unity-shaped project solely inside this run artifact."""

    project = runtime_root / "fixture-unity-root" / "PackagedSnapshotFixture"
    (project / "Assets").mkdir(parents=True, exist_ok=True)
    (project / "Packages").mkdir(parents=True, exist_ok=True)
    (project / "ProjectSettings").mkdir(parents=True, exist_ok=True)
    (project / "Packages" / "manifest.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )
    return project.resolve()


def build_runtime_env(
    packaged_root: Path,
    runtime_root: Path,
    token: str,
    fixture_project: Path,
) -> tuple[dict[str, str], Path, Path, Path]:
    """Create the child-only home, user data, config, logs, and fixture scope."""

    user_data = runtime_root / "user-data"
    config_dir = user_data / "config"
    log_dir = user_data / "logs"
    artifacts_dir = user_data / "artifacts"
    home_dir = runtime_root / "home"
    appdata_dir = runtime_root / "appdata"
    local_appdata_dir = runtime_root / "local-appdata"
    for directory in (config_dir, log_dir, artifacts_dir, home_dir, appdata_dir, local_appdata_dir):
        directory.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "dashboard": {"project_roots": [str(fixture_project.parent)]},
                "paths": {"blendshape_export": "Assets/VRCForge/blendshapes_export.json"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("VRCFORGE_"):
            env.pop(name, None)
    env.update(
        {
            "APPDATA": str(appdata_dir),
            "LOCALAPPDATA": str(local_appdata_dir),
            "USERPROFILE": str(home_dir),
            "HOME": str(home_dir),
            "XDG_CONFIG_HOME": str(home_dir / ".config"),
            "XDG_DATA_HOME": str(home_dir / ".local" / "share"),
            "VRCFORGE_APP_DIR": str(packaged_root),
            "VRCFORGE_USER_DATA_DIR": str(user_data),
            "VRCFORGE_CONFIG_DIR": str(config_dir),
            "VRCFORGE_LOG_DIR": str(log_dir),
            "VRCFORGE_ARTIFACTS_DIR": str(artifacts_dir),
            "VRCFORGE_DASHBOARD_DIR": str(packaged_root / "dashboard"),
            "VRCFORGE_SETTINGS_PATH": str(settings_path),
            "VRCFORGE_CONFIG_PATH": str(config_dir / "config.json"),
            "VRCFORGE_APP_SESSION_TOKEN": token,
            "VRCFORGE_DESKTOP_EXECUTOR": "0",
        }
    )
    return env, user_data, user_data / "project-cache.json", config_dir / "selected-project.json"


def request_status(
    base_url: str,
    token: str | None,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> int:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base_url}{path}", data=encoded, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read()
        return int(exc.code)


def app_auth_rejects_missing_and_wrong_tokens(base_url: str) -> bool:
    return request_status(base_url, None, "GET", "/api/projects") in {401, 403} and request_status(
        base_url, "packaged-project-snapshot-invalid-token", "GET", "/api/projects"
    ) in {401, 403}


def normalized_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).resolve(strict=True)).replace("\\", "/")
    except (OSError, RuntimeError, ValueError):
        return ""


def fixture_only_snapshot_matches(payload: Any, fixture_project: Path, *, selected: bool) -> bool:
    """Accept exactly one discovered project: this run's fixture, never host Unity."""

    if not isinstance(payload, dict):
        return False
    expected = normalized_path(fixture_project)
    projects = payload.get("projects")
    if not expected or not isinstance(projects, list) or len(projects) != 1:
        return False
    project = projects[0]
    if not isinstance(project, dict) or normalized_path(project.get("path")) != expected:
        return False
    expected_sources = ["configured-root", "manual"] if selected else ["configured-root"]
    if (
        str(project.get("name") or "") != fixture_project.name
        or project.get("source") != "configured-root"
        or project.get("sources") != expected_sources
        or bool(project.get("activeMcp"))
        or str(project.get("sessionId") or "")
        or str(project.get("cliInstanceId") or "")
        or project.get("selectable") is not True
    ):
        return False
    if bool(project.get("selected")) is not selected:
        return False
    return normalized_path(payload.get("selectedProjectPath")) == (expected if selected else "")


def fixture_snapshot(fixture_project: Path, *, selected: bool) -> dict[str, Any]:
    expected = normalized_path(fixture_project)
    return {
        "selectedProjectPath": expected if selected else "",
        "unityEditorPath": "",
        "projects": [
            {
                "name": fixture_project.name,
                "path": expected,
                "editorVersion": "2022.3.22f1",
                "hasVrcForge": False,
                "hasUnityMcpPackage": False,
                "selected": selected,
                "sources": ["configured-root", "manual"] if selected else ["configured-root"],
                "source": "configured-root",
                "activeMcp": False,
                "sessionId": "",
                "cliInstanceId": "",
                "unityVersion": "",
                "selectable": True,
            }
        ],
    }


def seed_project_cache(cache_path: Path, fixture_project: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "schema": PROJECT_CACHE_SCHEMA,
                "updatedAt": utc_now(),
                "durationMs": 1,
                "snapshot": fixture_snapshot(fixture_project, selected=False),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def project_cache_matches(cache_path: Path, fixture_project: Path, *, selected: bool = False) -> bool:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    return bool(
        isinstance(payload, dict)
        and payload.get("schema") == PROJECT_CACHE_SCHEMA
        and fixture_only_snapshot_matches(snapshot, fixture_project, selected=selected)
    )


def selection_document_matches(selection_path: Path, fixture_project: Path) -> bool:
    try:
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("schema") == PROJECT_SELECTION_SCHEMA
        and normalized_path(payload.get("selectedProjectPath")) == normalized_path(fixture_project)
        and bool(str(payload.get("updatedAt") or "").strip())
    )


def restart_recovery_matches(
    bootstrap: Any,
    cached_snapshot: Any,
    cache_path: Path,
    selection_path: Path,
    fixture_project: Path,
) -> bool:
    state = ((bootstrap.get("health") or {}).get("state") or {}) if isinstance(bootstrap, dict) else {}
    return bool(
        fixture_only_snapshot_matches(cached_snapshot, fixture_project, selected=False)
        and normalized_path(state.get("selectedProjectPath")) == normalized_path(fixture_project)
        and project_cache_matches(cache_path, fixture_project, selected=False)
        and selection_document_matches(selection_path, fixture_project)
    )


def support_bundle_redacted(
    base_url: str,
    token: str,
    version: str,
    probe_secrets: set[str],
    timeout: float,
) -> tuple[bool, dict[str, Any]]:
    response = request_json(
        base_url,
        token,
        "POST",
        "/api/app/support-bundle",
        {"includeFullPaths": False, "logLimit": 50},
        timeout=max(30.0, timeout),
    )
    bundle_path = Path(str(response.get("bundlePath") or ""))
    validation = validate_support_bundle(bundle_path, version)
    contents_clean = False
    if bundle_path.is_file():
        with zipfile.ZipFile(bundle_path) as bundle:
            contents_clean = not scan_support_bundle_privacy(bundle) and not contains_secret(
                "".join(
                    bundle.read(member).decode("utf-8", errors="replace")
                    for member in bundle.namelist()
                    if not member.endswith("/")
                ),
                probe_secrets,
            )
    return bool(
        response.get("ok") is True
        and response.get("redacted") is True
        and validation.get("ok") is True
        and contents_clean
    ), {
        "responseOk": response.get("ok"),
        "responseSchema": response.get("schema"),
        "redacted": response.get("redacted"),
        "validationOk": validation.get("ok"),
        "privacyFindings": validation.get("privacyFindings", []),
    }


def main() -> int:
    args = parse_args()
    version = read_expected_version(args.version)
    packaged_root = resolve_repo_path(args.packaged_root)
    artifacts_root = resolve_repo_path(args.artifacts_dir)
    payload_zip = resolve_repo_path(args.payload_zip or f"dist/release/VRCForge_Windows_x64_{version}.zip")
    run_dir = artifacts_root / f"packaged-project-snapshot-{run_stamp()}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    summary_path = run_dir / "packaged-project-snapshot-summary.json"
    backend_exe = packaged_root / "backend" / "vrcforge_backend.exe"
    token = secrets.token_urlsafe(32)
    probe_secrets = {token}
    assertions: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    stdout_handle = None
    stderr_handle = None
    first_cleanup: dict[str, Any] = {"ok": False, "portReleased": False}
    final_cleanup: dict[str, Any] = {"ok": False, "portReleased": False}
    first_cleanup_attempted = False
    binding: dict[str, Any] = {}
    packaged_root_integrity_ok = False
    fixture_only_ok = False
    state_selection_ok = False
    cache_file_ok = False
    selection_file_ok = False
    invalid_selection_no_drift = False
    restart_recovery_ok = False
    auth_required_ok = False
    bootstrap_redacted = False
    doctor_redacted = False
    support_redacted = False
    logs_redacted = False
    doctor_checked = False
    support_checked = False
    restart_checked = False
    bundle_evidence: dict[str, Any] = {}
    port = choose_port(args.port)
    base_url = f"http://127.0.0.1:{port}"
    runtime_root = run_dir / "runtime"
    fixture_project = create_disposable_unity_project(runtime_root)
    env, user_data, cache_path, selection_path = build_runtime_env(
        packaged_root, runtime_root, token, fixture_project
    )
    seed_project_cache(cache_path, fixture_project)

    try:
        missing = [
            str(path)
            for path in (backend_exe, packaged_root / "VERSION", packaged_root / "dashboard" / "index.html", payload_zip)
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(f"packaged payload is missing required files: {missing}")
        packaged_version = (packaged_root / "VERSION").read_text(encoding="utf-8").strip()
        if packaged_version != version:
            raise RuntimeError(f"packaged VERSION mismatch: expected {version}, got {packaged_version}")
        binding = package_binding(version, payload_zip)
        if not binding.get("ok"):
            raise RuntimeError("manifest/HEAD/ZIP package binding did not match input payload")
        packaged_root_integrity_ok = packaged_root_matches_zip(packaged_root, payload_zip)
        if not packaged_root_integrity_ok:
            raise RuntimeError("unpacked packaged root did not match the bound payload ZIP")

        process, stdout_handle, stderr_handle = start_backend(backend_exe, packaged_root, env, port, run_dir, "first")
        bootstrap_before = wait_for_bootstrap(base_url, token, process, max(1.0, float(args.timeout)))
        bootstrap_redacted = not contains_secret(bootstrap_before, probe_secrets)
        auth_required_ok = app_auth_rejects_missing_and_wrong_tokens(base_url)

        initial_cached = request_json(base_url, token, "GET", "/api/projects")
        fixture_only_ok = fixture_only_snapshot_matches(initial_cached, fixture_project, selected=False)
        if not fixture_only_ok:
            raise RuntimeError("packaged cache load returned a non-fixture project")

        selected_state = request_json(base_url, token, "POST", "/api/state", {"projectPath": str(fixture_project)})
        expected_fixture = normalized_path(fixture_project)
        state_selection_ok = normalized_path(selected_state.get("selectedProjectPath")) == expected_fixture
        cached_after_selection = request_json(base_url, token, "GET", "/api/projects")
        fixture_only_ok = fixture_only_ok and fixture_only_snapshot_matches(
            cached_after_selection, fixture_project, selected=False
        )
        if not fixture_only_ok:
            raise RuntimeError("selected-project write mutated the independent cached snapshot")
        cache_file_ok = project_cache_matches(cache_path, fixture_project, selected=False)
        selection_file_ok = selection_document_matches(selection_path, fixture_project)

        cache_before_invalid = cache_path.read_bytes()
        selection_before_invalid = selection_path.read_bytes()
        invalid_status = request_status(
            base_url,
            token,
            "POST",
            "/api/state",
            {"projectPath": str(runtime_root / "not-a-unity-project")},
        )
        after_invalid = request_json(base_url, token, "GET", "/api/projects")
        invalid_selection_no_drift = bool(
            invalid_status == 400
            and cache_path.read_bytes() == cache_before_invalid
            and selection_path.read_bytes() == selection_before_invalid
            and fixture_only_snapshot_matches(after_invalid, fixture_project, selected=False)
        )

        bootstrap_after = request_json(base_url, token, "GET", "/api/app/bootstrap")
        doctor = request_json(base_url, token, "GET", "/api/app/doctor")
        doctor_checked = True
        bootstrap_redacted = bootstrap_redacted and not contains_secret(bootstrap_after, probe_secrets)
        doctor_redacted = not contains_secret(doctor, probe_secrets)
        support_redacted, bundle_evidence = support_bundle_redacted(
            base_url, token, version, probe_secrets, float(args.timeout)
        )
        support_checked = True

        first_cleanup_attempted = True
        first_cleanup = stop_process(process, port)
        process = None
        stdout_handle.close()
        stdout_handle = None
        stderr_handle.close()
        stderr_handle = None
        if not first_cleanup.get("ok"):
            raise RuntimeError("first packaged backend did not stop cleanly")

        process, stdout_handle, stderr_handle = start_backend(backend_exe, packaged_root, env, port, run_dir, "restart")
        restart_bootstrap = wait_for_bootstrap(base_url, token, process, max(1.0, float(args.timeout)))
        cached_after_restart = request_json(base_url, token, "GET", "/api/projects")
        restart_checked = True
        restart_recovery_ok = restart_recovery_matches(
            restart_bootstrap,
            cached_after_restart,
            cache_path,
            selection_path,
            fixture_project,
        )
    except Exception as exc:  # noqa: BLE001 - retain bounded, redacted failure evidence.
        assertions.append(redact_text(str(exc), probe_secrets))
    finally:
        final_cleanup = stop_process(process, port)
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
        logs_redacted = logs_exclude_secrets(run_dir, user_data, probe_secrets)

    if not auth_required_ok:
        assertions.append("packaged project API did not reject missing and invalid app tokens")
    if not packaged_root_integrity_ok:
        assertions.append("unpacked packaged root did not match every bound payload ZIP file")
    if not fixture_only_ok:
        assertions.append("packaged project cache did not contain only the fixture project")
    if not state_selection_ok:
        assertions.append("authenticated state selection did not retain the fixture Unity project")
    if not cache_file_ok:
        assertions.append("project-cache.json did not persist the selected fixture snapshot")
    if not selection_file_ok:
        assertions.append("selected-project.json did not persist the selected fixture")
    if not invalid_selection_no_drift:
        assertions.append("invalid selected-project request was not rejected atomically")
    if restart_checked and not restart_recovery_ok:
        assertions.append("packaged restart did not recover cached project snapshot and selection")
    elif not restart_checked:
        assertions.append("packaged restart project recovery check did not complete")
    if not bootstrap_redacted:
        assertions.append("app bootstrap exposed the probe session token")
    if doctor_checked and not doctor_redacted:
        assertions.append("Doctor response exposed the probe session token")
    elif not doctor_checked:
        assertions.append("Doctor redaction check did not complete")
    if support_checked and not support_redacted:
        assertions.append("support bundle did not redact the probe session token")
    elif not support_checked:
        assertions.append("support bundle redaction check did not complete")
    if not logs_redacted:
        assertions.append("backend diagnostic logs exposed the probe session token")
    if (
        (first_cleanup_attempted and not first_cleanup.get("ok"))
        or not final_cleanup.get("ok")
        or port_is_open(port)
    ):
        assertions.append("packaged backend process or loopback port was not released")
    assertions = list(dict.fromkeys(assertions))

    summary = {
        "schema": SCHEMA,
        "ok": not assertions,
        "generatedAt": utc_now(),
        "version": version,
        "packageBinding": binding,
        "packagedRootIntegrityOk": packaged_root_integrity_ok,
        "packagedRoot": str(packaged_root),
        "backend": str(backend_exe),
        "isolatedUserData": str(user_data),
        "fixtureProject": str(fixture_project),
        "port": port,
        "authRequiredOk": auth_required_ok,
        "fixtureOnlyCacheOk": fixture_only_ok,
        "stateSelectionOk": state_selection_ok,
        "cacheFileOk": cache_file_ok,
        "selectionFileOk": selection_file_ok,
        "invalidSelectionNoDrift": invalid_selection_no_drift,
        "restartRecoveryOk": restart_recovery_ok,
        "bootstrapRedacted": bootstrap_redacted,
        "doctorRedacted": doctor_redacted,
        "supportBundleRedacted": support_redacted,
        "logsRedacted": logs_redacted,
        "supportBundle": bundle_evidence,
        "firstCleanup": first_cleanup,
        "finalCleanup": final_cleanup,
        "assertions": [redact_text(item, probe_secrets) for item in assertions],
    }
    write_summary(summary_path, summary)
    print(summary_path)
    if assertions:
        for assertion in summary["assertions"]:
            print(assertion, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
