from __future__ import annotations

import inspect
import threading
import sys
import time
import types
from types import SimpleNamespace

import pytest

from vrchat_blendshape_agent import (
    request_anthropic_plan_with_metadata,
    request_gemini_plan_with_metadata,
    request_vertex_ai_plan_with_metadata,
)


def _settings(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        llm_provider=provider,
        llm_api_key="key",
        llm_base_url="project=p;location=l" if provider == "vertexai" else "",
        llm_model="gemini-2.5-flash",
        llm_api_key_env="TEST_KEY",
        gemini_thinking_level="",
        llm_system_instruction="",
        llm_max_output_tokens=None,
        llm_sdk_max_retries=0,
        llm_api_type="messages" if provider == "anthropic" else "generate_content",
    )


class _CloseTrackingClient:
    def __init__(self, stream_factory):
        self._stream_factory = stream_factory
        self.close_count = 0
        self.closed = False
        self.models = self

    def close(self):
        self.close_count += 1
        self.closed = True

    def generate_content_stream(self, **_kwargs):
        return self._stream_factory()


def _thinking_chunk():
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(thought=True, text=None)]))]
    )


@pytest.mark.parametrize("provider", ["gemini", "vertexai"])
def test_google_thinking_only_stream_reports_activity_and_cancel_closes_client(monkeypatch, provider):
    cancel = __import__("threading").Event()
    activity = []
    client_holder = {}

    def stream():
        yield _thinking_chunk()
        cancel.set()
        time.sleep(0.15)
        yield _thinking_chunk()

    client = _CloseTrackingClient(stream)
    client_holder["client"] = client
    genai = types.ModuleType("google.genai")
    genai.Client = lambda **_kwargs: client
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    fn = request_gemini_plan_with_metadata if provider == "gemini" else request_vertex_ai_plan_with_metadata
    with pytest.raises(RuntimeError, match="cancelled"):
        fn(_settings(provider), "prompt", stream_callback=lambda _text: None,
           cancel_event=cancel, stream_activity_callback=activity.append)
    assert activity == [{"kind": "provider_activity"}]
    assert client.close_count >= 1
    assert client.closed is True


def test_messages_thinking_only_stream_reports_activity_and_cancel_closes_client(monkeypatch):
    cancel = __import__("threading").Event()
    activity = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(thinking="internal"))
            cancel.set()
            time.sleep(0.15)
            yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(thinking="late"))

        def get_final_message(self):
            return SimpleNamespace(content=[])

    class Client:
        close_count = 0

        def __init__(self):
            self.messages = self

        def stream(self, **_kwargs):
            return Stream()

        def close(self):
            self.close_count += 1

    client = Client()
    anthropic = __import__("anthropic")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kwargs: client)
    with pytest.raises(RuntimeError, match="cancelled"):
        request_anthropic_plan_with_metadata(_settings("anthropic"), "prompt", stream_callback=lambda _text: None,
                                             cancel_event=cancel, stream_activity_callback=activity.append)
    assert activity == [{"kind": "provider_activity"}]
    assert client.close_count >= 1


@pytest.mark.parametrize("provider", ["gemini", "vertexai", "anthropic"])
def test_provider_finally_close_does_not_mask_original_request_error(monkeypatch, provider):
    class BrokenClose:
        def close(self):
            raise RuntimeError("close failed")

    client = BrokenClose()
    if provider in {"gemini", "vertexai"}:
        genai = types.ModuleType("google.genai")
        genai.Client = lambda **_kwargs: client
        google = types.ModuleType("google")
        google.genai = genai
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.genai", genai)
        client.models = client
        client.generate_content = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("request failed"))
        fn = request_gemini_plan_with_metadata if provider == "gemini" else request_vertex_ai_plan_with_metadata
    else:
        anthropic = __import__("anthropic")
        monkeypatch.setattr(anthropic, "Anthropic", lambda **_kwargs: client)
        client.messages = client
        client.create = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("request failed"))
        fn = request_anthropic_plan_with_metadata
    with pytest.raises(RuntimeError, match="request failed"):
        fn(_settings(provider), "prompt")


@pytest.mark.parametrize("provider", ["gemini", "vertexai", "anthropic"])
def test_blocked_provider_call_is_released_by_cancel_watcher(monkeypatch, provider):
    cancel = threading.Event()
    released = threading.Event()

    class BlockingClient:
        def __init__(self):
            self.close_count = 0
            self.models = self
            self.messages = self

        def close(self):
            self.close_count += 1
            released.set()

        def generate_content(self, **_kwargs):
            assert released.wait(1.0)
            raise RuntimeError("transport closed")

        def create(self, **_kwargs):
            assert released.wait(1.0)
            raise RuntimeError("transport closed")

    client = BlockingClient()
    if provider in {"gemini", "vertexai"}:
        genai = types.ModuleType("google.genai")
        genai.Client = lambda **_kwargs: client
        google = types.ModuleType("google")
        google.genai = genai
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.genai", genai)
        fn = request_gemini_plan_with_metadata if provider == "gemini" else request_vertex_ai_plan_with_metadata
    else:
        anthropic = __import__("anthropic")
        monkeypatch.setattr(anthropic, "Anthropic", lambda **_kwargs: client)
        fn = request_anthropic_plan_with_metadata

    timer = threading.Timer(0.05, cancel.set)
    timer.start()
    try:
        with pytest.raises(RuntimeError, match="(cancelled|transport closed|request failed)"):
            fn(_settings(provider), "prompt", cancel_event=cancel)
    finally:
        timer.cancel()
        timer.join(timeout=1)
    assert client.close_count >= 1
    assert released.is_set()


def test_messages_lane_accepts_runtime_cancel_and_activity_controls() -> None:
    parameters = inspect.signature(request_anthropic_plan_with_metadata).parameters
    assert "cancel_event" in parameters
    assert "stream_activity_callback" in parameters


def test_gemini_lane_accepts_runtime_cancel_and_activity_controls() -> None:
    parameters = inspect.signature(request_gemini_plan_with_metadata).parameters
    assert "cancel_event" in parameters
    assert "stream_activity_callback" in parameters


def test_vertex_lane_accepts_runtime_cancel_and_activity_controls() -> None:
    parameters = inspect.signature(request_vertex_ai_plan_with_metadata).parameters
    assert "cancel_event" in parameters
    assert "stream_activity_callback" in parameters
