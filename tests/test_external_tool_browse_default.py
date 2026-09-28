"""External STDIO block browsing is state-free unless loading is explicit."""

from copy import deepcopy
import importlib

import pytest

from agent_mcp_2026 import PROTOCOL_VERSION
from agent_mcp_standard import LATEST_PROTOCOL_VERSION

LEAF = "avatar_structure/mesh_shape_data"
NAMES = ["vrcforge_scan_blendshapes", "vrcforge_apply_blendshapes"]


def make_requester(monkeypatch, transport, extra_leaf_tools=0):
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    full_description = "FULL-DESCRIPTION-BEGIN " + ("complete descriptor text " * 80) + " FULL-DESCRIPTION-END"
    tools = [
        {"name": NAMES[0], "description": full_description, "inputSchema": {"type": "object"},
         "_meta": {"toolBlock": LEAF, "permission": "Read"}},
        {"name": NAMES[1], "description": "write tool description", "inputSchema": {"type": "object"},
         "write": True, "_meta": {"toolBlock": LEAF, "permission": "Write"}},
    ]
    tools.extend(
        {"name": f"vrcforge_fixture_leaf_tool_{index}", "description": f"fixture {index}",
         "inputSchema": {"type": "object"}, "_meta": {"toolBlock": LEAF, "permission": "Read"}}
        for index in range(extra_leaf_tools)
    )

    class Bridge:
        calls = []

        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, exposure_layer="planning", tool_blocks=None, tool_names=None):
            available = tools if exposure_layer == "execution" else tools[:1]
            return {"tools": deepcopy(available)}

        def call_tool(self, name, arguments, **_kwargs):
            self.calls.append((name, arguments))
            return {"ok": True}

    captured = {}
    monkeypatch.setattr(module, "run_stdio_loop", lambda router: captured.setdefault("router", router))
    monkeypatch.setattr(module, "run_standard_stdio_loop", lambda router: captured.setdefault("router", router))
    bridge = Bridge()
    bridge.calls = []
    module.run_stdio_server(bridge, protocol_profile="vrcforge-2026" if transport == "2026" else "mcp-1x", exposure_layer="execution")
    router = captured["router"]
    if transport == "standard":
        router.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})

    def request(method="tools/list", name=None, arguments=None):
        meta = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {}} if transport == "2026" else {}
        params = {"_meta": meta}
        if name is not None:
            params.update(name=name, arguments=arguments or {})
        raw = router.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        return raw[0] if isinstance(raw, tuple) else raw

    return request, bridge, router, full_description


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_omitted_and_empty_names_browse_without_mutating_catalogue(monkeypatch, transport):
    request, bridge, router, full_description = make_requester(monkeypatch, transport)

    seed = request()["result"]["tools"]
    load_descriptor = next(item for item in seed if item["name"] == "vrcforge_load_tool_block")
    assert load_descriptor["inputSchema"]["properties"]["allTools"]["default"] is False
    assert load_descriptor["inputSchema"]["required"] == ["block"]
    assert "ALL tools in this leaf" in load_descriptor["description"]
    assert '"allTools":true' in load_descriptor["description"]

    for args in ({"block": LEAF}, {"block": LEAF, "toolNames": []}):
        result = request("tools/call", name="vrcforge_load_tool_block", arguments=args)["result"]["structuredContent"]
        assert result["status"] == "browsed"
        assert result["changed"] is False
        assert result["toolListChanged"] is False
        assert result["catalogGeneration"] == 0
        assert "activationHandle" not in result
        assert request()["result"]["tools"] == seed
        assert bridge.calls == []
        assert router.drain_notifications() == []

    for args in ({"block": "behavior"}, {"block": "behavior", "toolNames": []}, {"block": LEAF, "toolNames": []}):
        result = request("tools/call", name="vrcforge_load_tool_block", arguments=args)["result"]["structuredContent"]
        assert result["status"] == "browsed"
        assert result["catalogGeneration"] == 0
        assert result["loadedBlocks"] == ["core"]
        assert request()["result"]["tools"] == seed

    selected = request("tools/call", name="vrcforge_load_tool_block", arguments={"block": LEAF, "toolNames": [NAMES[0]]})["result"]["structuredContent"]
    assert selected["status"] == "loaded"
    assert selected["catalogGeneration"] == 1
    assert [item["name"] for item in selected["selectedTools"]] == [NAMES[0]]
    assert selected["selectedTools"][0]["description"] == full_description
    assert router.drain_notifications() == [{"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}]

    # Browsing a loaded leaf does not widen its displayed tools or invalidate its handle.
    handle = selected["activationHandle"]
    before_names = {item["name"] for item in request()["result"]["tools"]}
    browse = request("tools/call", name="vrcforge_load_tool_block", arguments={"block": LEAF})["result"]["structuredContent"]
    assert browse["status"] == "browsed"
    assert browse["catalogGeneration"] == 1
    assert {item["name"] for item in request()["result"]["tools"]} == before_names
    assert "activationHandle" not in browse
    assert router.drain_notifications() == []
    request("tools/call", name="vrcforge_invoke_loaded_read_tool", arguments={
        "activationHandle": handle, "toolName": NAMES[0], "arguments": {}})
    assert bridge.calls == [(NAMES[0], {})]

    all_tools = request("tools/call", name="vrcforge_load_tool_block", arguments={"block": LEAF, "allTools": True})["result"]["structuredContent"]
    assert all_tools["status"] == "loaded"
    assert all_tools["catalogGeneration"] == 2
    assert {item["name"] for item in request()["result"]["tools"]} >= before_names
    assert all_tools["activationHandle"]

    for arguments in (
        {"block": LEAF, "allTools": True, "toolNames": [NAMES[0]]},
        {"block": "behavior", "allTools": True},
        {"block": LEAF, "allTools": None},
        {"block": LEAF, "allTools": "true"},
        {"block": LEAF, "allTools": "*"},
        {"block": LEAF, "toolNames": None},
        {"block": LEAF, "toolNames": "*"},
        {"block": LEAF, "toolNames": ["*"]},
        {"block": LEAF, "allTools": True, "toolNames": None},
        {"block": LEAF, "allTools": True, "toolNames": ""},
    ):
        before_rejection = request()["result"]["tools"]
        rejected = request("tools/call", name="vrcforge_load_tool_block", arguments=arguments)["result"]["structuredContent"]
        assert rejected["ok"] is False
        assert rejected["status"] in {"invalid_tool_selection", "tool_leaf_required"}
        assert request()["result"]["tools"] == before_rejection


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_all_tools_bypasses_exact_name_selection_limit_for_large_leaf(monkeypatch, transport):
    request, _bridge, _router, _description = make_requester(monkeypatch, transport, extra_leaf_tools=36)
    response = request("tools/call", name="vrcforge_load_tool_block", arguments={
        "block": LEAF, "toolNames": [], "allTools": True})["result"]["structuredContent"]
    assert response["status"] == "loaded"
    assert len(response["selectedTools"]) == 38
    assert response["catalogGeneration"] == 1
