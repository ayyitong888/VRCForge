"""Offline tests for the ChatGPT subscription Responses transport."""

from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest

from agent_runtime_session_state import AgentRuntimeSessionState
from chatgpt_integration.adapter import (
    CHATGPT_RESPONSES_BASE_URL,
    ChatGPTResponsesAdapter,
    build_responses_input,
)
from provider_configuration_service import ProviderApiConfig
from provider_runtime_adapters import ProviderRuntimeRequest
from vrchat_blendshape_agent import (
    build_llm_settings,
    default_api_key_env_for_provider,
    extract_llm_token_usage,
    get_provider_defaults,
    normalize_base_url,
    provider_display_name,
    provider_requires_api_key,
    request_llm_plan_with_metadata,
)


class FakeClient:
    def __init__(self, events):
        self.responses = SimpleNamespace(create=lambda **payload: iter(events))
        self.closed = 0

    def close(self):
        self.closed += 1


def make_adapter(events, *, token_calls=None, client_kwargs=None):
    client = FakeClient(events)
    seen_kwargs = []

    def factory(**kwargs):
        seen_kwargs.append(kwargs)
        return client

    def token_provider(account_id):
        if token_calls is not None:
            token_calls.append(account_id)
        return "offline-oauth-access-token"

    adapter = ChatGPTResponsesAdapter(
        api_key="selected-account-id",
        base_url=CHATGPT_RESPONSES_BASE_URL,
        access_token_provider=token_provider,
        client_factory=client_kwargs or factory,
    )
    return adapter, client, seen_kwargs


def completed(output=None, **response_fields):
    response = {"status": "completed", "output": output or [], **response_fields}
    return {"type": "response.completed", "response": response}


def test_planner_request_uses_subscription_token_and_siwc_payload():
    token_calls = []
    adapter, client, sdk_kwargs = make_adapter(
        [completed([{
            "type": "function_call",
            "name": "vrcforge_plan_action",
            "arguments": '{"action":"reply","reply":"ready"}',
        }])],
        token_calls=token_calls,
    )

    result = adapter.send_request(ProviderRuntimeRequest(
        model="account-model", prompt="make a plan", instructions="plan only",
    ))

    assert token_calls == ["selected-account-id"]
    assert sdk_kwargs == [{
        "api_key": "offline-oauth-access-token",
        "base_url": CHATGPT_RESPONSES_BASE_URL,
        "max_retries": 0,
    }]
    assert result.text == '{"action": "reply", "reply": "ready"}'
    assert client.closed == 1


def test_probe_and_image_content_are_raw_responses_inputs():
    payloads = []
    client = FakeClient([completed(output=[{
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": "ok"}],
    }], output_text="ok")])
    adapter, _, _ = make_adapter([], client_kwargs=lambda **kwargs: client)
    client.responses.create = lambda **payload: payloads.append(deepcopy(payload)) or iter([
        completed(output=[{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "ok"}],
        }], output_text="ok")
    ])
    request = ProviderRuntimeRequest(model="account-model", prompt="inspect", instructions="", mode="probe")

    assert adapter.send_request(request).text == "ok"
    image_content = [
        {"type": "input_text", "text": "inspect"},
        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
    ]
    assert adapter.send_content(request, image_content).text == "ok"

    assert payloads[0]["input"] == [{"role": "user", "content": "inspect"}]
    assert payloads[1]["input"] == [{"role": "user", "content": image_content}]
    for payload in payloads:
        assert payload["store"] is False
        assert payload["stream"] is True
        assert payload["include"] == ["reasoning.encrypted_content"]
        assert "tools" not in payload
        assert not {"max_output_tokens", "temperature", "previous_response_id", "prompt_cache_retention"} & set(payload)


def test_structured_probe_is_not_wrapped_in_planner_tool():
    payloads = []
    client = FakeClient([])
    client.responses.create = lambda **payload: payloads.append(payload) or iter([
        completed(output_text='{"healthy":true}')
    ])
    adapter, _, _ = make_adapter([], client_kwargs=lambda **kwargs: client)
    request = ProviderRuntimeRequest(
        model="account-model", prompt="return JSON", instructions="", mode="probe", structured_output=True,
    )

    assert adapter.send_request(request).text == '{"healthy":true}'
    assert payloads[0]["text"] == {"format": {"type": "json_object"}}
    assert "tools" not in payloads[0]


def test_native_replay_preserves_order_namespace_and_cached_usage():
    original_output = [
        {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "ciphertext"},
        {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
         "phase": "commentary", "content": [{"type": "output_text", "text": "Reading."}]},
        {"id": "fc_1", "type": "function_call", "call_id": "call-1", "namespace": "vrcforge",
         "name": "vrcforge_read", "arguments": '{"path":"a.txt"}', "status": "completed"},
    ]
    usage = {
        "input_tokens": 21,
        "output_tokens": 8,
        "total_tokens": 29,
        "input_tokens_details": {"cached_tokens": 13, "text_tokens": 18},
        "output_tokens_details": {"reasoning_tokens": 2},
    }
    adapter, _, _ = make_adapter([completed(original_output, usage=usage)])
    native = adapter.send_native_request(
        model="account-model",
        messages=[{"role": "user", "content": "read this"}],
        tools=[{"type": "function", "function": {"name": "vrcforge_read", "parameters": {}}}],
    )

    assert native.assistant_message["responses_output"] == original_output
    assert native.usage["input_tokens_details"] == usage["input_tokens_details"]
    assert native.usage["output_tokens_details"] == usage["output_tokens_details"]
    assert AgentRuntimeSessionState._validate_native_assistant(native.assistant_message) == native.assistant_message
    trace = extract_llm_token_usage(
        SimpleNamespace(usage=native.usage),
        SimpleNamespace(llm_provider="chatgpt", llm_model="account-model"),
    )
    assert trace["cacheReadTokens"] == 13

    history = [
        {"role": "user", "content": "read this"},
        native.assistant_message,
        {"role": "tool", "tool_call_id": "call-1", "content": "file contents"},
    ]
    replay = build_responses_input(history)
    assert replay[1:4] == original_output
    assert replay[4] == {
        "type": "function_call_output", "call_id": "call-1", "output": "file contents",
    }


def test_sdk_output_message_phase_round_trips_without_loss():
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    sdk_message = ResponseOutputMessage(
        id="msg_sdk",
        content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
        role="assistant",
        status="completed",
        type="message",
        phase="final_answer",
    )
    expected = sdk_message.model_dump(mode="json", exclude_none=True)
    adapter, _, _ = make_adapter([completed([sdk_message])])
    native = adapter.send_native_request(model="account-model", messages=[], tools=[])

    assert native.assistant_message["responses_output"] == [expected]
    assert native.assistant_message["responses_output"][0]["phase"] == "final_answer"
    assert AgentRuntimeSessionState._validate_native_assistant(native.assistant_message) == native.assistant_message
    assert build_responses_input([native.assistant_message]) == [expected]


@pytest.mark.parametrize("output", [
    [{"type": "message", "role": "user", "content": []}],
    [{"type": "message", "role": "assistant", "content": [{"type": "unknown"}]}],
    [{"type": "function_call", "call_id": "c1", "namespace": "unknown", "name": "f", "arguments": "{}"}],
])
def test_native_response_rejects_invalid_output_and_namespace(output):
    adapter, _, _ = make_adapter([completed(output)])
    with pytest.raises(RuntimeError):
        adapter.send_native_request(
            model="account-model", messages=[],
            tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
        )


def test_native_history_rejects_mismatched_calls_and_unknown_namespace():
    base = {
        "role": "assistant", "content": None, "tool_calls": [],
        "responses_output": [{
            "id": "fc_1", "type": "function_call", "call_id": "call-1", "namespace": "vrcforge",
            "name": "f", "arguments": "{}", "status": "completed",
        }],
    }
    with pytest.raises(ValueError, match="do not match"):
        build_responses_input([base])
    unknown = deepcopy(base)
    unknown["responses_output"][0]["namespace"] = "other"
    with pytest.raises(ValueError, match="namespace"):
        build_responses_input([unknown])


@pytest.mark.parametrize("events", [
    [{"type": "response.failed", "response": {"error": {"code": "failed"}}}],
    [{"type": "response.incomplete", "response": {}}],
    [{"type": "error", "error": {"message": "failed"}}],
    [],
    [completed(status="incomplete")],
])
def test_stream_requires_successful_completed_event_and_closes_client(events):
    adapter, client, _ = make_adapter(events)
    with pytest.raises(RuntimeError):
        adapter.send_request(ProviderRuntimeRequest(model="account-model", prompt="hello", instructions="", mode="probe"))
    assert client.closed == 1


def test_cancel_watcher_closes_request_client():
    entered = threading.Event()
    cancel = threading.Event()
    result = []

    class BlockingStream:
        def __iter__(self):
            return self

        def __next__(self):
            entered.set()
            while not client.closed:
                time.sleep(0.005)
            raise OSError("transport closed")

        def close(self):
            return None

    client = FakeClient([])
    client.responses.create = lambda **payload: BlockingStream()
    adapter, _, _ = make_adapter([], client_kwargs=lambda **kwargs: client)

    def run_request():
        try:
            adapter.send_request(ProviderRuntimeRequest(
                model="account-model", prompt="hello", instructions="", mode="probe", cancel_event=cancel,
            ))
        except Exception as exc:
            result.append(exc)

    worker = threading.Thread(target=run_request)
    worker.start()
    assert entered.wait(1)
    cancel.set()
    worker.join(2)
    assert not worker.is_alive()
    assert client.closed >= 1
    assert isinstance(result[0], RuntimeError)
    assert "cancelled" in str(result[0])


def test_chatgpt_registry_uses_account_id_without_api_key_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    empty = build_llm_settings({"llm": {"provider": "chatgpt"}}, None, None)
    selected = build_llm_settings(
        {"llm": {"provider": "chatgpt", "api_key": "selected-account"}}, None, None,
    )

    assert empty["api_key"] == ""
    assert empty["api_key_env"] == ""
    assert selected["api_key"] == "selected-account"
    assert selected["model"] == ""
    assert get_provider_defaults("chatgpt")["model"] == ""
    assert default_api_key_env_for_provider("chatgpt") == ""
    assert provider_requires_api_key("chatgpt") is True
    assert provider_display_name("chatgpt") == "ChatGPT"
    assert normalize_base_url("https://attacker.invalid/v1", "chatgpt", "") == CHATGPT_RESPONSES_BASE_URL


def test_fixed_url_validation_precedes_token_lookup_and_binding_uses_account_id():
    token_calls = []
    with pytest.raises(ValueError, match="official OpenAI API endpoint"):
        ChatGPTResponsesAdapter(
            api_key="selected-account-id",
            base_url="https://attacker.invalid/v1",
            access_token_provider=lambda account: token_calls.append(account) or "token",
            client_factory=lambda **kwargs: FakeClient([]),
        )
    assert token_calls == []

    first = ProviderApiConfig("chatgpt", "account-one", CHATGPT_RESPONSES_BASE_URL, "model", "responses")
    same = ProviderApiConfig("chatgpt", "account-one", CHATGPT_RESPONSES_BASE_URL, "model", "responses")
    other = ProviderApiConfig("chatgpt", "account-two", CHATGPT_RESPONSES_BASE_URL, "model", "responses")
    assert first.native_binding() == same.native_binding()
    assert first.native_binding() != other.native_binding()


def test_responses_history_cannot_be_sent_through_legacy_chat_route(monkeypatch):
    import vrchat_blendshape_agent

    monkeypatch.setattr(vrchat_blendshape_agent, "normalize_provider_api_type", lambda *args: (
        "chat_completions", "chat_completions",
    ))
    settings = SimpleNamespace(llm_provider="chatgpt", llm_model="account-model", llm_api_type="chat_completions")
    with pytest.raises(ValueError, match="ChatGPT Responses history"):
        request_llm_plan_with_metadata(
            settings,
            "continue",
            native_messages=[{"role": "assistant", "content": None, "responses_output": []}],
            native_tools=[],
        )


@pytest.mark.parametrize("api_type", [None, "", "auto"])
@pytest.mark.parametrize("native_field", ["native_messages", "native_tools"])
def test_legacy_native_protocol_must_be_explicit_before_negotiation(monkeypatch, api_type, native_field):
    import vrchat_blendshape_agent

    def unexpected_negotiation(**kwargs):
        pytest.fail("An unspecified native protocol must not reach provider negotiation")

    monkeypatch.setattr(vrchat_blendshape_agent, "execute_provider_protocol_negotiation", unexpected_negotiation)
    settings = SimpleNamespace(llm_provider="custom", llm_model="fixture-model", llm_api_type=api_type)
    with pytest.raises(ValueError, match="explicit chat_completions"):
        request_llm_plan_with_metadata(settings, "continue", **{native_field: []})
