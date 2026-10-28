"""Precise integration blocks must survive internal and stdio projection."""
import pytest

from internal_tool_blocks import canonical_tool_owner
from test_stdio_offline_metadata import _router
from tools import vrcforge_agent_mcp_stdio as stdio


@pytest.mark.parametrize("block,names", [
    ("integrations/modular-avatar", ("setup_outfit", "preview_setup_outfit")),
    ("integrations/vrcfury", ("create_component_feature", "preview_component_feature")),
])
@pytest.mark.parametrize("prefix", ["vrcforge_", "vrc_", "unity_"])
def test_precise_legacy_integration_owner_precedes_tool_name_keywords(block, names, prefix):
    for transport_block in (block, "unity/" + block):
        for name in names:
            assert canonical_tool_owner(transport_block, prefix + name) == "behavior/interaction_generated_systems"
    # Explicit operation owners remain more precise than an integration leaf.
    assert canonical_tool_owner(block, "vrcforge_apply_clothing_fx") == "behavior/parameters_menus_layers"


@pytest.mark.parametrize("prefix", ["vrcforge_", "vrc_"])
def test_stdio_inventory_and_selected_load_agree_with_integration_alias(monkeypatch, tmp_path, prefix):
    router, _, online = _router(monkeypatch, tmp_path)
    online["value"] = True
    descriptors = [
        {"name": prefix + name, "description": "Integration fixture", "inputSchema": {"type": "object"},
         "write": write, "_meta": {"toolBlock": block, "permission": "Write" if write else "ReadOnly"}}
        for block, name, write in [
            ("integrations/modular-avatar", "setup_outfit", True),
            ("integrations/modular-avatar", "preview_setup_outfit", False),
            ("integrations/vrcfury", "create_component_feature", True),
            ("integrations/vrcfury", "preview_component_feature", False),
        ]
    ]
    def manifest(_self, layer, _blocks=None, names=None):
        return {"tools": [item for item in descriptors if (layer == "execution" or not item["write"])
                          and (names is None or item["name"] in names)]}
    monkeypatch.setattr(stdio.VRCForgeBridge, "manifest", manifest)
    def invoke(name, arguments):
        return router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})["result"]["structuredContent"]
    for block in ("integrations/modular-avatar", "integrations/vrcfury", "behavior/interaction_generated_systems"):
        inventory = invoke("vrcforge_list_tool_blocks", {"block": block})
        assert {item["name"] for item in inventory["tree"]["tools"]} == {item["name"] for item in descriptors}
        loaded = invoke("vrcforge_load_tool_block", {"block": block,
                        "toolNames": [prefix + "setup_outfit", prefix + "preview_setup_outfit"]})
        assert loaded["ok"] is True
