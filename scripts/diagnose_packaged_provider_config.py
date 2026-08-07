from __future__ import annotations

"""Exercise API and Vision configuration persistence in a packaged backend.

This probe is deliberately narrow: it starts only the packaged loopback
backend with an isolated user-data root, saves opaque fake credentials through
the authenticated Settings API, restarts the same isolated runtime, and checks
that both sections persist without appearing in app-facing diagnostics.  It
does not call a provider or touch a real user configuration.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

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


SCHEMA = "vrcforge.packaged_provider_config_probe.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify packaged API+Vision config restart persistence and diagnostic redaction."
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
        help="Artifact root. A packaged-provider-config-* run directory is created below it.",
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


def build_runtime_env(packaged_root: Path, runtime_root: Path, token: str) -> tuple[dict[str, str], Path, Path]:
    """Construct the complete private runtime scope before spawning a backend."""

    user_data = runtime_root / "user-data"
    config_dir = user_data / "config"
    log_dir = user_data / "logs"
    artifacts_dir = user_data / "artifacts"
    dashboard_dir = packaged_root / "dashboard"
    for directory in (config_dir, log_dir, artifacts_dir):
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
    env = os.environ.copy()
    # The probe performs authenticated writes. Never inherit an escape hatch or
    # a host-selected config document into the packaged child.
    env.pop("VRCFORGE_DISABLE_APP_AUTH", None)
    env.update(
        {
            "VRCFORGE_APP_DIR": str(packaged_root),
            "VRCFORGE_USER_DATA_DIR": str(user_data),
            "VRCFORGE_CONFIG_DIR": str(config_dir),
            "VRCFORGE_LOG_DIR": str(log_dir),
            "VRCFORGE_ARTIFACTS_DIR": str(artifacts_dir),
            "VRCFORGE_DASHBOARD_DIR": str(dashboard_dir),
            "VRCFORGE_SETTINGS_PATH": str(config_dir / "settings.json"),
            "VRCFORGE_CONFIG_PATH": str(config_dir / "config.json"),
            "VRCFORGE_APP_SESSION_TOKEN": token,
        }
    )
    return env, user_data, config_dir / "config.json"


def start_backend(
    backend_exe: Path,
    packaged_root: Path,
    env: dict[str, str],
    port: int,
    run_dir: Path,
    phase: str,
) -> tuple[subprocess.Popen[bytes], Any, Any]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    stdout_handle = (run_dir / f"backend-{phase}-stdout.log").open("wb")
    stderr_handle = (run_dir / f"backend-{phase}-stderr.log").open("wb")
    try:
        process = subprocess.Popen(
            [str(backend_exe), "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(packaged_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    return process, stdout_handle, stderr_handle


def request_status(base_url: str, token: str | None) -> int:
    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base_url}/api/config", method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read()
        return int(exc.code)


def app_auth_rejects_missing_and_wrong_tokens(base_url: str) -> bool:
    return request_status(base_url, None) in {401, 403} and request_status(
        base_url, "packaged-provider-config-invalid-token"
    ) in {401, 403}


def contains_secret(value: Any, secrets_to_find: set[str]) -> bool:
    """Return whether an app-facing payload carries one of this run's fake keys."""

    if isinstance(value, str):
        return any(secret in value for secret in secrets_to_find)
    if isinstance(value, dict):
        return any(contains_secret(item, secrets_to_find) for item in value.values())
    if isinstance(value, list):
        return any(contains_secret(item, secrets_to_find) for item in value)
    return False


def config_sections_match(payload: Any, api_key: str, vision_key: str) -> bool:
    if not isinstance(payload, dict):
        return False
    api = payload.get("apiConfig")
    vision = payload.get("visionConfig")
    return bool(
        isinstance(api, dict)
        and isinstance(vision, dict)
        and api.get("provider") == "openai"
        and api.get("model") == "gpt-4.1-mini"
        and api.get("api_key") == api_key
        and vision.get("provider") == "openai"
        and vision.get("model") == "gpt-4.1-mini"
        and vision.get("api_key") == vision_key
        and vision.get("enabled") is True
    )


def api_section_matches(payload: Any, api_key: str) -> bool:
    if not isinstance(payload, dict):
        return False
    api = payload.get("apiConfig")
    return bool(
        isinstance(api, dict)
        and api.get("provider") == "openai"
        and api.get("model") == "gpt-4.1-mini"
        and api.get("api_key") == api_key
    )


def persisted_sections_match(config_path: Path, api_key: str, vision_key: str) -> bool:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    api = payload.get("api")
    vision = payload.get("vision")
    return bool(
        isinstance(api, dict)
        and isinstance(vision, dict)
        and api.get("provider") == "openai"
        and api.get("model") == "gpt-4.1-mini"
        and api.get("api_key") == api_key
        and vision.get("provider") == "openai"
        and vision.get("model") == "gpt-4.1-mini"
        and vision.get("api_key") == vision_key
        and vision.get("enabled") is True
    )


def logs_exclude_secrets(run_dir: Path, user_data: Path, secrets_to_find: set[str]) -> bool:
    candidates = [*run_dir.glob("backend-*.log"), *user_data.joinpath("logs").glob("**/*")]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if contains_secret(text, secrets_to_find):
            return False
    return True


def redact_text(value: str, secrets_to_redact: set[str]) -> str:
    result = str(value)
    for secret in secrets_to_redact:
        result = result.replace(secret, "<redacted>")
    return result


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    version = read_expected_version(args.version)
    packaged_root = resolve_repo_path(args.packaged_root)
    artifacts_root = resolve_repo_path(args.artifacts_dir)
    payload_zip = resolve_repo_path(
        args.payload_zip or f"dist/release/VRCForge_Windows_x64_{version}.zip"
    )
    payload_zip_sha256 = sha256_file(payload_zip) if payload_zip.is_file() else ""
    run_dir = artifacts_root / f"packaged-provider-config-{run_stamp()}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    summary_path = run_dir / "packaged-provider-config-summary.json"
    backend_exe = packaged_root / "backend" / "vrcforge_backend.exe"
    dashboard_index = packaged_root / "dashboard" / "index.html"
    token = secrets.token_urlsafe(32)
    api_key = f"provider-config-api-{secrets.token_urlsafe(24)}"
    vision_key = f"provider-config-vision-{secrets.token_urlsafe(24)}"
    probe_secrets = {token, api_key, vision_key}
    assertions: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    stdout_handle = None
    stderr_handle = None
    first_cleanup: dict[str, Any] = {"ok": False, "portReleased": False}
    final_cleanup: dict[str, Any] = {"ok": False, "portReleased": False}
    bootstrap_redacted = False
    auth_required_ok = False
    doctor_redacted = False
    support_bundle_redacted = False
    logs_redacted = False
    initial_save_ok = False
    config_file_ok = False
    restart_recovery_ok = False
    bundle_evidence: dict[str, Any] = {}
    port = choose_port(args.port)
    base_url = f"http://127.0.0.1:{port}"
    env, user_data, config_path = build_runtime_env(packaged_root, run_dir / "runtime", token)

    try:
        missing = [
            str(path)
            for path in (backend_exe, packaged_root / "VERSION", dashboard_index, payload_zip)
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(f"packaged payload is missing required files: {missing}")
        packaged_version = (packaged_root / "VERSION").read_text(encoding="utf-8").strip()
        if packaged_version != version:
            raise RuntimeError(f"packaged VERSION mismatch: expected {version}, got {packaged_version}")

        process, stdout_handle, stderr_handle = start_backend(
            backend_exe, packaged_root, env, port, run_dir, "first"
        )
        bootstrap_before = wait_for_bootstrap(base_url, token, process, max(1.0, float(args.timeout)))
        auth_required_ok = app_auth_rejects_missing_and_wrong_tokens(base_url)
        api_response = request_json(
            base_url,
            token,
            "POST",
            "/api/config",
            {
                "provider": "openai",
                "api_key": api_key,
                "base_url": "https://example.invalid/v1",
                "model": "gpt-4.1-mini",
                "api_type": "responses",
                "thinking_level": "",
            },
        )
        vision_response = request_json(
            base_url,
            token,
            "POST",
            "/api/config/vision",
            {
                "provider": "openai",
                "api_key": vision_key,
                "base_url": "https://example.invalid/v1",
                "model": "gpt-4.1-mini",
                "enabled": True,
            },
        )
        initial_save_ok = api_section_matches(api_response, api_key) and config_sections_match(
            vision_response, api_key, vision_key
        )
        config_file_ok = persisted_sections_match(config_path, api_key, vision_key)
        bootstrap_after = request_json(base_url, token, "GET", "/api/app/bootstrap")
        doctor = request_json(base_url, token, "GET", "/api/app/doctor")
        bootstrap_redacted = not contains_secret(bootstrap_before, probe_secrets) and not contains_secret(
            bootstrap_after, probe_secrets
        )
        doctor_redacted = not contains_secret(doctor, probe_secrets)

        bundle_response = request_json(
            base_url,
            token,
            "POST",
            "/api/app/support-bundle",
            {"includeFullPaths": False, "logLimit": 50},
            timeout=max(30.0, float(args.timeout)),
        )
        bundle_path = Path(str(bundle_response.get("bundlePath") or ""))
        bundle_validation = validate_support_bundle(bundle_path, version)
        bundle_contents_clean = False
        if bundle_path.is_file():
            import zipfile

            with zipfile.ZipFile(bundle_path) as bundle:
                bundle_contents_clean = (
                    not scan_support_bundle_privacy(bundle)
                    and not contains_secret(
                        "".join(
                            bundle.read(member).decode("utf-8", errors="replace")
                            for member in bundle.namelist()
                            if not member.endswith("/")
                        ),
                        probe_secrets,
                    )
                )
        support_bundle_redacted = bool(
            bundle_response.get("ok") is True
            and bundle_response.get("redacted") is True
            and bundle_validation.get("ok") is True
            and bundle_contents_clean
        )
        bundle_evidence = {
            "responseOk": bundle_response.get("ok"),
            "responseSchema": bundle_response.get("schema"),
            "redacted": bundle_response.get("redacted"),
            "validationOk": bundle_validation.get("ok"),
            "privacyFindings": bundle_validation.get("privacyFindings", []),
        }

        first_cleanup = stop_process(process, port)
        process = None
        stdout_handle.close()
        stdout_handle = None
        stderr_handle.close()
        stderr_handle = None
        if not first_cleanup.get("ok"):
            raise RuntimeError("first packaged backend did not stop cleanly")

        process, stdout_handle, stderr_handle = start_backend(
            backend_exe, packaged_root, env, port, run_dir, "restart"
        )
        wait_for_bootstrap(base_url, token, process, max(1.0, float(args.timeout)))
        recovered_config = request_json(base_url, token, "GET", "/api/config")
        restart_recovery_ok = config_sections_match(recovered_config, api_key, vision_key)
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
        assertions.append("packaged config API did not reject missing and invalid app tokens")
    if not initial_save_ok:
        assertions.append("authenticated API+Vision save contract did not preserve both sections")
    if not config_file_ok:
        assertions.append("isolated config document did not persist both sections")
    if not restart_recovery_ok:
        assertions.append("packaged restart did not recover both config sections")
    if not bootstrap_redacted:
        assertions.append("app bootstrap exposed a probe credential")
    if not doctor_redacted:
        assertions.append("Doctor response exposed a probe credential")
    if not support_bundle_redacted:
        assertions.append("support bundle did not redact probe credentials")
    if not logs_redacted:
        assertions.append("backend diagnostic logs exposed a probe credential")
    if not first_cleanup.get("ok") or not final_cleanup.get("ok") or port_is_open(port):
        assertions.append("packaged backend process or loopback port was not released")
    assertions = list(dict.fromkeys(assertions))

    summary = {
        "schema": SCHEMA,
        "ok": not assertions,
        "generatedAt": utc_now(),
        "version": version,
        "payloadZip": str(payload_zip),
        "payloadZipSha256": payload_zip_sha256,
        "packagedRoot": str(packaged_root),
        "backend": str(backend_exe),
        "isolatedUserData": str(user_data),
        "port": port,
        "initialSaveOk": initial_save_ok,
        "configFileOk": config_file_ok,
        "restartRecoveryOk": restart_recovery_ok,
        "authRequiredOk": auth_required_ok,
        "bootstrapRedacted": bootstrap_redacted,
        "doctorRedacted": doctor_redacted,
        "supportBundleRedacted": support_bundle_redacted,
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
