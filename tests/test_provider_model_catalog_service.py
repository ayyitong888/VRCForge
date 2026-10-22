from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from provider_configuration_service import ProviderApiConfig
from provider_model_catalog_service import (
    ProviderModelCatalogPolicyPorts,
    ProviderModelCatalogSdkPorts,
    ProviderModelCatalogService,
)


def _descriptor(_provider: str, _model: str, _api_type: str | None) -> dict[str, Any]:
    return {
        "apiType": "chat_completions",
        "resolvedApiType": "chat_completions",
        "supportedApiTypes": ["chat_completions"],
        "capabilities": ["text"],
        "capabilitySource": "fake",
    }


def _client(response: Any) -> Any:
    return SimpleNamespace(models=SimpleNamespace(list=lambda **_kwargs: response))


def _service(
    *,
    calls: list[tuple[Any, ...]] | None = None,
    response: Any = None,
) -> ProviderModelCatalogService:
    observed = calls if calls is not None else []
    fake_response = response or {"data": [{"id": "model-a"}]}
    return ProviderModelCatalogService(
        ProviderModelCatalogPolicyPorts(
            validate_provider_api_key=lambda key: observed.append(("validate", key)),
            provider_requires_api_key=lambda provider: provider not in {"ollama", "vertexai"},
            provider_display_name=lambda provider: provider.title(),
            provider_model_descriptor=_descriptor,
            resolve_vertex_project_location=lambda value: (
                observed.append(("resolve", value)) or ("project-a", "us-test1")
            ),
        ),
        ProviderModelCatalogSdkPorts(
            openai_client=lambda key, url, timeout: (
                observed.append(("openai", key, url, timeout)) or _client(fake_response)
            ),
            google_ai_studio_client=lambda key: (
                observed.append(("gemini", key)) or _client(fake_response)
            ),
            google_vertex_client=lambda project, location: (
                observed.append(("vertex", project, location)) or _client(fake_response)
            ),
            anthropic_client=lambda key: (
                observed.append(("anthropic", key)) or _client(fake_response)
            ),
        ),
    )


def _config(provider: str, *, api_key: str = "safe-key") -> ProviderApiConfig:
    return ProviderApiConfig(
        provider=provider,
        api_key=api_key,
        base_url="projects/project-a/locations/us-test1" if provider == "vertexai" else "https://provider.example/v1",
        model="selected-model",
        api_type="chat_completions",
    )


def test_catalog_owner_has_no_dashboard_host_or_implementation_facade() -> None:
    source = Path("provider_model_catalog_service.py").read_text(encoding="utf-8")
    assert "_host" not in source
    assert "__getattr__" not in source
    assert "_impl_" not in source
    assert "sys.modules" not in source
    assert "dashboard_server" not in source
    assert ProviderModelCatalogPolicyPorts.__dataclass_params__.frozen is True
    assert ProviderModelCatalogSdkPorts.__dataclass_params__.frozen is True


@pytest.mark.parametrize(
    ("provider", "expected_call"),
    [
        ("openai", "openai"),
        ("gemini", "gemini"),
        ("vertexai", "vertex"),
        ("anthropic", "anthropic"),
    ],
)
def test_catalog_dispatches_to_injected_sdk_only(
    provider: str,
    expected_call: str,
) -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls=calls)

    assert service.fetch_provider_models(_config(provider)) == [
        {"id": "model-a", "label": "model-a"}
    ]
    assert any(call[0] == expected_call for call in calls)
    if provider == "vertexai":
        assert ("resolve", "projects/project-a/locations/us-test1") in calls
        assert ("vertex", "project-a", "us-test1") in calls


def test_catalog_rejects_missing_required_key_before_sdk() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(calls=calls)

    with pytest.raises(RuntimeError, match="API key is empty"):
        service.fetch_provider_models(_config("openai", api_key=""))

    assert not any(call[0] in {"openai", "gemini", "vertex", "anthropic"} for call in calls)


def test_catalog_normalizes_deduplicates_sorts_and_keeps_positive_limits() -> None:
    service = _service()
    response = {
        "models": [
            {"name": "Zulu", "displayName": "Zulu label", "max_input_tokens": "100"},
            {"id": "alpha", "context_window": 4096, "max_output_tokens": 0},
            {"id": "alpha", "label": "duplicate"},
            {"id": "", "label": "ignored"},
        ]
    }

    assert service.normalize_provider_model_list(response, "Fake") == [
        {"id": "alpha", "label": "alpha", "contextWindow": 4096},
        {"id": "Zulu", "label": "Zulu label", "maxInputTokens": 100},
    ]


def test_catalog_preserves_only_advertised_effort_and_thinking_capabilities() -> None:
    service = _service()
    result = service.normalize_provider_model_list(
        {
            "data": [
                {
                    "id": "claude-fixture",
                    "capabilities": {
                        "effort": {
                            "low": {"supported": True},
                            "medium": {"supported": False},
                            "high": {"supported": True},
                            "unknown": {},
                        },
                        "thinking": {
                            "types": {
                                "adaptive": {"supported": True},
                                "enabled": {"supported": False},
                            }
                        },
                    },
                }
            ]
        },
        "Anthropic",
    )
    assert result == [{
        "id": "claude-fixture",
        "label": "claude-fixture",
        "reasoningEfforts": ["high", "low"],
        "thinkingTypes": ["adaptive"],
    }]


def test_catalog_does_not_infer_reasoning_levels_from_boolean_thinking_flag() -> None:
    service = _service()
    assert service.normalize_provider_model_list(
        {"data": [{"id": "gemini-fixture", "thinking": True}]},
        "Gemini",
    ) == [{"id": "gemini-fixture", "label": "gemini-fixture"}]


def test_deepseek_catalog_preserves_advertised_order_without_selecting_default() -> None:
    from openai.types import Model

    item = Model.model_validate({"id": "deepseek-flash", "object": "model", "owned_by": "deepseek", "created": 0,
        "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"}})
    info = _service().build_provider_model_info(item, item.id)
    assert info["reasoningEfforts"] == ["low", "high", "max"]
    assert info["reasoningDisabledSupported"] is True
    assert "thinking_level" not in info and "default_level" not in info


@pytest.mark.parametrize(("reasoning", "expected"), [
    ({"supported_efforts": ["max", "high", "low", "none"], "default_effort": "high"}, ["max", "high", "low", "none"]),
    ({"supported_efforts": ["high", "none"], "mandatory": True}, ["high"]),
    ({"mandatory": False}, []),
    ({"supported_efforts": None, "mandatory": True}, ["max", "xhigh", "high", "medium", "low", "minimal"]),
])
def test_openrouter_catalog_distinguishes_enumerated_null_and_omitted(reasoning, expected) -> None:
    info = _service().build_provider_model_info({"id": "router/model", "reasoning": reasoning}, "router/model")
    assert info["reasoningEfforts"] == expected
    assert "thinking_level" not in info


def test_catalog_validation_reads_non_anthropic_advertised_efforts() -> None:
    from dataclasses import replace

    service = _service(response={"data": [{"id": "selected-model", "effort": {"supported_levels": ["low", "max"]}}]})
    assert service.supported_reasoning_efforts(replace(_config("deepseek"))) == ["none", "low", "max"]


def test_catalog_reads_sdk_attribute_objects_for_reasoning_capabilities() -> None:
    item = SimpleNamespace(
        id="claude-sdk-fixture",
        capabilities=SimpleNamespace(
            effort=SimpleNamespace(low=SimpleNamespace(supported=True), high=SimpleNamespace(supported=False)),
            thinking=SimpleNamespace(types=SimpleNamespace(adaptive=SimpleNamespace(supported=True))),
        ),
    )
    assert _service().normalize_provider_model_list({"data": [item]}, "Anthropic") == [{
        "id": "claude-sdk-fixture",
        "label": "claude-sdk-fixture",
        "reasoningEfforts": ["low"],
        "thinkingTypes": ["adaptive"],
    }]


def test_catalog_enriches_with_frozen_registry_descriptor() -> None:
    service = _service()
    config = _config("openai")

    assert service.provider_config_descriptor(config)["capabilitySource"] == "fake"
    enriched = service.enrich_provider_model_item(config, {"id": "model-a", "label": "A"})

    assert enriched == {
        "id": "model-a",
        "label": "A",
        "provider": "openai",
        "apiType": "chat_completions",
        "resolvedApiType": "chat_completions",
        "supportedApiTypes": ["chat_completions"],
        "capabilities": ["text"],
        "capabilitySource": "fake",
    }


def test_catalog_projects_registry_model_limits_without_overwriting_provider_fields() -> None:
    service = ProviderModelCatalogService(
        ProviderModelCatalogPolicyPorts(
            validate_provider_api_key=lambda _key: None,
            provider_requires_api_key=lambda _provider: True,
            provider_display_name=lambda provider: provider.title(),
            provider_model_descriptor=lambda provider, model, api_type: {
                **_descriptor(provider, model, api_type),
                "modelContextWindow": 1_000_000,
                "maxOutputTokens": 384_000,
                "modelVersion": "DeepSeek-V4-Flash-0731",
            },
            resolve_vertex_project_location=lambda _value: ("project", "location"),
        )
    )

    enriched = service.enrich_provider_model_item(
        _config("deepseek"),
        {"id": "deepseek-v4-flash", "label": "Flash", "contextWindow": 128_000},
    )

    assert enriched["contextWindow"] == 128_000
    assert enriched["modelContextWindow"] == 1_000_000
    assert enriched["maxOutputTokens"] == 384_000
    assert enriched["modelVersion"] == "DeepSeek-V4-Flash-0731"


def test_catalog_wraps_sdk_failure_without_fallback() -> None:
    class FailingModels:
        @staticmethod
        def list() -> None:
            raise RuntimeError("offline fake")

    service = ProviderModelCatalogService(
        ProviderModelCatalogPolicyPorts(
            validate_provider_api_key=lambda _key: None,
            provider_requires_api_key=lambda _provider: True,
            provider_display_name=lambda _provider: "OpenAI",
            provider_model_descriptor=_descriptor,
            resolve_vertex_project_location=lambda _value: ("project", "location"),
        ),
        ProviderModelCatalogSdkPorts(
            openai_client=lambda _key, _url, _timeout: SimpleNamespace(models=FailingModels()),
            google_ai_studio_client=lambda _key: pytest.fail("unexpected SDK"),
            google_vertex_client=lambda _project, _location: pytest.fail("unexpected SDK"),
            anthropic_client=lambda _key: pytest.fail("unexpected SDK"),
        ),
    )

    with pytest.raises(RuntimeError, match="model list request failed: offline fake"):
        service.fetch_provider_models(_config("openai"))


def test_catalog_resolves_smallest_configured_and_model_advertised_limit() -> None:
    service = _service(response={"data": [{
        "id": "selected-model",
        "context_window": 500_000,
        "input_token_limit": 450_000,
    }]})

    assert service.resolve_context_limit(_config("openai")) == 450_000
    assert service.resolve_context_limit(
        replace(_config("openai"), context_window=400_000)
    ) == 400_000


def test_catalog_resolution_caches_by_provider_base_url_and_credential_identity() -> None:
    calls: list[tuple[Any, ...]] = []
    service = _service(
        calls=calls,
        response={"data": [{"id": "selected-model", "context_window": 500_000}]},
    )

    assert service.resolve_context_limit(_config("openai")) == 500_000
    assert service.resolve_context_limit(_config("openai")) == 500_000
    assert len([call for call in calls if call[0] == "openai"]) == 1

    other_account = _config("openai", api_key="other-safe-key")
    assert service.resolve_context_limit(other_account) == 500_000
    assert len([call for call in calls if call[0] == "openai"]) == 2


def test_catalog_refresh_fetches_again_and_resolve_uses_refreshed_metadata() -> None:
    calls: list[tuple[Any, ...]] = []
    responses = iter([
        {"data": [{"id": "selected-model", "context_window": 500_000}]},
        {"data": [{"id": "selected-model", "context_window": 300_000}]},
    ])
    service = ProviderModelCatalogService(
        ProviderModelCatalogPolicyPorts(
            validate_provider_api_key=lambda key: calls.append(("validate", key)),
            provider_requires_api_key=lambda _provider: True,
            provider_display_name=lambda provider: provider.title(),
            provider_model_descriptor=_descriptor,
            resolve_vertex_project_location=lambda _value: ("project", "location"),
        ),
        ProviderModelCatalogSdkPorts(
            openai_client=lambda _key, _url, _timeout: _client(next(responses)),
            google_ai_studio_client=lambda _key: pytest.fail("unexpected SDK"),
            google_vertex_client=lambda _project, _location: pytest.fail("unexpected SDK"),
            anthropic_client=lambda _key: pytest.fail("unexpected SDK"),
        ),
    )

    config = _config("openai")
    assert service.resolve_context_limit(config) == 500_000
    assert service.fetch_provider_models(config)[0]["contextWindow"] == 300_000
    assert service.resolve_context_limit(config) == 300_000


def test_catalog_resolution_uses_static_descriptor_without_remote_fetch() -> None:
    calls: list[tuple[Any, ...]] = []
    service = ProviderModelCatalogService(
        ProviderModelCatalogPolicyPorts(
            validate_provider_api_key=lambda key: calls.append(("validate", key)),
            provider_requires_api_key=lambda _provider: True,
            provider_display_name=lambda provider: provider.title(),
            provider_model_descriptor=lambda provider, model, api_type: {
                **_descriptor(provider, model, api_type),
                "modelContextWindow": 600_000,
            },
            resolve_vertex_project_location=lambda _value: ("project", "location"),
        ),
        ProviderModelCatalogSdkPorts(
            openai_client=lambda *_args: pytest.fail("descriptor should avoid fetch"),
            google_ai_studio_client=lambda _key: pytest.fail("unexpected SDK"),
            google_vertex_client=lambda _project, _location: pytest.fail("unexpected SDK"),
            anthropic_client=lambda _key: pytest.fail("unexpected SDK"),
        ),
    )

    assert service.resolve_context_limit(_config("openai")) == 600_000


def test_catalog_resolution_keeps_explicit_bound_when_catalog_is_unavailable() -> None:
    service = _service(response={"data": [{"id": "different-model", "context_window": 500_000}]})
    configured = replace(_config("openai"), context_window=300_000)

    assert service.resolve_context_limit(configured) == 300_000
    unknown = _config("openai")
    assert service.resolve_context_limit(unknown) is None
