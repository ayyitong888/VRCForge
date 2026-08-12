"""1.3.3 Provider reasoning-effort controls.

Pure request-shape tests: per-provider parameter mapping, off/unknown sending
nothing, Anthropic response-budget adjustment, Gemini thinking_config reuse,
temperature exemption matrix, and dashboard config plumbing.
"""

import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import dashboard_server
from dashboard_server import ApiConfigRequest, ProviderTestRequest
from model_provider_adapters import (
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
from provider_runtime_adapters import ProviderRuntimeRequest
from provider_test_integration_service import (
    ProviderProbePolicyPorts,
    ProviderProbeSdkPorts,
    ProviderTestIntegrationService,
    ProviderTestServicePorts,
    ProviderTextProbeRunner,
)
from vrchat_blendshape_agent import (
    ANTHROPIC_RESPONSE_BASE_MAX_TOKENS,
    GEMINI_25_THINKING_BUDGET_TOKENS,
    REASONING_THINKING_BUDGET_TOKENS,
    Settings,
    build_anthropic_request_payload,
    build_gemini_generate_config,
    build_llm_settings,
    build_openai_compatible_request_payload,
    extract_json_block,
    get_provider_defaults,
    normalize_base_url,
    normalize_provider_name,
    provider_display_name,
    provider_requires_api_key,
    anthropic_model_supports_adaptive_thinking,
    anthropic_model_supports_manual_thinking,
    anthropic_model_supports_output_effort,
    gemini_model_thinking_mode,
    model_rejects_fixed_temperature,
    normalize_reasoning_effort,
    reasoning_effort_variants,
    reasoning_variants_descriptor,
    reasoning_thinking_budget,
)


def make_settings(provider: str, model: str = "test-model", thinking_level: str = "") -> Settings:
    return Settings(
        llm_provider=provider,
        llm_api_key="test-key",
        llm_base_url="https://provider.example/v1",
        llm_model=model,
        llm_api_key_env="TEST_API_KEY",
        gemini_thinking_level=thinking_level,
        unity_mcp_command=["unity-mcp"],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=3,
        unity_mcp_retry_backoff_seconds=2.0,
        unity_mcp_timeout_seconds=30,
        export_tool_name="vrc_export_blendshapes",
        execute_tool_name="vrc_apply_blendshapes",
        export_path=Path("Assets/VRCForge/blendshapes_export.json"),
        min_confidence=0.65,
    )


def make_configuration_owner(
    tmp_path: Path,
    *,
    document: dict | None = None,
    runtime_settings: Settings | None = None,
) -> ProviderConfigurationService:
    config_path = tmp_path / "provider-config.json"
    if document is not None:
        config_path.write_text(json.dumps(document), encoding="utf-8")
    settings = runtime_settings or make_settings("openai", model="gpt-4o")

    def write(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    return ProviderConfigurationService(
        ProviderConfigurationPersistencePorts(
            config_path=config_path,
            load_runtime_settings=lambda: settings,
            atomic_write_json=write,
            path_is_reparse_or_link=lambda _path: False,
        ),
        ProviderConfigurationPolicyPorts(
            default_provider="openai",
            normalize_provider_name=normalize_provider_name,
            get_provider_defaults=get_provider_defaults,
            normalize_base_url=normalize_base_url,
            normalize_provider_api_type=normalize_provider_api_type,
            normalize_reasoning_effort=normalize_reasoning_effort,
            reasoning_effort_variants=reasoning_effort_variants,
            validate_provider_api_key=validate_provider_api_key,
            provider_display_name=provider_display_name,
            provider_auth_label=lambda _provider: "Authorization: Bearer",
            provider_requires_api_key=provider_requires_api_key,
            provider_config_descriptor=lambda config: provider_model_descriptor(
                config.provider,
                config.model,
                config.api_type,
            ),
        ),
        RLock(),
    )


class FakeProbe:
    def __init__(self, response: str = "VRCForge provider test OK") -> None:
        self.response = response
        self.calls: list[tuple[ProviderApiConfig, str, bool]] = []

    def probe(
        self,
        config: ProviderApiConfig,
        prompt: str,
        *,
        structured: bool = False,
    ) -> str:
        self.calls.append((config, prompt, structured))
        return self.response


def make_provider_test_service(
    owner: ProviderConfigurationService,
    probe: object,
) -> ProviderTestIntegrationService:
    return ProviderTestIntegrationService(
        ProviderTestServicePorts(
            resolve_api_request=owner.resolve_api_request,
            normalize_provider_name=normalize_provider_name,
            provider_display_name=provider_display_name,
            provider_config_descriptor=lambda config: provider_model_descriptor(
                config.provider,
                config.model,
                config.api_type,
            ),
            provider_requires_api_key=provider_requires_api_key,
            extract_json_block=extract_json_block,
        ),
        probe,
    )


# --- level normalization -----------------------------------------------------


def test_normalize_reasoning_effort_matrix():
    assert normalize_reasoning_effort("none") == "none"
    assert normalize_reasoning_effort("minimal") == "minimal"
    assert normalize_reasoning_effort("low") == "low"
    assert normalize_reasoning_effort(" Medium ") == "medium"
    assert normalize_reasoning_effort("HIGH") == "high"
    assert normalize_reasoning_effort("xhigh") == "xhigh"
    assert normalize_reasoning_effort("max") == "max"
    assert normalize_reasoning_effort("") == ""
    assert normalize_reasoning_effort("off") == ""
    assert normalize_reasoning_effort("weird") == ""
    assert normalize_reasoning_effort(None) == ""


def test_reasoning_thinking_budget_mapping():
    assert reasoning_thinking_budget("") is None
    assert reasoning_thinking_budget("off") is None
    assert reasoning_thinking_budget("low") == 2048
    assert reasoning_thinking_budget("medium") == 8192
    assert reasoning_thinking_budget("high") == 16384
    assert reasoning_thinking_budget("xhigh") == 24576
    assert reasoning_thinking_budget("max") == 31999


def test_reasoning_variants_are_provider_and_model_aware():
    assert reasoning_effort_variants("openai", "gpt-4o") == []
    assert reasoning_effort_variants("openai", "o3") == ["low", "medium", "high"]
    assert reasoning_effort_variants("openai", "gpt-5.2-codex") == ["low", "medium", "high", "xhigh"]
    assert reasoning_effort_variants("openai", "gpt-5.6-sol") == ["none", "low", "medium", "high", "xhigh", "max"]
    assert reasoning_effort_variants("openai", "gpt-5.4-pro") == []
    assert reasoning_effort_variants("openai", "gpt-5.2-chat-latest") == []
    assert reasoning_effort_variants("deepseek", "deepseek-reasoner") == ["none", "high"]
    assert reasoning_effort_variants("deepseek", "deepseek-v4-flash", "responses") == [
        "none", "low", "medium", "high", "xhigh", "max"
    ]
    assert reasoning_effort_variants("deepseek", "deepseek-v4-pro", "messages") == [
        "none", "low", "medium", "high", "xhigh", "max"
    ]
    assert reasoning_effort_variants("deepseek", "deepseek-v4-flash", "chat_completions") == ["none", "low", "high", "max"]
    assert reasoning_effort_variants("deepseek", "deepseek-v4-pro", "chat_completions") == ["none", "low", "high", "max"]
    assert reasoning_effort_variants("deepseek", "unknown-model") == []
    assert reasoning_effort_variants("openrouter", "openai/o3") == [
        "none", "minimal", "low", "medium", "high", "xhigh"
    ]
    assert reasoning_effort_variants("anthropic", "claude-opus-4-6") == ["low", "medium", "high", "max"]
    assert reasoning_effort_variants("anthropic", "claude-opus-4-8") == ["low", "medium", "high", "xhigh", "max"]
    assert reasoning_effort_variants("anthropic", "claude-opus-4-5") == ["low", "medium", "high"]
    assert reasoning_effort_variants("anthropic", "claude-3-5-sonnet") == []
    assert reasoning_effort_variants("gemini", "gemini-2.5-flash") == ["none", "low", "medium", "high", "max"]
    assert reasoning_effort_variants("gemini", "gemini-3-pro") == ["low", "medium", "high"]
    assert reasoning_effort_variants("ollama", "qwen3") == []
    assert reasoning_effort_variants("custom", "o3") == []


def test_reasoning_descriptor_has_default_separate_from_explicit_none():
    descriptor = reasoning_variants_descriptor("deepseek", "deepseek-reasoner")
    assert descriptor["defaultKey"] == "default"
    assert descriptor["transport"] == "deepseek_chat_completions"
    assert [variant["key"] for variant in descriptor["variants"]] == ["none", "high"]
    assert {variant["requestMode"] for variant in descriptor["variants"]} == {"thinking_toggle"}
    assert "default" not in [variant["key"] for variant in descriptor["variants"]]


def test_flash_responses_reasoning_descriptor_is_transport_specific():
    descriptor = reasoning_variants_descriptor("deepseek", "deepseek-v4-flash", "responses")
    assert descriptor["transport"] == "deepseek_responses"
    assert descriptor["resolvedApiType"] == "responses"
    assert [variant["key"] for variant in descriptor["variants"]] == [
        "none", "low", "medium", "high", "xhigh", "max"
    ]


# --- OpenAI-compatible lane --------------------------------------------------


def test_openai_payload_sends_reasoning_effort():
    payload = build_openai_compatible_request_payload(make_settings("openai", model="o3", thinking_level="high"), "hello")
    assert payload["reasoning_effort"] == "high"


def test_deepseek_payload_uses_thinking_switch_and_supported_effort():
    high = build_openai_compatible_request_payload(
        make_settings("deepseek", model="deepseek-reasoner", thinking_level="high"), "hello"
    )
    assert high["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "reasoning_effort" not in high
    assert "temperature" not in high
    none = build_openai_compatible_request_payload(
        make_settings("deepseek", model="deepseek-reasoner", thinking_level="none"), "hello"
    )
    assert none["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in none


def test_openrouter_payload_uses_unified_reasoning_object():
    payload = build_openai_compatible_request_payload(
        make_settings("openrouter", model="openai/o3", thinking_level="xhigh"), "hello"
    )
    assert payload["extra_body"] == {"reasoning": {"effort": "xhigh"}}
    assert "reasoning_effort" not in payload


def test_openai_non_reasoning_model_does_not_receive_reasoning_effort():
    payload = build_openai_compatible_request_payload(
        make_settings("openai", model="gpt-4o", thinking_level="high"), "hello"
    )
    assert "reasoning_effort" not in payload


def test_openai_compatible_payload_off_sends_nothing():
    payload = build_openai_compatible_request_payload(make_settings("openai"), "hello")
    assert "reasoning_effort" not in payload


def test_unknown_and_custom_providers_send_no_reasoning_parameter():
    for provider in ("ollama", "custom"):
        payload = build_openai_compatible_request_payload(
            make_settings(provider, thinking_level="high"), "hello"
        )
        assert "reasoning_effort" not in payload, provider


def test_openai_compatible_payload_message_shape_preserved():
    payload = build_openai_compatible_request_payload(
        make_settings("openai", model="gpt-4o", thinking_level="low"), "user text"
    )
    assert payload["model"] == "gpt-4o"
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert payload["messages"][1]["content"] == "user text"


# --- temperature exemption matrix -------------------------------------------


def test_temperature_exemption_matrix():
    rejecting = ("o1", "o3-mini", "o4-mini-high", "gpt-5", "gpt-5-turbo", "openai/o3", "OpenAI/GPT-5")
    accepting = ("gpt-4o", "deepseek-chat", "olive-7b", "phi-4", "")
    for model in rejecting:
        assert model_rejects_fixed_temperature(model), model
    for model in accepting:
        assert not model_rejects_fixed_temperature(model), model


def test_temperature_omitted_for_reasoning_models_regardless_of_level():
    for level in ("", "high"):
        payload = build_openai_compatible_request_payload(
            make_settings("openai", model="o3-mini", thinking_level=level), "hello"
        )
        assert "temperature" not in payload, level
    payload = build_openai_compatible_request_payload(
        make_settings("openai", model="gpt-4o", thinking_level="high"), "hello"
    )
    assert payload["temperature"] == 0.1


# --- Anthropic lane ----------------------------------------------------------


def test_anthropic_payload_off_sends_no_thinking():
    payload = build_anthropic_request_payload(make_settings("anthropic", model="claude-opus-4-6"), "hello")
    assert payload["max_tokens"] == ANTHROPIC_RESPONSE_BASE_MAX_TOKENS
    assert "thinking" not in payload


def test_deepseek_anthropic_payload_uses_official_thinking_effort_shape():
    configured = make_settings(
        "deepseek", model="deepseek-v4-pro", thinking_level="high"
    )
    configured.llm_api_type = "messages"
    payload = build_anthropic_request_payload(configured, "hello")
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["output_config"] == {"effort": "high"}


@pytest.mark.parametrize(
    ("requested", "sent"),
    [
        ("low", "low"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "high"),
        ("max", "max"),
    ],
)
def test_deepseek_anthropic_payload_maps_product_effort_to_documented_values(
    requested: str, sent: str
):
    configured = make_settings(
        "deepseek", model="deepseek-v4-pro", thinking_level=requested
    )
    configured.llm_api_type = "messages"

    payload = build_anthropic_request_payload(configured, "hello")

    assert payload["output_config"] == {"effort": sent}


def test_deepseek_anthropic_payload_never_sends_undocumented_minimal():
    configured = make_settings(
        "deepseek", model="deepseek-v4-pro", thinking_level="minimal"
    )
    configured.llm_api_type = "messages"

    payload = build_anthropic_request_payload(configured, "hello")

    assert "thinking" not in payload
    assert "output_config" not in payload


def test_anthropic_adaptive_payload_raises_response_budget_per_level():
    for level in ("low", "medium", "high", "max"):
        budget = REASONING_THINKING_BUDGET_TOKENS[level]
        payload = build_anthropic_request_payload(
            make_settings("anthropic", model="claude-opus-4-6", thinking_level=level), "hello"
        )
        expected_max_tokens = ANTHROPIC_RESPONSE_BASE_MAX_TOKENS + budget
        if level in {"xhigh", "max"}:
            expected_max_tokens = max(expected_max_tokens, 65536)
        assert payload["max_tokens"] == expected_max_tokens, level
        assert payload["thinking"] == {"type": "adaptive"}, level
        assert payload["output_config"] == {"effort": level}, level


def test_anthropic_legacy_payload_uses_manual_budget_tokens():
    payload = build_anthropic_request_payload(
        make_settings("anthropic", model="claude-sonnet-4-5", thinking_level="medium"), "hello"
    )
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert "output_config" not in payload

    opus = build_anthropic_request_payload(
        make_settings("anthropic", model="claude-opus-4-5", thinking_level="medium"), "hello"
    )
    assert opus["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert opus["output_config"] == {"effort": "medium"}


def test_anthropic_adaptive_model_detection_handles_prefixes_and_boundaries():
    for model in ("claude-opus-4-6", "anthropic/claude-opus-4-8", "claude-sonnet-5", "claude-mythos-preview"):
        assert anthropic_model_supports_adaptive_thinking(model), model
    for model in ("claude-opus-4-5", "claude-sonnet-4-5", "custom-claude-opus-4-8"):
        assert not anthropic_model_supports_adaptive_thinking(model), model


def test_anthropic_manual_and_output_effort_model_detection_is_fail_closed():
    assert anthropic_model_supports_manual_thinking("claude-sonnet-4-5")
    assert anthropic_model_supports_manual_thinking("claude-3-7-sonnet-latest")
    assert not anthropic_model_supports_manual_thinking("claude-3-5-sonnet")
    assert anthropic_model_supports_output_effort("claude-opus-4-5")
    assert not anthropic_model_supports_output_effort("claude-haiku-4-5")


# --- Gemini / Vertex lane ----------------------------------------------------


class _FakeThinkingConfig:
    def __init__(self, thinking_budget: int | None = None, thinking_level: str | None = None) -> None:
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level


class _FakeGenerateContentConfig:
    def __init__(self, thinking_config: _FakeThinkingConfig) -> None:
        self.thinking_config = thinking_config


_FAKE_GENAI_TYPES = SimpleNamespace(
    GenerateContentConfig=_FakeGenerateContentConfig,
    ThinkingConfig=_FakeThinkingConfig,
)


def test_gemini_generate_config_off_returns_none():
    assert build_gemini_generate_config(make_settings("gemini"), _FAKE_GENAI_TYPES) is None


def test_gemini_25_generate_config_maps_level_to_budget():
    config = build_gemini_generate_config(
        make_settings("gemini", model="gemini-2.5-flash", thinking_level="high"), _FAKE_GENAI_TYPES
    )
    assert config is not None
    assert config.thinking_config.thinking_budget == GEMINI_25_THINKING_BUDGET_TOKENS["high"]
    assert config.thinking_config.thinking_level is None


def test_gemini_25_pro_max_uses_model_specific_budget_ceiling():
    config = build_gemini_generate_config(
        make_settings("gemini", model="gemini-2.5-pro", thinking_level="max"), _FAKE_GENAI_TYPES
    )
    assert config is not None
    assert config.thinking_config.thinking_budget == 32768


def test_gemini_3_generate_config_uses_level_instead_of_legacy_budget():
    config = build_gemini_generate_config(
        make_settings("vertexai", model="publishers/google/models/gemini-3.5-flash", thinking_level="medium"),
        _FAKE_GENAI_TYPES,
    )
    assert config is not None
    assert config.thinking_config.thinking_level == "medium"
    assert config.thinking_config.thinking_budget is None


def test_gemini_unknown_model_sends_no_thinking_config():
    assert build_gemini_generate_config(
        make_settings("vertexai", model="partner-model", thinking_level="high"), _FAKE_GENAI_TYPES
    ) is None


def test_gemini_thinking_mode_detection():
    assert gemini_model_thinking_mode("gemini-2.5-pro") == "budget"
    assert gemini_model_thinking_mode("publishers/google/models/gemini-3.1-pro-preview") == "level"
    assert gemini_model_thinking_mode("text-bison") == ""


# --- settings plumbing -------------------------------------------------------


def test_build_llm_settings_reads_thinking_level_from_llm_section():
    raw = {"llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o", "thinking_level": "high"}}
    assert build_llm_settings(raw, None, None)["thinking_level"] == "high"


def test_build_llm_settings_override_key_wins_even_when_empty():
    raw = {"llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o", "thinking_level": "high"}}
    assert build_llm_settings(raw, None, {"thinking_level": ""})["thinking_level"] == ""


# --- dashboard config plumbing -----------------------------------------------


def test_normalize_api_config_request_normalizes_thinking_level(tmp_path: Path):
    owner = make_configuration_owner(tmp_path)
    request = ApiConfigRequest(provider="openai", api_key="k", model="o3", thinking_level="High")
    assert owner.normalize_api_request(request).thinking_level == "high"
    request = ApiConfigRequest(provider="openai", api_key="k", model="o3", thinking_level="OFF")
    assert owner.normalize_api_request(request).thinking_level == ""


def test_normalize_api_config_rejects_unsupported_variant_instead_of_clamping(tmp_path: Path):
    owner = make_configuration_owner(tmp_path)
    request = ApiConfigRequest(provider="openai", api_key="k", model="gpt-4o", thinking_level="high")
    with pytest.raises(ValueError, match="not supported"):
        owner.normalize_api_request(request)


def test_reasoning_variants_api_is_backend_owned_and_secret_free():
    response = TestClient(dashboard_server.app).post(
        "/api/app/provider/reasoning-variants",
        json={"provider": "openai", "model": "o3"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["defaultKey"] == "default"
    assert [item["key"] for item in payload["variants"]] == ["low", "medium", "high"]
    assert "api_key" not in payload


def test_provider_connection_test_reuses_production_reasoning_resolver(tmp_path: Path):
    owner = make_configuration_owner(tmp_path)
    probe = FakeProbe()
    service = make_provider_test_service(owner, probe)
    request = ProviderTestRequest(
        provider="openai",
        api_key="k",
        model="o3",
        thinking_level="high",
        capability="text",
    )
    result = service.run(request)
    assert result["ok"] is True
    config = probe.calls[0][0]
    assert config.thinking_level == "high"


@pytest.mark.parametrize(("capability", "expected_text", "structured"), [
    ("text", "VRCForge provider test OK", False),
    ("structured", '{"ok":true,"name":"vrcforge"}', True),
])
def test_provider_test_service_flash_responses_probe_without_chat(
    tmp_path: Path,
    capability,
    expected_text,
    structured,
):
    requests = []

    class FakeResponsesAdapter:
        def send_request(self, request):
            requests.append(request)
            return SimpleNamespace(text=expected_text, usage={}, reasoning_summary=[])

    runner = ProviderTextProbeRunner(
        ProviderProbePolicyPorts(
            validate_provider_api_key=validate_provider_api_key,
            normalize_provider_api_type=normalize_provider_api_type,
            resolve_vertex_project_location=lambda _value: pytest.fail("unexpected Vertex probe"),
            build_gemini_generate_config=build_gemini_generate_config,
            build_anthropic_request_payload=build_anthropic_request_payload,
            build_openai_compatible_request_payload=build_openai_compatible_request_payload,
            model_rejects_fixed_temperature=model_rejects_fixed_temperature,
            settings_factory=Settings,
            runtime_request_factory=ProviderRuntimeRequest,
        ),
        ProviderProbeSdkPorts(
            responses_adapter=lambda _key, _url: FakeResponsesAdapter(),
            google_client=lambda _config, _location: pytest.fail("unexpected Google SDK"),
            google_types=lambda: pytest.fail("unexpected Google types"),
            anthropic_client=lambda _key, _url: pytest.fail("unexpected Anthropic SDK"),
            openai_client=lambda _key, _url, _timeout: pytest.fail("unexpected OpenAI SDK"),
        ),
    )
    owner = make_configuration_owner(tmp_path)
    payload = make_provider_test_service(owner, runner).run(
        ProviderTestRequest(
            provider="deepseek",
            api_key="safe-key",
            base_url="https://api.deepseek.example",
            model="deepseek-v4-flash",
            api_type="responses",
            capability=capability,
        )
    )
    assert payload["ok"] is True and payload["apiType"] == "responses" and payload["resolvedApiType"] == "responses"
    assert len(requests) == 1 and requests[0].mode == "probe" and requests[0].structured_output is structured
    assert requests[0].max_output_tokens == (512 if structured else 64)


def test_provider_connection_test_reports_unsupported_variant_without_sending(tmp_path: Path):
    owner = make_configuration_owner(tmp_path)
    probe = FakeProbe()
    service = make_provider_test_service(owner, probe)
    request = ProviderTestRequest(
        provider="openai",
        api_key="k",
        model="gpt-4o",
        thinking_level="high",
        capability="text",
    )
    result = service.run(request)
    assert result["ok"] is False
    assert "not supported" in result["message"]
    assert probe.calls == []


def test_serialize_api_config_always_emits_thinking_level(tmp_path: Path):
    owner = make_configuration_owner(tmp_path)
    config = ProviderApiConfig(
        provider="openai",
        api_key="secret",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        thinking_level="medium",
    )
    owner.save_api_config(config)
    serialized = owner.serialize_api_config(include_secret=False)
    assert serialized["thinking_level"] == "medium"
    assert "thinking_level" in serialized


def test_load_initial_dashboard_api_config_preserves_explicit_off(tmp_path: Path):
    legacy = make_settings("openai", model="o3", thinking_level="high")
    owner = make_configuration_owner(
        tmp_path,
        document={"api": {"provider": "openai", "model": "o3", "thinking_level": ""}},
        runtime_settings=legacy,
    )
    loaded = owner.current_api_config()
    assert loaded.thinking_level == ""
