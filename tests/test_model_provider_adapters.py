from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashboard_server
from model_provider_adapters import (
    ProviderApiTypeError,
    ProviderCredentialError,
    normalize_provider_api_type,
    provider_model_descriptor,
    validate_provider_api_key,
)
from vrchat_blendshape_agent import Settings, request_openai_compatible_plan_with_metadata


@pytest.mark.parametrize("key", ["bad key", " bad", "bad ", "bad\nkey", "bad\rkey", "bad\tkey", "bad\ue00dkey"])
def test_provider_credential_rejects_non_header_text_without_echoing_secret(key: str) -> None:
    with pytest.raises(ProviderCredentialError) as exc_info:
        validate_provider_api_key(key)

    assert "re-enter" in str(exc_info.value).lower()
    assert key not in str(exc_info.value)


def test_provider_credential_preserves_valid_ascii_and_empty() -> None:
    assert validate_provider_api_key("") == ""
    assert validate_provider_api_key("sk-Valid_~.123") == "sk-Valid_~.123"


@pytest.mark.parametrize("invalid_key", ["unsafe\ncredential", "unsafe\rcredential", "unsafe\ue00dcredential"])
def test_agent_transport_blocks_invalid_key_before_fake_sdk_construction(
    monkeypatch: pytest.MonkeyPatch, invalid_key: str
) -> None:
    created: list[dict[str, object]] = []

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    settings = Settings(
        llm_provider="deepseek", llm_api_key=invalid_key, llm_base_url="https://example.invalid/v1",
        llm_model="test", llm_api_key_env="", gemini_thinking_level="", unity_mcp_command=[],
        unity_mcp_host="127.0.0.1", unity_mcp_port=0, unity_mcp_instance="", unity_mcp_retries=0,
        unity_mcp_retry_backoff_seconds=0.0, unity_mcp_timeout_seconds=0, export_tool_name="", execute_tool_name="",
        export_path=Path("export.json"), min_confidence=0.0,
    )

    with pytest.raises(ProviderCredentialError) as exc_info:
        request_openai_compatible_plan_with_metadata(settings, "test")

    assert created == []
    assert settings.llm_api_key not in str(exc_info.value)


@pytest.mark.parametrize("invalid_key", ["saved\ncredential", "saved\rcredential", "saved\ue00dcredential"])
def test_models_saved_key_fallback_rejects_invalid_key_before_fetch(
    monkeypatch: pytest.MonkeyPatch, invalid_key: str
) -> None:
    saved = dashboard_server.DASHBOARD_API_CONFIG
    called = False

    def fake_fetch(_config: object) -> list[dict[str, str]]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(dashboard_server, "fetch_provider_models", fake_fetch)
    dashboard_server.DASHBOARD_API_CONFIG = dashboard_server.DashboardApiConfig(
        provider="openai", api_key=invalid_key, base_url="https://api.openai.com/v1", model="test"
    )
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/models", json={"provider": "openai", "api_key": "", "base_url": "", "model": "test"})
        assert response.status_code == 400
        assert "re-enter" in response.json()["detail"].lower()
        assert invalid_key not in response.text
        assert not called
    finally:
        dashboard_server.DASHBOARD_API_CONFIG = saved


@pytest.mark.parametrize("invalid_key", ["saved\ncredential", "saved\rcredential", "saved\ue00dcredential"])
def test_config_saved_key_fallback_and_provider_test_reject_invalid_credentials(invalid_key: str) -> None:
    saved = dashboard_server.DASHBOARD_API_CONFIG
    dashboard_server.DASHBOARD_API_CONFIG = dashboard_server.DashboardApiConfig(
        provider="openai", api_key=invalid_key, base_url="https://api.openai.com/v1", model="test"
    )
    try:
        with TestClient(dashboard_server.app) as client:
            saved_response = client.post("/api/config", json={"provider": "openai", "api_key": "", "model": "test"})
            probe_response = client.post("/api/app/provider/test", json={"provider": "openai", "api_key": "draft\ncredential", "model": "test"})
        assert saved_response.status_code == 400
        assert probe_response.status_code == 200
        assert probe_response.json()["ok"] is False
        assert "re-enter" in probe_response.json()["message"].lower()
        assert invalid_key not in probe_response.text
    finally:
        dashboard_server.DASHBOARD_API_CONFIG = saved


def test_initial_loader_preserves_legacy_provider_model_and_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    legacy_key = "legacy\ue00dcredential"
    config_path.write_text(
        '{"api":{"provider":"deepseek","api_key":"' + legacy_key + '","model":"legacy-model"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard_server, "RUNTIME_SETTINGS_PATH", tmp_path / "missing-settings.json")

    config = dashboard_server.load_initial_dashboard_api_config()

    assert config.provider == "deepseek"
    assert config.model == "legacy-model"
    assert config.api_key == legacy_key
    assert config.api_type == "chat_completions"


def test_internal_legacy_config_uses_provider_transport_instead_of_chat_default() -> None:
    config = dashboard_server.DashboardApiConfig(
        provider="gemini",
        api_key="",
        base_url="",
        model="gemini-2.5-flash",
    )

    descriptor = dashboard_server.provider_config_descriptor(config)

    assert config.api_type is None
    assert descriptor["api_type"] == "generate_content"
    assert descriptor["resolvedApiType"] == "generate_content"


@pytest.mark.parametrize(
    ("provider", "model", "api_type", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", None, ("auto", "responses")),
        ("deepseek", "deepseek-v4-flash", "auto", ("auto", "responses")),
        ("deepseek", "deepseek-v4-pro", "auto", ("auto", "chat_completions")),
        ("deepseek", "DeepSeek-V4-Flash", "auto", ("auto", "chat_completions")),
        ("deepseek", "future-model", "auto", ("auto", "chat_completions")),
        ("gemini", "gemini-test", None, ("generate_content", "generate_content")),
        ("vertexai", "vertex-test", None, ("generate_content", "generate_content")),
        ("anthropic", "claude-test", None, ("messages", "messages")),
    ],
)
def test_provider_api_type_preserves_legacy_and_resolves_auto(
    provider: str, model: str, api_type: str | None, expected: tuple[str, str]
) -> None:
    assert normalize_provider_api_type(provider, model, api_type) == expected


@pytest.mark.parametrize(
    ("provider", "model", "api_type"),
    [
        ("deepseek", "deepseek-v4-pro", "responses"),
        ("deepseek", "future-model", "responses"),
        ("deepseek", "DeepSeek-V4-Flash", "responses"),
        ("openai", "gpt-test", "responses"),
        ("gemini", "gemini-test", "chat_completions"),
        ("anthropic", "claude-test", "generate_content"),
        ("ollama", "local", "messages"),
        ("openai", "gpt-test", "not-a-type"),
    ],
)
def test_provider_api_type_rejects_incompatible_transport(provider: str, model: str, api_type: str) -> None:
    with pytest.raises(ProviderApiTypeError) as exc_info:
        normalize_provider_api_type(provider, model, api_type)
    assert "API key" not in str(exc_info.value)


def test_deepseek_registry_descriptor_is_known_only_for_exact_models() -> None:
    flash = provider_model_descriptor("deepseek", "deepseek-v4-flash", "auto")
    pro = provider_model_descriptor("deepseek", "deepseek-v4-pro", "chat_completions")
    unknown = provider_model_descriptor("deepseek", "deepseek-v4-pro-preview", "auto")
    mixed_case = provider_model_descriptor("deepseek", "DeepSeek-V4-Flash", "auto")

    assert flash["resolvedApiType"] == "responses"
    assert flash["supportedApiTypes"] == ["responses", "chat_completions"]
    assert flash["capabilities"] == ["text", "structured_json", "reasoning", "tools"]
    assert flash["capabilitySource"] == "official_registry"
    assert pro["supportedApiTypes"] == ["chat_completions"]
    assert pro["capabilitySource"] == "official_registry"
    assert unknown["capabilities"] == []
    assert unknown["capabilitySource"] == "unknown"
    assert mixed_case["resolvedApiType"] == "chat_completions"
    assert mixed_case["capabilitySource"] == "unknown"
    assert "tokenLimit" not in flash


def test_api_models_enriches_registry_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = dashboard_server.DASHBOARD_API_CONFIG
    monkeypatch.setattr(
        dashboard_server,
        "fetch_provider_models",
        lambda _config: [
            {"id": "deepseek-v4-flash", "label": "Flash", "contextWindow": 123},
            {"id": "unlisted-model", "label": "Unknown"},
        ],
    )
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/models",
                json={
                    "provider": "deepseek",
                    "api_key": "safe-key",
                    "model": "deepseek-v4-flash",
                    "api_type": "auto",
                },
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["api_type"] == "auto"
        assert payload["resolvedApiType"] == "responses"
        assert payload["models"][0]["provider"] == "deepseek"
        assert payload["models"][0]["contextWindow"] == 123
        assert payload["models"][0]["capabilitySource"] == "official_registry"
        assert payload["models"][1]["capabilities"] == []
        assert payload["models"][1]["capabilitySource"] == "unknown"
    finally:
        dashboard_server.DASHBOARD_API_CONFIG = saved


def test_config_save_persists_api_type_and_saved_key_fallback_keeps_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(dashboard_server, "CONFIG_PATH", config_path)
    saved = dashboard_server.DASHBOARD_API_CONFIG
    dashboard_server.DASHBOARD_API_CONFIG = dashboard_server.DashboardApiConfig(
        provider="deepseek", api_key="saved-key", base_url="https://api.deepseek.com", model="deepseek-v4-pro",
        api_type="chat_completions", thinking_level="low",
    )
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/config",
                json={"provider": "deepseek", "api_key": "", "model": "deepseek-v4-flash", "api_type": "auto"},
            )
        assert response.status_code == 200
        api = response.json()["apiConfig"]
        assert api["api_type"] == "auto"
        assert api["resolvedApiType"] == "responses"
        assert config_path.exists()
        assert '"api_type": "auto"' in config_path.read_text(encoding="utf-8")
    finally:
        dashboard_server.DASHBOARD_API_CONFIG = saved


def test_provider_test_saved_key_preserves_requested_responses_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = dashboard_server.DASHBOARD_API_CONFIG
    dashboard_server.DASHBOARD_API_CONFIG = dashboard_server.DashboardApiConfig(
        provider="deepseek",
        api_key="saved-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="auto",
        thinking_level="low",
    )
    captured: dict[str, object] = {}

    def fake_test(request: dashboard_server.ProviderTestRequest) -> dict[str, object]:
        captured["api_type"] = request.api_type
        captured["api_key"] = request.api_key
        return {"ok": True}

    monkeypatch.setattr(dashboard_server, "run_provider_test_sync", fake_test)
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post(
                "/api/app/provider/test",
                json={
                    "provider": "deepseek",
                    "apiKey": "",
                    "baseUrl": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash",
                    "api_type": "responses",
                    "thinkingLevel": "low",
                    "capability": "text",
                },
            )
        assert response.status_code == 200
        assert captured == {"api_type": "responses", "api_key": "saved-key"}
    finally:
        dashboard_server.DASHBOARD_API_CONFIG = saved
