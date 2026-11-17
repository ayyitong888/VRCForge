from __future__ import annotations

import json

import pytest

from agent_gateway import AgentGateway, AgentGatewayConfig
from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge


@pytest.mark.parametrize("has_write_tools", [False, True])
def test_preflight_reports_write_readiness_from_gateway_catalogue(tmp_path, monkeypatch, has_write_tools):
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, token="fixture-token"))
    gateway.register_tool("vrcforge_health", "Read health.", "read/debug", lambda _: {"ok": True})
    if has_write_tools:
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
    assert report["writeReady"] is has_write_tools
    assert "allowWriteRequests" not in report
    assert report["advertisesRequestApply"] is False
    assert report["advertisesDirectApply"] is False


def test_preflight_ignores_a_stale_local_write_switch(tmp_path, monkeypatch):
    config = tmp_path / "gateway.json"
    config.write_text(json.dumps({"token": "fixture-token", "enabled": True, "allow_write_requests": False}), encoding="utf-8")
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:1", config_path=config, start_runtime=False, timeout_seconds=1)

    def read_catalogue(_method, params, **_kwargs):
        tools = [{"name": "vrcforge_health", "_meta": {"permission": "ReadOnly"}}]
        if params["exposureLayer"] == "execution":
            tools.append({"name": "vrcforge_register_project", "_meta": {"permission": "Write"}})
        return {"tools": tools}

    monkeypatch.setattr(bridge, "_mcp_request", read_catalogue)
    report = bridge.preflight()
    assert report["readReady"] and report["writeReady"]
    assert "allowWriteRequests" not in report


def test_planning_pinned_preflight_requests_only_read_catalogue(tmp_path, monkeypatch):
    config = tmp_path / "gateway.json"
    config.write_text('{"token":"fixture-token","enabled":true}', encoding="utf-8")
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:1", config_path=config, start_runtime=False, timeout_seconds=1)
    requests = []

    def read_catalogue(_method, params, **_kwargs):
        requests.append(params["exposureLayer"])
        return {"tools": [{"name": "vrcforge_health", "_meta": {"permission": "ReadOnly"}}]}

    monkeypatch.setattr(bridge, "_mcp_request", read_catalogue)
    report = bridge.preflight("planning")
    assert requests == ["planning"]
    assert report["externalMcpExposureLayer"] == "planning"
    assert report["readReady"] and not report["writeReady"]


def test_preflight_still_rejects_write_tool_exposed_during_planning(tmp_path, monkeypatch):
    config = tmp_path / "gateway.json"
    config.write_text('{"token":"fixture-token","enabled":true}', encoding="utf-8")
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:1", config_path=config, start_runtime=False, timeout_seconds=1)
    monkeypatch.setattr(bridge, "_mcp_request", lambda *_args, **_kwargs: {
        "tools": [{"name": "vrcforge_register_project", "_meta": {"permission": "Write"}}],
    })
    report = bridge.preflight()
    assert not report["ok"]
    assert report["status"] == "external_tool_contract_not_ready"
