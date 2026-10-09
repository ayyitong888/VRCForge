from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
from typing import Any

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from chatgpt_integration.adapter import build_responses_input
from provider_configuration_service import ProviderApiConfig
from vrchat_blendshape_agent import (
    LlmPlanResponse,
    Settings,
    request_llm_plan_with_metadata,
    request_responses_plan_with_metadata,
)
import vrchat_blendshape_agent


def _settings(*, provider: str = "deepseek", api_type: str | None = "responses") -> Settings:
    base_urls = {
        "deepseek": "https://api.deepseek.example",
        "openai": "https://api.openai.com/v1",
        "custom": "https://provider.example/v1",
    }
    models = {
        "deepseek": "deepseek-v4-flash",
        "openai": "gpt-4.1-mini",
        "custom": "fixture-model",
    }
    return Settings(
        llm_provider=provider,
        llm_api_key="safe-key",
        llm_base_url=base_urls[provider],
        llm_model=models[provider],
        llm_api_key_env="",
        gemini_thinking_level="medium",
        unity_mcp_command=[],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=0,
        unity_mcp_instance="",
        unity_mcp_retries=0,
        unity_mcp_retry_backoff_seconds=0,
        unity_mcp_timeout_seconds=0,
        export_tool_name="",
        execute_tool_name="",
        export_path=Path("fixture.json"),
        min_confidence=0.5,
        llm_max_output_tokens=8192,
        llm_api_type=api_type,
    )


def test_deepseek_responses_binding_is_explicit_and_route_scoped() -> None:
    config = ProviderApiConfig(
        "deepseek", "secret-a", "https://api.deepseek.example", "deepseek-v4-flash",
        api_type="responses", thinking_level="low",
    )
    binding = config.native_binding()

    assert binding
    assert replace(config).native_binding() == binding
    assert replace(config, api_key="secret-b").native_binding() != binding
    assert replace(config, model="deepseek-v4-pro").native_binding() != binding
    assert replace(config, api_type="chat_completions").native_binding() != binding
    assert ProviderApiConfig(
        "deepseek", "secret-a", "https://api.deepseek.example", "deepseek-v4-flash", api_type="auto"
    ).native_binding() == ""
    assert ProviderApiConfig(
        "openai", "secret-a", "https://api.openai.com/v1", "gpt-4.1-mini", api_type="responses"
    ).native_binding() == ""


def test_chatgpt_and_chat_completions_native_bindings_remain_unchanged() -> None:
    chatgpt = ProviderApiConfig(
        "chatgpt", "account-one", "https://api.openai.com/v1", "model", "responses"
    )
    chat_completions = ProviderApiConfig(
        "custom", "secret", "https://provider.example/v1", "model", "chat_completions", "medium"
    )

    assert chatgpt.native_binding() == "4d34bfa97ab4a4c250fd95b54afddb91f1513aef62cede710315a582dbff3276"
    assert chat_completions.native_binding() == "24558380b15bfb3cc56458e0140513ec9612fc94eefef846b29a17462ffb9317"


def test_explicit_deepseek_responses_accepts_native_messages_and_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [
        {"role": "user", "content": "inspect the project"},
        {"role": "assistant", "content": None, "responses_output": [{"type": "reasoning", "summary": []}]},
    ]
    tools = [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}]
    expected = LlmPlanResponse(text="continued", reasoning={})
    observed: dict[str, Any] = {}

    def request(candidate: Settings, prompt: str, **kwargs: Any) -> LlmPlanResponse:
        observed.update(settings=candidate, prompt=prompt, kwargs=kwargs)
        return expected

    monkeypatch.setattr(vrchat_blendshape_agent, "request_responses_plan_with_metadata", request)

    result = request_llm_plan_with_metadata(
        _settings(), "continue", native_messages=messages, native_tools=tools
    )

    assert result is expected
    assert observed["settings"].llm_provider == "deepseek"
    assert observed["settings"].llm_api_type == "responses"
    assert observed["prompt"] == "continue"
    assert observed["kwargs"]["native_messages"] == messages
    assert observed["kwargs"]["native_tools"] == tools


@pytest.mark.parametrize("api_type", [None, "", "auto"])
@pytest.mark.parametrize("native_field", ["native_messages", "native_tools"])
def test_automatic_deepseek_protocol_rejects_native_payloads(
    monkeypatch: pytest.MonkeyPatch, api_type: str | None, native_field: str
) -> None:
    monkeypatch.setattr(
        vrchat_blendshape_agent,
        "execute_provider_protocol_negotiation",
        lambda **_kwargs: pytest.fail("Automatic native requests must stop before protocol negotiation"),
    )

    with pytest.raises(ValueError):
        request_llm_plan_with_metadata(
            _settings(api_type=api_type), "continue", **{native_field: []}
        )


@pytest.mark.parametrize("provider", ["openai", "custom"])
@pytest.mark.parametrize("native_field", ["native_messages", "native_tools"])
def test_other_responses_providers_reject_native_payloads(
    provider: str, native_field: str
) -> None:
    with pytest.raises(ValueError):
        request_llm_plan_with_metadata(
            _settings(provider=provider), "continue", **{native_field: []}
        )


def test_deepseek_responses_dispatches_native_receipt_usage_and_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [{"role": "user", "content": "inspect"}]
    tools = [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}]
    stream_callback = lambda _text: None
    cancel_event = Event()
    activity_callback = lambda _activity: None
    responses_output = [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "checking"}]},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
    ]
    assistant_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
        }],
        "responses_output": responses_output,
    }
    native_response = SimpleNamespace(
        assistant_message=assistant_message,
        usage={
            "input_tokens": 18,
            "output_tokens": 9,
            "total_tokens": 27,
            "input_tokens_details": {"cached_tokens": 11},
        },
        reasoning_summary=[],
        finish_reason="tool_calls",
    )
    calls: list[dict[str, Any]] = []

    class FakeDeepSeekResponsesAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def send_native_request(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return native_response

        def send_request(self, _request: Any) -> Any:
            pytest.fail("Native DeepSeek history must use send_native_request")

    monkeypatch.setattr(
        vrchat_blendshape_agent, "DeepSeekResponsesAdapter", FakeDeepSeekResponsesAdapter
    )

    response = request_responses_plan_with_metadata(
        _settings(),
        "ignored by native replay",
        stream_callback=stream_callback,
        cancel_event=cancel_event,
        stream_activity_callback=activity_callback,
        native_messages=messages,
        native_tools=tools,
    )

    assert calls == [{
        "model": "deepseek-v4-flash",
        "messages": messages,
        "tools": tools,
        "stream_callback": stream_callback,
        "cancel_event": cancel_event,
        "stream_activity_callback": activity_callback,
        "reasoning_effort": "high",
        "max_output_tokens": 8192,
    }]
    assert response.assistant_message["responses_output"] == responses_output
    assert response.assistant_message["tool_calls"] == assistant_message["tool_calls"]
    assert response.finish_reason == "tool_calls"
    assert response.usage == {
        "schema": "vrcforge.provider_usage.v1",
        "provider": "deepseek",
        "providerLabel": "DeepSeek",
        "model": "deepseek-v4-flash",
        "source": "deepseek-responses",
        "exact": True,
        "inputTokens": 18,
        "outputTokens": 9,
        "totalTokens": 27,
        "cacheReadTokens": 11,
    }


def _deepseek_native_assistant() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
        }],
        "responses_output": [
            {
                "type": "reasoning",
                "content": [{"type": "reasoning_text", "text": "fixture"}],
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
            },
        ],
    }


def _stored_native_state(directory: Path) -> AgentRuntimeSessionState:
    return AgentRuntimeSessionState(
        AgentRuntimeSessionStatePorts(RLock(), NativeHistoryStore(directory))
    )


def test_deepseek_raw_response_survives_store_restart_and_native_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant = _deepseek_native_assistant()
    binding = "deepseek-responses-binding"
    state = _stored_native_state(tmp_path)

    state.begin_native_turn("session", binding=binding, turn_id="turn-1", message="inspect")
    state.append_native_assistant("session", binding=binding, message=assistant)
    disk_record = NativeHistoryStore(tmp_path).load("session", binding)

    assert disk_record is not None
    assert disk_record["native"]["messages"][-1] == assistant
    assert AgentRuntimeSessionState._validate_native_assistant(disk_record["native"]["messages"][-1]) == assistant

    restarted = _stored_native_state(tmp_path)
    reopened = restarted.native_conversation("session", binding=binding)
    assert reopened == disk_record["native"]
    assert reopened["messages"][-1]["responses_output"] == assistant["responses_output"]

    replay_calls: list[dict[str, Any]] = []
    native_response = SimpleNamespace(
        assistant_message=assistant,
        usage={},
        reasoning_summary=[],
        finish_reason="tool_calls",
    )

    class FakeDeepSeekResponsesAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def send_native_request(self, **kwargs: Any) -> Any:
            replay_calls.append(kwargs)
            return native_response

    monkeypatch.setattr(
        vrchat_blendshape_agent, "DeepSeekResponsesAdapter", FakeDeepSeekResponsesAdapter
    )
    request_responses_plan_with_metadata(
        _settings(), "continue", native_messages=reopened["messages"], native_tools=[]
    )

    assert replay_calls[0]["messages"] == reopened["messages"]
    assert replay_calls[0]["messages"][-1]["responses_output"] == assistant["responses_output"]


def test_native_session_validator_rejects_bad_namespace_and_malformed_reasoning() -> None:
    invalid_namespace = _deepseek_native_assistant()
    invalid_namespace["responses_output"][1]["namespace"] = "other"
    with pytest.raises(ValueError, match="namespace"):
        AgentRuntimeSessionState._validate_native_assistant(invalid_namespace)

    malformed_reasoning = _deepseek_native_assistant()
    malformed_reasoning["responses_output"][0]["content"][0]["text"] = None
    with pytest.raises(ValueError, match="reasoning content"):
        AgentRuntimeSessionState._validate_native_assistant(malformed_reasoning)


def test_chatgpt_responses_codec_remains_stricter_than_shared_native_validator() -> None:
    assistant = _deepseek_native_assistant()

    assert AgentRuntimeSessionState._validate_native_assistant(assistant) == assistant
    with pytest.raises(ValueError, match="encrypted reasoning"):
        build_responses_input([assistant])
