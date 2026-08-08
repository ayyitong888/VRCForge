from __future__ import annotations

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
