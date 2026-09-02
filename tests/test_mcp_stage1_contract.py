from __future__ import annotations

import asyncio
from pathlib import Path

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_gateway import AgentGateway
from mcp_tool_descriptor import standardize_tool_descriptor


def _meta() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "blackbox-test", "version": "1"},
    }


def test_external_blackbox_handshake_list_generation_and_planning_write_lock():
    state = {"layer": "planning", "generation": 0}
    calls: list[str] = []
    tools = {
        "vrcforge_read_probe": {"name": "vrcforge_read_probe", "description": "When to use: read. When NOT to use: write. Negative example: write.", "inputSchema": {"type": "object"}},
        "vrcforge_write_probe": {"name": "vrcforge_write_probe", "write": True, "description": "When to use: write. When NOT to use: plan. Negative example: plan.", "inputSchema": {"type": "object"}, "_meta": {"permission": "Write"}},
    }

    def list_tools(params):
        layer = str(params.get("exposureLayer") or state["layer"])
        state["layer"] = layer
        return [tools["vrcforge_read_probe"]] + ([tools["vrcforge_write_probe"]] if layer == "execution" else [])

    async def call_tool(name, _arguments):
        calls.append(name)
        return {"ok": True, "status": "success", "operationId": "op-blackbox-1"}

    router = Mcp2026Router(
        list_tools,
        call_tool,
        tool_list_revision=lambda: state["generation"],
        tool_call_catalogue=lambda _params: list_tools({"exposureLayer": state["layer"]}),
    )

    async def run():
        discover, _ = await router.handle_async({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}})
        planning, _ = await router.handle_async({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": _meta(), "exposureLayer": "planning"}})
        blocked, _ = await router.handle_async({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"_meta": _meta(), "name": "vrcforge_write_probe", "arguments": {}}})
        execution, _ = await router.handle_async({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {"_meta": _meta(), "exposureLayer": "execution"}})
        allowed, _ = await router.handle_async({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"_meta": _meta(), "name": "vrcforge_write_probe", "arguments": {}}})
        state["generation"] = 1
        refreshed, _ = await router.handle_async({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {"_meta": _meta(), "exposureLayer": "execution"}})
        return discover, planning, blocked, execution, allowed, refreshed

    discover, planning, blocked, execution, allowed, refreshed = asyncio.run(run())
    assert discover["result"]["capabilities"]["tools"]["listChanged"] is True
    assert [item["name"] for item in planning["result"]["tools"]] == ["vrcforge_read_probe"]
    assert blocked["error"]["code"] == -32602
    assert {item["name"] for item in execution["result"]["tools"]} == set(tools)
    assert allowed["result"]["structuredContent"]["operationId"] == "op-blackbox-1"
    assert refreshed["result"]["catalogGeneration"] == 1
    assert router.drain_notifications() == []
    assert calls == ["vrcforge_write_probe"]


def test_descriptor_exposes_stage1_contract_and_future_unavailable_boundaries():
    descriptor = standardize_tool_descriptor(
        {
            "name": "vrcforge_set_property",
            "description": "When to use: set one property. When NOT to use: bulk edit. Negative example: use a hierarchy path as identity.",
            "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        },
        write=True,
        block="avatar",
        add_identity_schema=True,
    )
    assert descriptor["canonicalName"] == "vrcforge.set.property"
    assert descriptor["requiredIdentity"]["hierarchyPathFallback"] is False
    assert descriptor["_meta"]["resources"]["status"] == "unavailable"
    assert descriptor["_meta"]["promptSkillProvenance"]["status"] == "unavailable"
    assert descriptor["outputSchema"]["properties"]["operationId"]["type"] == "string"
    assert "executionTarget" in descriptor["inputSchema"]["properties"]


def test_gateway_read_and_supervised_write_return_operation_and_fresh_readback_contract(tmp_path: Path):
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)

    gateway.register_tool("vrcforge_stage1_read", "Read one exact value.", "unity", lambda _args: {"ok": True, "value": 1})
    gateway.register_external_mcp_unity_tool("vrcforge_stage1_read", "diagnostics")
    read_result = gateway.call_external_mcp_tool("vrcforge_stage1_read", {})
    assert read_result["operationId"].startswith("mcpread_")
    assert read_result["commitState"] == "not_started"
    assert read_result["readbackState"] == "complete"

    gateway.approval_transactions.register_write_handler(
        "vrcforge_stage1_supervised_write",
        "Write one exact value.",
        "medium",
        lambda _args: {"ok": True, "status": "success", "mutationStarted": True, "mutationApplied": True, "commitState": "complete"},
        pre_write_checkpoint_required=False,
        verification_finalize_handler=lambda _args, _baseline, result: {**dict(result), "persistedReadback": True},
    )
    gateway.register_external_mcp_unity_tool("vrcforge_stage1_supervised_write", "avatar")
    write_result = gateway.call_external_mcp_tool("vrcforge_stage1_supervised_write", {"value": 2})
    assert write_result["operationId"].startswith("mcpwrite_")
    assert write_result["readbackState"] == "verified"
    assert write_result["persistenceState"] == "verified"
    assert write_result["outcome"]["status"] != "failed"
