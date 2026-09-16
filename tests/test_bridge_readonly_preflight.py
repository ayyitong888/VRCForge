from __future__ import annotations

import pytest

from agent_gateway import AgentGateway, AgentGatewayConfig
from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge


@pytest.mark.parametrize("allow_writes", [False, True])
def test_preflight_accepts_actual_gateway_catalogue_without_legacy_wrapper(tmp_path, monkeypatch, allow_writes):
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, token="fixture-token", allow_write_requests=allow_writes))
    gateway.register_tool("vrcforge_health", "Read health.", "read/debug", lambda _: {"ok": True})
    gateway.approval_transactions.register_write_handler("vrcforge_register_project", "Register project.", "medium", lambda _: {"ok": True})
    monkeypatch.delenv("VRCFORGE_AGENT_TOKEN", raising=False)
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:1", config_path=gateway.config_path, start_runtime=False, timeout_seconds=1)

    def read_catalogue(method, params, **_kwargs):
        assert method == "tools/list"
        return {"tools": gateway.build_external_mcp_tools(params["exposureLayer"], params["toolBlocks"])}

    monkeypatch.setattr(bridge, "_mcp_request", read_catalogue)
    report = bridge.preflight()
    assert report["ok"] is True, report
    assert report["readReady"] is True
    assert report["writeReady"] is allow_writes
    assert report["advertisesRequestApply"] is False
    assert report["advertisesDirectApply"] is False


def test_preflight_still_rejects_write_tool_exposed_during_planning(tmp_path, monkeypatch):
    config = tmp_path / "gateway.json"
    config.write_text('{"token":"fixture-token","enabled":true,"allow_write_requests":false}', encoding="utf-8")
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:1", config_path=config, start_runtime=False, timeout_seconds=1)
    monkeypatch.setattr(bridge, "_mcp_request", lambda *_args, **_kwargs: {
        "tools": [{"name": "vrcforge_register_project", "_meta": {"permission": "Write"}}],
    })
    report = bridge.preflight()
    assert not report["ok"]
    assert report["status"] == "external_tool_contract_not_ready"
