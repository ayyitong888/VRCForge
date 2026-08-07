from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_probe():
    path = REPO_ROOT / "scripts" / "diagnose_packaged_provider_config.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_config(api_key: str, vision_key: str) -> dict:
    return {
        "apiConfig": {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "api_key": api_key,
        },
        "visionConfig": {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "api_key": vision_key,
            "enabled": True,
        },
    }


def test_config_sections_require_exact_api_and_vision_values() -> None:
    probe = load_probe()
    payload = sample_config("api-probe", "vision-probe")

    assert probe.api_section_matches(payload, "api-probe") is True
    assert probe.config_sections_match(payload, "api-probe", "vision-probe") is True
    assert probe.config_sections_match(payload, "api-probe", "wrong") is False


def test_persisted_sections_require_both_sections(tmp_path: Path) -> None:
    probe = load_probe()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api": {"provider": "openai", "model": "gpt-4.1-mini", "api_key": "api-probe"},
                "vision": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "api_key": "vision-probe",
                    "enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )

    assert probe.persisted_sections_match(config_path, "api-probe", "vision-probe") is True
    config_path.write_text(json.dumps({"api": {}}), encoding="utf-8")
    assert probe.persisted_sections_match(config_path, "api-probe", "vision-probe") is False


def test_redaction_helpers_detect_probe_secret_in_payload_and_logs(tmp_path: Path) -> None:
    probe = load_probe()
    secret = "provider-config-api-probe"
    assert probe.contains_secret({"nested": [secret]}, {secret}) is True
    assert probe.contains_secret({"nested": ["<redacted>"]}, {secret}) is False
    assert probe.redact_text(f"error {secret}", {secret}) == "error <redacted>"

    user_data = tmp_path / "user-data"
    (user_data / "logs").mkdir(parents=True)
    (tmp_path / "backend-first-stdout.log").write_text("safe", encoding="utf-8")
    (user_data / "logs" / "backend.log").write_text("safe", encoding="utf-8")
    assert probe.logs_exclude_secrets(tmp_path, user_data, {secret}) is True
    (user_data / "logs" / "backend.log").write_text(secret, encoding="utf-8")
    assert probe.logs_exclude_secrets(tmp_path, user_data, {secret}) is False


def test_probe_cli_exposes_payload_binding(monkeypatch) -> None:
    probe = load_probe()
    monkeypatch.setattr(
        probe.sys,
        "argv",
        ["diagnose_packaged_provider_config.py", "--payload-zip", "dist/release/example.zip"],
    )

    args = probe.parse_args()

    assert args.payload_zip == "dist/release/example.zip"


def test_runtime_env_forces_isolated_config_and_auth(monkeypatch, tmp_path: Path) -> None:
    probe = load_probe()
    inherited_config = tmp_path / "must-not-be-used.json"
    monkeypatch.setenv("VRCFORGE_CONFIG_PATH", str(inherited_config))
    monkeypatch.setenv("VRCFORGE_DISABLE_APP_AUTH", "1")

    env, _user_data, config_path = probe.build_runtime_env(
        tmp_path / "package",
        tmp_path / "runtime",
        "probe-session-token",
    )

    assert Path(env["VRCFORGE_CONFIG_PATH"]) == config_path
    assert config_path != inherited_config
    assert env["VRCFORGE_APP_SESSION_TOKEN"] == "probe-session-token"
    assert "VRCFORGE_DISABLE_APP_AUTH" not in env


def test_auth_gate_requires_both_negative_requests(monkeypatch) -> None:
    probe = load_probe()
    statuses = iter((401, 403))
    monkeypatch.setattr(probe, "request_status", lambda _base_url, _token: next(statuses))
    assert probe.app_auth_rejects_missing_and_wrong_tokens("http://127.0.0.1:1") is True

    statuses = iter((200, 401))
    monkeypatch.setattr(probe, "request_status", lambda _base_url, _token: next(statuses))
    assert probe.app_auth_rejects_missing_and_wrong_tokens("http://127.0.0.1:1") is False


def test_session_token_is_treated_as_probe_secret(tmp_path: Path) -> None:
    probe = load_probe()
    token = "provider-config-session-token"
    user_data = tmp_path / "user-data"
    (user_data / "logs").mkdir(parents=True)
    (tmp_path / "backend-first-stdout.log").write_text(token, encoding="utf-8")

    assert probe.contains_secret({"session": token}, {token}) is True
    assert probe.logs_exclude_secrets(tmp_path, user_data, {token}) is False
