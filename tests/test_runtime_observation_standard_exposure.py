import pytest

import dashboard_server
from agent_gateway import AgentGatewayConfig
from tools import vrcforge_agent_mcp_stdio as stdio


@pytest.mark.parametrize("layer", ["planning", "execution"])
def test_standard_router_loads_runtime_observation_in_gm_leaf(tmp_path, monkeypatch, layer):
    gateway = dashboard_server.AGENT_GATEWAY
    old_config, old_audit = gateway.config_path, gateway.audit_dir
    gateway.configure_paths(tmp_path / "config.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, require_token=False,
                                         allow_write_requests=True, execution_mode="full"))

    class Bridge:
        def preflight(self):
            return {"runtimeOnline": True}

        def manifest(self, layer, tool_blocks, tool_names=None):
            return {"tools": gateway.build_external_mcp_tools(layer, tool_blocks=tool_blocks)}

        def call_tool(self, name, arguments, **kwargs):
            return gateway.call_external_mcp_tool(name, arguments)

    captured = {}
    monkeypatch.setattr(stdio, "run_standard_stdio_loop", lambda router: captured.update(router=router))
    try:
        stdio.run_stdio_server(Bridge(), protocol_profile="mcp-1x", exposure_layer=layer)
        router = captured["router"]

        def rpc(method, params):
            response = router.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            assert "error" not in response, response
            return response["result"]

        rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "runtime-observation-exposure", "version": "1"}})
        status, start = "vrcforge_get_runtime_observation", "vrcforge_start_runtime_observation"
        assert {status, start}.isdisjoint({x["name"] for x in rpc("tools/list", {})["tools"]})
        rpc("tools/call", {"name": "vrcforge_load_tool_block",
                           "arguments": {"block": "behavior/interaction_generated_systems"}})
        result = rpc("tools/list", {})
        catalogue = {x["name"]: x for x in result["tools"]}
        assert status in catalogue
        assert (start in catalogue) == (layer == "execution")
        assert "jobId" in catalogue[status]["inputSchema"]["properties"]
        assert not result.get("nextCursor")
    finally:
        gateway.configure_paths(old_config, old_audit)
