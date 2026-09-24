from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

import dashboard_server as dashboard
from provider_configuration_service import ProviderApiConfig
from provider_test_integration_service import ProviderTextProbeRunner, ProviderProbeSdkPorts
from tests.test_provider_test_integration_service import _probe_policy


def config(protocol="responses"):
    return ProviderApiConfig("openai", "private-review-key", "https://example.invalid/v1", "same-main-model", protocol, "")


def test_review_uses_independent_tool_free_responses_request(monkeypatch):
    observed = {}
    def send(request):
        observed["request"] = request
        return SimpleNamespace(text='{"decision":"allow_auto"}')
    sdk = ProviderProbeSdkPorts(
        responses_adapter=lambda key, url: observed.update(key=key, url=url) or SimpleNamespace(send_request=send),
        google_client=lambda *_: None, google_types=lambda: None,
        anthropic_client=lambda *_: None, openai_client=lambda *_: None,
    )
    probe = ProviderTextProbeRunner(_probe_policy(observed), sdk)
    monkeypatch.setattr(dashboard, "PROVIDER_TEXT_PROBE", probe)
    monkeypatch.setattr(dashboard, "request_llm_plan_with_metadata", lambda *_a, **_k: pytest.fail("planner transport used"))
    model = dashboard._RuntimePlannerModel(SimpleNamespace(current_config=config))
    result = model.review("Review only this frozen approval.")
    assert result.text == '{"decision":"allow_auto"}'
    assert observed["key"] == "private-review-key"
    request = vars(observed["request"])
    assert request["mode"] == "probe"
    assert request["model"] == "same-main-model"
    assert "independent approval reviewer" in request["instructions"]
    assert request["max_output_tokens"] == 4096
    assert request["prompt"] == "Review only this frozen approval."
    assert not {"tools", "native_tools", "messages", "native_messages"} & request.keys()
    assert "private-review-key" not in str(request)
    assert isinstance(request["cancel_event"], Event)
    assert model.active_call_count() == 0


@pytest.mark.parametrize("protocol", ["chat_completions", "messages", "generate_content"])
def test_probe_cancellation_closes_client_and_preserves_transport_error(protocol):
    observed = {}
    cancel, closed = Event(), Event()
    def request(**kwargs):
        assert "tools" not in kwargs
        cancel.set()
        assert closed.wait(1)
        raise ValueError("original transport failure")
    def close():
        closed.set()
        raise RuntimeError("cleanup must not mask error")
    client = SimpleNamespace(close=close, models=SimpleNamespace(generate_content=request),
        messages=SimpleNamespace(create=request), chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
    sdk = ProviderProbeSdkPorts(responses_adapter=lambda *_: None,
        google_client=lambda *_: client, google_types=lambda: None,
        anthropic_client=lambda *_: client, openai_client=lambda *_: client)
    with pytest.raises(ValueError, match="original transport failure"):
        ProviderTextProbeRunner(_probe_policy(observed), sdk).probe(
            config(protocol), "review", structured=True, cancel_event=cancel,
        )
    assert closed.is_set()


def test_review_overall_deadline_cancels_tracked_worker(monkeypatch):
    cancelled = Event()
    def probe(_config, _prompt, *, structured, cancel_event, **options):
        assert structured
        assert cancel_event.wait(1)
        cancelled.set()
        raise RuntimeError("cancelled transport")
    monkeypatch.setattr(dashboard, "PROVIDER_TEXT_PROBE", SimpleNamespace(
        probe=probe, probe_settings=lambda _: SimpleNamespace()))
    model = dashboard._RuntimePlannerModel(SimpleNamespace(current_config=config))
    model._REVIEW_TIMEOUT_SECONDS = 0.02
    with pytest.raises(dashboard.RuntimePlannerProviderTimeoutError, match="overall"):
        model.review("review")
    assert cancelled.wait(1)
    assert model.shutdown(1)["ok"]


def test_review_stop_cancels_tracked_worker(monkeypatch):
    started = Event()
    def probe(_config, _prompt, *, structured, cancel_event, **options):
        started.set()
        assert cancel_event.wait(1)
        return '{"decision":"allow_auto"}'
    monkeypatch.setattr(dashboard, "PROVIDER_TEXT_PROBE", SimpleNamespace(
        probe=probe, probe_settings=lambda _: SimpleNamespace()))
    model = dashboard._RuntimePlannerModel(SimpleNamespace(current_config=config))
    monkeypatch.setattr(model, "_runtime_cancel_requested", lambda _: started.is_set())
    with pytest.raises(dashboard.RuntimePlannerProviderCancelledError):
        model.review("review")
    assert model.shutdown(1)["ok"]


@pytest.mark.parametrize("protocol", ["chat_completions", "messages", "generate_content", "responses"])
def test_review_role_and_budget_use_production_payload_builders(protocol):
    from vrchat_blendshape_agent import (
        Settings, build_openai_compatible_request_payload,
        build_anthropic_request_payload, build_gemini_generate_config,
    )
    from provider_runtime_adapters import ProviderRuntimeRequest, OpenAIResponsesAdapter
    observed = {}
    def create(**payload):
        observed["payload"] = payload
        return SimpleNamespace(text='{"decision":"manual"}', output_text='{"decision":"manual"}', output=[],
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"decision":"manual"}'))],
            content=[SimpleNamespace(text='{"decision":"manual"}')])
    client = SimpleNamespace(close=lambda: None, models=SimpleNamespace(generate_content=create),
        messages=SimpleNamespace(create=create), responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    policy = replace(_probe_policy(observed), settings_factory=Settings,
        runtime_request_factory=ProviderRuntimeRequest,
        build_openai_compatible_request_payload=build_openai_compatible_request_payload,
        build_anthropic_request_payload=build_anthropic_request_payload,
        build_gemini_generate_config=build_gemini_generate_config)
    sdk = ProviderProbeSdkPorts(
        responses_adapter=lambda key, url: OpenAIResponsesAdapter(api_key=key, base_url=url, client_factory=lambda **_: client),
        google_client=lambda *_: client,
        google_types=lambda: SimpleNamespace(GenerateContentConfig=lambda **values: values),
        anthropic_client=lambda *_: client, openai_client=lambda *_: client)
    role = "Independent approval reviewer; JSON decision only."
    assert ProviderTextProbeRunner(policy, sdk).probe(config(protocol), "frozen evidence", structured=True,
        instructions=role, max_output_tokens=4096) == '{"decision":"manual"}'
    payload = observed["payload"]
    assert "tools" not in payload
    if protocol == "chat_completions":
        assert payload["messages"] == [{"role": "system", "content": role}, {"role": "user", "content": "frozen evidence"}]
        assert payload["max_tokens"] == 4096
    elif protocol == "messages":
        assert payload["system"] == role
        assert payload["max_tokens"] == 4096
    elif protocol == "generate_content":
        assert payload["config"]["system_instruction"] == role
        assert payload["config"]["max_output_tokens"] == 4096
    else:
        assert payload["instructions"] == role
        assert payload["max_output_tokens"] == 4096
