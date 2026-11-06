from types import SimpleNamespace

from provider_runtime_adapters import OpenAIResponsesAdapter


def adapter():
    return OpenAIResponsesAdapter(api_key="safe-key", base_url="https://example.invalid")


def test_nonstream_responses_incomplete_max_output_tokens_returns_length_without_text():
    response = adapter().parse_response(SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=[SimpleNamespace(type="reasoning", summary=["thinking"])],
        output_text="",
        usage={},
        model="test-model",
    ))
    assert response.finish_reason == "length"
    assert response.text == ""


def test_nonstream_responses_incomplete_discards_uncompleted_function_call():
    response = adapter().parse_response({
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{
            "type": "function_call", "status": "in_progress", "call_id": "call-1",
            "name": "vrcforge_plan_action", "arguments": '{"action":"reply"',
        }],
    })
    assert response.finish_reason == "length"
    assert response.text == ""
    assert response.truncation["discardedToolCalls"][0]["id"] == "call-1"


def test_stream_responses_incomplete_preserves_text_and_drops_partial_call():
    call = {
        "type": "function_call", "status": "in_progress", "call_id": "call-1",
        "name": "vrcforge_plan_action", "arguments": '{"action":"reply"',
    }
    response = adapter()._consume_stream(iter([
        {"type": "response.output_item.added", "output_index": 0, "item": call},
        {"type": "response.output_text.delta", "delta": "partial answer"},
        {"type": "response.incomplete", "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [call, {
                "type": "message", "role": "assistant", "status": "incomplete",
                "content": [{"type": "output_text", "text": "partial answer"}],
            }],
            "usage": {},
        }},
    ]), lambda _text: None, planner_mode=True)
    assert response.finish_reason == "length"
    assert response.text == "partial answer"
    assert response.truncation["discardedToolCalls"][0]["id"] == "call-1"


def test_stream_responses_incomplete_reports_started_call_when_terminal_omits_output():
    call = {
        "type": "function_call", "call_id": "call-2", "name": "vrcforge_plan_action",
        "arguments": "",
    }
    response = adapter()._consume_stream(iter([
        {"type": "response.output_item.added", "output_index": 0, "item": call},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"action":'},
        {"type": "response.incomplete", "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [], "usage": {},
        }},
    ]), lambda _text: None, planner_mode=True)
    assert response.finish_reason == "length"
    assert response.truncation["discardedToolCalls"][0]["arguments"] == '{"action":'
