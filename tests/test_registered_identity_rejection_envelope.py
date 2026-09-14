"""Actual dashboard registration and Gateway rejection through both public routers.

Only live descriptor/process I/O is replaced by the production pure identity
validator. No Unity write or synthetic write registration is involved.
"""
from copy import deepcopy

import pytest

from agent_mcp_standard import McpStandardRouter, LATEST_PROTOCOL_VERSION
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from execution_target import validate_execution_target
from operation_context import ensure_operation_result


@pytest.fixture
def registered_rejection(monkeypatch, tmp_path):
    import agent_gateway
    import dashboard_server as server

    gateway = server.AGENT_GATEWAY
    config = agent_gateway.AgentGatewayConfig(enabled=True, allow_write_requests=True)
    monkeypatch.setattr(gateway, "ensure_config", lambda: config)
    monkeypatch.setattr(agent_gateway, "validate_runtime_execution_target", validate_execution_target)
    name = "vrcforge_manage_fx_animator"
    descriptor = gateway.shared_agent_tool_descriptor(name, write=True)
    assert descriptor["_meta"]["permission"].lower() == "write"
    assert gateway._write_handlers[name].requires_approved_execution_context
    monkeypatch.setattr(type(gateway.approval_transactions), "prepare_external_mcp_write",
                        lambda *_args: pytest.fail("Identity rejection reached preparation"))
    arguments = {"projectPath": str(tmp_path), "action": "ensure_transition",
                 "executionTarget": {"schema": "vrcforge.execution_target.v1", "scope": "scene"}}
    return gateway, descriptor, name, arguments


@pytest.mark.parametrize("modern", [False, True])
def test_actual_registered_scope_rejection_has_consistent_no_write_envelope(registered_rejection, modern):
    gateway, descriptor, name, arguments = registered_rejection
    call = lambda n, a: gateway.call_external_mcp_tool(n, a)
    params = {"name": name, "arguments": arguments}
    if modern:
        router = Mcp2026Router(lambda _params: [descriptor], call)
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                           "io.modelcontextprotocol/clientCapabilities": {}}
        response, status = router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert status == 200
    else:
        router = McpStandardRouter(lambda: [descriptor], call)
        initialized = router.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "identity-regression", "version": "1"}}})
        assert "result" in initialized
        response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    assert "result" in response, response
    result = response["result"]["structuredContent"]
    assert result["errorDetails"]["errorCode"] == "identity_scope_insufficient"
    assert result["outcome"]["mutationStarted"] is False
    assert result["outcome"]["commitState"] == "not_started"
    assert result["operationStatus"] == "failed"
    assert result["mutationStarted"] is False
    assert result["mutationApplied"] is False
    assert result["commitState"] == "not_started"
    assert result["persistenceState"] == "not_started"
    assert result["cleanupState"] == "not_applicable"


@pytest.mark.parametrize("outer", [{"mutationStarted": True}, {"mutationApplied": True}, {"toolRoutingStarted": True},
                                    {"commitState": "unknown"}, {"committed": True}])
def test_nested_rejection_does_not_override_conflicting_outer_write_facts(registered_rejection, outer):
    gateway, _, name, arguments = registered_rejection
    result = deepcopy(gateway.call_external_mcp_tool(name, arguments))
    result.update(outer)
    actual = ensure_operation_result(result, write=True)
    assert actual["commitState"] == "unknown"
    if "mutationStarted" in outer:
        assert actual["mutationStarted"] is True
    else:
        assert actual["mutationStarted"] is None


def test_missing_write_facts_stay_unknown():
    actual = ensure_operation_result({"ok": False, "status": "failed"}, write=True)
    assert actual["mutationStarted"] is None
    assert actual["commitState"] == "unknown"
