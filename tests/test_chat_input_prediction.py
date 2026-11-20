import asyncio
import json
import ssl
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import httpx
import openai
import pytest
from fastapi import FastAPI

import chat_input_prediction as prediction
from chatgpt_integration import service as chatgpt_service
from provider_configuration_service import ProviderApiConfig


START = "/api/app/chat/input-prediction"
CANCEL = START + "/cancel"


def test_validates_raw_suggestions():
    assert prediction.parse_suggestion("继续搞好") == "继续搞好"
    sentence = "Please compare the installation steps for both versions again."
    assert prediction.parse_suggestion(sentence) == sentence
    for invalid in (
        "",
        "下一步\n继续",
        "好的，我会继续",
        "我来修复",
        "Sure, I'll continue",
        "字" * 41,
        "one two three four five six seven eight nine ten eleven twelve thirteen",
        '{"suggestion":"继续"}',
    ):
        assert prediction.parse_suggestion(invalid) is None


def test_chatgpt_prediction_uses_lowest_advertised_effort_or_suppresses(monkeypatch):
    expected_model = "gpt-6.1-sol"
    account_id = "fake-account"
    advertised = {}
    calls = []

    def cached_model_reasoning_efforts(account, model):
        assert account == account_id
        assert model == expected_model
        return advertised.get(model)

    auth = SimpleNamespace(cached_model_reasoning_efforts=cached_model_reasoning_efforts)
    monkeypatch.setattr(chatgpt_service, "get_chatgpt_auth_service", lambda: auth)
    config = ProviderApiConfig("chatgpt", account_id, "https://api.openai.com/v1", expected_model, "responses", "high")
    configuration = SimpleNamespace(bind_runtime_config=lambda **kwargs: nullcontext(config))

    async def request_text(bound, settings, prompt, resolve_vertex):
        calls.append((bound.model, bound.thinking_level))
        return "继续"

    monkeypatch.setattr(prediction, "request_prediction_text", request_text)
    app = FastAPI()
    app.include_router(prediction.build_input_prediction_router(configuration, lambda _: None, None))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            async def predict():
                response = await client.post(START, json={
                    "request_id": str(uuid4()), "recent_user_messages": ["继续"],
                    "assistant_tail": "现已完成第一轮。",
                })
                return response.json()["suggestion"]

            assert await predict() is None
            assert calls == []
            advertised[expected_model] = {"high", "low", "xhigh"}
            assert await predict() == "继续"
            advertised[expected_model] = {"high", "none", "low"}
            assert await predict() == "继续"
            assert calls == [(expected_model, "low"), (expected_model, "none")]

    asyncio.run(run())


def test_private_prediction_routes_cancel_before_start_and_abort_upstream(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")

    async def run():
        upstream_started = asyncio.Event()
        upstream_disconnected = asyncio.Event()
        upstream_payloads = []

        async def handle_upstream(reader, writer):
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                content_length = next(int(value) for name, value in (
                    line.split(b":", 1) for line in headers.split(b"\r\n")[1:] if b":" in line
                ) if name.lower() == b"content-length")
                upstream_payloads.append(json.loads(await reader.readexactly(content_length)))
                upstream_started.set()
                assert await reader.read() == b""
                upstream_disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        async with await asyncio.start_server(handle_upstream, "127.0.0.1", 0) as upstream:
            port = upstream.sockets[0].getsockname()[1]
            config = ProviderApiConfig("openai", "test-key", f"http://127.0.0.1:{port}/v1", "gpt-5.2", "chat_completions", "high")
            configuration = SimpleNamespace(bind_runtime_config=lambda **kwargs: nullcontext(config))

            class Settings:
                llm_model = config.model
                llm_provider = config.provider

            def settings_factory(bound):
                assert bound.thinking_level == "none"
                instance = Settings()
                instance.gemini_thinking_level = bound.thinking_level
                return instance

            app = FastAPI()
            app.include_router(prediction.build_input_prediction_router(configuration, settings_factory, None))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                body = {
                    "request_id": str(uuid4()),
                    "recent_user_messages": [f"message {index}" for index in range(21)],
                    "assistant_tail": "x" * 2001,
                }
                await client.post(CANCEL, json={"request_id": body["request_id"]})
                assert (await client.post(START, json=body)).json() == {"suggestion": None}
                assert not upstream_started.is_set()
                body["request_id"] = str(uuid4())
                pending = asyncio.create_task(client.post(START, json=body))
                await asyncio.wait_for(upstream_started.wait(), timeout=8)
                await client.post(CANCEL, json={"request_id": body["request_id"]})
                await asyncio.wait_for(upstream_disconnected.wait(), timeout=2)
                assert (await pending).json() == {"suggestion": None}
                assert len(upstream_payloads) == 1
                request = upstream_payloads[0]
                assert request["reasoning_effort"] == "none"
                assert "tools" not in request
                assert "response_format" not in request
                prompt = json.loads(request["messages"][1]["content"])
                assert prompt["recent_user_messages"] == body["recent_user_messages"][-20:]
                assert len(prompt["assistant_tail"]) == 2000

    asyncio.run(run())


def test_prediction_reuses_tls_context_without_sharing_http_clients(monkeypatch):
    created_contexts = []
    created_clients = []
    original_client = httpx.AsyncClient

    def create_ssl_context(*, trust_env):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        created_contexts.append((trust_env, context))
        return context

    class PredictionClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda _: httpx.Response(400)), **kwargs)
            created_clients.append((self, kwargs["trust_env"], kwargs.get("verify", True)))

    async def run():
        async def request(base_url):
            config = ProviderApiConfig("openai", "test-key", base_url, "gpt-5.2", "responses", "none")
            with pytest.raises(openai.BadRequestError):
                await prediction.request_prediction_text(config, SimpleNamespace(), "Continue", None)

        monkeypatch.setenv("SSL_CERT_FILE", "test-ca-a")
        await request("https://example.invalid/v1")
        await request("https://example.invalid/v1")
        await request("http://localhost/v1")
        monkeypatch.setenv("SSL_CERT_FILE", "test-ca-b")
        await request("https://example.invalid/v1")
        await request("http://localhost/v1")

    prediction._prediction_ssl_context.cache_clear()
    try:
        monkeypatch.setattr(httpx, "create_ssl_context", create_ssl_context)
        monkeypatch.setattr(httpx, "AsyncClient", PredictionClient)
        asyncio.run(run())
    finally:
        prediction._prediction_ssl_context.cache_clear()

    assert [trust_env for trust_env, _ in created_contexts] == [True, False, True]
    assert [(trust_env, context) for _, trust_env, context in created_clients] == [
        created_contexts[index] for index in (0, 0, 1, 2, 1)
    ]
    assert len({id(client) for client, _, _ in created_clients}) == 5
    assert all(client.is_closed for client, _, _ in created_clients)
