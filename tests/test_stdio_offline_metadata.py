from __future__ import annotations

import urllib.error

from tools import vrcforge_agent_mcp_stdio as stdio


def _router(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        stdio,
        "run_standard_stdio_loop",
        lambda router: captured.setdefault("router", router),
    )
    bridge = stdio.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=tmp_path / "unused.json",
        timeout_seconds=0.01,
        start_runtime=False,
    )
    monkeypatch.setattr(bridge, "require_token", lambda: "fixture-token")
    calls = []

    online = {"value": False, "resources": 0, "prompts": 0}

    def request(*_args, **kwargs):
        calls.append(kwargs.get("payload", {}).get("method"))
        method = kwargs.get("payload", {}).get("method")
        if not online["value"]:
            raise urllib.error.URLError("offline fixture")
        if method == "tools/list":
            return {"result": {"tools": []}}
        if method == "resources/list":
            online["resources"] += 1
            return {"result": {"resources": [], "resourceGeneration": online["resources"]}}
        if method == "prompts/list":
            online["prompts"] += 1
            return {"result": {"prompts": [], "promptGeneration": f"p{online['prompts']}"}}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(bridge, "request_json", request)
    stdio.run_stdio_server(bridge, protocol_profile="mcp-1x", exposure_layer="execution")
    router = captured["router"]
    router.handle({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    })
    return router, calls, online


def test_real_bridge_offline_local_block_inventory_skips_revision_polls(monkeypatch, tmp_path):
    router, calls, _online = _router(monkeypatch, tmp_path)
    response = router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "vrcforge_list_tool_blocks", "arguments": {}},
    })
    assert "error" not in response
    assert response["result"]["structuredContent"]["ok"] is True
    assert "resources/list" not in calls
    assert "prompts/list" not in calls


def test_real_bridge_offline_preflight_returns_tool_result_without_revision_polls(monkeypatch, tmp_path):
    router, calls, _online = _router(monkeypatch, tmp_path)
    response = router.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "vrcforge_bridge_preflight", "arguments": {}},
    })
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "resources/list" not in calls
    assert "prompts/list" not in calls


def test_real_bridge_same_session_recovers_revision_notifications(monkeypatch, tmp_path):
    router, calls, online = _router(monkeypatch, tmp_path)
    offline = router.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "vrcforge_list_tool_blocks", "arguments": {}},
    })
    assert "error" not in offline
    online["value"] = True
    recovered = router.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "vrcforge_list_tool_blocks", "arguments": {}},
    })
    assert "error" not in recovered
    notifications = router.drain_notifications()
    assert {item["method"] for item in notifications} == {
        "notifications/resources/list_changed",
        "notifications/prompts/list_changed",
    }
    assert calls.count("resources/list") == 2
    assert calls.count("prompts/list") == 2
