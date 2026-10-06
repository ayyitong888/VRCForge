from __future__ import annotations

import asyncio
import io
import json
import pytest
from types import SimpleNamespace
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION, create_asgi_app


def request(method):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {
        "_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                  "io.modelcontextprotocol/clientCapabilities": {}}}}


def headers(method, session=None, token="good"):
    result = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
              "MCP-Protocol-Version": PROTOCOL_VERSION, "Mcp-Method": method,
              "Authorization": "Bearer " + token}
    if session:
        result["Mcp-Session-Id"] = session
    return result


def test_server_session_context_and_anonymous_requests_do_not_share():
    current = ContextVar("test_mcp_session", default="outside")
    seen = []
    @contextmanager
    def bind(session):
        token = current.set(session)
        try:
            yield
        finally:
            current.reset(token)
    def catalogue(params):
        seen.append(current.get())
        return []
    router = Mcp2026Router(catalogue, lambda *_: {})
    app = create_asgi_app(router, bearer_validator=lambda token: token in {"good", "rotated"}, request_context=bind)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            async def post(method, session=None, token="good"):
                return await client.post("/", json=request(method), headers=headers(method, session, token))
            a = (await post("server/discover")).headers["Mcp-Session-Id"]
            b = (await post("server/discover")).headers["Mcp-Session-Id"]
            assert a != b
            await post("tools/list", a)
            await post("tools/list", b)
            await post("tools/list", a)
            await post("tools/list")
            await post("tools/list")
            assert seen[:3] == [a, b, a]
            assert len(set(seen[2:])) == 3
            assert current.get() == "outside"
            assert (await post("tools/list", "unknown")).status_code == 404
            assert (await post("tools/list", a, "rotated")).status_code == 403
            assert (await client.delete("/", headers={"Authorization": "Bearer good", "Mcp-Session-Id": a})).status_code == 204
            assert (await post("tools/list", a)).status_code == 404
            assert (await post("tools/list", b)).status_code == 200
    asyncio.run(exercise())


def test_bridge_discovers_once_and_roundtrips_session_header(monkeypatch, tmp_path):
    from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:8757", config_path=None,
                           timeout_seconds=1, start_runtime=False)
    calls = []
    def send(*args, **kwargs):
        method = kwargs["payload"]["method"]
        calls.append((method, kwargs["extra_headers"].get("Mcp-Session-Id")))
        if method == "server/discover":
            kwargs["response_headers"]["Mcp-Session-Id"] = "opaque-" + kwargs["token"]
        return {"jsonrpc": "2.0", "id": kwargs["payload"]["id"], "result": {"tools": []}}
    monkeypatch.setattr(bridge, "request_json", send)
    bridge._mcp_request("tools/list", {}, token="first")
    bridge._mcp_request("resources/list", {}, token="first")
    bridge._mcp_request("tools/list", {}, token="second")
    assert calls == [("server/discover", None), ("tools/list", "opaque-first"),
                     ("resources/list", "opaque-first"), ("server/discover", None), ("tools/list", "opaque-second")]


def test_session_idle_expiry_capacity_and_invalid_discovery(monkeypatch):
    import agent_mcp_2026 as transport
    clock = [10.0]
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(transport, "HTTP_MAX_SESSIONS", 1)
    app = create_asgi_app(Mcp2026Router(lambda *_: [], lambda *_: {}), bearer_validator=lambda t: t == "good")
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            invalid = request("server/discover")
            invalid["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "invalid"
            rejected = await client.post("/", json=invalid, headers=headers("server/discover"))
            assert rejected.status_code == 400
            assert "Mcp-Session-Id" not in rejected.headers
            first = await client.post("/", json=request("server/discover"), headers=headers("server/discover"))
            session = first.headers["Mcp-Session-Id"]
            assert (await client.post("/", json=request("server/discover"), headers=headers("server/discover"))).status_code == 503
            clock[0] += transport.HTTP_SESSION_IDLE_SECONDS
            assert (await client.post("/", json=request("tools/list"), headers=headers("tools/list", session))).status_code == 404
            assert (await client.post("/", json=request("server/discover"), headers=headers("server/discover"))).status_code == 200
    asyncio.run(exercise())


def test_actual_bridge_http_reader_captures_discovery_response_header(monkeypatch):
    from tools import vrcforge_agent_mcp_stdio as module
    outgoing = []
    def urlopen(req, **kwargs):
        outgoing.append(req)
        response = io.BytesIO(json.dumps({"result": {"tools": []}}).encode())
        response.headers = {"Mcp-Session-Id": "wire-session"}
        return response
    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    bridge = module.VRCForgeBridge(base_url="http://127.0.0.1:8757", config_path=None, timeout_seconds=1, start_runtime=False)
    bridge._mcp_request("tools/list", {}, token="secret-fixture")
    assert len(outgoing) == 2
    assert outgoing[0].get_header("Mcp-session-id") is None
    assert outgoing[1].get_header("Mcp-session-id") == "wire-session"
    assert bridge._mcp_session_owner != "secret-fixture"


@pytest.mark.parametrize("error_kind", ["session_expired", "tool_not_found", "unstructured_404"])
def test_bridge_session_rejection_clears_only_expired_context_without_replay(monkeypatch, error_kind):
    from agent_mcp_2026 import _error
    from tools import vrcforge_agent_mcp_stdio as module
    bridge = module.VRCForgeBridge(base_url="http://127.0.0.1:8757", config_path=None, timeout_seconds=1, start_runtime=False)
    calls = []
    rejected = False
    def send(*args, **kwargs):
        nonlocal rejected
        method = kwargs["payload"]["method"]
        calls.append((method, kwargs["extra_headers"].get("Mcp-Session-Id")))
        if method == "server/discover":
            kwargs["response_headers"]["Mcp-Session-Id"] = "fresh" if rejected else "old"
        if method == "tools/call":
            rejected = True
            body = (_error(None, -32000, "Unknown or expired MCP session") if error_kind == "session_expired"
                    else _error(1, -32601, "Tool not found") if error_kind == "tool_not_found"
                    else {"error": "Unknown or expired MCP session"})
            raise module.ExternalHttpBridgeError(status_code=404, path="/mcp", body=json.dumps(body))
        return {"result": {"tools": []}}
    monkeypatch.setattr(bridge, "request_json", send)
    bridge._mcp_request("tools/list", {}, token="fixture")
    with pytest.raises(module.ExternalHttpBridgeError):
        bridge._mcp_request("tools/call", {"name": "write_fixture", "arguments": {}}, token="fixture")
    assert calls == [("server/discover", None), ("tools/list", "old"), ("tools/call", "old")]
    assert bridge._mcp_session_id == ("" if error_kind == "session_expired" else "old")
    bridge._mcp_request("tools/list", {}, token="fixture")
    if error_kind == "session_expired":
        assert calls[-2:] == [("server/discover", None), ("tools/list", "fresh")]
    else:
        assert calls[-1] == ("tools/list", "old")
