import json

import pytest

from tools import vrcforge_agent_mcp_stdio as stdio


class _OfflineBridge:
    def manifest(self, *_args, **_kwargs):
        raise ConnectionError("backend unavailable")

    def call_tool(self, *_args, **_kwargs):
        raise AssertionError("domain call must not run when its catalogue is unavailable")


class _ReachableBridge:
    def manifest(self, *_args, **_kwargs):
        return {"tools": []}

    def call_tool(self, *_args, **_kwargs):
        raise AssertionError("unknown tool must be rejected before dispatch")


@pytest.fixture
def captured_router(monkeypatch):
    captured = {}
    monkeypatch.setattr(stdio, "run_standard_stdio_loop", lambda router: captured.setdefault("router", router))
    stdio.run_stdio_server(_OfflineBridge(), protocol_profile="mcp-1x", exposure_layer="execution")
    router = captured["router"]
    router.handle({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    })
    return router


@pytest.fixture
def reachable_router(monkeypatch):
    captured = {}
    monkeypatch.setattr(stdio, "run_standard_stdio_loop", lambda router: captured.setdefault("router", router))
    stdio.run_stdio_server(_ReachableBridge(), protocol_profile="mcp-1x", exposure_layer="execution")
    router = captured["router"]
    router.handle({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    })
    return router


def test_domain_lookup_preserves_backend_unavailable_error(captured_router):
    response = captured_router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "vrcforge_bind_execution_target", "arguments": {}},
    })
    assert response["error"]["code"] == -32603
    assert "backend unavailable" in json.dumps(response["error"])


def test_offline_catalogue_still_exposes_local_controls(captured_router):
    response = captured_router.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    names = {item["name"] for item in response["result"]["tools"]}
    assert {"vrcforge_bridge_preflight", "vrcforge_list_tool_blocks", "vrcforge_load_tool_block"} <= names


def test_offline_local_control_call_remains_available(captured_router):
    response = captured_router.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "vrcforge_list_tool_blocks", "arguments": {}},
    })
    assert response["result"]["structuredContent"]["ok"] is True


def test_reachable_backend_unknown_tool_stays_not_exposed(reachable_router):
    response = reachable_router.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "vrcforge_bind_execution_target", "arguments": {}},
    })
    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "Tool is not exposed by this MCP server"
