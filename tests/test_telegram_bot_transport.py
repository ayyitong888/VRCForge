import asyncio
import json

import httpx
import pytest

import telegram_bot_transport as transport_module
from telegram_bot_transport import TelegramBotError, TelegramBotTransport, split_telegram_text


def make_transport(handler):
    return TelegramBotTransport(
        "123456:secret-token",
        transport=httpx.MockTransport(handler),
        timeout=1,
    )


def test_edit_and_callback_ack_send_exact_bot_api_payloads():
    async def case():
        requests = []
        def handler(request):
            requests.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
            return httpx.Response(200, json={"ok": True, "result": True})
        transport = make_transport(handler)
        try:
            await transport.edit_message_text(7, 55, "next page", reply_markup={"inline_keyboard": []})
            await transport.answer_callback_query("synthetic-callback")
        finally:
            await transport.close()
        assert requests == [("editMessageText", {"chat_id": 7, "message_id": 55, "text": "next page",
                             "reply_markup": {"inline_keyboard": []}}),
                            ("answerCallbackQuery", {"callback_query_id": "synthetic-callback"})]
    asyncio.run(case())


@pytest.mark.parametrize("mode,url", [
    ("custom", "socks5://127.0.0.1:1080"),
    ("custom", "http://user:pass@127.0.0.1:8080"),
    ("custom", "http://127.0.0.1:8080/path"),
    ("custom", "http://127.0.0.1:8080/?query=1"),
    ("custom", "http://127.0.0.1:99999"),
    ("direct", "http://127.0.0.1:8080"),
])
def test_proxy_url_is_fail_closed(mode, url):
    with pytest.raises(ValueError):
        TelegramBotTransport("123456:secret-token", proxy_mode=mode, proxy_url=url)


def test_proxy_modes_set_httpx_options_without_fallback(monkeypatch):
    captured = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def aclose(self):
            pass

    monkeypatch.setattr(transport_module.httpx, "AsyncClient", FakeClient)
    TelegramBotTransport("123456:secret-token")
    TelegramBotTransport("123456:secret-token", proxy_mode="direct")
    TelegramBotTransport("123456:secret-token", proxy_mode="custom", proxy_url="http://127.0.0.1:8080")
    assert "trust_env" not in captured[0] and "proxy" not in captured[0]
    assert captured[1]["trust_env"] is False and "proxy" not in captured[1]
    assert captured[2]["trust_env"] is False and captured[2]["proxy"] == "http://127.0.0.1:8080"


def test_custom_proxy_uses_loopback_connect_without_direct_fallback(monkeypatch):
    async def run():
        requests = []

        async def proxy_client(reader, writer):
            try:
                data = await reader.readuntil(b"\r\n\r\n")
                requests.append(data)
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(proxy_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        # If the custom proxy were ignored, this unreachable environment proxy
        # would be consulted instead; the loopback server must receive CONNECT.
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        client = TelegramBotTransport("123456:secret-token", proxy_mode="custom",
                                      proxy_url=f"http://127.0.0.1:{port}", timeout=1)
        try:
            with pytest.raises(TelegramBotError) as error:
                await client.get_me()
            assert error.value.code == "network_error"
            assert "secret-token" not in str(error.value)
            assert len(requests) == 1
            assert requests[0].startswith(b"CONNECT api.telegram.org:443 HTTP/1.1")
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_get_me_uses_fixed_origin_and_keeps_token_out_of_repr_and_errors():
    async def run():
        seen = {}
    
        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}})
    
        client = make_transport(handler)
        assert "secret-token" not in repr(client)
        assert await client.get_me() == {"id": 1}
        assert seen["url"].startswith("https://api.telegram.org/bot")
        await client.close()
    
    
    asyncio.run(run())
def test_get_updates_preserves_offset_timeout_and_allowed_updates():
    async def run():
        payload = {}
    
        def handler(request):
            payload.update(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": []})
    
        client = make_transport(handler)
        await client.get_updates(12, 25, ["message", "callback_query"])
        assert payload == {"offset": 12, "timeout": 25, "allowed_updates": ["message", "callback_query"]}
        await client.close()
    
    
    asyncio.run(run())
def test_split_text_uses_utf16_units_without_surrogate_halves():
    chunks = split_telegram_text("a" * 4095 + "\U0001f600" + "z")
    assert chunks == ["a" * 4095, "\U0001f600z"]
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)
    assert all("\\ud800" not in chunk and "\\udfff" not in chunk for chunk in chunks)


def test_send_message_chunks_and_does_not_retry():
    async def run():
        calls = []
    
        def handler(request):
            calls.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})
    
        client = make_transport(handler)
        result = await client.send_message(7, "x" * 4097)
        assert calls == ["x" * 4096, "x"]
        assert [item["message_id"] for item in result] == [1, 2]
        await client.close()
    
    
    asyncio.run(run())
def test_429_is_bounded_and_4xx_is_safe():
    async def run():
        def rate_limited(request):
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 999999}})
    
        client = make_transport(rate_limited)
        with pytest.raises(TelegramBotError) as caught:
            await client.get_me()
        assert caught.value.code == "rate_limited"
        assert caught.value.retry_after is None
        assert "secret-token" not in str(caught.value)
        await client.close()
    
        client = make_transport(lambda request: httpx.Response(400, json={"ok": False, "description": "private"}))
        with pytest.raises(TelegramBotError) as caught:
            await client.get_me()
        assert caught.value.code == "protocol_error"
        assert "private" not in str(caught.value)
        await client.close()
    
    
    asyncio.run(run())
def test_cancel_does_not_turn_into_retry_and_close_is_isolated():
    async def run():
        started = asyncio.Event()
    
        async def handler(request):
            started.set()
            await asyncio.sleep(60)
            return httpx.Response(200, json={"ok": True, "result": []})
    
        injected = httpx.AsyncClient(base_url="https://api.telegram.org", transport=httpx.MockTransport(handler))
        client = TelegramBotTransport("token", client=injected)
        task = asyncio.create_task(client.get_updates(timeout=25))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.close()
        assert not injected.is_closed
        await injected.aclose()
    
    
    asyncio.run(run())
def test_other_api_methods_use_expected_method_names():
    async def run():
        methods = []
    
        def handler(request):
            methods.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"ok": True, "result": True})
    
        client = make_transport(handler)
        await client.send_chat_action(1, "typing")
        await client.edit_message_text(1, 2, "hello")
        await client.answer_callback_query("callback", text="ok")
        await client.set_my_commands([{"command": "start", "description": "Start"}], owner_id=7)
        assert methods == ["sendChatAction", "editMessageText", "answerCallbackQuery", "setMyCommands"]
        await client.close()
    
    
    
    asyncio.run(run())
def test_send_photo_uses_multipart_png_bytes_without_url_or_path():
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    async def scenario():
        transport = TelegramBotTransport("123:secret", transport=httpx.MockTransport(handler))
        result = await transport.send_photo(42, b"\x89PNG\r\n\x1a\nfixture", "approved preview")
        assert result["message_id"] == 1
        assert seen["content_type"].startswith("multipart/form-data;")
        assert b'filename="preview.png"' in seen["body"]
        assert b"image/png" in seen["body"]
        assert b"\x89PNG\r\n\x1a\nfixture" in seen["body"]
        assert b"http://" not in seen["body"] and b"https://" not in seen["body"]
        await transport.close()
    asyncio.run(scenario())


def test_send_photo_rejects_url_path_and_invalid_png_before_network():
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})
    async def scenario():
        transport = TelegramBotTransport("123:secret", transport=httpx.MockTransport(handler))
        with pytest.raises(ValueError):
            await transport.send_photo(42, b"https://example.invalid/photo.png")
        with pytest.raises(ValueError):
            await transport.send_photo(42, b"C:\\capture.png")
        with pytest.raises(ValueError):
            await transport.send_photo(42, b"not-png")
        assert calls == []
        await transport.close()
    asyncio.run(scenario())
