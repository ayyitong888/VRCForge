"""Protocol requests must distinguish lookup rejection from uncertain execution."""
from urllib.error import URLError

import pytest

from agent_mcp_standard import McpStandardRouter


def initialize(router):
    router.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "catalogue-failure-regression", "version": "1"},
    }})


@pytest.mark.parametrize("per_call", [False, True])
@pytest.mark.parametrize("tool_name", ["vrcforge_bind_execution_target", "vrcforge_manage_fx_animator"])
def test_failed_catalogue_cannot_claim_uncertain_mutation(per_call, tool_name):
    dispatched = []

    def unavailable(*_args):
        raise URLError(ConnectionRefusedError("backend connection refused"))

    options = ({"tool_call_catalogue_for_call": unavailable} if per_call
               else {"tool_call_catalogue": unavailable})
    router = McpStandardRouter(lambda: [], lambda *args: dispatched.append(args), **options)
    initialize(router)
    response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": tool_name, "arguments": {}}})
    error = response["error"]
    data = error["data"]
    assert error["code"] == -32603
    assert "connection refused" in data["error"]
    assert data["failurePhase"] == "tool_catalogue_lookup"
    assert data["tool"] == tool_name
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["committed"] is False
    assert data["commitState"] == "not_started"
    assert data["commitStateKnown"] is True
    assert data["checkpointRecoveryRequired"] is False
    assert "Read back the exact target" not in data.get("nextAction", "")
    assert dispatched == []


def test_exception_after_dispatch_keeps_uncertain_write_state():
    dispatched = []

    def execute(name, args):
        dispatched.append(name)
        raise URLError(ConnectionRefusedError("response lost after dispatch"))

    router = McpStandardRouter(lambda: [{"name": "fixture_write", "write": True}], execute)
    initialize(router)
    response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "fixture_write", "arguments": {}}})
    data = response["error"]["data"]
    assert dispatched == ["fixture_write"]
    assert data["commitState"] == "unknown"
    assert data["mutationStarted"] is None
    assert data["committed"] is None


def test_available_catalogue_still_rejects_an_unknown_tool():
    dispatched = []
    router = McpStandardRouter(lambda: [], lambda *args: dispatched.append(args))
    initialize(router)
    response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "missing_tool", "arguments": {}}})
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["commitState"] == "not_started"
    assert not dispatched
