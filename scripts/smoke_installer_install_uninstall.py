from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "vrcforge.installer_install_uninstall_smoke.v1"


def main() -> int:
    args = parse_args()
    report = run_smoke(args)
    path = write_report(report)
    print(json.dumps({"ok": report["ok"], "status": report["summary"]["status"], "reportPath": str(path)}, indent=2))
    if report["ok"] or (report["summary"]["status"] == "blocked" and args.allow_blocked):
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test VRCForge NSIS installer install/uninstall.")
    parser.add_argument("--installer", default="dist/release/VRCForge_Offline_Installer_x64.exe")
    parser.add_argument("--install-dir", default=r"C:\Program Files\VRCForge")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--backend-port", type=int, default=8791)
    parser.add_argument("--allow-blocked", action="store_true", help="Exit 0 when admin elevation is required but unavailable.")
    return parser.parse_args()


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    installer = Path(args.installer).expanduser().resolve()
    install_dir = Path(args.install_dir).expanduser().resolve()
    steps: list[dict[str, Any]] = []
    blocked_reason = ""
    backend_process: subprocess.Popen[str] | None = None
    try:
        steps.append({"name": "installer.exists", "ok": installer.is_file(), "path": str(installer), "size": installer.stat().st_size if installer.is_file() else 0})
        if not installer.is_file():
            raise RuntimeError("Installer does not exist.")
        steps.append({"name": "admin.check", "ok": is_admin(), "required": True})
        if not is_admin():
            blocked_reason = "NSIS installers request admin elevation and write Program Files plus HKLM uninstall registry keys."
            return build_report(args, installer, install_dir, started_at, steps, ok=False, status="blocked", blocked_reason=blocked_reason)
        if install_dir.exists():
            raise RuntimeError(f"Install directory already exists; refusing to overwrite during smoke: {install_dir}")

        install_cmd = [str(installer), "/S", f"/D={install_dir}"]
        install_result = subprocess.run(install_cmd, cwd=str(installer.parent), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout, check=False)
        steps.append(command_step("installer.install", install_cmd, install_result))
        if install_result.returncode != 0:
            raise RuntimeError("Installer returned a non-zero exit code.")

        expected = [
            install_dir / "VRCForge.exe",
            install_dir / "backend" / "vrcforge_backend.exe",
            install_dir / "dashboard" / "index.html",
            install_dir / "Uninstall.exe",
        ]
        missing = [str(path) for path in expected if not path.exists()]
        steps.append({"name": "install.payload_verify", "ok": not missing, "missing": missing})
        if missing:
            raise RuntimeError("Installed payload is incomplete.")

        backend_process = start_installed_backend(args, install_dir)
        health = wait_for_health(args.backend_port, args.timeout)
        steps.append(
            {
                "name": "installed_backend.health",
                "ok": bool(health.get("version")),
                "version": health.get("version"),
                "portableMode": health.get("portableMode"),
            }
        )
        if not health.get("version"):
            raise RuntimeError("Installed backend health probe did not return a version.")
        stop_process(backend_process)
        backend_process = None

        uninstall = install_dir / "Uninstall.exe"
        uninstall_cmd = [str(uninstall), "/S"]
        uninstall_result = subprocess.run(uninstall_cmd, cwd=str(install_dir), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout, check=False)
        steps.append(command_step("installer.uninstall", uninstall_cmd, uninstall_result))
        if uninstall_result.returncode != 0:
            raise RuntimeError("Uninstaller returned a non-zero exit code.")
        removed = not install_dir.exists()
        steps.append({"name": "uninstall.removed", "ok": removed, "installDir": str(install_dir)})
        if not removed:
            raise RuntimeError("Install directory still exists after uninstall.")
        return build_report(args, installer, install_dir, started_at, steps, ok=True, status="passed", blocked_reason="")
    except Exception as exc:  # noqa: BLE001
        steps.append({"name": "installer_smoke.error", "ok": False, "error": str(exc)})
        return build_report(args, installer, install_dir, started_at, steps, ok=False, status="failed", blocked_reason=blocked_reason)
    finally:
        if backend_process is not None:
            stop_process(backend_process)


def start_installed_backend(args: argparse.Namespace, install_dir: Path) -> subprocess.Popen[str]:
    exe = install_dir / "backend" / "vrcforge_backend.exe"
    data_root = Path.cwd() / "artifacts" / "installer-smoke" / "installed-runtime"
    config_dir = data_root / "config"
    logs_dir = data_root / "logs"
    artifacts_dir = data_root / "artifacts"
    for directory in (config_dir, logs_dir, artifacts_dir):
        directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "VRCFORGE_USER_DATA_DIR": str(data_root),
            "VRCFORGE_CONFIG_DIR": str(config_dir),
            "VRCFORGE_LOG_DIR": str(logs_dir),
            "VRCFORGE_ARTIFACTS_DIR": str(artifacts_dir),
            "VRCFORGE_DASHBOARD_DIR": str(install_dir / "dashboard"),
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


def wait_for_health(port: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:  # noqa: S310 - loopback smoke.
                return json.loads(response.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return {}


def command_step(name: str, command: list[str], result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
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


def build_report(
    args: argparse.Namespace,
    installer: Path,
    install_dir: Path,
    started_at: str,
    steps: list[dict[str, Any]],
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
        "installDir": str(install_dir),
        "timeout": args.timeout,
        "summary": {
            "status": status,
            "blockedReason": blocked_reason,
            "failedSteps": [step["name"] for step in steps if not step.get("ok")],
        },
        "steps": steps,
    }


def write_report(report: dict[str, Any]) -> Path:
    root = Path.cwd() / "artifacts" / "installer-smoke"
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
