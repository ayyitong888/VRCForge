"""Protocol-neutral tests for native Responses history and stream handling."""

from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest

from responses_native_protocol import (
    NativeResponse,
    build_responses_input,
    flat_function_tools,
    parse_native_response,
    stream_response,
)


TOOLS = [{
    "type": "function",
    "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object"}},
}]
REASONING = {
    "id": "rs_1",
    "type": "reasoning",
    "status": "completed",
    "content": [{"type": "reasoning_text", "text": "Inspect the request.", "index": 0}],
    "summary": [],
    "metadata": {"source": "deepseek"},
}
CALL = {
    "id": "fc_1",
    "type": "function_call",
    "call_id": "call_1",
    "name": "read_file",
    "arguments": '{"path":"notes.txt"}',
    "status": "completed",
}
MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": "Reading."}],
}
USAGE = {
    "input_tokens": 31,
    "output_tokens": 11,
    "total_tokens": 42,
    "input_tokens_details": {"cached_tokens": 7},
    "output_tokens_details": {"reasoning_tokens": 4},
}


class FakeClient:
    def __init__(self, events=()):
        self.responses = SimpleNamespace(create=lambda **_payload: iter(events))
        self.closed = 0

    def close(self):
        self.closed += 1


def _error_factory(message, response):
    error = RuntimeError(message)
    error.usage = response.get("usage", {})
    return error


def _stream(client, **kwargs):
    return stream_response(
        client,
        {},
        kwargs.pop("cancel_event", None),
        kwargs.pop("stream_callback", None),
        kwargs.pop("activity_callback", None),
        error_factory=kwargs.pop("error_factory", _error_factory),
        provider_label=kwargs.pop("provider_label", "DeepSeek Responses"),
    )


def test_deepseek_flat_tools_and_unqualified_function_calls():
    tools = flat_function_tools(TOOLS)
    assert tools == [{
        "type": "function",
        "name": "read_file",
        "description": "Read a file",
        "parameters": {"type": "object"},
    }]

    response = parse_native_response(
        {"output": [CALL, MESSAGE], "usage": USAGE},
        tools,
        namespace=None,
        require_encrypted_reasoning=False,
    )
    assert isinstance(response, NativeResponse)
    assert response.assistant_message["tool_calls"] == [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"notes.txt"}'},
    }]
    assert "namespace" not in response.assistant_message["responses_output"][0]

    with pytest.raises(RuntimeError, match="namespace"):
        parse_native_response(
            {"output": [{**CALL, "namespace": None}, MESSAGE]},
            tools,
            namespace=None,
            require_encrypted_reasoning=False,
        )


@pytest.mark.parametrize("tool", [
    {"type": "custom", "name": "patch"},
    {"type": "namespace", "name": "vrcforge", "tools": TOOLS},
    {"type": "browser"},
    {"type": "function", "name": "read_file", "namespace": "vrcforge", "parameters": {}},
])
def test_flat_function_tools_reject_nonflat_definitions(tool):
    with pytest.raises(ValueError):
        flat_function_tools([tool])


def test_flat_function_tools_reject_duplicate_names():
    with pytest.raises(ValueError, match="incomplete"):
        flat_function_tools([TOOLS[0], {"type": "function", "name": "read_file", "parameters": {}}])


def test_flat_function_tools_preserve_explicit_strict_constraint():
    tool = deepcopy(TOOLS[0])
    tool["function"]["strict"] = True
    assert flat_function_tools([tool])[0]["strict"] is True


def test_plain_reasoning_and_matching_history_round_trip_without_field_loss():
    original = [deepcopy(REASONING), deepcopy(CALL), deepcopy(MESSAGE)]
    parsed = parse_native_response(
        {"output": original, "usage": USAGE},
        TOOLS,
        namespace=None,
        require_encrypted_reasoning=False,
    )
    assert parsed.assistant_message["responses_output"] == original
    assert parsed.reasoning_summary == []
    assert parsed.usage["input_tokens_details"] == USAGE["input_tokens_details"]
    assert parsed.usage["output_tokens_details"] == USAGE["output_tokens_details"]

    history = [
        {"role": "system", "content": "system policy"},
        {"role": "developer", "content": "developer note"},
        {"role": "user", "content": "read notes.txt"},
        parsed.assistant_message,
        {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
    ]
    replay = build_responses_input(
        history,
        namespace=None,
        require_encrypted_reasoning=False,
        system_role="system",
    )
    assert replay[0] == {"role": "system", "content": "system policy"}
    assert replay[1] == {"role": "developer", "content": "developer note"}
    assert replay[3:6] == original
    assert replay[6] == {"type": "function_call_output", "call_id": "call_1", "output": "file contents"}
    assert "namespace" not in replay[4]


def test_system_role_default_and_explicit_developer_role():
    result = build_responses_input([
        {"role": "system", "content": "system"},
        {"role": "developer", "content": "developer"},
    ])
    assert [item["role"] for item in result] == ["developer", "developer"]


@pytest.mark.parametrize("content", [
    [{"type": "reasoning_text"}],
    [{"type": "reasoning_text", "text": 5}],
    [{"type": "output_text", "text": "wrong kind"}],
])
def test_plain_reasoning_content_requires_reasoning_text_strings(content):
    with pytest.raises(RuntimeError, match="reasoning"):
        parse_native_response(
            {"output": [{**REASONING, "content": content}, MESSAGE]},
            [],
            namespace=None,
            require_encrypted_reasoning=False,
        )


def test_plain_reasoning_requires_content_or_encrypted_content():
    with pytest.raises(RuntimeError, match="neither encrypted content nor reasoning content"):
        parse_native_response(
            {"output": [{"type": "reasoning", "summary": []}, MESSAGE]},
            [],
            namespace=None,
            require_encrypted_reasoning=False,
        )


def test_chatgpt_defaults_still_require_encrypted_reasoning():
    with pytest.raises(RuntimeError, match="encrypted reasoning"):
        parse_native_response({"output": [REASONING, MESSAGE]}, [])

    history = {
        "role": "assistant",
        "content": "Reading.",
        "tool_calls": [],
        "responses_output": [REASONING, MESSAGE],
    }
    with pytest.raises(ValueError, match="encrypted reasoning"):
        build_responses_input([history])


def test_stream_rebuilds_sparse_terminal_from_completed_items_and_keeps_usage():
    events = [
        {"type": "response.reasoning_text.delta", "delta": "thinking"},
        {"type": "response.output_item.added", "output_index": 0, "item": CALL},
        {"type": "response.output_item.done", "output_index": 0, "item": CALL},
        {"type": "response.output_text.delta", "delta": "Reading."},
        {"type": "response.completed", "response": {"status": "completed", "output": [], "usage": USAGE}},
    ]
    client = FakeClient(events)
    text = []
    activity = []

    response = _stream(client, stream_callback=text.append, activity_callback=activity.append)

    assert response["output"] == [CALL]
    assert response["usage"] == USAGE
    assert text == ["Reading."]
    assert activity == [
        {"kind": "reasoning_activity"},
        {"kind": "tool_call_activity"},
        {"kind": "tool_call_activity"},
    ]
    assert client.closed == 1


def test_failed_terminal_uses_error_factory_with_usage_and_closes_client():
    client = FakeClient([{
        "type": "response.failed",
        "response": {"status": "failed", "output": [], "usage": USAGE},
    }])
    with pytest.raises(RuntimeError, match="DeepSeek Responses") as error:
        _stream(client)
    assert error.value.usage == USAGE
    assert client.closed == 1


def test_precancelled_stream_closes_already_created_client_without_request():
    cancel = threading.Event()
    cancel.set()
    client = FakeClient()

    with pytest.raises(RuntimeError, match="DeepSeek Responses request cancelled"):
        _stream(client, cancel_event=cancel)

    assert client.closed == 1


def test_cancel_watcher_closes_client_and_joins():
    entered = threading.Event()
    cancel = threading.Event()
    result = []
    client = FakeClient()

    class BlockingStream:
        def __iter__(self):
            return self

        def __next__(self):
            entered.set()
            while client.closed == 0:
                time.sleep(0.005)
            raise OSError("stream closed")

        def close(self):
            return None

    client.responses.create = lambda **_payload: BlockingStream()

    def request():
        try:
            _stream(client, cancel_event=cancel)
        except Exception as exc:
            result.append(exc)

    worker = threading.Thread(target=request)
    worker.start()
    assert entered.wait(1)
    cancel.set()
    worker.join(2)

    assert not worker.is_alive()
    assert client.closed == 1
    assert len(result) == 1 and "cancelled" in str(result[0])
    assert not any(thread.name == "responses-native-cancel" for thread in threading.enumerate())
