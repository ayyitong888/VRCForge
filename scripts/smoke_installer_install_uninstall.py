from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

try:
    import winreg
except ImportError:  # pragma: no cover - the installer gate only runs on Windows.
    winreg = None  # type: ignore[assignment]
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = "vrcforge.installer_install_uninstall_smoke.v2"
SENTINEL_NAME = "installer-smoke-preservation.json"
SMOKE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
PRODUCTION_CLEAN_CONFIRMATION = "I-OWN-THIS-DISPOSABLE-WINDOWS-ENVIRONMENT"


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
    parser.add_argument(
        "--scope",
        choices=("isolated-smoke", "production-clean"),
        default="isolated-smoke",
        help="Use isolated-smoke for compiler-scoped smoke builds; production-clean is reserved for exact release installers in a disposable clean Windows environment.",
    )
    parser.add_argument(
        "--production-clean-confirmation",
        default="",
        help=f"Required exact value for production-clean: {PRODUCTION_CLEAN_CONFIRMATION}",
    )
    parser.add_argument("--smoke-id", default="", help="Required 32-lowercase-hex identity for an isolated smoke-flavor installer.")
    parser.add_argument("--install-dir", default="", help="Must be %%ProgramFiles%%\\VRCForge-Smoke-<smoke-id>.")
    parser.add_argument("--user-data-root", default="", help="Must be %%LOCALAPPDATA%%\\VRCForge\\installer-smoke\\<smoke-id>.")
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
    smoke_id = str(getattr(args, "smoke_id", "")).strip()
    scope_mode = str(getattr(args, "scope", "isolated-smoke") or "isolated-smoke").strip()
    install_dir = resolve_install_dir(args.install_dir, smoke_id)
    user_data_root = resolve_user_data_root(args.user_data_root, smoke_id)
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
        scope = (
            production_clean_scope_step(args, smoke_id, install_dir, user_data_root)
            if scope_mode == "production-clean"
            else smoke_scope_step(smoke_id, install_dir, user_data_root)
        )
        steps.append(scope)
        if not scope["ok"]:
            if scope_mode == "production-clean":
                raise RuntimeError(
                    "Production installer evidence requires an explicitly confirmed disposable clean Windows environment with no existing VRCForge identity or user data."
                )
            raise RuntimeError("Installer smoke requires an exact isolated smoke identity, install leaf, and user-data root.")
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

        shortcut_phase = "after_upgrade" if upgrade_installer is not None else "after_install"
        shortcut_step = inspect_installed_shortcuts(
            install_dir,
            smoke_id=str(getattr(args, "smoke_id", "") or "").strip(),
            phase=shortcut_phase,
            timeout=args.timeout,
        )
        steps.append(shortcut_step)
        if not shortcut_step["ok"]:
            raise RuntimeError("Installed shortcuts did not match the installed target, working directory, and icon.")

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


def inspect_installed_shortcuts(
    install_dir: Path,
    *,
    smoke_id: str,
    phase: str,
    timeout: float,
) -> dict[str, Any]:
    shortcut_name = f"VRCForge Smoke {smoke_id}.lnk" if smoke_id else "VRCForge.lnk"
    start_menu_group = f"VRCForge Smoke {smoke_id}" if smoke_id else "VRCForge"
    env = os.environ.copy()
    env.update(
        {
            "VRCFORGE_SHORTCUT_DESKTOP_NAME": shortcut_name,
            "VRCFORGE_SHORTCUT_START_GROUP": start_menu_group,
        }
    )
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = (
        "$ErrorActionPreference='Stop';"
        "$shell=New-Object -ComObject WScript.Shell;"
        "$items=@("
        "[pscustomobject]@{kind='desktop';path=(Join-Path $shell.SpecialFolders.Item('Desktop') $env:VRCFORGE_SHORTCUT_DESKTOP_NAME)},"
        "[pscustomobject]@{kind='start-menu';path=(Join-Path (Join-Path $shell.SpecialFolders.Item('Programs') $env:VRCFORGE_SHORTCUT_START_GROUP) 'VRCForge.lnk')}"
        ");"
        "$rows=@();"
        "foreach($item in $items){"
        "$exists=Test-Path -LiteralPath $item.path -PathType Leaf;"
        "if($exists){$link=$shell.CreateShortcut($item.path);"
        "$rows+=[pscustomobject]@{kind=$item.kind;path=$item.path;exists=$true;targetPath=$link.TargetPath;workingDirectory=$link.WorkingDirectory;iconLocation=$link.IconLocation}}"
        "else{$rows+=[pscustomobject]@{kind=$item.kind;path=$item.path;exists=$false;targetPath='';workingDirectory='';iconLocation=''}}"
        "};"
        "ConvertTo-Json -InputObject @($rows) -Compress"
    )
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=str(install_dir),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        return {
            "name": f"shortcuts.{phase}",
            "ok": False,
            "entries": [],
            "error": (result.stderr or result.stdout or "Shortcut inspection failed.").strip()[-2000:],
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "name": f"shortcuts.{phase}",
            "ok": False,
            "entries": [],
            "error": f"Shortcut inspection returned invalid JSON: {exc}",
        }
    return evaluate_shortcut_contract(payload, install_dir, phase=phase)


def evaluate_shortcut_contract(payload: Any, install_dir: Path, *, phase: str) -> dict[str, Any]:
    entries = payload if isinstance(payload, list) else []
    expected_target = install_dir / "VRCForge.exe"
    expected_working_directory = install_dir
    expected_icon = install_dir / "VRCForge.ico"
    results: list[dict[str, Any]] = []
    for kind in ("desktop", "start-menu"):
        source = next((item for item in entries if isinstance(item, dict) and item.get("kind") == kind), {})
        icon_path, icon_index = split_icon_location(str(source.get("iconLocation") or ""))
        checks = {
            "exists": source.get("exists") is True,
            "targetPath": windows_path_equal(str(source.get("targetPath") or ""), expected_target),
            "workingDirectory": windows_path_equal(
                str(source.get("workingDirectory") or ""), expected_working_directory
            ),
            "iconPath": windows_path_equal(icon_path, expected_icon),
            "iconIndex": icon_index == 0,
        }
        results.append(
            {
                "kind": kind,
                "path": str(source.get("path") or ""),
                "targetPath": str(source.get("targetPath") or ""),
                "workingDirectory": str(source.get("workingDirectory") or ""),
                "iconLocation": str(source.get("iconLocation") or ""),
                "checks": checks,
                "ok": all(checks.values()),
            }
        )
    return {
        "name": f"shortcuts.{phase}",
        "ok": len(entries) == 2 and all(item["ok"] for item in results),
        "expected": {
            "targetPath": str(expected_target),
            "workingDirectory": str(expected_working_directory),
            "iconPath": str(expected_icon),
            "iconIndex": 0,
        },
        "entries": results,
    }


def split_icon_location(value: str) -> tuple[str, int | None]:
    path, separator, index = value.strip().rpartition(",")
    if not separator:
        return value.strip().strip('"'), None
    try:
        parsed_index = int(index.strip())
    except ValueError:
        parsed_index = None
    return path.strip().strip('"'), parsed_index


def windows_path_equal(actual: str, expected: Path) -> bool:
    if not actual.strip():
        return False
    actual_normalized = os.path.normcase(os.path.normpath(actual.strip().strip('"')))
    expected_normalized = os.path.normcase(os.path.normpath(str(expected)))
    return actual_normalized == expected_normalized


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


def resolve_user_data_root(override: str, smoke_id: str = "") -> Path:
    if override.strip():
        return Path(override).expanduser().resolve()
    if SMOKE_ID_PATTERN.fullmatch(smoke_id):
        return default_smoke_user_data_root(smoke_id).resolve()
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


def default_smoke_install_dir(smoke_id: str) -> Path:
    return default_install_dir().parent / f"VRCForge-Smoke-{smoke_id}"


def resolve_install_dir(override: str, smoke_id: str) -> Path:
    if override.strip():
        return Path(override).expanduser().resolve()
    if SMOKE_ID_PATTERN.fullmatch(smoke_id):
        return default_smoke_install_dir(smoke_id).resolve()
    return default_install_dir().resolve()


def default_smoke_user_data_root(smoke_id: str) -> Path:
    return default_user_data_root().parent / "installer-smoke" / smoke_id


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


def smoke_scope_step(smoke_id: str, install_dir: Path, user_data_root: Path) -> dict[str, Any]:
    normalized_id = smoke_id.strip()
    valid_id = bool(SMOKE_ID_PATTERN.fullmatch(normalized_id))
    expected_install = default_smoke_install_dir(normalized_id).resolve() if valid_id else None
    expected_user_data = default_smoke_user_data_root(normalized_id).resolve() if valid_id else None
    return {
        "name": "smoke_scope.identity",
        "ok": bool(valid_id and install_dir == expected_install and user_data_root == expected_user_data),
        "smokeId": normalized_id,
        "expectedInstallDir": str(expected_install) if expected_install else "",
        "installDir": str(install_dir),
        "expectedUserDataRoot": str(expected_user_data) if expected_user_data else "",
        "userDataRoot": str(user_data_root),
        "requiresExactScope": True,
    }


def production_clean_scope_step(
    args: argparse.Namespace,
    smoke_id: str,
    install_dir: Path,
    user_data_root: Path,
) -> dict[str, Any]:
    expected_install = default_install_dir().resolve()
    expected_user_data = default_user_data_root().resolve()
    confirmation = str(getattr(args, "production_clean_confirmation", "") or "")
    existing_identities = production_identity_presence(expected_install, expected_user_data)
    exact_paths = install_dir == expected_install and user_data_root == expected_user_data
    return {
        "name": "production_scope.clean_identity",
        "ok": bool(
            not smoke_id
            and confirmation == PRODUCTION_CLEAN_CONFIRMATION
            and exact_paths
            and not existing_identities
        ),
        "mode": "production-clean",
        "productionIdentity": True,
        "confirmationAccepted": confirmation == PRODUCTION_CLEAN_CONFIRMATION,
        "smokeIdEmpty": not smoke_id,
        "exactPaths": exact_paths,
        "expectedInstallDir": str(expected_install),
        "installDir": str(install_dir),
        "expectedUserDataRoot": str(expected_user_data),
        "userDataRoot": str(user_data_root),
        "existingIdentities": existing_identities,
        "requiresDisposableEnvironment": True,
    }


def production_identity_presence(install_dir: Path, user_data_root: Path) -> list[str]:
    existing: list[str] = []
    for label, path in (
        ("install-dir", install_dir),
        ("user-data-root", user_data_root),
        ("user-start-menu", production_start_menu_dir()),
        ("public-desktop-shortcut", production_public_desktop_shortcut()),
        ("user-desktop-shortcut", production_user_desktop_shortcut()),
    ):
        if os.path.lexists(path):
            existing.append(f"{label}:{path}")
    existing.extend(production_registry_identities())
    return existing


def production_start_menu_dir() -> Path:
    app_data = os.environ.get("APPDATA", "").strip()
    base = Path(app_data).expanduser() if app_data else Path.home() / "AppData" / "Roaming"
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "VRCForge"


def production_public_desktop_shortcut() -> Path:
    public = os.environ.get("PUBLIC", "").strip()
    base = Path(public).expanduser() if public else Path(default_install_dir().anchor) / "Users" / "Public"
    return base / "Desktop" / "VRCForge.lnk"


def production_user_desktop_shortcut() -> Path:
    profile = os.environ.get("USERPROFILE", "").strip()
    base = Path(profile).expanduser() if profile else Path.home()
    return base / "Desktop" / "VRCForge.lnk"


def production_registry_identities() -> list[str]:
    if winreg is None:
        return ["registry:windows-registry-unavailable"]
    identities: list[str] = []
    keys = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge",
        r"Software\VRCForge",
    )
    views = (
        ("64", getattr(winreg, "KEY_WOW64_64KEY", 0)),
        ("32", getattr(winreg, "KEY_WOW64_32KEY", 0)),
    )
    for key_path in keys:
        for view_name, view_flag in views:
            try:
                handle = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view_flag)
            except FileNotFoundError:
                continue
            except OSError as exc:
                identities.append(f"registry-check-error-{view_name}:{key_path}:{exc}")
                continue
            else:
                winreg.CloseKey(handle)
                identities.append(f"registry-{view_name}:{key_path}")
    return identities


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
        "upgradeInstallerSha256": sha256_file(upgrade_installer) if upgrade_installer else "",
        "installDir": str(install_dir),
        "scope": {
            "mode": str(getattr(args, "scope", "isolated-smoke") or "isolated-smoke"),
            "productionIdentity": str(getattr(args, "scope", "isolated-smoke") or "isolated-smoke")
            == "production-clean",
            "cleanEnvironmentConfirmed": str(getattr(args, "production_clean_confirmation", "") or "")
            == PRODUCTION_CLEAN_CONFIRMATION,
        },
        "smoke": {
            "id": str(getattr(args, "smoke_id", "")).strip(),
            "requiredPattern": SMOKE_ID_PATTERN.pattern,
            "expectedInstallDir": str(
                default_smoke_install_dir(str(getattr(args, "smoke_id", "")).strip()).resolve()
            )
            if SMOKE_ID_PATTERN.fullmatch(str(getattr(args, "smoke_id", "")).strip())
            else "",
        },
        "userData": {
            "root": str(user_data_root),
            "expectedDefaultRoot": str(default_user_data_root().resolve()),
            "matchesTauriAndBackendDefault": user_data_root == default_user_data_root().resolve(),
            "expectedSmokeRoot": str(
                default_smoke_user_data_root(str(getattr(args, "smoke_id", "")).strip()).resolve()
            )
            if SMOKE_ID_PATTERN.fullmatch(str(getattr(args, "smoke_id", "")).strip())
            else "",
            "matchesSmokeScope": bool(
                SMOKE_ID_PATTERN.fullmatch(str(getattr(args, "smoke_id", "")).strip())
                and user_data_root
                == default_smoke_user_data_root(str(getattr(args, "smoke_id", "")).strip()).resolve()
            ),
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
