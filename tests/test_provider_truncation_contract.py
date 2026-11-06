from __future__ import annotations

from types import SimpleNamespace
import sys

from vrchat_blendshape_agent import (
    LlmPlanResponse,
    _native_anthropic_response,
    _native_gemini_response,
    _native_messages_to_anthropic,
    _native_messages_to_gemini,
    build_anthropic_request_payload,
    request_anthropic_plan_with_metadata,
)


def settings():
    return SimpleNamespace(llm_provider="gemini", llm_model="gemini-test", llm_max_output_tokens=80)


def test_response_truncation_defaults_to_empty():
    assert LlmPlanResponse("", {}).truncation == {}


def test_anthropic_explicit_output_cap_keeps_thinking_with_valid_budget():
    config = settings()
    config.llm_provider = "anthropic"
    config.llm_model = "claude-sonnet-4-5"
    config.gemini_thinking_level = "medium"
    config.llm_system_instruction = ""
    payload = build_anthropic_request_payload(config, "hello")
    assert payload["max_tokens"] == 80
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 79}


def test_gemini_max_tokens_retains_typed_complete_function_call_and_signature():
    response = SimpleNamespace(
        candidates=[SimpleNamespace(
            finish_reason="MAX_TOKENS",
            content=SimpleNamespace(role="model", parts=[
                {"text": "Partial answer ", "thought": False},
                {"text": "private thought", "thought": True},
                {"function_call": {"name": "read", "args": {"path": "a.txt"}},
                 "thought_signature": "opaque"},
            ]),
        )],
        usage_metadata={}, model_version="gemini-test",
    )
    result = _native_gemini_response(settings(), response)
    assert result.finish_reason == "length"
    assert result.text == "Partial answer "
    assert "private thought" not in result.text
    assert result.assistant_message["tool_calls"][0]["function"]["name"] == "read"
    assert any(part.get("thought_signature") == "opaque" for part in result.assistant_message["gemini_parts"])
    assert result.truncation["maxOutputTokens"] == 80
    assert any(part.get("text") == "Partial answer " for part in result.assistant_message["gemini_parts"])


def test_gemini_history_replays_signature_and_maps_tool_result_to_function_response():
    messages = [
        {"role": "system", "content": "rules", "hidden": True},
        {"role": "user", "content": "read", "hidden": True},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a"}'}}
        ], "gemini_parts": [
            {"functionCall": {"name": "read", "args": {"path": "a"}}, "thought_signature": "opaque"}
        ]},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"text":"done"}'},
    ]
    contents, system = _native_messages_to_gemini(messages)
    assert system == "rules"
    assert contents[0]["parts"][0]["text"] == "read"
    assert contents[1]["parts"][0]["thought_signature"] == "opaque"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "read"
    assert "hidden" not in str(contents)


def test_messages_native_translation_preserves_tool_ids_and_hides_internal_flags():
    messages = [
        {"role": "user", "content": "read", "hidden": True},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a"}'}}
        ], "anthropic_content": [{"type": "thinking", "thinking": "opaque", "signature": "sig"},
                                 {"type": "tool_use", "id": "call-1", "name": "read", "input": {"path": "a"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "done"},
    ]
    converted = _native_messages_to_anthropic(messages)
    assert converted[0]["content"] == "read"
    assert converted[1]["content"][0]["signature"] == "sig"
    assert converted[2]["content"][0]["tool_use_id"] == "call-1"
    assert "hidden" not in str(converted)


def test_messages_only_closes_tool_blocks_reported_by_content_block_stop():
    response = {
        "stop_reason": "max_tokens",
        "content": [
            {"type": "text", "text": "Partial"},
            {"type": "tool_use", "id": "complete", "name": "read", "input": {"path": "a"}},
            {"type": "tool_use", "id": "open", "name": "write", "input": {}},
        ],
        "usage": {},
    }
    result = _native_anthropic_response(
        settings(), response, source="anthropic", completed_indices={0, 1},
        partial_calls=[{"id": "open", "function": {"name": "write", "arguments": '{"path":'}}],
    )
    assert result.finish_reason == "length"
    assert [call["id"] for call in result.assistant_message["tool_calls"]] == ["complete"]
    assert result.assistant_message["anthropic_content"] == response["content"][:2]
    assert result.truncation["discardedToolCalls"][0]["id"] == "open"
    assert result.truncation["discardedToolCalls"][0]["name"] == "write"


def test_messages_keeps_partial_text_even_when_text_block_has_no_stop_event():
    result = _native_anthropic_response(
        settings(), {"stop_reason": "max_tokens", "content": [
            {"type": "text", "text": "partial text"},
            {"type": "thinking", "thinking": "private", "signature": "sig"},
        ], "usage": {}}, source="anthropic", completed_indices=set(), streamed_text="partial text",
    )
    assert result.text == "partial text"
    assert result.assistant_message["content"] == "partial text"
    assert result.assistant_message["anthropic_content"][0]["text"] == "partial text"
    assert "private" not in result.assistant_message["content"]


def test_gemini_camel_case_finish_reason_normalizes_max_tokens():
    response = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "partial"}]}}]}
    result = _native_gemini_response(settings(), response)
    assert result.finish_reason == "length"
    assert result.assistant_message["gemini_parts"] == [{"text": "partial"}]


def test_messages_stream_executes_only_calls_with_content_block_stop(monkeypatch):
    events = []
    final_blocks = []
    for index, name in enumerate(("read", "scan", "broken", "unclosed")):
        block = {"type": "tool_use", "id": f"call-{index}", "name": name, "input": {}}
        events.append({"type": "content_block_start", "index": index, "content_block": block})
        fragment = '{"path":"a.txt"}' if index in {0, 1, 3} else '{"path":'
        events.append({"type": "content_block_delta", "index": index,
                       "delta": {"type": "input_json_delta", "partial_json": fragment}})
        final_block = {**block, "input": {"path": "a.txt"} if index < 2 else {}}
        final_blocks.append(final_block)
        if index < 3:
            events.append({"type": "content_block_stop", "index": index})

    class FakeStream:
        def __enter__(self):
            self._events = iter(events)
            return self

        def __iter__(self):
            return self._events

        def __exit__(self, *_args):
            return False

        def get_final_message(self):
            return {"content": final_blocks, "stop_reason": "max_tokens", "usage": {}, "model": "claude-test"}

    class FakeMessages:
        def stream(self, **payload):
            self.payload = payload
            return FakeStream()

        def create(self, **_payload):
            raise AssertionError("native transcript must use the streaming boundary events")

    class FakeAnthropic:
        def __init__(self, **_kwargs):
            self.messages = FakeMessages()

        def close(self):
            pass

    class FakeHttpxClient:
        def __init__(self, **_kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=FakeHttpxClient))
    config = settings()
    config.llm_provider = "anthropic"
    config.llm_model = "claude-test"
    config.llm_api_key = "test-key"
    config.llm_system_instruction = ""
    config.gemini_thinking_level = ""
    config.llm_base_url = ""
    config.llm_sdk_max_retries = 0
    result = request_anthropic_plan_with_metadata(
        config, "", stream_callback=lambda _text: None,
        native_messages=[{"role": "user", "content": "work"}],
        native_tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
    )
    assert result.finish_reason == "length"
    assert [call["id"] for call in result.assistant_message["tool_calls"]] == ["call-0", "call-1", "call-2"]
    assert result.assistant_message["tool_calls"][2]["function"]["arguments"] == '{"path":'
    assert result.truncation["discardedToolCalls"][0]["id"] == "call-3"
    assert result.truncation["discardedToolCalls"][0]["name"] == "unclosed"
