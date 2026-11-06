import pytest

from responses_native_protocol import parse_native_response, stream_response
from tests.test_native_responses_protocol import CALL, FakeClient


def _incomplete_response(output):
    return {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": output}


def _stream(client):
    return stream_response(
        client, {}, None, None, None,
        error_factory=lambda message, _response: RuntimeError(message),
        provider_label="Responses",
    )


def test_terminal_reasoning_item_replaces_streamed_opaque_state():
    streamed = {
        "id": "rs_same", "type": "reasoning", "status": "completed",
        "content": [{"type": "reasoning_text", "text": "kept"}],
        "summary": [], "encrypted_content": "opaque-stream-value",
    }
    terminal = {**streamed, "encrypted_content": "opaque-terminal-value"}
    response = _stream(FakeClient([
        {"type": "response.output_item.done", "output_index": 0, "item": streamed},
        {"type": "response.incomplete", "response": _incomplete_response([terminal])},
    ]))

    assert response["output"] == [terminal]
    parsed = parse_native_response(response, [], require_encrypted_reasoning=True)
    assert parsed.assistant_message["responses_output"] == [terminal]


@pytest.mark.parametrize(("field", "value"), [
    ("call_id", "different-call"),
    ("name", "different-tool"),
    ("arguments", '{"path":"different.txt"}'),
])
def test_terminal_function_call_must_match_completed_call_identity_and_arguments(field, value):
    streamed = dict(CALL)
    terminal = {**CALL, field: value}
    with pytest.raises(RuntimeError, match="terminal output changed completed tool-call"):
        _stream(FakeClient([
            {"type": "response.output_item.done", "output_index": 0, "item": streamed},
            {"type": "response.incomplete", "response": _incomplete_response([terminal])},
        ]))
