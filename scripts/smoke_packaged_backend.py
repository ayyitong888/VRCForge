from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "vrcforge.packaged_backend_smoke.v1"
ORIGIN = "tauri://localhost"
REQUIRED_SUPPORT_MEMBERS = {
    "metadata.json",
    "bootstrap.json",
    "doctor.json",
    "diagnostics.json",
    "agent-audit.json",
    "checkpoints.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe the frozen VRCForge backend and emit stable-gate evidence."
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
        help="Artifact root. A packaged-backend-smoke-* run directory is created below it.",
    )
    parser.add_argument(
        "--payload-zip",
        default="",
        help="Release payload ZIP to bind this runtime proof to. Defaults to dist/release/VRCForge_Windows_x64_<version>.zip.",
    )
    parser.add_argument("--port", type=int, default=0, help="Loopback port. Zero selects a free ephemeral port.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Backend startup timeout in seconds.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def read_expected_version(value: str) -> str:
    version = str(value or "").strip()
    if version:
        return version
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_port(requested: int) -> int:
    if requested:
        if requested < 1 or requested > 65535:
            raise ValueError("--port must be between 1 and 65535")
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request_json(
    base_url: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=encoded,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": ORIGIN,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {path}")
    return payload


def wait_for_bootstrap(
    base_url: str,
    token: str,
    process: subprocess.Popen[bytes],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = "backend did not accept a request"
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"packaged backend exited during startup with code {exit_code}")
        try:
            return request_json(base_url, token, "GET", "/api/app/bootstrap", timeout=3.0)
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for packaged bootstrap: {last_error}")


def validate_support_bundle(path: Path, version: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "missingMembers": [],
        "privacyFindings": [],
    }
    if not path.is_file():
        result["error"] = "support bundle path is not a file"
        return result
    try:
        with zipfile.ZipFile(path) as bundle:
            names = set(bundle.namelist())
            missing = sorted(REQUIRED_SUPPORT_MEMBERS - names)
            result["missingMembers"] = missing
            bad_member = bundle.testzip()
            metadata = json.loads(bundle.read("metadata.json")) if "metadata.json" in names else {}
            bootstrap = json.loads(bundle.read("bootstrap.json")) if "bootstrap.json" in names else {}
            privacy_findings = scan_support_bundle_privacy(bundle)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        result["error"] = str(exc)
        return result

    privacy = metadata.get("privacy") if isinstance(metadata.get("privacy"), dict) else {}
    result.update(
        {
            "badMember": bad_member or "",
            "metadataSchema": metadata.get("schema"),
            "metadataVersion": metadata.get("version"),
            "metadataPortableMode": metadata.get("portableMode"),
            "redactsSecrets": privacy.get("redactsSecrets"),
            "includesFullPaths": privacy.get("includesFullPaths"),
            "bootstrapOk": bootstrap.get("ok"),
            "privacyFindings": privacy_findings,
        }
    )
    result["ok"] = bool(
        not result["missingMembers"]
        and not bad_member
        and metadata.get("schema") == "vrcforge.support-bundle.v1"
        and metadata.get("version") == version
        and metadata.get("portableMode") is True
        and privacy.get("redactsSecrets") is True
        and not bool(privacy.get("includesFullPaths"))
        and bootstrap.get("ok") is True
        and not privacy_findings
    )
    return result


def scan_support_bundle_privacy(bundle: zipfile.ZipFile) -> list[str]:
    findings: list[str] = []
    secret_value = re.compile(
        r'(?i)"(?:api[_-]?key|app[_-]?session[_-]?token|gateway[_-]?token|access[_-]?token|password|secret)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
    )
    secret_token = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/-]{16,})")
    user_path = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+")
    allowed_values = {"", "<redacted>", "[redacted]", "redacted", "***", "configured", "present"}
    for info in bundle.infolist():
        if info.is_dir() or info.file_size > 5 * 1024 * 1024:
            continue
        if Path(info.filename).suffix.lower() not in {".json", ".txt", ".log", ".md"}:
            continue
        text = bundle.read(info).decode("utf-8", errors="replace")
        if secret_token.search(text):
            findings.append(f"{info.filename}:token-pattern")
        if user_path.search(text.replace("\\\\", "\\")):
            findings.append(f"{info.filename}:absolute-user-path")
        for match in secret_value.finditer(text):
            value = match.group(1).strip().lower()
            if value not in allowed_values:
                findings.append(f"{info.filename}:secret-value")
                break
    return sorted(set(findings))


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex(("127.0.0.1", port)) == 0


def stop_process(process: subprocess.Popen[bytes] | None, port: int) -> dict[str, Any]:
    forced = False
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=10)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and port_is_open(port):
        time.sleep(0.1)
    port_released = not port_is_open(port)
    return {
        "ok": bool((process is None or process.poll() is not None) and port_released),
        "forced": forced,
        "exitCode": process.poll() if process is not None else None,
        "portReleased": port_released,
    }


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    version = read_expected_version(args.version)
    payload_zip_arg = Path(args.payload_zip) if args.payload_zip else REPO_ROOT / "dist" / "release" / f"VRCForge_Windows_x64_{version}.zip"
    payload_zip = payload_zip_arg.resolve() if payload_zip_arg.is_absolute() else (REPO_ROOT / payload_zip_arg).resolve()
    payload_zip_sha256 = sha256_file(payload_zip) if payload_zip.is_file() else ""
    packaged_root_arg = Path(args.packaged_root)
    packaged_root = (
        packaged_root_arg.resolve()
        if packaged_root_arg.is_absolute()
        else (REPO_ROOT / packaged_root_arg).resolve()
    )
    artifacts_root_arg = Path(args.artifacts_dir)
    artifacts_root = (
        artifacts_root_arg.resolve()
        if artifacts_root_arg.is_absolute()
        else (REPO_ROOT / artifacts_root_arg).resolve()
    )
    run_dir = artifacts_root / f"packaged-backend-smoke-{run_stamp()}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    summary_path = run_dir / "packaged-bootstrap-summary.json"
    runtime_root = run_dir / "runtime"
    user_data = runtime_root / "user-data"
    config_dir = user_data / "config"
    log_dir = user_data / "logs"
    runtime_artifacts = user_data / "artifacts"
    for directory in (config_dir, log_dir, runtime_artifacts):
        directory.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "dashboard": {"project_roots": []},
                "paths": {"blendshape_export": "Assets/VRCForge/blendshapes_export.json"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    backend_exe = packaged_root / "backend" / "vrcforge_backend.exe"
    dashboard_dir = packaged_root / "dashboard"
    port = choose_port(args.port)
    base_url = f"http://127.0.0.1:{port}"
    token = secrets.token_urlsafe(32)
    process: subprocess.Popen[bytes] | None = None
    stdout_handle = None
    stderr_handle = None
    assertions: list[str] = []
    bootstrap_ok = False
    proof_index_ok = False
    support_bundle_ok = False
    portable_mode = False
    support_bundle_path = ""
    bootstrap_evidence: dict[str, Any] = {}
    proof_evidence: dict[str, Any] = {}
    bundle_evidence: dict[str, Any] = {}
    cleanup: dict[str, Any] = {"ok": False, "portReleased": False}

    try:
        missing_inputs = [
            str(path)
            for path in (backend_exe, packaged_root / "VERSION", dashboard_dir / "index.html", payload_zip)
            if not path.is_file()
        ]
        if missing_inputs:
            raise FileNotFoundError(f"packaged payload is missing required files: {missing_inputs}")
        packaged_version = (packaged_root / "VERSION").read_text(encoding="utf-8").strip()
        if packaged_version != version:
            raise RuntimeError(f"packaged VERSION mismatch: expected {version}, got {packaged_version}")

        env = os.environ.copy()
        env.update(
            {
                "VRCFORGE_APP_DIR": str(packaged_root),
                "VRCFORGE_USER_DATA_DIR": str(user_data),
                "VRCFORGE_CONFIG_DIR": str(config_dir),
                "VRCFORGE_LOG_DIR": str(log_dir),
                "VRCFORGE_ARTIFACTS_DIR": str(runtime_artifacts),
                "VRCFORGE_DASHBOARD_DIR": str(dashboard_dir),
                "VRCFORGE_SETTINGS_PATH": str(config_dir / "settings.json"),
                "VRCFORGE_APP_SESSION_TOKEN": token,
            }
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        stdout_handle = (run_dir / "backend-stdout.log").open("wb")
        stderr_handle = (run_dir / "backend-stderr.log").open("wb")
        process = subprocess.Popen(
            [str(backend_exe), "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(packaged_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
        )

        bootstrap = wait_for_bootstrap(base_url, token, process, max(1.0, float(args.timeout)))
        app = bootstrap.get("app") if isinstance(bootstrap.get("app"), dict) else {}
        health = bootstrap.get("health") if isinstance(bootstrap.get("health"), dict) else {}
        portable_mode = health.get("portableMode") is True
        bootstrap_ok = bool(
            bootstrap.get("ok") is True
            and app.get("version") == version
            and app.get("surface") == "tauri-agentic-desktop"
            and health.get("schema") == "vrcforge.bootstrap_health.v1"
            and portable_mode
        )
        bootstrap_evidence = {
            "ok": bootstrap.get("ok"),
            "version": app.get("version"),
            "surface": app.get("surface"),
            "healthSchema": health.get("schema"),
            "portableMode": health.get("portableMode"),
        }

        proof_index = request_json(base_url, token, "GET", "/api/app/optimization/proofs?limit=10")
        proof_index_ok = bool(
            proof_index.get("ok") is True
            and proof_index.get("schema") == "vrcforge.optimization.proof_index.v1"
            and proof_index.get("readOnly") is True
            and isinstance(proof_index.get("proofs"), list)
        )
        proof_evidence = {
            "ok": proof_index.get("ok"),
            "schema": proof_index.get("schema"),
            "readOnly": proof_index.get("readOnly"),
            "count": proof_index.get("count"),
        }

        bundle_response = request_json(
            base_url,
            token,
            "POST",
            "/api/app/support-bundle",
            {"includeFullPaths": False, "logLimit": 50},
            timeout=30.0,
        )
        support_bundle_path = str(bundle_response.get("bundlePath") or "")
        bundle_path = Path(support_bundle_path).resolve() if support_bundle_path else Path()
        bundle_validation = (
            validate_support_bundle(bundle_path, version)
            if support_bundle_path
            else {"ok": False, "error": "bundlePath was empty"}
        )
        support_bundle_ok = bool(
            bundle_response.get("ok") is True
            and bundle_response.get("schema") == "vrcforge.support-bundle.v1"
            and bundle_response.get("redacted") is True
            and bundle_validation.get("ok") is True
        )
        bundle_evidence = {
            "responseOk": bundle_response.get("ok"),
            "responseSchema": bundle_response.get("schema"),
            "redacted": bundle_response.get("redacted"),
            "validation": bundle_validation,
        }
    except Exception as exc:  # noqa: BLE001 - evidence must record the concrete runtime failure.
        assertions.append(str(exc))
    finally:
        cleanup = stop_process(process, port)
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()

    if not bootstrap_ok:
        assertions.append("packaged bootstrap contract did not pass")
    if not proof_index_ok:
        assertions.append("packaged optimizer proof index contract did not pass")
    if not support_bundle_ok:
        assertions.append("packaged support bundle contract did not pass")
    if not cleanup.get("ok"):
        assertions.append("packaged backend did not stop cleanly")
    assertions = list(dict.fromkeys(assertions))

    summary = {
        "schema": SCHEMA,
        "ok": not assertions,
        "generatedAt": utc_now(),
        "version": version,
        "portableMode": portable_mode,
        "bootstrapOk": bootstrap_ok,
        "proofIndexOk": proof_index_ok,
        "supportBundleOk": support_bundle_ok,
        "supportBundlePath": support_bundle_path,
        "payloadZip": str(payload_zip),
        "payloadZipSha256": payload_zip_sha256,
        "packagedRoot": str(packaged_root),
        "backend": str(backend_exe),
        "port": port,
        "bootstrap": bootstrap_evidence,
        "proofIndex": proof_evidence,
        "supportBundle": bundle_evidence,
        "cleanup": cleanup,
        "assertions": assertions,
    }
    write_summary(summary_path, summary)
    print(summary_path)
    if assertions:
        for assertion in assertions:
            print(assertion, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
