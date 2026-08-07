from __future__ import annotations

import argparse
import hashlib
import json
import math
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
SCHEMA = "vrcforge.packaged_backend_smoke.v2"
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
    raw_bodies: list[str] | None = None,
) -> dict[str, Any]:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ORIGIN,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=encoded,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        if raw_bodies is not None:
            raw_bodies.append(detail)
        raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc
    if raw_bodies is not None:
        raw_bodies.append(raw)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {path}")
    return payload


def request_failure(
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Return a bounded raw HTTP failure body for authentication proof."""

    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base_url}{path}", method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")[:4000]
    except urllib.error.HTTPError as exc:
        return {
            "status": int(exc.code),
            "body": exc.read().decode("utf-8", errors="replace")[:4000],
        }
    raise RuntimeError(f"Expected authentication failure for {path}, got HTTP {response.status}: {raw}")


def isolated_backend_environment(
    overrides: dict[str, str],
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    """Remove every inherited VRCForge control before adding probe-owned values."""

    source = os.environ if inherited is None else inherited
    environment = {
        str(key): str(value)
        for key, value in source.items()
        if not str(key).upper().startswith("VRCFORGE_")
    }
    environment.update(overrides)
    return environment


def evaluate_authentication_negatives(
    missing_token_response: Any,
    wrong_token_response: Any,
) -> tuple[bool, dict[str, Any]]:
    """Accept only explicit authentication rejection for both negative probes."""

    missing_status = (
        missing_token_response.get("status")
        if isinstance(missing_token_response, dict)
        else None
    )
    wrong_status = (
        wrong_token_response.get("status")
        if isinstance(wrong_token_response, dict)
        else None
    )
    ok = missing_status in {401, 403} and wrong_status in {401, 403}
    return ok, {
        "missingTokenStatus": missing_status,
        "wrongTokenStatus": wrong_status,
    }


def bounded_runtime_probe_timeout(value: float) -> float:
    """Give Doctor/support probes time to finish while keeping CI bounded."""

    normalized = float(value)
    if not math.isfinite(normalized):
        normalized = 60.0
    return max(30.0, min(normalized, 120.0))


def inspect_doctor_report(report: Any) -> dict[str, Any]:
    """Validate the Doctor wire contract and derive its semantic exit code."""
    empty_summary = {
        "okCount": 0,
        "warningCount": 0,
        "errorCount": 0,
        "unknownCount": 0,
    }
    result: dict[str, Any] = {
        "valid": False,
        "errorFree": False,
        "expectedExitCode": 2,
        "summary": empty_summary,
        "statuses": [],
    }
    if not isinstance(report, dict) or report.get("schema") != "vrcforge.doctor.v1":
        return result
    summary = report.get("summary")
    checks = report.get("checks")
    if not isinstance(summary, dict) or not isinstance(checks, list):
        return result

    normalized: dict[str, int] = {}
    for key in empty_summary:
        value = summary.get(key)
        if type(value) is not int or value < 0:
            return result
        normalized[key] = value

    status_keys = {
        "ok": "okCount",
        "warning": "warningCount",
        "error": "errorCount",
        "unknown": "unknownCount",
    }
    computed = {key: 0 for key in empty_summary}
    seen_ids: set[str] = set()
    statuses: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            return result
        check_id = check.get("id")
        status = check.get("status")
        fixable = check.get("fixable")
        if (
            not isinstance(check_id, str)
            or not check_id.strip()
            or check_id in seen_ids
            or status not in status_keys
            or type(fixable) is not bool
        ):
            return result
        seen_ids.add(check_id)
        statuses.append(status)
        computed[status_keys[status]] += 1
    if normalized != computed:
        return result

    expected_exit = (
        2
        if normalized["errorCount"] > 0
        else 1
        if normalized["warningCount"] > 0 or normalized["unknownCount"] > 0
        else 0
    )
    valid = type(report.get("ok")) is bool and report.get("ok") is (expected_exit != 2)
    return {
        "valid": valid,
        "errorFree": valid and expected_exit in {0, 1},
        "expectedExitCode": expected_exit,
        "summary": normalized,
        "statuses": statuses,
    }


def evaluate_packaged_doctor(report: Any) -> tuple[bool, dict[str, Any]]:
    contract = inspect_doctor_report(report)
    checks = report.get("checks") if isinstance(report, dict) and isinstance(report.get("checks"), list) else []
    checks_by_id = {
        item.get("id"): item
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    install_integrity = checks_by_id.get("desktop.install_integrity", {})
    install_detail = (
        install_integrity.get("detail")
        if isinstance(install_integrity, dict) and isinstance(install_integrity.get("detail"), dict)
        else {}
    )
    degraded = "doctor.degraded" in checks_by_id
    ok = bool(
        contract["valid"]
        and contract["errorFree"]
        and not degraded
        and isinstance(install_integrity, dict)
        and install_integrity.get("status") == "ok"
        and install_detail.get("schemaValid") is True
        and install_detail.get("manifestVersionMatched") is True
        and install_detail.get("versionFileMatched") is True
    )
    return ok, {
        "schema": report.get("schema") if isinstance(report, dict) else None,
        "reportValid": contract["valid"],
        "errorFree": contract["errorFree"],
        "summary": contract["summary"],
        "degraded": degraded,
        "installIntegrityStatus": install_integrity.get("status") if isinstance(install_integrity, dict) else None,
        "schemaValid": install_detail.get("schemaValid"),
        "manifestVersionMatched": install_detail.get("manifestVersionMatched"),
        "versionFileMatched": install_detail.get("versionFileMatched"),
        "fileChecks": install_detail.get("fileChecks"),
    }


def evaluate_packaged_cli_doctor(payload: Any, semantic_exit_code: int) -> tuple[bool, dict[str, Any]]:
    report = payload.get("report") if isinstance(payload, dict) and isinstance(payload.get("report"), dict) else {}
    contract = inspect_doctor_report(report)
    payload_exit = payload.get("exitCode") if isinstance(payload, dict) else None
    payload_summary = payload.get("summary") if isinstance(payload, dict) else None
    expected_exit = contract["expectedExitCode"]
    ok = bool(
        contract["valid"]
        and contract["errorFree"]
        and expected_exit in {0, 1}
        and semantic_exit_code == expected_exit
        and payload_exit == expected_exit
        and payload_summary == contract["summary"]
        and payload.get("schema") == "vrcforge.cli-doctor.v1"
        and payload.get("error") is None
    )
    return ok, {
        "schema": payload.get("schema") if isinstance(payload, dict) else None,
        "reportSchema": report.get("schema"),
        "reportValid": contract["valid"],
        "errorFree": contract["errorFree"],
        "semanticExitCode": semantic_exit_code,
        "payloadExitCode": payload_exit,
        "expectedExitCode": expected_exit,
        "summary": payload_summary,
        "error": payload.get("error") if isinstance(payload, dict) else None,
    }


def evaluate_packaged_unity_readiness(payload: Any) -> tuple[bool, dict[str, Any]]:
    """Require the controlled no-project readiness projection to fail closed."""

    status = payload.get("unityStatus") if isinstance(payload, dict) else {}
    status = status if isinstance(status, dict) else {}
    ok = bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("schema") == "vrcforge.unity_readiness_refresh.v1"
        and status.get("connected") is False
        and status.get("mcpServerReachable") is False
        and status.get("unityInstanceRegistered") is False
        and status.get("projectPath") == ""
        and status.get("error") == "No Unity project is selected."
    )
    return ok, {
        "ok": payload.get("ok") if isinstance(payload, dict) else None,
        "schema": payload.get("schema") if isinstance(payload, dict) else None,
        "connected": status.get("connected"),
        "mcpServerReachable": status.get("mcpServerReachable"),
        "unityInstanceRegistered": status.get("unityInstanceRegistered"),
        "projectPath": status.get("projectPath"),
        "error": status.get("error"),
    }


def evaluate_packaged_know_yourself(payload: Any) -> tuple[bool, dict[str, Any]]:
    """Validate the read-only no-project agent-tool result without retaining it."""

    result = payload.get("result") if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}
    provider = result.get("provider") if isinstance(result.get("provider"), dict) else {}
    project_context = result.get("projectContext") if isinstance(result.get("projectContext"), dict) else {}
    boundaries = result.get("operatingBoundaries") if isinstance(result.get("operatingBoundaries"), dict) else {}
    ok = bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("tool") == "vrcforge_know_yourself"
        and result.get("ok") is True
        and result.get("schema") == "vrcforge.know_yourself.v1"
        and project_context.get("projectSelected") is False
        and result.get("readyForUnityWork") is False
        and provider.get("automaticTestCallMade") is False
        and boundaries.get("skillMutatesUnityProject") is False
        and boundaries.get("skillInstallsDependencies") is False
        and boundaries.get("skillLaunchesOrClosesUnity") is False
        and boundaries.get("directUnityProjectWrites") is False
    )
    return ok, {
        "ok": payload.get("ok") if isinstance(payload, dict) else None,
        "tool": payload.get("tool") if isinstance(payload, dict) else None,
        "schema": result.get("schema"),
        "projectSelected": project_context.get("projectSelected"),
        "readyForUnityWork": result.get("readyForUnityWork"),
        "providerAutomaticTestCallMade": provider.get("automaticTestCallMade"),
        "readOnlyBoundaries": {
            key: boundaries.get(key)
            for key in (
                "skillMutatesUnityProject",
                "skillInstallsDependencies",
                "skillLaunchesOrClosesUnity",
                "directUnityProjectWrites",
            )
        },
    }


def evaluate_packaged_planning_manifest(payload: Any) -> tuple[bool, dict[str, Any]]:
    """Require a planning-only manifest with no direct or projected writes."""

    tools = payload.get("tools") if isinstance(payload, dict) else []
    tools = tools if isinstance(tools, list) else []
    write_targets = payload.get("writeTargets") if isinstance(payload, dict) else None
    invalid_tools = [
        str(item.get("name") or "<unnamed>")
        for item in tools
        if not isinstance(item, dict) or item.get("write") is not False
    ]
    know_yourself_exposed = any(
        isinstance(item, dict) and item.get("name") == "vrcforge_know_yourself"
        for item in tools
    )
    ok = bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("enabled") is True
        and payload.get("requiresToken") is True
        and payload.get("allowWriteRequests") is False
        and payload.get("exposureLayer") == "planning"
        and write_targets == []
        and not invalid_tools
        and know_yourself_exposed
    )
    return ok, {
        "ok": payload.get("ok") if isinstance(payload, dict) else None,
        "enabled": payload.get("enabled") if isinstance(payload, dict) else None,
        "requiresToken": payload.get("requiresToken") if isinstance(payload, dict) else None,
        "allowWriteRequests": payload.get("allowWriteRequests") if isinstance(payload, dict) else None,
        "exposureLayer": payload.get("exposureLayer") if isinstance(payload, dict) else None,
        "writeTargetsEmpty": write_targets == [],
        "allToolsReadOnly": not invalid_tools,
        "knowYourselfExposed": know_yourself_exposed,
        "toolCount": payload.get("toolCount") if isinstance(payload, dict) else None,
    }


def find_secret_leaks(value: Any, secrets_to_protect: list[str]) -> list[str]:
    """Return stable labels for literal session-token leaks in probe evidence."""

    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return [f"secret-{index + 1}" for index, secret in enumerate(secrets_to_protect) if secret and secret in text]


def find_secret_leaks_in_files(paths: list[Path], secrets_to_protect: list[str]) -> list[str]:
    """Inspect only probe-owned local evidence files for literal token leakage."""

    findings: list[str] = []
    encoded_secrets = [secret.encode("utf-8") for secret in secrets_to_protect]
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as source:
            data = source.read()
        for index, secret in enumerate(encoded_secrets):
            if secret and secret in data:
                findings.append(f"{path.name}:secret-{index + 1}")
    return sorted(set(findings))


def find_secret_leaks_in_zip(path: Path, secrets_to_protect: list[str]) -> list[str]:
    """Inspect support-bundle members because ZIP compression hides text from file scans."""

    findings: list[str] = []
    if not path.is_file():
        return findings
    encoded_secrets = [secret.encode("utf-8") for secret in secrets_to_protect]
    overlap_size = max((len(secret) for secret in encoded_secrets), default=1) - 1
    with zipfile.ZipFile(path) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            tail = b""
            found: set[int] = set()
            with bundle.open(member) as source:
                while chunk := source.read(1024 * 1024):
                    candidate = tail + chunk
                    for index, secret in enumerate(encoded_secrets):
                        if secret and secret in candidate:
                            found.add(index)
                    tail = candidate[-overlap_size:] if overlap_size else b""
            findings.extend(f"{member.filename}:secret-{index + 1}" for index in sorted(found))
    return sorted(set(findings))


def collect_token_privacy_findings(
    *,
    raw_values: Any,
    evidence_files: list[Path],
    support_bundle: Path | None,
    secrets_to_protect: list[str],
) -> list[str]:
    findings = [*find_secret_leaks(raw_values, secrets_to_protect)]
    findings.extend(find_secret_leaks_in_files(evidence_files, secrets_to_protect))
    if support_bundle is not None:
        findings.extend(find_secret_leaks_in_zip(support_bundle, secrets_to_protect))
    return sorted(set(findings))


def wait_for_bootstrap(
    base_url: str,
    token: str,
    process: subprocess.Popen[bytes],
    timeout: float,
    raw_bodies: list[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = "backend did not accept a request"
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"packaged backend exited during startup with code {exit_code}")
        try:
            return request_json(
                base_url,
                token,
                "GET",
                "/api/app/bootstrap",
                timeout=3.0,
                raw_bodies=raw_bodies,
            )
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
        if info.is_dir():
            continue
        if info.file_size > 5 * 1024 * 1024:
            findings.append(f"{info.filename}:privacy-scan-size-limit")
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
    (config_dir / "config.json").write_text("{}\n", encoding="utf-8")
    gateway_token = secrets.token_urlsafe(32)
    gateway_config_path = config_dir / "agent_gateway.json"
    gateway_config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "require_token": True,
                "token": gateway_token,
                "allow_write_requests": False,
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
    wrong_app_token = secrets.token_urlsafe(32)
    wrong_gateway_token = secrets.token_urlsafe(32)
    protected_tokens = [token, gateway_token, wrong_app_token, wrong_gateway_token]
    process: subprocess.Popen[bytes] | None = None
    stdout_handle = None
    stderr_handle = None
    assertions: list[str] = []
    bootstrap_ok = False
    app_auth_negative_ok = False
    gateway_auth_negative_ok = False
    proof_index_ok = False
    support_bundle_ok = False
    doctor_ok = False
    cli_doctor_ok = False
    unity_readiness_refresh_ok = False
    gateway_manifest_ok = False
    know_yourself_ok = False
    token_privacy_ok = False
    portable_mode = False
    support_bundle_path = ""
    bootstrap_evidence: dict[str, Any] = {}
    proof_evidence: dict[str, Any] = {}
    bundle_evidence: dict[str, Any] = {}
    doctor_evidence: dict[str, Any] = {}
    cli_doctor_evidence: dict[str, Any] = {}
    unity_readiness_evidence: dict[str, Any] = {}
    gateway_manifest_evidence: dict[str, Any] = {}
    know_yourself_evidence: dict[str, Any] = {}
    token_privacy_evidence: dict[str, Any] = {"findings": []}
    app_auth_evidence: dict[str, Any] = {}
    gateway_auth_evidence: dict[str, Any] = {}
    cleanup: dict[str, Any] = {"ok": False, "portReleased": False}
    raw_privacy_values: dict[str, Any] = {}
    raw_api_bodies: list[str] = []
    support_bundle_file: Path | None = None

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

        env = isolated_backend_environment(
            {
                "VRCFORGE_APP_DIR": str(packaged_root),
                "VRCFORGE_USER_DATA_DIR": str(user_data),
                "VRCFORGE_CONFIG_DIR": str(config_dir),
                "VRCFORGE_CONFIG_PATH": str(config_dir / "config.json"),
                "VRCFORGE_LOG_DIR": str(log_dir),
                "VRCFORGE_ARTIFACTS_DIR": str(runtime_artifacts),
                "VRCFORGE_DASHBOARD_DIR": str(dashboard_dir),
                "VRCFORGE_SETTINGS_PATH": str(config_dir / "settings.json"),
                "VRCFORGE_APP_SESSION_TOKEN": token,
                "VRCFORGE_DESKTOP_EXECUTOR": "0",
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

        bootstrap = wait_for_bootstrap(
            base_url,
            token,
            process,
            max(1.0, float(args.timeout)),
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["bootstrap"] = bootstrap
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

        missing_auth = request_failure(base_url, "", "GET", "/api/app/bootstrap")
        wrong_auth = request_failure(base_url, wrong_app_token, "GET", "/api/app/bootstrap")
        raw_privacy_values["missingAppAuthFailure"] = missing_auth.get("body")
        raw_privacy_values["wrongAppAuthFailure"] = wrong_auth.get("body")
        app_auth_negative_ok, app_auth_evidence = evaluate_authentication_negatives(
            missing_auth,
            wrong_auth,
        )

        proof_index = request_json(
            base_url,
            token,
            "GET",
            "/api/app/optimization/proofs?limit=10",
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["proofIndex"] = proof_index
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

        readiness_refresh = request_json(
            base_url,
            token,
            "POST",
            "/api/app/unity/readiness/refresh",
            {},
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["readinessRefresh"] = readiness_refresh
        unity_readiness_refresh_ok, unity_readiness_evidence = evaluate_packaged_unity_readiness(
            readiness_refresh
        )

        missing_gateway_auth = request_failure(
            base_url,
            "",
            "GET",
            "/api/agent/manifest?exposure_layer=planning",
        )
        wrong_gateway_auth = request_failure(
            base_url,
            wrong_gateway_token,
            "GET",
            "/api/agent/manifest?exposure_layer=planning",
        )
        raw_privacy_values["missingGatewayAuthFailure"] = missing_gateway_auth.get("body")
        raw_privacy_values["wrongGatewayAuthFailure"] = wrong_gateway_auth.get("body")
        gateway_auth_negative_ok, gateway_auth_evidence = evaluate_authentication_negatives(
            missing_gateway_auth,
            wrong_gateway_auth,
        )

        planning_manifest = request_json(
            base_url,
            gateway_token,
            "GET",
            "/api/agent/manifest?exposure_layer=planning",
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["planningManifest"] = planning_manifest
        gateway_manifest_ok, gateway_manifest_evidence = evaluate_packaged_planning_manifest(
            planning_manifest
        )

        know_yourself = request_json(
            base_url,
            gateway_token,
            "POST",
            "/api/agent/tool/vrcforge_know_yourself",
            {"agent_name": "packaged-readiness-smoke", "params": {}},
            timeout=bounded_runtime_probe_timeout(args.timeout),
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["knowYourself"] = know_yourself
        know_yourself_ok, know_yourself_evidence = evaluate_packaged_know_yourself(know_yourself)

        runtime_probe_timeout = bounded_runtime_probe_timeout(args.timeout)
        doctor = request_json(
            base_url,
            token,
            "GET",
            "/api/app/doctor",
            timeout=runtime_probe_timeout,
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["doctor"] = doctor
        doctor_ok, doctor_evidence = evaluate_packaged_doctor(doctor)

        cli_doctor = subprocess.run(
            [
                str(backend_exe),
                "--cli",
                "--endpoint",
                base_url,
                "--token",
                token,
                "--json",
                "doctor",
            ],
            cwd=str(packaged_root),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=runtime_probe_timeout,
            creationflags=creationflags,
        )
        raw_privacy_values["cliDoctorStdout"] = cli_doctor.stdout
        raw_privacy_values["cliDoctorStderr"] = cli_doctor.stderr
        try:
            cli_doctor_payload = json.loads(cli_doctor.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("packaged CLI Doctor did not emit valid JSON") from exc
        raw_privacy_values["cliDoctorPayload"] = cli_doctor_payload
        cli_doctor_ok, cli_doctor_evidence = evaluate_packaged_cli_doctor(
            cli_doctor_payload,
            cli_doctor.returncode,
        )

        bundle_response = request_json(
            base_url,
            token,
            "POST",
            "/api/app/support-bundle",
            {"includeFullPaths": False, "logLimit": 50},
            timeout=runtime_probe_timeout,
            raw_bodies=raw_api_bodies,
        )
        raw_privacy_values["supportBundleResponse"] = bundle_response
        support_bundle_path = str(bundle_response.get("bundlePath") or "")
        support_bundle_file = Path(support_bundle_path).resolve() if support_bundle_path else None
        bundle_validation = (
            validate_support_bundle(support_bundle_file, version)
            if support_bundle_file is not None
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
        raw_privacy_values["exception"] = str(exc)
        assertions.append(str(exc))
    finally:
        cleanup = stop_process(process, port)
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
        try:
            gateway_config_path.unlink(missing_ok=True)
            gateway_config_removed = not gateway_config_path.exists()
        except OSError as exc:
            gateway_config_removed = False
            raw_privacy_values["gatewayConfigRemovalError"] = str(exc)
        cleanup["gatewayConfigRemoved"] = gateway_config_removed
        cleanup["ok"] = bool(cleanup.get("ok") and gateway_config_removed)

    if not bootstrap_ok:
        assertions.append("packaged bootstrap contract did not pass")
    if not app_auth_negative_ok:
        assertions.append("packaged App endpoint authentication negative checks did not pass")
    if not gateway_auth_negative_ok:
        assertions.append("packaged Gateway endpoint authentication negative checks did not pass")
    if not proof_index_ok:
        assertions.append("packaged optimizer proof index contract did not pass")
    if not support_bundle_ok:
        assertions.append("packaged support bundle contract did not pass")
    if not doctor_ok:
        assertions.append("packaged Doctor install-integrity contract did not pass")
    if not cli_doctor_ok:
        assertions.append("packaged CLI Doctor self-test did not pass")
    if not unity_readiness_refresh_ok:
        assertions.append("packaged no-project Unity readiness refresh contract did not pass")
    if not gateway_manifest_ok:
        assertions.append("packaged planning manifest contract did not expose Know Yourself safely")
    if not know_yourself_ok:
        assertions.append("packaged Know Yourself read-only contract did not pass")
    if not cleanup.get("ok"):
        assertions.append("packaged backend did not stop cleanly")
    assertions = list(dict.fromkeys(assertions))

    raw_privacy_values["apiBodies"] = raw_api_bodies
    raw_privacy_values["assertions"] = assertions
    final_evidence_files = [path for path in run_dir.rglob("*") if path.is_file()]
    try:
        token_findings = collect_token_privacy_findings(
            raw_values=raw_privacy_values,
            evidence_files=final_evidence_files,
            support_bundle=support_bundle_file,
            secrets_to_protect=protected_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - an incomplete privacy scan fails closed.
        token_findings = ["privacy-scan-failed"]
        raw_privacy_values["privacyScanError"] = str(exc)
    token_privacy_evidence = {"findings": token_findings}
    token_privacy_ok = not token_findings
    if not token_privacy_ok:
        assertions.append("packaged readiness evidence leaked a session token or could not be scanned")
    assertions = list(dict.fromkeys(assertions))

    summary = {
        "schema": SCHEMA,
        "ok": not assertions,
        "generatedAt": utc_now(),
        "version": version,
        "portableMode": portable_mode,
        "bootstrapOk": bootstrap_ok,
        "appAuthNegativeOk": app_auth_negative_ok,
        "gatewayAuthNegativeOk": gateway_auth_negative_ok,
        "proofIndexOk": proof_index_ok,
        "supportBundleOk": support_bundle_ok,
        "doctorOk": doctor_ok,
        "cliDoctorOk": cli_doctor_ok,
        "unityReadinessRefreshOk": unity_readiness_refresh_ok,
        "gatewayManifestOk": gateway_manifest_ok,
        "knowYourselfOk": know_yourself_ok,
        "tokenPrivacyOk": token_privacy_ok,
        "supportBundlePath": support_bundle_path,
        "payloadZip": str(payload_zip),
        "payloadZipSha256": payload_zip_sha256,
        "packagedRoot": str(packaged_root),
        "backend": str(backend_exe),
        "port": port,
        "bootstrap": bootstrap_evidence,
        "appAuth": app_auth_evidence,
        "gatewayAuth": gateway_auth_evidence,
        "proofIndex": proof_evidence,
        "supportBundle": bundle_evidence,
        "doctor": doctor_evidence,
        "cliDoctor": cli_doctor_evidence,
        "unityReadinessRefresh": unity_readiness_evidence,
        "gatewayManifest": gateway_manifest_evidence,
        "knowYourself": know_yourself_evidence,
        "tokenPrivacy": token_privacy_evidence,
        "cleanup": cleanup,
        "assertions": assertions,
    }
    summary_leaks = find_secret_leaks(summary, protected_tokens)
    if summary_leaks:
        assertions = list(dict.fromkeys([*assertions, "packaged summary leaked a session token"]))
        summary["ok"] = False
        summary["tokenPrivacyOk"] = False
        summary["tokenPrivacy"] = {"findings": sorted(set(token_privacy_evidence["findings"] + summary_leaks))}
        summary["assertions"] = assertions
    write_summary(summary_path, summary)
    print(summary_path)
    if assertions:
        for assertion in assertions:
            print(assertion, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
