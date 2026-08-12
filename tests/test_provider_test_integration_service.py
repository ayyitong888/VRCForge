from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from provider_configuration_service import ProviderApiConfig
from provider_test_integration_service import (
    ProviderProbePolicyPorts,
    ProviderProbeSdkPorts,
    ProviderTestIntegrationService,
    ProviderTestServicePorts,
    ProviderTextProbeRunner,
)


@dataclass
class Request:
    provider: str = "openai"
    api_key: str = "safe-key"
    base_url: str | None = "https://provider.example/v1"
    model: str | None = "model-a"
    api_type: str | None = "chat_completions"
    thinking_level: str = ""
    capability: str = "text"


class FakeProbe:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[ProviderApiConfig, str, bool]] = []

    def probe(
        self,
        config: ProviderApiConfig,
        prompt: str,
        *,
        structured: bool = False,
    ) -> str:
        self.calls.append((config, prompt, structured))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return str(result)


def _config(request: Request) -> ProviderApiConfig:
    return ProviderApiConfig(
        provider=request.provider,
        api_key=request.api_key,
        base_url=str(request.base_url or ""),
        model=str(request.model or ""),
        api_type=request.api_type,
        thinking_level=request.thinking_level,
    )


def _descriptor(_config: ProviderApiConfig) -> dict[str, Any]:
    return {
        "apiType": "chat_completions",
        "resolvedApiType": "chat_completions",
    }


def _service(probe: FakeProbe, *, resolver=_config) -> ProviderTestIntegrationService:
    return ProviderTestIntegrationService(
        ProviderTestServicePorts(
            resolve_api_request=resolver,
            normalize_provider_name=lambda provider: provider.strip().lower(),
            provider_display_name=lambda provider: provider.title(),
            provider_config_descriptor=_descriptor,
            provider_requires_api_key=lambda provider: provider != "ollama",
            extract_json_block=lambda text: text,
        ),
        probe,
    )


def test_provider_test_owner_has_no_dashboard_host_or_implementation_facade() -> None:
    source = Path("provider_test_integration_service.py").read_text(encoding="utf-8")
    assert "self._host" not in source
    assert "__getattr__" not in source
    assert "_impl_" not in source
    assert "sys.modules" not in source
    assert "dashboard_server" not in source
    assert ProviderTestServicePorts.__dataclass_params__.frozen is True
    assert ProviderProbePolicyPorts.__dataclass_params__.frozen is True
    assert ProviderProbeSdkPorts.__dataclass_params__.frozen is True


def test_provider_test_text_and_structured_results_preserve_envelope() -> None:
    probe = FakeProbe(["VRCForge provider test OK", '{"ok": true}'])
    service = _service(probe)

    text = service.run(Request(capability="text"))
    structured = service.run(Request(capability="structured"))

    assert text["ok"] is True
    assert text["status"] == "ok"
    assert text["responsePreview"] == "VRCForge provider test OK"
    assert structured["ok"] is True
    assert structured["status"] == "ok"
    assert probe.calls[0][2] is False
    assert probe.calls[1][2] is True


def test_provider_test_invalid_structured_response_is_warning() -> None:
    service = _service(FakeProbe(["not-json"]))

    result = service.run(Request(capability="structured"))

    assert result["ok"] is False
    assert result["status"] == "warning"
    assert "did not validate" in result["message"]


def test_provider_test_deepseek_auto_stops_after_first_success() -> None:
    probe = FakeProbe(['{"ok": true}', RuntimeError("must not run")])
    result = _service(probe).run(
        Request(
            provider="deepseek",
            model="deepseek-auto",
            api_type="auto",
            capability="structured",
        )
    )

    assert result["ok"] is True
    assert result["recommendedModel"] == "deepseek-v4-pro"
    assert result["recommendedApiType"] == "responses"
    assert [(call[0].model, call[0].api_type) for call in probe.calls] == [
        ("deepseek-v4-pro", "responses"),
    ]
    assert result["attempts"] == [
        {"model": "deepseek-v4-pro", "apiType": "responses", "status": "verified"}
    ]
    assert "safe-key" not in str(result["attempts"])


def test_provider_test_continues_only_after_explicit_protocol_compatibility_error() -> None:
    unsupported = RuntimeError("endpoint not found")
    unsupported.status_code = 404  # type: ignore[attr-defined]
    probe = FakeProbe([unsupported, "VRCForge provider test OK", RuntimeError("must not run")])

    result = _service(probe).run(
        Request(provider="deepseek", model="deepseek-v4-pro", api_type="auto")
    )

    assert result["ok"] is True
    assert [(call[0].model, call[0].api_type) for call in probe.calls] == [
        ("deepseek-v4-pro", "responses"),
        ("deepseek-v4-pro", "messages"),
    ]
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "unsupported",
        "verified",
    ]


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("ordinary provider failure"), id="ordinary"),
        pytest.param(TimeoutError("provider timed out"), id="timeout"),
    ],
)
def test_provider_test_stops_after_non_compatibility_exception(failure: BaseException) -> None:
    probe = FakeProbe([failure, "VRCForge provider test OK"] * 3)

    result = _service(probe).run(
        Request(provider="deepseek", model="deepseek-v4-pro", api_type="auto")
    )

    assert result["ok"] is False
    assert len(probe.calls) == 1
    assert result["attempts"][0]["status"] == "failed"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_provider_test_stops_after_auth_rate_limit_or_server_error(status: int) -> None:
    failure = RuntimeError(f"HTTP {status}")
    failure.status_code = status  # type: ignore[attr-defined]
    probe = FakeProbe([failure, "VRCForge provider test OK"] * 3)

    result = _service(probe).run(
        Request(provider="deepseek", model="deepseek-v4-pro", api_type="auto")
    )

    assert result["ok"] is False
    assert len(probe.calls) == 1
    assert result["attempts"][0]["status"] == "failed"


def test_provider_test_stops_after_invalid_structured_response() -> None:
    probe = FakeProbe(["not-json", '{"ok": true}'] * 3)

    result = _service(probe).run(
        Request(
            provider="deepseek",
            model="deepseek-v4-pro",
            api_type="auto",
            capability="structured",
        )
    )

    assert result["ok"] is False
    assert result["status"] == "warning"
    assert len(probe.calls) == 1


def test_provider_test_vision_is_honestly_skipped_without_sdk_call() -> None:
    probe = FakeProbe([])
    result = _service(probe).run(Request(capability="vision"))

    assert result["ok"] is True
    assert result["status"] == "skipped"
    assert result["skipped"] is True
    assert probe.calls == []


def test_deepseek_messages_probe_uses_official_anthropic_base() -> None:
    observed: dict[str, Any] = {}
    sdk = ProviderProbeSdkPorts(
        responses_adapter=lambda *_args: pytest.fail("unexpected Responses SDK"),
        google_client=lambda *_args: pytest.fail("unexpected Google SDK"),
        google_types=lambda: pytest.fail("unexpected Google types"),
        anthropic_client=lambda key, url: (
            observed.update(client=(key, url))
            or SimpleNamespace(
                messages=SimpleNamespace(
                    create=lambda **kwargs: (
                        observed.update(request=kwargs)
                        or SimpleNamespace(content=[SimpleNamespace(text="ok")])
                    )
                )
            )
        ),
        openai_client=lambda *_args: pytest.fail("unexpected Chat SDK"),
    )
    runner = ProviderTextProbeRunner(_probe_policy(observed), sdk)
    config = ProviderApiConfig(
        "deepseek", "safe-key", "https://api.deepseek.com",
        "deepseek-v4-pro", "messages", "high",
    )

    assert runner.probe(config, "probe") == "ok"
    assert observed["client"] == ("safe-key", "https://api.deepseek.com/anthropic")
    assert observed["request"]["model"] == "deepseek-v4-pro"


def test_provider_test_normalization_and_probe_failures_are_bounded_results() -> None:
    def fail_resolve(_request: Request) -> ProviderApiConfig:
        raise ValueError("bad config")

    normalization = _service(FakeProbe([]), resolver=fail_resolve).run(Request())
    probe_failure = _service(FakeProbe([RuntimeError("offline fake")])).run(Request())

    assert normalization["status"] == "error"
    assert normalization["message"] == "bad config"
    assert probe_failure["status"] == "error"
    assert probe_failure["message"] == "offline fake"


def test_provider_test_required_key_is_checked_before_probe() -> None:
    probe = FakeProbe([])
    result = _service(probe).run(Request(api_key=""))

    assert result["ok"] is False
    assert result["message"] == "Openai API key is empty."
    assert probe.calls == []


def _probe_policy(observed: dict[str, Any]) -> ProviderProbePolicyPorts:
    def settings_factory(**kwargs: Any) -> Any:
        observed["settings"] = kwargs
        return SimpleNamespace(**kwargs)

    def runtime_request_factory(**kwargs: Any) -> Any:
        observed["runtime_request"] = kwargs
        return SimpleNamespace(**kwargs)

    return ProviderProbePolicyPorts(
        validate_provider_api_key=lambda key: observed.setdefault("validated", []).append(key),
        normalize_provider_api_type=lambda _provider, _model, api_type: (
            str(api_type or "chat_completions"),
            str(api_type or "chat_completions"),
        ),
        resolve_vertex_project_location=lambda value: (
            observed.setdefault("vertex", []).append(value) or ("project-a", "us-test1")
        ),
        build_gemini_generate_config=lambda settings, types: (
            observed.update(gemini_policy=(settings, types)) or {"thinking": "fake"}
        ),
        build_anthropic_request_payload=lambda settings, prompt: (
            observed.update(anthropic_policy=(settings, prompt))
            or {"model": settings.llm_model, "messages": []}
        ),
        build_openai_compatible_request_payload=lambda settings, prompt: (
            observed.update(openai_policy=(settings, prompt))
            or {"model": settings.llm_model, "messages": []}
        ),
        model_rejects_fixed_temperature=lambda model: model.startswith("reasoning"),
        settings_factory=settings_factory,
        runtime_request_factory=runtime_request_factory,
    )


def test_probe_runner_openai_uses_fake_sdk_and_production_request_shape() -> None:
    observed: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        observed["openai_request"] = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fake answer"))]
        )

    runner = ProviderTextProbeRunner(
        _probe_policy(observed),
        ProviderProbeSdkPorts(
            responses_adapter=lambda _key, _url: pytest.fail("unexpected responses adapter"),
            google_client=lambda _config, _location: pytest.fail("unexpected Google SDK"),
            google_types=lambda: pytest.fail("unexpected Google types"),
            anthropic_client=lambda _key, _url: pytest.fail("unexpected Anthropic SDK"),
            openai_client=lambda key, url, timeout: (
                observed.update(openai_client=(key, url, timeout))
                or SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            ),
        ),
    )
    config = ProviderApiConfig(
        "openai",
        "safe-key",
        "https://provider.example/v1",
        "model-a",
        "chat_completions",
    )

    assert runner.probe(config, "probe", structured=True) == "fake answer"
    assert observed["openai_client"] == (
        "safe-key",
        "https://provider.example/v1",
        30.0,
    )
    assert observed["openai_request"]["max_tokens"] == 64
    assert observed["openai_request"]["response_format"] == {"type": "json_object"}
    assert observed["settings"]["unity_mcp_port"] == 0
    assert observed["settings"]["export_path"] == Path(
        "Assets/VRCForge/blendshapes_export.json"
    )


def test_probe_runner_responses_google_and_anthropic_use_only_fake_sdks() -> None:
    observed: dict[str, Any] = {}

    class FakeResponses:
        def send_request(self, request: Any) -> Any:
            observed["responses_request"] = request
            return SimpleNamespace(text="responses fake")

    def google_generate(**kwargs: Any) -> Any:
        observed["google_request"] = kwargs
        return SimpleNamespace(text="google fake")

    def anthropic_create(**kwargs: Any) -> Any:
        observed["anthropic_request"] = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text="anthropic"), SimpleNamespace(text="fake")]
        )

    sdk = ProviderProbeSdkPorts(
        responses_adapter=lambda key, url: (
            observed.update(responses_client=(key, url)) or FakeResponses()
        ),
        google_client=lambda config, location: (
            observed.update(google_client=(config.provider, location))
            or SimpleNamespace(models=SimpleNamespace(generate_content=google_generate))
        ),
        google_types=lambda: "fake-google-types",
        anthropic_client=lambda key, url: (
            observed.update(anthropic_client=(key, url))
            or SimpleNamespace(messages=SimpleNamespace(create=anthropic_create))
        ),
        openai_client=lambda _key, _url, _timeout: pytest.fail("unexpected OpenAI SDK"),
    )
    runner = ProviderTextProbeRunner(_probe_policy(observed), sdk)

    responses = ProviderApiConfig(
        "deepseek",
        "safe-key",
        "https://responses.example/v1",
        "deepseek-reasoner",
        "responses",
        "high",
    )
    google = ProviderApiConfig(
        "vertexai",
        "",
        "projects/project-a/locations/us-test1",
        "gemini-model",
        "generate_content",
        "high",
    )
    anthropic = ProviderApiConfig(
        "anthropic",
        "safe-key",
        "",
        "claude-model",
        "messages",
    )

    assert runner.probe(responses, "probe", structured=True) == "responses fake"
    assert runner.probe(google, "probe") == "google fake"
    assert runner.probe(anthropic, "probe") == "anthropic\nfake"
    assert observed["runtime_request"]["max_output_tokens"] == 512
    assert observed["runtime_request"]["structured_output"] is True
    assert observed["google_client"] == ("vertexai", ("project-a", "us-test1"))
    assert observed["google_request"]["config"] == {"thinking": "fake"}
    assert observed["anthropic_client"] == ("safe-key", "")


def test_probe_runner_custom_site_routes_each_explicit_protocol_without_silent_chat_fallback() -> None:
    observed: list[tuple[str, str]] = []

    class FakeResponses:
        def send_request(self, _request: Any) -> Any:
            observed.append(("responses", "https://custom.example/v1"))
            return SimpleNamespace(text="responses")

    sdk = ProviderProbeSdkPorts(
        responses_adapter=lambda _key, url: FakeResponses(),
        google_client=lambda config, _location: (
            observed.append(("generate_content", config.base_url))
            or SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_kwargs: SimpleNamespace(text="google")))
        ),
        google_types=lambda: "types",
        anthropic_client=lambda _key, url: (
            observed.append(("messages", url))
            or SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(content=[SimpleNamespace(text="messages")])))
        ),
        openai_client=lambda _key, url, _timeout: (
            observed.append(("chat_completions", str(url)))
            or SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="chat"))]))))
        ),
    )
    runner = ProviderTextProbeRunner(_probe_policy({}), sdk)

    for api_type, expected in (
        ("responses", "responses"),
        ("chat_completions", "chat"),
        ("messages", "messages"),
        ("generate_content", "google"),
    ):
        config = ProviderApiConfig(
            "custom",
            "safe-key",
            "https://custom.example/v1",
            "site-model",
            api_type,
        )
        assert runner.probe(config, "probe") == expected

    assert observed == [
        ("responses", "https://custom.example/v1"),
        ("chat_completions", "https://custom.example/v1"),
        ("messages", "https://custom.example"),
        ("generate_content", "https://custom.example/v1"),
    ]
