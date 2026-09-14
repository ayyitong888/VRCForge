import pytest

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
