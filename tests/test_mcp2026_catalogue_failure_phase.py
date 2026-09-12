from __future__ import annotations

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION


def _request(name: str) -> dict:
    return {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            },
            "name": name, "arguments": {},
        },
    }


def test_catalogue_failure_is_before_routing_and_mutation():
    called = []
    router = Mcp2026Router(
        lambda _params: [{"name": "fixture"}],
        lambda name, _arguments: called.append(name),
        tool_call_catalogue=lambda _params: (_ for _ in ()).throw(ConnectionError("backend unavailable")),
    )
    response, status = router.handle(_request("fixture"))
    data = response["error"]["data"]
    assert status == 500
    assert response["error"]["code"] == -32603
    assert data["failurePhase"] == "tool_catalogue_lookup"
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["committed"] is False
    assert data["commitState"] == "not_started"
    assert called == []


def test_handler_failure_remains_unknown_after_dispatch():
    router = Mcp2026Router(
        lambda _params: [{"name": "fixture"}],
        lambda _name, _arguments: (_ for _ in ()).throw(RuntimeError("handler failed")),
        tool_call_catalogue=lambda _params: [{"name": "fixture"}],
    )
    response, _status = router.handle(_request("fixture"))
    data = response["error"]["data"]
    assert data["failurePhase"] == "protocol_router_internal"
    assert data["toolRoutingStarted"] is None
    assert data["mutationStarted"] is None
    assert data["committed"] is None
