"""Regression for explicit pre-routing failures observed through public MCP."""
import pytest

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from operation_context import ensure_operation_result
from tools.vrcforge_agent_mcp_stdio import external_rejection


def rejection(status="tool_leaf_required"):
    return external_rejection(
        status=status,
        error="The selected tool or block is not allowed.",
        error_code=("external_tool_leaf_required" if status == "tool_leaf_required"
                    else "prompt_skill_provenance_mismatch"),
        failure_layer="external_tool_discovery",
        failure_phase="before_route",
        operation_kind="discovery",
    )


@pytest.mark.parametrize("status", ["tool_leaf_required", "gateway_http_rejection"])
def test_explicit_unrouted_rejection_has_failed_operation_status(status):
    router = Mcp2026Router(
        lambda _params: [{"name": "inspect", "inputSchema": {"type": "object"}}],
        lambda _name, _arguments: rejection(status),
    )
    response, http_status = router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "inspect", "arguments": {}, "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    })
    assert http_status == 200
    assert response["result"]["isError"] is True
    result = response["result"]["structuredContent"]
    assert result["status"] == status
    assert result["outcome"]["status"] == "failed"
    assert result["operationStatus"] == "failed"
    assert result["commitState"] == "not_started"
    assert result["mutationStarted"] is False


def test_routed_failure_with_uncertain_commit_stays_unknown():
    result = rejection("gateway_http_rejection")
    for target in (result, result["errorDetails"]):
        target.update(toolRoutingStarted=True, mutationStarted=None,
                      committed=None, commitState="unknown")
    actual = ensure_operation_result(result, write=True)
    assert actual["operationStatus"] == "unknown"
    assert actual["commitState"] == "unknown"
    assert actual["mutationStarted"] is None


def test_incomplete_failure_evidence_does_not_invent_known_status():
    actual = ensure_operation_result({"ok": False, "status": "gateway_http_rejection"}, write=True)
    assert actual["operationStatus"] == "unknown"
    assert actual["commitState"] == "unknown"


def test_outer_routed_write_is_not_overridden_by_nested_rejection():
    result = rejection()
    result.update(toolRoutingStarted=True, mutationStarted=True,
                  committed=None, commitState="unknown")
    actual = ensure_operation_result(result, write=True)
    assert actual["operationStatus"] == "unknown"
    assert actual["commitState"] == "unknown"
    assert actual["mutationStarted"] is True
