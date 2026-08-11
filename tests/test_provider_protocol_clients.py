from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Iterator

import pytest

import dashboard_server
from provider_configuration_service import ProviderApiConfig


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
