from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = "vrcforge.installer_install_uninstall_smoke.v1"
SENTINEL_NAME = "installer-smoke-preservation.json"


def main() -> int:
    args = parse_args()
    report = run_smoke(args)
    path = write_report(report, args.artifacts_dir)
    print(json.dumps({"ok": report["ok"], "status": report["summary"]["status"], "reportPath": str(path)}, indent=2))
    if report["ok"] or (report["summary"]["status"] == "blocked" and args.allow_blocked):
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test VRCForge NSIS installer install/uninstall.")
    parser.add_argument("--installer", default="dist/release/VRCForge_Offline_Installer_x64.exe")
    parser.add_argument("--upgrade-installer", default="", help="Optional older installer to install before upgrading with --installer.")
    parser.add_argument("--install-dir", default=str(default_install_dir()), help="Defaults to %%ProgramFiles%%\\VRCForge.")
    parser.add_argument("--user-data-root", default="", help="Override the VRCForge user data root. Defaults to %%LOCALAPPDATA%%\\VRCForge\\agentic-app.")
    parser.add_argument("--artifacts-dir", default="", help="Directory for the JSON smoke report. Defaults to ./artifacts/installer-smoke.")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--backend-port", type=int, default=8791)
    parser.add_argument("--dry-run", action="store_true", help="Write evidence without running installers or changing user data.")
    parser.add_argument("--allow-blocked", action="store_true", help="Exit 0 when admin elevation is required but unavailable.")
    return parser.parse_args()


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    installer = Path(args.installer).expanduser().resolve()
    upgrade_installer = Path(args.upgrade_installer).expanduser().resolve() if args.upgrade_installer else None
    install_dir = Path(args.install_dir).expanduser().resolve()
    user_data_root = resolve_user_data_root(args.user_data_root)
    steps: list[dict[str, Any]] = []
    phases = {
        "install": "skipped",
        "uninstall": "skipped",
        "upgrade": "skipped",
        "preservation": "skipped",
    }
    blocked_reason = ""
    backend_process: subprocess.Popen[str] | None = None
    install_attempt_owned = False
    sentinel_path = user_data_root / SENTINEL_NAME
    try:
        steps.append(
            {
                "name": "installer.exists",
                "ok": args.dry_run or installer.is_file(),
                "exists": installer.is_file(),
                "required": not args.dry_run,
                "path": str(installer),
                "size": installer.stat().st_size if installer.is_file() else 0,
            }
        )
        if not installer.is_file() and not args.dry_run:
            raise RuntimeError("Installer does not exist.")
        if upgrade_installer is not None:
            steps.append(
                {
                    "name": "upgrade_installer.exists",
                    "ok": args.dry_run or upgrade_installer.is_file(),
                    "exists": upgrade_installer.is_file(),
                    "required": not args.dry_run,
                    "path": str(upgrade_installer),
                    "size": upgrade_installer.stat().st_size if upgrade_installer.is_file() else 0,
                }
            )
            if not upgrade_installer.is_file() and not args.dry_run:
                raise RuntimeError("Upgrade installer does not exist.")
        steps.append(user_data_root_step(user_data_root, override_used=bool(args.user_data_root.strip())))
        if args.dry_run:
            return build_report(
                args,
                installer,
                upgrade_installer,
                install_dir,
                user_data_root,
                sentinel_path,
                started_at,
                steps,
                phases,
                ok=True,
                status="skipped",
                blocked_reason="",
            )
        steps.append({"name": "admin.check", "ok": is_admin(), "required": True})
        if not is_admin():
            phases["install"] = "blocked"
            phases["uninstall"] = "blocked"
            phases["upgrade"] = "blocked" if upgrade_installer is not None else "skipped"
            blocked_reason = "NSIS installers request admin elevation and write Program Files plus HKLM uninstall registry keys."
            return build_report(
                args,
                installer,
                upgrade_installer,
                install_dir,
                user_data_root,
                sentinel_path,
                started_at,
                steps,
                phases,
                ok=False,
                status="blocked",
                blocked_reason=blocked_reason,
            )
        if install_dir.exists() and is_empty_directory(install_dir):
            install_dir.rmdir()
        if install_dir.exists():
            raise RuntimeError(f"Install directory already exists; refusing to overwrite during smoke: {install_dir}")

        sentinel = create_preservation_sentinel(user_data_root, installer, upgrade_installer)
        steps.append({"name": "preservation.sentinel_created", "ok": sentinel_path.is_file(), "path": str(sentinel_path), "sentinelId": sentinel["id"]})
        if not sentinel_path.is_file():
            raise RuntimeError("Preservation sentinel was not created.")

        first_installer = upgrade_installer or installer
        install_attempt_owned = True
        install_result = run_installer(first_installer, install_dir, args.timeout)
        steps.append(command_step("installer.install", install_result.args, install_result))
        phases["install"] = "passed" if install_result.returncode == 0 else "failed"
        if install_result.returncode != 0:
            raise RuntimeError("Installer returned a non-zero exit code.")

        if upgrade_installer is not None:
            upgrade_result = run_installer(installer, install_dir, args.timeout)
            steps.append(command_step("installer.upgrade", upgrade_result.args, upgrade_result))
            phases["upgrade"] = "passed" if upgrade_result.returncode == 0 and sentinel_path.is_file() else "failed"
            if upgrade_result.returncode != 0:
                raise RuntimeError("Upgrade installer returned a non-zero exit code.")
            steps.append({"name": "preservation.after_upgrade", "ok": sentinel_path.is_file(), "path": str(sentinel_path)})
            if not sentinel_path.is_file():
                raise RuntimeError("User data sentinel was not preserved after upgrade.")

        expected = [
            install_dir / "VRCForge.exe",
            install_dir / "VERSION",
            install_dir / "backend" / "vrcforge_backend.exe",
            install_dir / "dashboard" / "index.html",
            install_dir / "Uninstall.exe",
        ]
        missing = [str(path) for path in expected if not path.exists()]
        steps.append({"name": "install.payload_verify", "ok": not missing, "missing": missing})
        if missing:
            raise RuntimeError("Installed payload is incomplete.")

        if port_is_open(args.backend_port):
            raise RuntimeError(f"Backend smoke port {args.backend_port} is already in use.")
        expected_version = (install_dir / "VERSION").read_text(encoding="utf-8").strip()
        backend_process = start_installed_backend(args, install_dir, user_data_root)
        health = wait_for_health(args.backend_port, args.timeout, backend_process)
        process_alive = backend_process.poll() is None
        health_ok = bool(
            process_alive
            and health.get("version") == expected_version
            and health.get("portableMode") is True
        )
        steps.append(
            {
                "name": "installed_backend.health",
                "ok": health_ok,
                "version": health.get("version"),
                "expectedVersion": expected_version,
                "portableMode": health.get("portableMode"),
                "processAlive": process_alive,
                "pid": backend_process.pid,
                "userDataRoot": str(user_data_root),
            }
        )
        if not health_ok:
            raise RuntimeError("Installed backend health did not match the installed payload.")
        stop_process(backend_process)
        backend_cleanup_ok = wait_for_port_released(args.backend_port, timeout=10.0)
        steps.append(
            {
                "name": "installed_backend.cleanup",
                "ok": backend_cleanup_ok,
                "pid": backend_process.pid,
                "port": args.backend_port,
                "portReleased": backend_cleanup_ok,
            }
        )
        backend_process = None
        if not backend_cleanup_ok:
            raise RuntimeError("Installed backend port remained in use after process stop.")

        uninstall_steps, removed = uninstall_installed_payload(install_dir, args.timeout)
        steps.extend(uninstall_steps)
        phases["uninstall"] = "passed" if removed else "failed"
        if not removed:
            raise RuntimeError("Installed payload was not removed cleanly.")
        install_attempt_owned = False
        preserved = sentinel_path.is_file() and read_json_file(sentinel_path).get("id") == sentinel["id"]
        phases["preservation"] = "passed" if preserved else "failed"
        steps.append(
            {
                "name": "preservation.after_uninstall",
                "ok": preserved,
                "path": str(sentinel_path),
                "userDataRootExists": user_data_root.exists(),
            }
        )
        if not preserved:
            raise RuntimeError("User data sentinel was not preserved after uninstall.")
        return build_report(
            args,
            installer,
            upgrade_installer,
            install_dir,
            user_data_root,
            sentinel_path,
            started_at,
            steps,
            phases,
            ok=True,
            status="passed",
            blocked_reason="",
        )
    except Exception as exc:  # noqa: BLE001
        steps.append({"name": "installer_smoke.error", "ok": False, "error": str(exc)})
        if backend_process is not None:
            stop_process(backend_process)
            backend_process = None
        if install_attempt_owned and install_dir.exists():
            cleanup_steps, removed = uninstall_installed_payload(install_dir, args.timeout, prefix="failure_cleanup")
            steps.extend(cleanup_steps)
            phases["uninstall"] = "passed" if removed else "failed"
        return build_report(
            args,
            installer,
            upgrade_installer,
            install_dir,
            user_data_root,
            sentinel_path,
            started_at,
            steps,
            phases,
            ok=False,
            status="failed",
            blocked_reason=blocked_reason,
        )
    finally:
        if backend_process is not None:
            stop_process(backend_process)


def run_installer(installer: Path, install_dir: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    cmd = nsis_install_command(installer, install_dir)
    return subprocess.run(cmd, cwd=str(installer.parent), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)


def uninstall_installed_payload(
    install_dir: Path,
    timeout: float,
    *,
    prefix: str = "uninstall",
) -> tuple[list[dict[str, Any]], bool]:
    uninstall = install_dir / "Uninstall.exe"
    if not uninstall.is_file():
        return ([{"name": f"{prefix}.executable", "ok": False, "path": str(uninstall)}], False)
    uninstall_cmd = [str(uninstall), "/S"]
    result = subprocess.run(
        uninstall_cmd,
        cwd=str(install_dir.parent),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    steps = [command_step(f"{prefix}.command", uninstall_cmd, result)]
    removed = result.returncode == 0 and wait_for_path_removed(install_dir, timeout=30.0)
    empty_remaining = bool(install_dir.exists() and is_empty_directory(install_dir))
    cleanup_removed = False
    if result.returncode == 0 and not removed and empty_remaining:
        try:
            install_dir.rmdir()
            cleanup_removed = not install_dir.exists()
        except OSError:
            cleanup_removed = False
    removed = removed or cleanup_removed
    steps.append(
        {
            "name": f"{prefix}.removed",
            "ok": removed,
            "installDir": str(install_dir),
            "emptyDirectoryRemaining": empty_remaining,
            "smokeCleanupRemovedEmptyDir": cleanup_removed,
        }
    )
    return steps, removed


def nsis_install_command(installer: Path, install_dir: Path) -> str:
    # NSIS requires /D to be the final raw command-line segment and not quoted,
    # even when the target path contains spaces.
    return f'"{installer}" /S /D={install_dir}'


def start_installed_backend(args: argparse.Namespace, install_dir: Path, user_data_root: Path) -> subprocess.Popen[str]:
    exe = install_dir / "backend" / "vrcforge_backend.exe"
    config_dir = user_data_root / "config"
    logs_dir = user_data_root / "logs"
    artifacts_dir = user_data_root / "artifacts"
    for directory in (config_dir, logs_dir, artifacts_dir):
        directory.mkdir(parents=True, exist_ok=True)
    settings_path = ensure_runtime_settings(config_dir)
    env = os.environ.copy()
    env.update(
        {
            "VRCFORGE_APP_DIR": str(install_dir),
            "VRCFORGE_USER_DATA_DIR": str(user_data_root),
            "VRCFORGE_CONFIG_DIR": str(config_dir),
            "VRCFORGE_LOG_DIR": str(logs_dir),
            "VRCFORGE_ARTIFACTS_DIR": str(artifacts_dir),
            "VRCFORGE_DASHBOARD_DIR": str(install_dir / "dashboard"),
            "VRCFORGE_SETTINGS_PATH": str(settings_path),
        }
    )
    return subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(args.backend_port)],
        cwd=str(install_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def ensure_runtime_settings(config_dir: Path) -> Path:
    settings_path = config_dir / "settings.json"
    if settings_path.exists():
        return settings_path
    settings_path.write_text(
        json.dumps(
            {
                "dashboard": {"project_roots": []},
                "paths": {"blendshape_export": "Assets/VRCForge/blendshapes_export.json"},
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return settings_path


def wait_for_health(
    port: int,
    timeout: float,
    process: subprocess.Popen[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return {}
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:  # noqa: S310 - loopback smoke.
                return json.loads(response.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return {}


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex(("127.0.0.1", int(port))) == 0


def wait_for_port_released(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_is_open(port):
            return True
        time.sleep(0.1)
    return not port_is_open(port)


def wait_for_path_removed(path: Path, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not path.exists():
            return True
        time.sleep(0.5)
    return not path.exists()


def is_empty_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    except OSError:
        return False
    return False


def command_step(name: str, command: list[str] | str, result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "name": name,
        "ok": result.returncode == 0,
        "command": command,
        "exitCode": result.returncode,
        "stdoutTail": (result.stdout or "")[-2000:],
        "stderrTail": (result.stderr or "")[-2000:],
    }


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def resolve_user_data_root(override: str) -> Path:
    if override.strip():
        return Path(override).expanduser().resolve()
    return default_user_data_root().resolve()


def default_user_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip() or os.environ.get("APPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "VRCForge" / "agentic-app"
    return Path.home() / "AppData" / "Local" / "VRCForge" / "agentic-app"


def default_install_dir() -> Path:
    program_files = os.environ.get("ProgramFiles", "").strip() or os.environ.get("ProgramW6432", "").strip()
    if program_files:
        return Path(program_files).expanduser() / "VRCForge"
    system_drive = os.environ.get("SystemDrive", "").strip() or "C:"
    return Path(f"{system_drive}\\Program Files") / "VRCForge"


def legacy_user_data_roots() -> dict[str, str]:
    base_value = os.environ.get("LOCALAPPDATA", "").strip() or os.environ.get("APPDATA", "").strip()
    base = Path(base_value).expanduser() if base_value else Path.home() / "AppData" / "Local"
    return {
        "config": str(base / "VRCForge" / "config"),
        "cache": str(base / "VRCForge" / "cache"),
        "logs": str(base / "VRCForge" / "logs"),
    }


def user_data_root_step(user_data_root: Path, *, override_used: bool = False) -> dict[str, Any]:
    expected = default_user_data_root().resolve()
    return {
        "name": "user_data.default_root",
        "ok": override_used or user_data_root == expected,
        "path": str(user_data_root),
        "expectedDefault": str(expected),
        "overrideUsed": override_used,
        "matchesTauriAndBackendDefault": user_data_root == expected,
        "legacyRoots": legacy_user_data_roots(),
    }


def create_preservation_sentinel(user_data_root: Path, installer: Path, upgrade_installer: Path | None) -> dict[str, Any]:
    user_data_root.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "id": f"installer-smoke-{uuid4().hex}",
        "createdAt": utc_now(),
        "installer": str(installer),
        "upgradeInstaller": str(upgrade_installer) if upgrade_installer else "",
        "purpose": "Verify installer upgrade/uninstall preserves VRCForge user data.",
    }
    (user_data_root / SENTINEL_NAME).write_text(json.dumps(sentinel, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return sentinel


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report(
    args: argparse.Namespace,
    installer: Path,
    upgrade_installer: Path | None,
    install_dir: Path,
    user_data_root: Path,
    sentinel_path: Path,
    started_at: str,
    steps: list[dict[str, Any]],
    phases: dict[str, str],
    *,
    ok: bool,
    status: str,
    blocked_reason: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "schema": SCHEMA,
        "startedAt": started_at,
        "finishedAt": utc_now(),
        "installer": str(installer),
        "installerSha256": sha256_file(installer),
        "upgradeInstaller": str(upgrade_installer) if upgrade_installer else "",
        "installDir": str(install_dir),
        "userData": {
            "root": str(user_data_root),
            "expectedDefaultRoot": str(default_user_data_root().resolve()),
            "matchesTauriAndBackendDefault": user_data_root == default_user_data_root().resolve(),
            "sentinelPath": str(sentinel_path),
            "legacyRoots": legacy_user_data_roots(),
        },
        "timeout": args.timeout,
        "summary": {
            "status": status,
            "phases": phases,
            "blockedReason": blocked_reason,
            "failedSteps": [step["name"] for step in steps if not step.get("ok")],
        },
        "steps": steps,
    }


def write_report(report: dict[str, Any], artifacts_dir: str = "") -> Path:
    root = Path(artifacts_dir).expanduser().resolve() if artifacts_dir.strip() else Path.cwd() / "artifacts" / "installer-smoke"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"installer-install-uninstall-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return path


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
