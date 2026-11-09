import pytest

from test_external_mcp_tool_selection import requester
from test_stdio_offline_metadata import _router
from tools import vrcforge_agent_mcp_stdio as stdio


@pytest.mark.parametrize("available", [(), ("execution",), ("planning", "execution")])
def test_block_inventory_distinguishes_unavailable_from_empty(monkeypatch, tmp_path, available):
    router, _, online = _router(monkeypatch, tmp_path)
    online["value"] = True

    def manifest(_self, layer, *_args, **_kwargs):
        if layer not in available:
            raise ConnectionError("connection unavailable")
        return {"tools": []}

    monkeypatch.setattr(stdio.VRCForgeBridge, "manifest", manifest)
    reply = router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "vrcforge_list_tool_blocks", "arguments": {"block": "behavior/parameters_menus_layers"}},
    })["result"]["structuredContent"]
    assert reply["ok"] is True  # Offline local navigation remains usable.
    assert reply["manifestComplete"] is (len(available) == 2)
    assert reply["catalogStatus"] == {0: "unavailable", 1: "partial", 2: "complete"}[len(available)]
    for layer in ("planning", "execution"):
        assert reply["tree"][layer + "ToolCount"] == (0 if layer in available else None)
        assert (layer in reply["unavailableLayers"]) is (layer not in available)
    assert reply["tree"]["loadCall"]["name"] == "vrcforge_load_tool_block"


@pytest.mark.parametrize("transport", ["standard", "2026"])
def test_stdio_search_returns_callable_load_route_in_current_exposure(monkeypatch, transport):
    material_leaf = "appearance/materials_shaders"
    material_preview = "vrcforge_preview_material_shader_assignment"
    material_write = "vrcforge_set_material_shader"
    mesh_leaf = "avatar_structure/mesh_shape_data"
    mesh_read = "vrcforge_scan_blendshapes"
    mesh_write = "vrcforge_apply_blendshapes"

    def descriptor(name, description, block, *, write=False, fields=()):
        return {
            "name": name, "description": description, "write": write,
            "inputSchema": {"type": "object", "properties": {field: {} for field in fields}},
            "_meta": {"toolBlock": block, "permission": "write" if write else "read"},
        }

    catalogue = [
        descriptor(material_preview, "Preview an existing material scalar shader property change.",
                   material_leaf, fields=("materialAssetPath", "propertyChanges")),
        descriptor(material_write, "Apply a previewed material scalar shader property change.",
                   material_leaf, write=True, fields=("materialAssetPath", "propertyChanges")),
        descriptor(mesh_read, "Scan avatar Blendshapes and inspect weights.", mesh_leaf),
        descriptor(mesh_write, "Apply avatar Blendshape weights.", mesh_leaf, write=True),
    ]

    class Bridge:
        calls = []

        def manifest(self, layer, _blocks=None, names=None):
            return {"tools": [
                item for item in catalogue
                if (layer == "execution" or not item["write"])
                and (names is None or item["name"] in names)
            ]}

        def call_tool(self, name, arguments, **_kwargs):
            self.calls.append((name, arguments))
            return {"ok": True, "status": "previewed"}

    captured = []
    monkeypatch.setattr(stdio, "run_standard_stdio_loop", captured.append)
    monkeypatch.setattr(stdio, "run_stdio_loop", captured.append)
    bridge = Bridge()
    stdio.run_stdio_server(
        bridge, protocol_profile="mcp-1x" if transport == "standard" else "vrcforge-2026",
        exposure_layer="planning",
    )
    router = captured[0]
    request = requester(router, transport)

    def call(name, arguments):
        return request("tools/call", name=name, arguments=arguments)["result"]["structuredContent"]

    control = next(
        item for item in request()["result"]["tools"] if item["name"] == "vrcforge_list_tool_blocks"
    )
    assert "query" in control["inputSchema"]["properties"]
    result = request("tools/call", name=control["name"], arguments={
        "query": "preview material scalar propertyChanges",
    })["result"]
    search = result["structuredContent"]
    assert search["status"] == "found"
    match = search["matches"][0]
    assert match["leafPath"] == material_leaf
    assert match["toolNames"] == [material_preview]
    assert material_preview in result["content"][0]["text"]
    assert material_preview not in {item["name"] for item in request()["result"]["tools"]}
    assert not router.drain_notifications()

    load = match["loadCall"]
    activated = call(load["name"], load["arguments"])
    assert [item["name"] for item in activated["selectedTools"]] == [material_preview]
    assert router.drain_notifications() == [
        {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
    ]
    assert call(control["name"], {"query": material_preview})["matches"][0]["loadCall"] is None
    assert all(
        material_write not in match["toolNames"]
        for match in call(control["name"], {"query": material_write})["matches"]
    )
    browse = call(control["name"], {"block": mesh_leaf})
    assert {item["name"] for item in browse["tree"]["tools"]} == {mesh_read}
    assert browse["tree"]["executionToolCount"] == 2

    args = {"materialAssetPath": "Assets/Face.mat", "propertyChanges": [{"propertyName": "_UseMain2ndTex", "value": 1}]}
    receipt = call("vrcforge_invoke_loaded_read_tool", {
        "activationHandle": activated["activationHandle"], "toolName": material_preview, "arguments": args,
    })
    assert receipt["status"] == "previewed"
    assert bridge.calls == [(material_preview, args)]
