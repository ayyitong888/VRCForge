import copy
import json
import threading
from types import SimpleNamespace

import pytest

from provider_runtime_adapters import DeepSeekResponsesAdapter, ProviderResponseError


TOOLS = [{"type": "function", "function": {
    "name": "call_tool", "description": "Call an exposed read tool.",
    "parameters": {"type": "object", "properties": {}},
}}]
REASONING = {"type": "reasoning", "id": "r1", "summary": [],
             "content": [{"type": "reasoning_text", "text": "fixture reasoning"}]}
CALL = {"type": "function_call", "id": "f1", "call_id": "c1",
        "name": "call_tool", "arguments": "{}"}
MESSAGE = {"type": "message", "role": "assistant", "id": "m1",
           "content": [{"type": "output_text", "text": "done", "annotations": []}]}


def make_adapter(outputs):
    payloads, clients = [], []

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        output = outputs.pop(0)
        response = {"status": "completed", "output": output,
                    "usage": {"input_tokens": 10000, "output_tokens": 3,
                              "input_tokens_details": {"cached_tokens": 8192}}}
        client = SimpleNamespace(closed=0)

        def create(**payload):
            payloads.append(copy.deepcopy(payload))
            return iter([{"type": "response.completed", "response": response}])

        def close():
            client.closed += 1

        client.close = close
        client.responses = SimpleNamespace(create=create)
        clients.append(client)
        return client

    return DeepSeekResponsesAdapter(api_key="fixture", base_url="https://fixture.invalid",
                                   client_factory=factory, max_retries=0), payloads, clients


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def test_native_transport_replays_complete_history_without_changing_prefix_or_tools():
    adapter, payloads, clients = make_adapter([[REASONING, CALL], [MESSAGE], [MESSAGE]])
    messages = [{"role": "system", "content": "fixed policy"}, {"role": "user", "content": "inspect"}]
    first = adapter.send_native_request(model="deepseek-flash", messages=messages, tools=TOOLS,
                                        reasoning_effort="low")
    assert first.assistant_message["responses_output"] == [REASONING, CALL]
    messages += [first.assistant_message, {"role": "tool", "tool_call_id": "c1", "content": "snapshot ref"}]
    second = adapter.send_native_request(model="deepseek-flash", messages=messages, tools=TOOLS,
                                         reasoning_effort="low")
    messages += [second.assistant_message, {"role": "user", "content": "follow up"}]
    adapter.send_native_request(model="deepseek-flash", messages=messages, tools=TOOLS, reasoning_effort="low")
    for previous, current in zip(payloads, payloads[1:]):
        assert encoded(previous["tools"]) == encoded(current["tools"])
        assert encoded(previous["input"]) == encoded(current["input"][:len(previous["input"])])
    assert payloads[0]["input"][0] == {"role": "system", "content": "fixed policy"}
    assert payloads[1]["input"][2:4] == [REASONING, CALL]
    assert all(payload["stream"] is True and payload["store"] is False for payload in payloads)
    assert all("include" not in payload and "max_output_tokens" not in payload for payload in payloads)
    assert all(client.closed == 1 for client in clients)
    assert first.usage["input_tokens_details"]["cached_tokens"] == 8192


def test_native_rejects_unadvertised_call_but_preserves_usage_without_retry():
    adapter, payloads, clients = make_adapter([[dict(CALL, name="not_exposed")]])
    with pytest.raises(ProviderResponseError) as caught:
        adapter.send_native_request(model="deepseek-flash", messages=[], tools=TOOLS)
    assert len(payloads) == 1 and clients[0].closed == 1
    assert caught.value.usage["input_tokens_details"]["cached_tokens"] == 8192


def test_cancelled_native_request_does_not_create_client():
    adapter, payloads, clients = make_adapter([])
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.send_native_request(model="deepseek-flash", messages=[], tools=TOOLS, cancel_event=cancelled)
    assert clients == payloads == []


def test_oauth_namespace_history_cannot_reach_deepseek_transport():
    adapter, payloads, clients = make_adapter([])
    message = {"role": "assistant", "content": None,
               "tool_calls": [{"id": "c1", "type": "function",
                               "function": {"name": "call_tool", "arguments": "{}"}}],
               "responses_output": [dict(CALL, namespace="vrcforge")]}
    with pytest.raises(ValueError, match="namespace"):
        adapter.send_native_request(model="deepseek-flash", messages=[message], tools=TOOLS)
    assert clients == payloads == []


def test_deepseek_native_transport_does_not_import_oauth_module(monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("chatgpt_integration"), "DeepSeek must not depend on removable OAuth"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    adapter, payloads, clients = make_adapter([[MESSAGE]])
    result = adapter.send_native_request(model="deepseek-flash", messages=[], tools=TOOLS)
    assert result.text == "done"
    assert len(payloads) == 1 and clients[0].closed == 1


def test_installed_sdk_preserves_plain_reasoning_and_replays_tool_results():
    import httpx
    from openai import OpenAI

    requests = []
    clients = []

    def handler(request):
        assert str(request.url) == "https://fixture.invalid/responses"
        assert request.headers["authorization"] == "Bearer fixture"
        requests.append(json.loads(request.content))
        output = [REASONING, CALL] if len(requests) == 1 else [MESSAGE]
        terminal = {"id": f"response-{len(requests)}", "object": "response", "created_at": 0,
                    "model": "deepseek-flash", "status": "completed", "output": output,
                    "usage": {"input_tokens": 10000, "output_tokens": 3, "total_tokens": 10003,
                              "input_tokens_details": {"cached_tokens": 8192},
                              "output_tokens_details": {"reasoning_tokens": 1}}}
        events = [{"type": "response.output_item.done", "output_index": i, "item": item}
                  for i, item in enumerate(output)]
        events.append({"type": "response.completed", "response": terminal})
        body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    def factory(**kwargs):
        client = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False))
        clients.append(client)
        return client

    adapter = DeepSeekResponsesAdapter(api_key="fixture", base_url="https://fixture.invalid",
                                       client_factory=factory, max_retries=0)
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": "inspect"}]
    first = adapter.send_native_request(model="deepseek-flash", messages=messages, tools=TOOLS)
    assert first.assistant_message["responses_output"][0]["content"] == REASONING["content"]
    messages += [first.assistant_message, {"role": "tool", "tool_call_id": "c1", "content": "snapshot"}]
    second = adapter.send_native_request(model="deepseek-flash", messages=messages, tools=TOOLS)
    assert second.text == "done"
    assert second.usage["input_tokens_details"]["cached_tokens"] == 8192
    assert requests[1]["input"][2:4] == first.assistant_message["responses_output"]
    assert all(client.is_closed() for client in clients)
