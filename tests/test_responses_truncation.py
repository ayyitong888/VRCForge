from tests.test_native_responses_protocol import CALL, MESSAGE, TOOLS, REASONING, FakeClient, _stream
from responses_native_protocol import parse_native_response


def test_incomplete_max_output_tokens_keeps_text_and_returns_length():
    response = parse_native_response({
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{**MESSAGE, "content": [{"type": "output_text", "text": "partial"}]}],
    }, TOOLS, namespace=None, require_encrypted_reasoning=False)
    assert response.finish_reason == "length"
    assert response.text == "partial"
    assert response.assistant_message["tool_calls"] == []


def test_incomplete_reasoning_only_returns_empty_length_receipt():
    response = parse_native_response({
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [REASONING],
    }, TOOLS, namespace=None, require_encrypted_reasoning=False)
    assert response.finish_reason == "length"
    assert response.text == ""
    assert response.assistant_message["tool_calls"] == []


def test_incomplete_response_retains_only_protocol_completed_calls():
    complete = {**CALL, "call_id": "complete", "arguments": "{}"}
    partial = {**CALL, "id": "fc_partial", "call_id": "partial", "arguments": '{"path":', "status": "in_progress"}
    events = [
        {"type": "response.output_item.added", "output_index": 0, "item": complete},
        {"type": "response.output_item.added", "output_index": 1, "item": partial},
        {"type": "response.function_call_arguments.done", "output_index": 0,
         "item_id": complete["id"], "arguments": "{}"},
        {"type": "response.incomplete", "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [complete, partial],
        }},
    ]
    response = _stream(FakeClient(events))
    assert response["status"] == "incomplete"
    assert [item["call_id"] for item in response["output"]] == ["complete"]
    assert response["_truncation"]["discardedToolCalls"] == [{
        "id": "partial", "name": "read_file", "arguments": '{"path":',
        "reason": "Tool arguments were truncated before the protocol completion event.",
        "suggestion": "Resend this operation as a smaller tool call.",
    }]


def test_output_item_done_authorizes_completed_call_even_if_terminal_item_status_is_in_progress():
    complete = {**CALL, "status": "in_progress"}
    response = _stream(FakeClient([
        {"type": "response.output_item.done", "output_index": 0, "item": complete},
        {"type": "response.incomplete", "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [complete],
        }},
    ]))
    native = parse_native_response(
        response, TOOLS, namespace=None, require_encrypted_reasoning=False,
    )
    assert native.finish_reason == "length"
    assert [call["id"] for call in native.assistant_message["tool_calls"]] == ["call_1"]


def test_incomplete_unfinished_only_call_becomes_empty_length_receipt():
    partial = {**CALL, "status": "in_progress", "arguments": '{"path":'}
    response = _stream(FakeClient([
        {"type": "response.output_item.added", "output_index": 0, "item": partial},
        {"type": "response.incomplete", "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [partial],
        }},
    ]))
    assert response["output"] == []
    assert response["_truncation"]["discardedToolCalls"][0]["id"] == "call_1"
    native = parse_native_response(response, TOOLS, namespace=None, require_encrypted_reasoning=False)
    assert native.finish_reason == "length"
    assert native.assistant_message["tool_calls"] == []


def test_history_projection_drops_internal_hidden_flag_but_retains_message():
    history = [{"role": "user", "content": "continue", "hidden": True}]
    from responses_native_protocol import build_responses_input
    assert build_responses_input(history) == [{"role": "user", "content": "continue"}]


def test_incomplete_reasoning_without_encrypted_signature_is_filtered_for_replay():
    from responses_native_protocol import parse_native_response

    response = parse_native_response({
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "reasoning", "id": "rs_partial", "summary": []}],
    }, [], namespace=None, require_encrypted_reasoning=True)

    assert response.finish_reason == "length"
    assert response.text == ""
    assert response.assistant_message == {"role": "assistant", "content": None, "tool_calls": []}
    assert "responses_output" not in response.assistant_message


def test_completed_reasoning_without_encrypted_signature_remains_rejected():
    import pytest
    from responses_native_protocol import parse_native_response

    with pytest.raises(RuntimeError, match="encrypted reasoning"):
        parse_native_response({
            "status": "completed",
            "output": [{"type": "reasoning", "id": "rs_1", "summary": []}],
        }, [], namespace=None, require_encrypted_reasoning=True)


def test_arguments_done_call_survives_incomplete_terminal_without_output_item_done():
    partial = {**CALL, "arguments": "", "status": "in_progress"}
    response = _stream(FakeClient([
        {"type": "response.output_item.added", "output_index": 0, "item": partial},
        {"type": "response.function_call_arguments.done", "output_index": 0,
         "item_id": partial["id"], "arguments": '{"path":"a.txt"}'},
        {"type": "response.incomplete", "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
        }},
    ]))

    native = parse_native_response(response, TOOLS, namespace=None, require_encrypted_reasoning=False)

    assert native.finish_reason == "length"
    assert native.assistant_message["tool_calls"][0]["function"]["arguments"] == '{"path":"a.txt"}'


def test_empty_completed_response_is_allowed_only_when_explicitly_requested():
    import pytest

    response = {"status": "completed", "output": []}
    with pytest.raises(RuntimeError, match="without a message or tool call"):
        parse_native_response(response, [], namespace=None, require_encrypted_reasoning=False)

    allowed = parse_native_response(
        response, [], namespace=None, require_encrypted_reasoning=False, allow_empty_final=True,
    )
    assert allowed.finish_reason == "stop"
    assert allowed.assistant_message == {"role": "assistant", "content": None, "tool_calls": []}
