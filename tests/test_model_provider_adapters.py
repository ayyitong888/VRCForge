from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import pytest

from model_provider_adapters import (
    ProviderApiTypeError,
    ProviderCredentialError,
    normalize_provider_api_type,
    provider_model_descriptor,
    validate_provider_api_key,
)
from provider_configuration_service import (
    ProviderApiConfig,
    ProviderConfigurationPersistencePorts,
    ProviderConfigurationPolicyPorts,
    ProviderConfigurationService,
)
from provider_model_catalog_service import (
    ProviderModelCatalogPolicyPorts,
    ProviderModelCatalogSdkPorts,
    ProviderModelCatalogService,
)
from provider_test_integration_service import (
    ProviderTestIntegrationService,
    ProviderTestServicePorts,
)
from vrchat_blendshape_agent import Settings, request_openai_compatible_plan_with_metadata


@dataclass(frozen=True)
class _RuntimeSettings:
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    gemini_thinking_level: str = ""


@dataclass(frozen=True)
class _ApiRequest:
    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    api_type: str | None = None
    thinking_level: str = ""


@dataclass(frozen=True)
class _ProviderTestRequest:
    provider: str = "openai"
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    api_type: str | None = None
    thinking_level: str = ""
    capability: str = "text"


_DEFAULTS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"},
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro"},
    "gemini": {"base_url": "", "model": "gemini-2.5-flash"},
    "vertexai": {"base_url": "", "model": "gemini-2.5-flash"},
    "anthropic": {"base_url": "", "model": "claude-sonnet-4-5"},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2"},
}


def _provider_name(value: str) -> str:
    return str(value or "openai").strip().lower()


def _provider_defaults(provider: str) -> Mapping[str, str]:
    return _DEFAULTS.get(provider, _DEFAULTS["openai"])


def _requires_key(provider: str) -> bool:
    return provider not in {"ollama", "vertexai"}


def _config_policy() -> ProviderConfigurationPolicyPorts:
    return ProviderConfigurationPolicyPorts(
        default_provider="openai",
        normalize_provider_name=_provider_name,
        get_provider_defaults=_provider_defaults,
        normalize_base_url=lambda value, _provider, default: str(value or default).rstrip("/"),
        normalize_provider_api_type=normalize_provider_api_type,
        normalize_reasoning_effort=lambda value: str(value or "").strip().lower(),
        reasoning_effort_variants=lambda _provider, _model, _api_type: ["low", "medium", "high"],
        validate_provider_api_key=validate_provider_api_key,
        provider_display_name=lambda provider: provider.title(),
        provider_auth_label=lambda _provider: "Authorization: Bearer",
        provider_requires_api_key=_requires_key,
        provider_config_descriptor=lambda config: provider_model_descriptor(
            config.provider,
            config.model,
            config.api_type,
        ),
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _config_owner(path: Path) -> ProviderConfigurationService:
    return ProviderConfigurationService(
        ProviderConfigurationPersistencePorts(
            config_path=path,
            load_runtime_settings=_RuntimeSettings,
            atomic_write_json=_write_json,
            path_is_reparse_or_link=lambda _path: False,
        ),
        _config_policy(),
        RLock(),
    )


def _catalog() -> ProviderModelCatalogService:
    def unexpected_sdk(*_args: object) -> object:
        raise AssertionError("model SDK must not be constructed by this test")

    return ProviderModelCatalogService(
        ProviderModelCatalogPolicyPorts(
            validate_provider_api_key=validate_provider_api_key,
            provider_requires_api_key=_requires_key,
            provider_display_name=lambda provider: provider.title(),
            provider_model_descriptor=provider_model_descriptor,
            resolve_vertex_project_location=lambda _value: ("test-project", "test-location"),
        ),
        ProviderModelCatalogSdkPorts(
            openai_client=unexpected_sdk,
            google_ai_studio_client=unexpected_sdk,
            google_vertex_client=unexpected_sdk,
            anthropic_client=unexpected_sdk,
        ),
    )


class _CapturingProbe:
    def __init__(self, text: str = "VRCForge provider test OK") -> None:
        self.text = text
        self.configs: list[ProviderApiConfig] = []

    def probe(
        self,
        config: ProviderApiConfig,
        _prompt: str,
        *,
        structured: bool = False,
    ) -> str:
        del structured
        self.configs.append(config)
        return self.text


def _provider_tests(
    owner: ProviderConfigurationService,
    catalog: ProviderModelCatalogService,
    probe: _CapturingProbe,
) -> ProviderTestIntegrationService:
    return ProviderTestIntegrationService(
        ProviderTestServicePorts(
            resolve_api_request=owner.resolve_api_request,
            normalize_provider_name=_provider_name,
            provider_display_name=lambda provider: provider.title(),
            provider_config_descriptor=catalog.provider_config_descriptor,
            provider_requires_api_key=_requires_key,
            extract_json_block=lambda value: value,
        ),
        probe,
    )


def _write_api_document(path: Path, config: ProviderApiConfig) -> None:
    path.write_text(
        json.dumps(
            {
                "api": {
                    "provider": config.provider,
                    "api_key": config.api_key,
                    "base_url": config.base_url,
                    "model": config.model,
                    "api_type": config.api_type,
                    "thinking_level": config.thinking_level,
                }
            }
        ),
        encoding="utf-8",
    )


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
    tmp_path: Path,
    invalid_key: str,
) -> None:
    path = tmp_path / "config.json"
    _write_api_document(
        path,
        ProviderApiConfig(
            provider="openai",
            api_key=invalid_key,
            base_url="https://api.openai.com/v1",
            model="test",
        ),
    )
    owner = _config_owner(path)
    catalog = _catalog()

    with pytest.raises(ProviderCredentialError) as exc_info:
        config = owner.resolve_api_request(_ApiRequest(model="test"))
        catalog.fetch_provider_models(config)

    assert "re-enter" in str(exc_info.value).lower()
    assert invalid_key not in str(exc_info.value)


@pytest.mark.parametrize("invalid_key", ["saved\ncredential", "saved\rcredential", "saved\ue00dcredential"])
def test_config_saved_key_fallback_and_provider_test_reject_invalid_credentials(
    tmp_path: Path,
    invalid_key: str,
) -> None:
    path = tmp_path / "config.json"
    _write_api_document(
        path,
        ProviderApiConfig(
            provider="openai",
            api_key=invalid_key,
            base_url="https://api.openai.com/v1",
            model="test",
        ),
    )
    owner = _config_owner(path)
    provider_tests = _provider_tests(owner, _catalog(), _CapturingProbe())

    with pytest.raises(ProviderCredentialError) as saved_error:
        owner.resolve_api_request(_ApiRequest(model="test"))
    probe_result = provider_tests.run(
        _ProviderTestRequest(api_key="draft\ncredential", model="test")
    )

    assert "re-enter" in str(saved_error.value).lower()
    assert probe_result["ok"] is False
    assert "re-enter" in str(probe_result["message"]).lower()
    assert invalid_key not in json.dumps(probe_result)


def test_initial_loader_preserves_legacy_provider_model_and_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    legacy_key = "legacy\ue00dcredential"
    config_path.write_text(
        '{"api":{"provider":"deepseek","api_key":"' + legacy_key + '","model":"legacy-model"}}',
        encoding="utf-8",
    )

    config = _config_owner(config_path).current_api_config()

    assert config.provider == "deepseek"
    assert config.model == "legacy-model"
    assert config.api_key == legacy_key
    assert config.api_type == "chat_completions"


def test_internal_legacy_config_uses_provider_transport_instead_of_chat_default() -> None:
    config = ProviderApiConfig(
        provider="gemini",
        api_key="",
        base_url="",
        model="gemini-2.5-flash",
    )

    descriptor = _catalog().provider_config_descriptor(config)

    assert config.api_type is None
    assert descriptor["api_type"] == "generate_content"
    assert descriptor["resolvedApiType"] == "generate_content"


@pytest.mark.parametrize(
    ("provider", "model", "api_type", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", None, ("auto", "responses")),
        ("deepseek", "deepseek-v4-flash", "auto", ("auto", "responses")),
        ("deepseek", "deepseek-v4-pro", "auto", ("auto", "chat_completions")),
        ("deepseek", "deepseek-auto", "auto", ("auto", "responses")),
        ("deepseek", "DeepSeek-V4-Flash", "auto", ("auto", "chat_completions")),
        ("deepseek", "future-model", "auto", ("auto", "chat_completions")),
        ("gemini", "gemini-test", None, ("generate_content", "generate_content")),
        ("vertexai", "vertex-test", None, ("generate_content", "generate_content")),
        ("anthropic", "claude-test", None, ("messages", "messages")),
        ("openai", "gpt-test", "auto", ("auto", "responses")),
        ("openai", "gpt-test", "responses", ("responses", "responses")),
        ("custom", "site-model", "messages", ("messages", "messages")),
        ("custom", "site-model", "generate_content", ("generate_content", "generate_content")),
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
        ("deepseek", "deepseek-auto", "chat_completions"),
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


def test_api_models_enriches_registry_without_network() -> None:
    catalog = _catalog()
    config = ProviderApiConfig(
        provider="deepseek",
        api_key="safe-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="auto",
    )
    models = [
        catalog.enrich_provider_model_item(
            config,
            {"id": "deepseek-v4-flash", "label": "Flash", "contextWindow": 123},
        ),
        catalog.enrich_provider_model_item(
            config,
            {"id": "unlisted-model", "label": "Unknown"},
        ),
    ]
    descriptor = catalog.provider_config_descriptor(config)
    payload = {"models": models, **descriptor}

    assert payload["api_type"] == "auto"
    assert payload["resolvedApiType"] == "responses"
    assert payload["models"][0]["provider"] == "deepseek"
    assert payload["models"][0]["contextWindow"] == 123
    assert payload["models"][0]["capabilitySource"] == "official_registry"
    assert payload["models"][1]["capabilities"] == []
    assert payload["models"][1]["capabilitySource"] == "unknown"


def test_config_save_persists_api_type_and_saved_key_fallback_keeps_it(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    _write_api_document(
        config_path,
        ProviderApiConfig(
            provider="deepseek",
            api_key="saved-key",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            api_type="chat_completions",
            thinking_level="low",
        ),
    )
    owner = _config_owner(config_path)
    config = owner.resolve_api_request(
        _ApiRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_type="auto",
        )
    )

    owner.save_api_config(config)

    descriptor = _catalog().provider_config_descriptor(owner.current_api_config())
    assert config.api_key == "saved-key"
    assert descriptor["api_type"] == "auto"
    assert descriptor["resolvedApiType"] == "responses"
    assert '"api_type": "auto"' in config_path.read_text(encoding="utf-8")


def test_provider_test_saved_key_preserves_requested_responses_transport(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    _write_api_document(
        config_path,
        ProviderApiConfig(
            provider="deepseek",
            api_key="saved-key",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_type="auto",
            thinking_level="low",
        ),
    )
    owner = _config_owner(config_path)
    probe = _CapturingProbe()
    provider_tests = _provider_tests(owner, _catalog(), probe)

    result = provider_tests.run(
        _ProviderTestRequest(
            provider="deepseek",
            api_key="",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_type="responses",
            thinking_level="low",
            capability="text",
        )
    )

    assert result["ok"] is True
    assert len(probe.configs) == 1
    assert probe.configs[0].api_type == "responses"
    assert probe.configs[0].api_key == "saved-key"
