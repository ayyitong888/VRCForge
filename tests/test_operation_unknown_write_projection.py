"""Exercise the Gateway and public MCP envelopes, including the archived timeout."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path

import httpx
import pytest

from agent_gateway import AgentGateway
from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION, create_asgi_app
from agent_mcp_standard import McpStandardRouter, LATEST_PROTOCOL_VERSION
from external_tool_result_contract import build_external_tool_error, external_write_failure_view

TOOL = "vrcforge_set_material_shader"
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = Path(os.environ["VRCFORGE_REGRESSION_ARCHIVE_ROOT"]) if os.environ.get("VRCFORGE_REGRESSION_ARCHIVE_ROOT") else ROOT / ".tmp" / "regression-archives"


def public_response(payload, transport):
    catalogue = lambda *_: [{"name": TOOL, "write": True, "inputSchema": {"type": "object"}}]
    callback = lambda *_: deepcopy(payload)
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": TOOL, "arguments": {}}}
    if transport == "standard":
        router = McpStandardRouter(catalogue, callback)
        router.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        return router.handle(request)["result"]["structuredContent"]
    request["params"]["_meta"] = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                                      "io.modelcontextprotocol/clientCapabilities": {}}
    app = create_asgi_app(Mcp2026Router(catalogue, callback), bearer_validator=lambda token: token == "local-test")

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/", json=request, headers={
                "Authorization": "Bearer local-test", "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL_VERSION, "Mcp-Method": "tools/call", "Mcp-Name": TOOL})
        assert response.status_code == 200
        return response.json()["result"]["structuredContent"]
    return asyncio.run(call())


def project_failure(raw, source_error=None, *, next_action=None):
    source_error = source_error or {}
    error = build_external_tool_error(error=raw.get("error", "Write failed"),
        error_code=source_error.get("errorCode", "unity_core_timeout"),
        failure_layer=source_error.get("failureLayer", "unity_core_transport"),
        failure_phase=source_error.get("failurePhase", "tool_dispatch_or_response"),
        operation_kind="write", tool=TOOL, raw_result=raw)
    applied = {**deepcopy(raw), "result": deepcopy(raw), "errorDetails": error,
               "writeFailure": external_write_failure_view(error), "retryable": False,
               "nextAction": next_action}
    return AgentGateway._external_mcp_write_result(AgentGateway.__new__(AgentGateway), TOOL, applied)


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_archived_551_raw_failure_remains_unknown_through_gateway_and_public_mcp(transport):
    archive = ARCHIVE_ROOT / "551.json"
    if not archive.exists():
        pytest.skip("Local original public evidence is not distributed with tests")
    observed = json.loads(archive.read_text("utf-8"))["results"][2]["structuredContent"]
    assert observed["mutationStarted"] is False  # The original public defect.
    raw = observed["errorDetails"]["rawResult"]
    assert raw["mutationStarted"] is None and raw["committed"] is None
    gateway = project_failure(raw, observed["errorDetails"], next_action=observed["nextAction"])
    assert gateway["mutationStarted"] is None  # Pinpoint the final envelope boundary.
    public = public_response(gateway, transport)
    for value in (public, public["outcome"], public["writeFailure"], public["errorDetails"]):
        assert value["mutationStarted"] is None
        assert value["commitState"] == "unknown"
    assert public["mutationApplied"] is None
    assert public["errorDetails"]["safeToRetry"] is False
    assert public["nextAction"] is None  # Existing transaction projection is outside this fix.
    assert public["errorDetails"]["nextAction"] == observed["errorDetails"]["nextAction"]
    assert public["outcome"]["nextAction"] == (
        None if transport == "2026" else observed["outcome"]["nextAction"]
    )  # Record the existing transport projection difference; errorDetails retains the hint.


@pytest.mark.parametrize("transport", ["2026", "standard"])
@pytest.mark.parametrize("state,mutation,committed", [("unknown", None, None), ("not_started", False, False), ("partial", True, False)])
def test_portable_failed_write_facts_survive_public_projection(transport, state, mutation, committed):
    gateway = project_failure({"ok": False, "status": "failed", "commitState": state,
                               "mutationStarted": mutation, "committed": committed})
    public = public_response(gateway, transport)
    assert public["mutationStarted"] is mutation
    assert public["committed"] is committed
    assert public["commitState"] == state
    assert public["errorDetails"] == gateway["errorDetails"]
    assert public["nextAction"] == gateway["nextAction"]


@pytest.mark.parametrize("transport", ["2026", "standard"])
@pytest.mark.parametrize("status,mutation,state", [("success", True, "complete"), ("pending", True, "pending"),
                                                   ("failed", True, "rolled_back"), ("preview", False, "not_started")])
def test_explicit_existing_states_survive_final_public_envelope(transport, status, mutation, state):
    raw = {"ok": status != "failed", "status": status, "mutationStarted": mutation, "commitState": state,
           "safeToRetry": False, "nextAction": "Inspect the existing receipt."}
    public = public_response(raw, transport)
    for key in raw:
        assert public[key] == raw[key]
