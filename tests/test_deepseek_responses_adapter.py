from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

import dashboard_server
from model_provider_adapters import ProviderCredentialError
from provider_runtime_adapters import DeepSeekResponsesAdapter, ProviderRuntimeRequest
from vrchat_blendshape_agent import (
    Settings,
    build_openai_compatible_request_payload,
    request_deepseek_responses_plan_with_metadata,
    request_llm_plan_with_metadata,
)


class FakeResponses:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, list) and self.result and isinstance(self.result[0], dict) and "type" not in self.result[0]:
            return self.result.pop(0)
        return self.result


class FakeClient:
    def __init__(self, result):
        self.responses = FakeResponses(result)


def settings(*, api_type: str | None = "responses") -> Settings:
    return Settings(
        llm_provider="deepseek", llm_api_key="safe-key", llm_base_url="https://api.deepseek.example",
        llm_model="deepseek-v4-flash", llm_api_key_env="", gemini_thinking_level="low",
        unity_mcp_command=[], unity_mcp_host="127.0.0.1", unity_mcp_port=0, unity_mcp_instance="",
        unity_mcp_retries=0, unity_mcp_retry_backoff_seconds=0, unity_mcp_timeout_seconds=0,
        export_tool_name="", execute_tool_name="", export_path=__import__("pathlib").Path("x"), min_confidence=0.5,
        llm_api_type=api_type,
    )


def adapter_for(result):
    holder: dict[str, FakeClient] = {}
    def factory(**_kwargs):
        holder["client"] = FakeClient(result)
        return holder["client"]
    return DeepSeekResponsesAdapter(api_key="safe-key", base_url="https://api.deepseek.example", client_factory=factory), holder


def test_nonstream_message_sends_stateless_input_and_safe_payload() -> None:
    result = SimpleNamespace(output_text='{"reply":"ok"}', output=[], usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5))
    adapter, holder = adapter_for(result)
    response = adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="full history", instructions="system", reasoning_effort="low", max_output_tokens=12))
    call = holder["client"].responses.calls[0]
    assert response.text == '{"reply":"ok"}'
    assert call["input"] == "full history"
    assert call["instructions"] == "system"
    assert call["store"] is False and call["reasoning"] == {"effort": "low"}
    assert call["tools"][0]["name"] == "vrcforge_plan_action"
    assert "safe-key" not in str(call)


def test_probe_modes_are_tool_free_and_structured_when_requested() -> None:
    result = {"output_text": "plain", "output": [], "usage": {}}
    client = FakeClient(result)
    adapter = DeepSeekResponsesAdapter(
        api_key="safe-key", base_url="https://api.deepseek.example", client_factory=lambda **_kwargs: client
    )
    plain = adapter.send_request(ProviderRuntimeRequest(
        model="deepseek-v4-flash", prompt="p", instructions="s", mode="probe"
    ))
    structured = adapter.send_request(ProviderRuntimeRequest(
        model="deepseek-v4-flash", prompt="p", instructions="s", mode="probe", structured_output=True
    ))
    plain_call, structured_call = client.responses.calls
    assert plain.text == "plain" and "tools" not in plain_call and "tool_choice" not in plain_call
    assert structured_call["text"] == {"format": {"type": "json_object"}}
    assert "tools" not in structured_call and "tool_choice" not in structured_call


def test_nonstream_empty_completion_retries_once_then_succeeds() -> None:
    adapter, holder = adapter_for([
        {"output": [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": "thinking"}]}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}]},
    ])

    response = adapter.send_request(ProviderRuntimeRequest(
        model="deepseek-v4-flash",
        prompt="p",
        instructions="s",
        mode="probe",
        structured_output=True,
    ))

    assert response.text == '{"ok":true}'
    assert len(holder["client"].responses.calls) == 2


def test_nonstream_empty_completion_retries_only_once() -> None:
    adapter, holder = adapter_for([
        {"output": [{"type": "reasoning"}]},
        {"output": [{"type": "reasoning"}]},
    ])

    with pytest.raises(RuntimeError, match="no message or planner action"):
        adapter.send_request(ProviderRuntimeRequest(
            model="deepseek-v4-flash",
            prompt="p",
            instructions="s",
            mode="probe",
        ))

    assert len(holder["client"].responses.calls) == 2


@contextmanager
def _loopback_responses_server(records: list[dict]):
    """Test-only 127.0.0.1 server: fixed Bearer auth, fixture-owned thread, finally shutdown/join."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):  # pragma: no cover - keep test output secret-free.
            return

        def do_POST(self):  # noqa: N802 - stdlib handler entry point.
            length = int(self.headers.get("Content-Length", "0"))
            records.append({"path": self.path, "authorization": self.headers.get("Authorization", ""), "body": json.loads(self.rfile.read(length))})
            if self.path != "/v1/responses" or self.headers.get("Authorization") != "Bearer test-loopback-key":
                self.send_response(401)
                self.end_headers()
                return
            stream = bool(records[-1]["body"].get("stream"))
            self.send_response(200)
            if stream:
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                final = {
                    "id": "resp-loopback", "object": "response", "created_at": 0, "model": "deepseek-v4-flash",
                    "output": [{"id": "msg-loopback", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "streamed"}]}],
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                }
                for event in (
                    {"type": "response.output_text.delta", "delta": "streamed"},
                    {"type": "response.completed", "response": final},
                ):
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return
            response = {
                "id": "resp-loopback", "object": "response", "created_at": 0, "model": "deepseek-v4-flash",
                "output": [{"id": "msg-loopback", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "plain"}]}],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, name="test-deepseek-responses-loopback", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_installed_openai_sdk_uses_responses_nonstream_and_semantic_sse_only() -> None:
    pytest.importorskip("openai")
    records: list[dict] = []
    with _loopback_responses_server(records) as base_url:
        adapter = DeepSeekResponsesAdapter(api_key="test-loopback-key", base_url=base_url)
        plain = adapter.send_request(ProviderRuntimeRequest(
            model="deepseek-v4-flash", prompt="complete history", instructions="system instructions", mode="probe"
        ))
        chunks: list[str] = []
        streamed = adapter.send_request(ProviderRuntimeRequest(
            model="deepseek-v4-flash", prompt="complete history", instructions="system instructions",
            mode="probe", stream_callback=chunks.append,
        ))
    assert plain.text == "plain" and plain.usage["total_tokens"] == 5
    assert streamed.text == "streamed" and chunks == ["streamed"] and streamed.usage["input_tokens"] == 3
    assert len(records) == 2
    assert all(record["path"] == "/v1/responses" for record in records)
    assert all(record["authorization"] == "Bearer test-loopback-key" for record in records)
    assert all(record["body"]["input"] == "complete history" for record in records)
    assert all(record["body"]["instructions"] == "system instructions" for record in records)
    assert "[DONE]" not in str(records)
    assert "test-loopback-key" not in str({"text": plain.text, "stream": streamed.text, "usage": streamed.usage})


@contextmanager
def _loopback_models_server(records: list[dict]):
    """Test-only model endpoint with scoped lifecycle and fixed bearer auth."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):  # pragma: no cover - fixture keeps key out of test output.
            return

        def do_GET(self):  # noqa: N802 - stdlib handler entry point.
            records.append({"path": self.path, "authorization": self.headers.get("Authorization", "")})
            if self.path != "/v1/models" or self.headers.get("Authorization") != "Bearer test-models-key":
                self.send_response(401)
                self.end_headers()
                return
            payload = json.dumps({
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-pro", "object": "model"},
                    {"id": "deepseek-v4-flash", "object": "model", "context_window": 128000},
                ],
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, name="test-deepseek-models-loopback", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_installed_openai_sdk_lists_models_at_configured_base_url_with_bearer_and_parses_models() -> None:
    """Photo 4 regression: no locale-dependent HTTP header encoding reaches `/models`."""

    pytest.importorskip("openai")
    records: list[dict] = []
    with _loopback_models_server(records) as base_url:
        models = dashboard_server.fetch_openai_compatible_models(
            dashboard_server.DashboardApiConfig(
                provider="deepseek",
                api_key="test-models-key",
                base_url=base_url,
                model="deepseek-v4-flash",
            )
        )
    assert records == [{"path": "/v1/models", "authorization": "Bearer test-models-key"}]
    assert models == [
        {"id": "deepseek-v4-flash", "label": "deepseek-v4-flash", "contextWindow": 128000},
        {"id": "deepseek-v4-pro", "label": "deepseek-v4-pro"},
    ]
    assert "test-models-key" not in str(models)


def test_function_call_is_normalized_but_not_executed() -> None:
    result = {"output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "brief"}]},
        {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"skill","skill_tool":"vrc_list"}'},
    ], "usage": {"input_tokens": 1, "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 1}}}
    adapter, _holder = adapter_for(result)
    response = adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s"))
    assert response.text == '{"action": "skill", "skill_tool": "vrc_list"}'
    assert response.usage["reasoning_tokens"] == 1


@pytest.mark.parametrize("item", [
    {"type": "function_call", "name": "other", "arguments": '{"action":"reply"}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": "not json"},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"write"}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"reply","reply":"ok","extra":true}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"reply","reply":5}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"reply"}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"skill","skill_tool":""}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"skill","skill_tool":"x","skill_params":[]}'},
    {"type": "function_call", "name": "vrcforge_plan_action", "arguments": '{"action":"shell","shell_command":""}'},
])
def test_invalid_function_call_fails_closed(item) -> None:
    adapter, _holder = adapter_for({"output": [item]})
    with pytest.raises(RuntimeError):
        adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s"))


def test_stream_requires_completed_event_and_propagates_text() -> None:
    final = {"output_text": '{"reply":"ok"}', "output": [], "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}
    adapter, _holder = adapter_for([
        {"type": "response.output_text.delta", "delta": '{"reply":"'},
        {"type": "response.output_text.delta", "delta": 'ok"}'},
        {"type": "response.completed", "response": final},
    ])
    chunks: list[str] = []
    response = adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s", stream_callback=chunks.append))
    assert response.text == '{"reply":"ok"}' and chunks == ['{"reply":"', 'ok"}']
    assert response.usage["total_tokens"] == 6


@pytest.mark.parametrize("events", [
    [{"type": "response.failed"}],
    [{"type": "response.incomplete"}],
    [{"type": "response.output_text.delta", "delta": "x"}],
])
def test_stream_failure_or_missing_final_never_falls_back(events) -> None:
    adapter, holder = adapter_for(events)
    with pytest.raises(RuntimeError):
        adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s", stream_callback=lambda _x: None))
    assert len(holder["client"].responses.calls) == 1


def test_streamed_function_call_is_normalized() -> None:
    adapter, _holder = adapter_for([
        {"type": "response.output_item.added", "item": {"type": "function_call", "name": "vrcforge_plan_action"}},
        {"type": "response.function_call_arguments.delta", "delta": '{"action":"reply","reply":"你好"}'},
        {"type": "response.completed", "response": {"output": [], "usage": {}}},
    ])
    response = adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s", stream_callback=lambda _x: None))
    assert response.text == '{"action": "reply", "reply": "你好"}'


def test_completed_response_parses_final_message_when_no_delta_arrived() -> None:
    adapter, _holder = adapter_for([
        {"type": "response.completed", "response": {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"reply":"final"}'}]}],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }},
    ])
    response = adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s", stream_callback=lambda _x: None))
    assert response.text == '{"reply":"final"}'


def test_completed_response_without_message_or_action_fails_closed() -> None:
    adapter, _holder = adapter_for([
        {"type": "response.completed", "response": {"output": [], "usage": {}}},
    ])
    with pytest.raises(RuntimeError, match="without a message or planner action"):
        adapter.send_request(ProviderRuntimeRequest(
            model="deepseek-v4-flash",
            prompt="p",
            instructions="s",
            mode="probe",
            stream_callback=lambda _x: None,
        ))


def test_images_are_rejected_before_client_construction() -> None:
    created = []
    adapter = DeepSeekResponsesAdapter(api_key="safe-key", base_url="https://api.deepseek.example", client_factory=lambda **kwargs: created.append(kwargs))
    with pytest.raises(RuntimeError):
        adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s", reference_image_paths=("a.png",)))
    assert created == []


@pytest.mark.parametrize("invalid_key", ["bad key", "bad\nkey", "bad\rkey", "bad\tkey", "bad\ue00dkey"])
def test_responses_adapter_rejects_non_visible_ascii_before_client_factory_and_never_echoes(
    invalid_key: str,
) -> None:
    created: list[dict] = []

    with pytest.raises(ProviderCredentialError) as error:
        DeepSeekResponsesAdapter(
            api_key=invalid_key,
            base_url="https://api.deepseek.example",
            client_factory=lambda **kwargs: created.append(kwargs),
        )

    assert created == []
    assert invalid_key not in str(error.value)


def test_runtime_auto_dispatches_responses_without_chat(monkeypatch) -> None:
    def factory(self, request):
        return SimpleNamespace(text='{"reply":"ok"}', usage={}, reasoning_summary=[])
    monkeypatch.setattr(DeepSeekResponsesAdapter, "send_request", factory)
    def no_chat(*_args, **_kwargs):
        raise AssertionError("Chat must not be called for auto Flash Responses")
    monkeypatch.setattr("vrchat_blendshape_agent.request_openai_compatible_plan_with_metadata", no_chat)
    response = request_llm_plan_with_metadata(settings(api_type="auto"), "p")
    assert response.text == '{"reply":"ok"}'


def test_runtime_missing_api_type_migrates_flash_to_responses_without_chat(monkeypatch) -> None:
    def factory(self, request):
        return SimpleNamespace(text='{"reply":"ok"}', usage={}, reasoning_summary=[])
    monkeypatch.setattr(DeepSeekResponsesAdapter, "send_request", factory)
    monkeypatch.setattr(
        "vrchat_blendshape_agent.request_openai_compatible_plan_with_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Chat must not be called for migrated Flash Responses")
        ),
    )

    response = request_llm_plan_with_metadata(settings(api_type=None), "p")

    assert response.text == '{"reply":"ok"}'


def test_direct_wrapper_does_not_echo_key_on_error() -> None:
    with pytest.raises(RuntimeError) as error:
        request_deepseek_responses_plan_with_metadata(settings(), "p", reference_image_paths=["x.png"])
    assert "safe-key" not in str(error.value)


@pytest.mark.parametrize("model,level,expected_effort", [
    ("deepseek-v4-flash", "low", "low"),
    ("deepseek-v4-pro", "max", "max"),
])
def test_deepseek_v4_chat_payload_has_reasoning_effort_and_thinking(model, level, expected_effort) -> None:
    configured = settings(api_type="chat_completions")
    configured.llm_model = model
    configured.gemini_thinking_level = level
    payload = build_openai_compatible_request_payload(configured, "p")
    assert payload["reasoning_effort"] == expected_effort
    assert payload["extra_body"] == {"thinking": {"type": "enabled"}}


def test_deepseek_v4_chat_none_only_disables_thinking() -> None:
    configured = settings(api_type="chat_completions")
    configured.gemini_thinking_level = "none"
    payload = build_openai_compatible_request_payload(configured, "p")
    assert "reasoning_effort" not in payload
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}


def test_legacy_deepseek_chat_payload_keeps_existing_thinking_policy() -> None:
    configured = settings(api_type="chat_completions")
    configured.llm_model = "deepseek-chat"
    configured.gemini_thinking_level = "high"
    payload = build_openai_compatible_request_payload(configured, "p")
    assert "reasoning_effort" not in payload
    assert payload["extra_body"] == {"thinking": {"type": "enabled"}}


def test_reasoning_only_response_fails_closed() -> None:
    adapter, _holder = adapter_for({"output": [{"type": "reasoning", "summary": "brief"}]})
    with pytest.raises(RuntimeError):
        adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s"))


def test_request_client_inherits_configured_retry_limit() -> None:
    result = {"output_text": '{"reply":"ok"}', "output": [], "usage": {}}
    created: list[dict] = []
    def factory(**kwargs):
        created.append(kwargs)
        return FakeClient(result)
    configured = settings()
    configured.llm_sdk_max_retries = 2
    response = request_deepseek_responses_plan_with_metadata(configured, "p", client_factory=factory)
    assert response.text == '{"reply":"ok"}'
    assert created == [{"api_key": "safe-key", "base_url": "https://api.deepseek.example", "max_retries": 2}]


@pytest.mark.parametrize(
    ("max_retries", "expected_kwargs"),
    [
        (None, {"api_key": "safe-key", "base_url": "https://api.deepseek.example"}),
        (0, {"api_key": "safe-key", "base_url": "https://api.deepseek.example", "max_retries": 0}),
        (3, {"api_key": "safe-key", "base_url": "https://api.deepseek.example", "max_retries": 3}),
    ],
)
def test_responses_adapter_passes_only_the_configured_sdk_retry_boundary(max_retries, expected_kwargs) -> None:
    created: list[dict] = []
    adapter = DeepSeekResponsesAdapter(
        api_key="safe-key",
        base_url="https://api.deepseek.example",
        client_factory=lambda **kwargs: created.append(kwargs) or FakeClient({"output_text": "ok", "output": []}),
        max_retries=max_retries,
    )

    response = adapter.send_request(ProviderRuntimeRequest(model="deepseek-v4-flash", prompt="p", instructions="s", mode="probe"))

    assert response.text == "ok"
    assert created == [expected_kwargs]


@pytest.mark.parametrize("invalid", [-1, True, "2"])
def test_adapter_rejects_invalid_retry_limit(invalid) -> None:
    with pytest.raises(ValueError, match="max retries"):
        DeepSeekResponsesAdapter(api_key="safe-key", base_url="https://api.deepseek.example", max_retries=invalid)
