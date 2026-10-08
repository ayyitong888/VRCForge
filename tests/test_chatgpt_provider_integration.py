"""No OAuth account identifier may be mistaken for a provider API key."""

from types import SimpleNamespace

from provider_configuration_service import ProviderApiConfig
from provider_model_catalog_service import ProviderModelCatalogService
from provider_runtime_adapters import ProviderRuntimeRequest, ProviderRuntimeResponse
from provider_test_integration_service import ProviderTextProbeRunner
from provider_vision_service import ProviderVisionSdkRunner, VisionModelConfig


def test_model_catalog_uses_selected_oauth_account(monkeypatch):
    import chatgpt_oauth_service

    calls = []
    monkeypatch.setattr(chatgpt_oauth_service, "get_chatgpt_auth_service", lambda: SimpleNamespace(
        models=lambda account: calls.append(account) or [{"id": "server-model", "name": "Server model"}],
    ))
    service = ProviderModelCatalogService(SimpleNamespace(), SimpleNamespace())
    config = ProviderApiConfig("chatgpt", "account-ref", "https://api.openai.com/v1", "", "responses")
    assert service.fetch_provider_models(config) == [{"id": "server-model", "name": "Server model"}]
    assert calls == ["account-ref"]


def test_text_probe_uses_oauth_adapter_not_generic_key_sdk(monkeypatch):
    import chatgpt_responses_adapter

    seen = []

    class Adapter:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def send_request(self, request):
            seen.append(request)
            return ProviderRuntimeResponse(text="ok")

    monkeypatch.setattr(chatgpt_responses_adapter, "ChatGPTResponsesAdapter", Adapter)
    runner = ProviderTextProbeRunner(SimpleNamespace(
        validate_provider_api_key=lambda key: None,
        normalize_provider_api_type=lambda *args: ("responses", "responses"),
        runtime_request_factory=ProviderRuntimeRequest,
    ), SimpleNamespace())
    config = ProviderApiConfig("chatgpt", "account-ref", "https://api.openai.com/v1", "server-model", "responses")
    assert runner.probe(config, "hello") == "ok"
    assert seen[0]["api_key"] == "account-ref"
    assert seen[1].mode == "probe"


def test_vision_uses_validated_images_and_oauth_adapter(monkeypatch):
    import chatgpt_responses_adapter

    seen = []

    class Adapter:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def send_content(self, request, content):
            seen.append((request, content))
            return ProviderRuntimeResponse(text="image result", usage={"input_tokens": 10})

    monkeypatch.setattr(chatgpt_responses_adapter, "ChatGPTResponsesAdapter", Adapter)
    runner = ProviderVisionSdkRunner(SimpleNamespace(validate_provider_api_key=lambda key: None), SimpleNamespace())
    config = VisionModelConfig("chatgpt", "account-ref", "https://api.openai.com/v1", "server-model")
    text, usage = runner.run(config, "inspect", [{"dataUrl": "data:image/png;base64,aGVsbG8="}])
    assert text == "image result"
    assert usage == {"input_tokens": 10}
    assert seen[1][0].mode == "probe"
    assert seen[1][1] == [
        {"type": "input_text", "text": "inspect"},
        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
    ]
