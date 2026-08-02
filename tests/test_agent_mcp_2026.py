from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest

from agent_mcp_2026 import PROTOCOL_VERSION, Mcp2026Router, create_asgi_app, run_stdio_loop


def _meta(**extra):
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        **extra,
    }


def _request(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {"_meta": _meta(), **(params or {})}}


@pytest.fixture
def router():
    return Mcp2026Router(
        lambda: [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}, {"name": "generic"}],
        lambda name, arguments: {"ok": True, "echo": f"{name}:{arguments.get('value', '')}"},
        server_name="VRCForge",
        server_version="1.4.0",
    )


def test_discover_list_and_call_are_strict_2026(router):
    discover, status = router.handle(_request("server/discover"))
    assert status == 200
    assert discover["result"]["supportedVersions"] == [PROTOCOL_VERSION]
    assert discover["result"]["capabilities"] == {"tools": {}}
    assert discover["result"]["resultType"] == "complete"
    assert discover["result"]["_meta"]["io.modelcontextprotocol/serverInfo"] == {"name": "VRCForge", "version": "1.4.0"}

    listed, status = router.handle(_request("tools/list"))
    assert status == 200
    assert listed["result"]["tools"][1]["inputSchema"] == {"type": "object", "additionalProperties": True}

    called, status = router.handle(_request("tools/call", {"name": "echo", "arguments": {"value": "ok"}}))
    assert status == 200
    assert called["result"]["structuredContent"] == {"ok": True, "echo": "echo:ok"}
    assert json.loads(called["result"]["content"][0]["text"]) == called["result"]["structuredContent"]
    assert called["result"]["isError"] is False


@pytest.mark.parametrize(
    "message, code",
    [
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, -32020),
        (_request("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2025-11-25", "io.modelcontextprotocol/clientCapabilities": {}}}), -32022),
        (_request("tools/list", {"_meta": _meta(**{"io.modelcontextprotocol/clientInfo": {"name": "", "version": "1"}})}), -32602),
        (_request("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION}}), -32021),
        (_request("initialize"), -32601),
        (_request("unknown"), -32601),
        (_request("tools/call", {"name": "", "arguments": []}), -32602),
        (_request("tools/call", {"name": "hidden", "arguments": {}}), -32602),
    ],
)
def test_router_rejects_legacy_and_bad_shapes(router, message, code):
    response, status = router.handle(message)
    assert response["error"]["code"] == code
    assert status == (404 if code == -32601 else 400)


def test_http_transport_enforces_headers_origin_bearer_and_body(router):
    async def exercise():
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good", max_body_bytes=400)
        transport = httpx.ASGITransport(app=app)
        headers = {
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": PROTOCOL_VERSION,
            "mcp-method": "tools/call",
            "mcp-name": "echo",
            "authorization": "Bearer good",
            "origin": "http://127.0.0.1:1234",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/", json=_request("tools/call", {"name": "echo", "arguments": {}}), headers=headers)
            assert response.status_code == 200
            assert response.json()["result"]["resultType"] == "complete"

            for bad_headers, expected_status, expected_code in (
                ({**headers, "origin": "https://evil.example"}, 403, -32600),
                ({**headers, "authorization": "Bearer bad"}, 401, -32000),
                ({**headers, "mcp-name": "other"}, 400, -32020),
                ({**headers, "accept": "application/json"}, 400, -32020),
            ):
                rejected = await client.post("/", json=_request("tools/call", {"name": "echo", "arguments": {}}), headers=bad_headers)
                assert rejected.status_code == expected_status
                assert rejected.json()["error"]["code"] == expected_code
            unknown_headers = {key: value for key, value in headers.items() if key != "mcp-name"}
            unknown = await client.post("/", json=_request("other"), headers={**unknown_headers, "mcp-method": "other"})
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == -32601
            assert (await client.get("/")).status_code == 405
            assert (await client.get("/")).headers["allow"] == "POST"
            assert (await client.delete("/")).json()["error"]["code"] == -32601
            batch = await client.post("/", content=b"[]", headers={**headers, "mcp-method": "tools/list"})
            assert batch.status_code == 400
            assert batch.json()["error"]["code"] == -32600
            too_large = await client.post("/", content=b"{" + b"x" * 1000, headers={**headers, "mcp-method": "tools/list"})
            assert too_large.status_code == 400
            non_finite = await client.post(
                "/",
                content=json.dumps(_request("tools/call", {"name": "echo", "arguments": {"value": float("nan")}})).encode(),
                headers=headers,
            )
            assert non_finite.status_code == 400
            assert non_finite.json()["error"]["code"] == -32700

            duplicate_cases = (
                ([*headers.items(), ("authorization", "Bearer good")], 401),
                ([*headers.items(), ("mcp-protocol-version", PROTOCOL_VERSION)], 400),
                ([*headers.items(), ("mcp-method", "tools/call")], 400),
                ([*headers.items(), ("mcp-name", "echo")], 400),
                ([*headers.items(), ("origin", "http://localhost")], 400),
                ([*headers.items(), ("host", "test"), ("host", "evil.example")], 400),
            )
            for duplicate_headers, expected_status in duplicate_cases:
                duplicate = await client.post(
                    "/",
                    content=json.dumps(_request("tools/call", {"name": "echo", "arguments": {}})).encode(),
                    headers=duplicate_headers,
                )
                assert duplicate.status_code == expected_status

            extra_name = await client.post(
                "/",
                json=_request("tools/list"),
                headers={**headers, "mcp-method": "tools/list"},
            )
            assert extra_name.status_code == 400
            assert extra_name.json()["error"]["code"] == -32020

    asyncio.run(exercise())


def test_stdio_is_newline_json_without_length_prefix(router):
    source = io.StringIO(json.dumps(_request("tools/list")) + "\n" + "not-json\n")
    sink = io.StringIO()
    assert run_stdio_loop(router, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["result"]["resultType"] == "complete"
    assert output[1]["error"]["code"] == -32700
    assert "Content-Length" not in sink.getvalue()


def test_stdio_rejects_oversized_frame_then_continues(router):
    valid = json.dumps(_request("tools/list"))
    source = io.StringIO("{" + "x" * 600 + "\n" + valid + "\n")
    sink = io.StringIO()
    assert run_stdio_loop(router, input_stream=source, output_stream=sink, max_line_bytes=512) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["error"]["code"] == -32700
    assert output[1]["result"]["resultType"] == "complete"


@pytest.mark.parametrize("bad_value", [object(), float("nan")])
def test_tool_result_must_be_strict_json(bad_value):
    invalid_router = Mcp2026Router(
        lambda: [{"name": "bad"}],
        lambda _name, _arguments: {"value": bad_value},
    )
    response, status = invalid_router.handle(_request("tools/call", {"name": "bad", "arguments": {}}))
    assert status == 500
    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "Internal MCP server error"


def test_tool_result_cycle_is_shaped_and_stdio_survives():
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    invalid_router = Mcp2026Router(
        lambda: [{"name": "bad"}, {"name": "good"}],
        lambda name, _arguments: cyclic if name == "bad" else {"ok": True},
    )
    source = io.StringIO(
        json.dumps(_request("tools/call", {"name": "bad", "arguments": {}}))
        + "\n"
        + json.dumps(_request("tools/call", {"name": "good", "arguments": {}}))
        + "\n"
    )
    sink = io.StringIO()
    assert run_stdio_loop(invalid_router, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["error"]["code"] == -32603
    assert output[1]["result"]["structuredContent"] == {"ok": True}


def test_async_callbacks_work(router):
    async def tool_list():
        return [{"name": "async", "inputSchema": {"type": "object"}}]

    async def tool_call(name, args):
        return {"content": [{"type": "text", "text": name}]}

    async_router = Mcp2026Router(tool_list, tool_call)
    result, status = asyncio.run(async_router.handle_async(_request("tools/list")))
    assert status == 200
    assert result["result"]["tools"][0]["name"] == "async"
