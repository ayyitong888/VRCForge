from copy import deepcopy
from types import SimpleNamespace

import pytest

from provider_runtime_adapters import OpenAIResponsesAdapter, ProviderResponseError


def test_openai_responses_native_transport_preserves_openai_reasoning_and_budget():
    payloads = []
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "cipher"},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read_file",
         "arguments": '{"path":"a.txt"}', "status": "completed"},
    ]
    response = {"status": "completed", "model": "test-model", "output": output}

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        client = SimpleNamespace()
        client.responses = SimpleNamespace(create=lambda **payload: payloads.append(deepcopy(payload)) or iter([
            {"type": "response.completed", "response": response},
        ]))
        client.close = lambda: None
        return client

    adapter = OpenAIResponsesAdapter(api_key="safe-key", base_url="https://example.invalid",
                                     client_factory=factory, max_retries=0)
    result = adapter.send_native_request(
        model="test-model",
        messages=[{"role": "system", "content": "policy"}, {"role": "user", "content": "read"}],
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}],
        max_output_tokens=8192,
    )

    assert result.finish_reason == "tool_calls"
    assert result.assistant_message["tool_calls"][0]["id"] == "call_1"
    assert result.assistant_message["responses_output"] == output
    assert payloads[0]["input"][0]["role"] == "developer"
    assert payloads[0]["tools"][0]["name"] == "read_file"
    assert payloads[0]["max_output_tokens"] == 8192
    assert payloads[0]["store"] is False and payloads[0]["stream"] is True


def test_openai_native_empty_completion_is_scoped_to_hidden_continuation():
    from agent_output_truncation import CONTINUE_MESSAGE

    responses = [
        {"type": "response.completed", "response": {"status": "completed", "output": []}},
        {"type": "response.completed", "response": {"status": "completed", "output": []}},
    ]

    def factory(**_kwargs):
        response = responses.pop(0)
        client = SimpleNamespace()
        client.responses = SimpleNamespace(create=lambda **_payload: iter([response]))
        client.close = lambda: None
        return client

    adapter = OpenAIResponsesAdapter(api_key="safe-key", base_url="https://example.invalid", client_factory=factory)
    result = adapter.send_native_request(
        model="test-model",
        messages=[
            {"role": "assistant", "content": "partial"},
            {"role": "user", "content": CONTINUE_MESSAGE, "hidden": True},
        ],
        tools=[],
    )
    assert result.finish_reason == "stop"
    assert result.assistant_message == {"role": "assistant", "content": None, "tool_calls": []}

    with pytest.raises(ProviderResponseError, match="without a message or tool call"):
        adapter.send_native_request(
            model="test-model", messages=[{"role": "user", "content": CONTINUE_MESSAGE}], tools=[],
        )

