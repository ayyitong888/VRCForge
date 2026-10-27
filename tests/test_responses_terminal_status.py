import json
from types import SimpleNamespace

import pytest

from provider_runtime_adapters import (
    DeepSeekResponsesAdapter,
    OpenAIResponsesAdapter,
    ProviderResponseError,
    ProviderRuntimeRequest,
)


@pytest.mark.parametrize("adapter_type", [OpenAIResponsesAdapter, DeepSeekResponsesAdapter])
@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress", "queued", "completed"])
@pytest.mark.parametrize("mode", ["planner", "probe"])
def test_nonstream_terminal_status_before_dispatch(adapter_type, status, mode):
    action = {"action": "skill", "skill_tool": "vrcforge_get_property", "skill_params": {}}
    response = {
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{"type": "function_call", "name": "vrcforge_plan_action", "arguments": json.dumps(action)}] if mode == "planner" else [],
        "output_text": "probe result",
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
    }
    calls = []
    closed = []

    def create(**kwargs):
        calls.append(kwargs)
        return response

    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: closed.append(True))
    adapter = adapter_type(api_key="fixture-key", base_url="https://provider.example/v1", client_factory=lambda **kwargs: client)
    request = ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="read the property", instructions="fixture", mode=mode)
    if status == "completed":
        result = adapter.send_request(request)
        if mode == "planner":
            assert json.loads(result.text) == action
        else:
            assert result.text == "probe result"
    else:
        with pytest.raises(ProviderResponseError, match="did not complete") as rejected:
            adapter.send_request(request)
        assert rejected.value.usage["total_tokens"] == 20
    assert len(calls) == 1
    assert closed == [True]
