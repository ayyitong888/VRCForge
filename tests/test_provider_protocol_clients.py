from __future__ import annotations

import json
import sys
import threading
import types
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Iterator

import pytest

import dashboard_server
from vrchat_blendshape_agent import (
    Settings,
    build_openai_compatible_request_payload,
    request_llm_plan_with_metadata,
    request_openai_compatible_plan_with_metadata,
)
from provider_configuration_service import ProviderApiConfig


def _native_chat_settings(api_type: str | None = "chat_completions") -> Settings:
    return Settings(
        llm_provider="custom",
        llm_api_key="native-key",
        llm_base_url="http://native.test/v1",
        llm_model="native-model",
        llm_api_key_env="",
        gemini_thinking_level="",
        unity_mcp_command=[],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=0,
        unity_mcp_instance="",
        unity_mcp_retries=0,
        unity_mcp_retry_backoff_seconds=0.0,
        unity_mcp_timeout_seconds=1,
        export_tool_name="",
        execute_tool_name="",
        export_path=__import__("pathlib").Path("export.json"),
        min_confidence=0.0,
        llm_api_type=api_type,
    )


def test_native_post_tool_transcript_omits_empty_assistant_tool_calls() -> None:
    transcript = [
        {"role": "user", "content": "read the project version"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "checking the project file",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "2022.3.22f1"},
        {"role": "assistant", "content": None, "reasoning_content": "tool result received", "tool_calls": []},
    ]

    payload = build_openai_compatible_request_payload(
        _native_chat_settings(), "continue", native_messages=transcript, native_tools=[]
    )

    assert payload["messages"][1]["tool_calls"] == transcript[1]["tool_calls"]
    assert payload["messages"][1]["reasoning_content"] == "checking the project file"
    assert payload["messages"][3] == {
        "role": "assistant", "content": None, "reasoning_content": "tool result received"
    }


def test_native_chat_requires_explicit_chat_protocol_and_preserves_receipt() -> None:
    with pytest.raises(ValueError, match="chat_completions"):
        request_llm_plan_with_metadata(
            _native_chat_settings(None),
            "continue",
            native_tools=[{"type": "function", "function": {"name": "read"}}],
        )


def test_native_chat_ordinary_response_keeps_tool_receipt_private(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {
                "id": "chat-native-1",
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "opaque-provider-thinking",
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"path":"x"}'},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }

    class FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self) -> None:
            return

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    tool = {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}
    response = request_openai_compatible_plan_with_metadata(
        _native_chat_settings(), "continue", native_tools=[tool]
    )

    assert calls[0]["tools"] == [tool]
    assert "response_format" not in calls[0]
    assert response.finish_reason == "tool_calls"
    assert "id" not in response.assistant_message
    assert response.assistant_message["tool_calls"][0]["id"] == "call-1"
    assert response.assistant_message["reasoning_content"] == "opaque-provider-thinking"
    assert response.reasoning["itemCount"] == 0


def test_native_chat_stream_assembles_indexed_tool_fragments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Completions:
        def create(self, **kwargs: object) -> object:
            if not kwargs.get("stream"):
                raise AssertionError("native stream test must use streaming")
            return iter([
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-stream", "function": {"name": "read", "arguments": '{"pa'}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th":"x"}'}}]}, "finish_reason": "tool_calls"}]},
            ])

    class FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self) -> None:
            return

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    response = request_openai_compatible_plan_with_metadata(
        _native_chat_settings(), "continue", stream_callback=lambda _text: None,
        native_tools=[{"type": "function", "function": {"name": "read"}}],
    )
    call = response.assistant_message["tool_calls"][0]
    assert call["id"] == "call-stream"
    assert call["function"]["name"] == "read"
    assert call["function"]["arguments"] == '{"path":"x"}'
    assert response.finish_reason == "tool_calls"


@pytest.mark.parametrize("cancel_at_end", [False, True])
def test_native_compatible_stream_retains_exact_replay_and_checks_terminal_cancel(monkeypatch, cancel_at_end):
    cancel = threading.Event()
    records, activity = [], []
    tool = {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=self)

        def create(self, **kwargs):
            records.append(kwargs)
            if len(records) == 1:
                raise RuntimeError("stream_options is not supported")
            if len(records) > 2:
                return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}]}

            def stream():
                yield {"choices": [{"delta": {"reasoning_content": "synthetic-"}, "finish_reason": None}]}
                yield {"choices": [{"delta": {"reasoning_content": "replay", "tool_calls": [
                    {"index": 0, "id": "paired", "function": {"name": "read", "arguments": "{}"}}
                ]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
                if cancel_at_end:
                    cancel.set()
            return stream()

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    options = dict(stream_callback=lambda _: None, cancel_event=cancel,
                   stream_activity_callback=activity.append, native_tools=[tool],
                   native_messages=[{"role": "user", "content": "read"}])
    if cancel_at_end:
        with pytest.raises(RuntimeError, match="cancelled"):
            request_openai_compatible_plan_with_metadata(_native_chat_settings(), "", **options)
        assert len(records) == 2
        return
    result = request_openai_compatible_plan_with_metadata(_native_chat_settings(), "", **options)
    assert result.assistant_message["reasoning_content"] == "synthetic-replay"
    assert result.usage["totalTokens"] == 10
    assert {"kind": "tool_call_activity"} in activity
    transcript = [*options["native_messages"], result.assistant_message,
                  {"role": "tool", "tool_call_id": "paired", "content": '{"ok":true}'}]
    request_openai_compatible_plan_with_metadata(_native_chat_settings(), "", native_messages=transcript, native_tools=[tool])
    assert records[-1]["messages"] == transcript
    assert "synthetic-replay" not in repr(result)


@pytest.mark.parametrize("partial", [
    {"tool_calls": [{"index": 0, "id": "unfinished", "function": {"name": "read", "arguments": "{"}}]},
    {"reasoning_content": "synthetic-partial"},
])
def test_native_compatible_partial_stream_is_never_reissued(monkeypatch, partial):
    records = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=self)

        def create(self, **kwargs):
            records.append(kwargs)
            if len(records) == 1:
                raise RuntimeError("stream_options is not supported")
            if len(records) > 2:
                raise AssertionError("A partially generated native call must not be reissued")

            def stream():
                yield {"choices": [{"delta": partial, "finish_reason": None}]}
                raise RuntimeError("stream not supported after partial generation")
            return stream()

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    with pytest.raises(RuntimeError, match="partial generation"):
        request_openai_compatible_plan_with_metadata(_native_chat_settings(), "", native_tools=[], stream_callback=lambda _: None)
    assert len(records) == 2


@pytest.mark.parametrize("finish_reason", [None, "length"])
def test_native_chat_stream_never_returns_receipt_for_eof_or_truncation(
    monkeypatch: pytest.MonkeyPatch, finish_reason: str | None,
) -> None:
    class Completions:
        def create(self, **kwargs: object) -> object:
            return iter([{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "partial", "function": {"name": "read", "arguments": "{"}}]}, "finish_reason": finish_reason}]}])

    class FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self) -> None:
            return

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    with pytest.raises(RuntimeError, match="Native Chat"):
        request_openai_compatible_plan_with_metadata(
            _native_chat_settings(), "continue", stream_callback=lambda _text: None,
            native_tools=[{"type": "function", "function": {"name": "read"}}],
        )


def test_native_chat_stream_cancelled_tool_only_turn_has_no_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()

    class Completions:
        def create(self, **kwargs: object) -> object:
            def stream() -> object:
                yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "cancelled", "function": {"name": "read"}}]}, "finish_reason": None}]}
                cancel.set()
                yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
            return stream()

    class FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self) -> None:
            return

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    with pytest.raises(RuntimeError, match="cancelled"):
        request_openai_compatible_plan_with_metadata(
            _native_chat_settings(), "continue", stream_callback=lambda _text: None,
            cancel_event=cancel,
            native_tools=[{"type": "function", "function": {"name": "read"}}],
        )


@contextmanager
def _custom_protocol_server(records: list[dict[str, str]]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            records.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "anthropic": self.headers.get("x-api-key", ""),
                    "google": self.headers.get("x-goog-api-key", ""),
                }
            )
            path = self.path.split("?", 1)[0]
            if path == "/v1/responses":
                payload = {
                    "id": "resp-test",
                    "object": "response",
                    "created_at": 0,
                    "model": "site-model",
                    "output": [
                        {
                            "id": "msg-test",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "responses-ok"}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }
            elif path == "/v1/chat/completions":
                payload = {
                    "id": "chat-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "site-model",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "chat-ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            elif path == "/v1/messages":
                payload = {
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "model": "site-model",
                    "content": [{"type": "text", "text": "messages-ok"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            elif path.endswith("/models/site-model:generateContent"):
                payload = {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "generate-ok"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, name="provider-protocol-clients", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@contextmanager
def _cross_origin_redirect_servers(
    source_records: list[str],
    target_records: list[str],
) -> Iterator[str]:
    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            target_records.append(self.headers.get("Authorization", "") or self.headers.get("x-api-key", "") or self.headers.get("x-goog-api-key", ""))
            self.send_response(200)
            self.end_headers()

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    target_thread = Thread(target=target.serve_forever, name="provider-redirect-target", daemon=True)
    target_thread.start()

    class SourceHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            source_records.append(self.path)
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}{self.path}")
            self.end_headers()

    source = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
    source_thread = Thread(target=source.serve_forever, name="provider-redirect-source", daemon=True)
    source_thread.start()
    try:
        yield f"http://127.0.0.1:{source.server_port}/v1"
    finally:
        source.shutdown()
        source_thread.join(timeout=5)
        source.server_close()
        target.shutdown()
        target_thread.join(timeout=5)
        target.server_close()


def test_custom_site_four_protocol_clients_use_only_the_configured_origin() -> None:
    pytest.importorskip("openai")
    pytest.importorskip("anthropic")
    pytest.importorskip("google.genai")
    records: list[dict[str, str]] = []
    with _custom_protocol_server(records) as base_url:
        results = {}
        for api_type in ("responses", "chat_completions", "messages", "generate_content"):
            results[api_type] = dashboard_server.PROVIDER_TEXT_PROBE.probe(
                ProviderApiConfig(
                    provider="custom",
                    api_key="test-custom-key",
                    base_url=base_url,
                    model="site-model",
                    api_type=api_type,
                ),
                "Return one short marker.",
            )

    assert results == {
        "responses": "responses-ok",
        "chat_completions": "chat-ok",
        "messages": "messages-ok",
        "generate_content": "generate-ok",
    }
    assert len(records) == 4
    assert all(record["path"].startswith("/v1/") for record in records)
    assert "test-custom-key" not in str(results)


def test_custom_protocol_clients_do_not_follow_cross_origin_redirects_with_key() -> None:
    source_records: list[str] = []
    target_records: list[str] = []
    errors: list[str] = []
    with _cross_origin_redirect_servers(source_records, target_records) as base_url:
        for api_type in ("responses", "chat_completions", "messages", "generate_content"):
            with pytest.raises(Exception) as exc_info:
                dashboard_server.PROVIDER_TEXT_PROBE.probe(
                    ProviderApiConfig(
                        provider="custom",
                        api_key="redirect-test-key",
                        base_url=base_url,
                        model="site-model",
                        api_type=api_type,
                    ),
                    "Return one short marker.",
                )
            errors.append(str(exc_info.value))

    assert len(source_records) == 4
    assert target_records == []
    assert "redirect-test-key" not in str(errors)
