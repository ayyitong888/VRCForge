"""Real mcp-1x router/bridge with HTTP replaced; no live Gateway or Unity."""
import copy
import json

import pytest

from tools import vrcforge_agent_mcp_stdio as stdio


TOOL = "vrcforge_inspect_skinned_mesh_deformation"
BLOCK = "avatar_structure/mesh_shape_data"


@pytest.fixture
def session(monkeypatch, tmp_path, request):
    exposure = getattr(request, "param", "execution")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"token": "fixture-token", "enabled": True,
                                  "allow_write_requests": True}), encoding="utf-8")
    bridge = stdio.VRCForgeBridge(base_url="http://127.0.0.1:1", config_path=config,
                                 timeout_seconds=.1, start_runtime=False)
    calls, routers = [], []
    state = {"visible": True, "authenticated": True, "resources": 1, "prompts": "1"}
    descriptor = {"name": TOOL, "description": "When to use: inspect. When NOT to use: edit.",
                  "inputSchema": {"type": "object"},
                  "_meta": {"toolBlock": BLOCK, "permission": "ReadOnly"}}
    payload = {"ok": True, "status": "ok", "operationId": "fixture-operation",
               "result": {"world": {"aabb": {"min": {"y": -0.123456789}}}}}

    def http(method, path, **kwargs):
        request = kwargs["payload"]
        params = request["params"]
        calls.append((request["method"], copy.deepcopy(params)))
        assert kwargs["token"] == "fixture-token"
        assert method == "POST" and path == "/mcp"
        if not state["authenticated"]:
            raise stdio.ExternalHttpBridgeError(status_code=401, path=path, body="denied")
        if state.get("fault") == "offline":
            raise TimeoutError("offline fixture")
        if state.get("fault") == "malformed":
            return {"jsonrpc": "2.0", "id": request["id"], "result": []}
        if request["method"] == "tools/list":
            result = {"tools": [copy.deepcopy(descriptor)] if state["visible"] else []}
        elif request["method"] == "resources/list":
            result = {"resources": [], "resourceGeneration": state["resources"]}
        elif request["method"] == "prompts/list":
            result = {"prompts": [], "promptGeneration": state["prompts"]}
        else:
            state["resources"] += 1
            result = {"structuredContent": copy.deepcopy(payload)}
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}

    monkeypatch.delenv("VRCFORGE_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(bridge, "request_json", http)
    monkeypatch.setattr(stdio, "run_standard_stdio_loop", routers.append)
    stdio.run_stdio_server(bridge, protocol_profile="mcp-1x", exposure_layer=exposure)
    router = routers[0]
    ident = 0

    def request(method, params):
        nonlocal ident
        ident += 1
        return router.handle({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})

    request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "fixture", "version": "1"}})
    loaded = request("tools/call", {"name": "vrcforge_load_tool_block",
                                    "arguments": {"block": BLOCK, "toolNames": [TOOL]}})
    assert not loaded.get("error")
    calls.clear()
    router.drain_notifications()
    return request, calls, state, router, payload, bridge


def test_one_fresh_catalog_per_read_preserves_payload_and_generation(session):
    request, calls, state, router, payload, _ = session
    exact_arguments = {"projectPath": "fixture-project", "gameObjectPath": "exact/object",
                       "componentIndex": 0, "executionTarget": {"namespace": "fixture-session"}}
    result = request("tools/call", {"name": TOOL, "arguments": exact_arguments})
    assert result["result"]["structuredContent"]["result"] == payload["result"]
    assert [method for method, _ in calls] == [
        "tools/list", "resources/list", "prompts/list", "tools/call",
        "resources/list", "prompts/list"]
    assert calls[0][1]["exposureLayer"] == "execution"
    assert next(params for method, params in calls if method == "tools/call")["arguments"] == exact_arguments
    assert router.drain_notifications() == [
        {"jsonrpc": "2.0", "method": "notifications/resources/list_changed"}]
    calls.clear()
    request("tools/call", {"name": TOOL, "arguments": exact_arguments})
    assert sum(method == "tools/list" for method, _ in calls) == 1


@pytest.mark.parametrize("change", ["removed", "unauthenticated", "unloaded"])
def test_previous_success_does_not_cache_visibility_or_authentication(session, change):
    request, calls, state, _, _, _ = session
    assert not request("tools/call", {"name": TOOL, "arguments": {}}).get("error")
    if change == "removed":
        state["visible"] = False
    elif change == "unauthenticated":
        state["authenticated"] = False
    else:
        request("tools/call", {"name": "vrcforge_unload_tool_block", "arguments": {"block": BLOCK}})
    calls.clear()
    rejected = request("tools/call", {"name": TOOL, "arguments": {}})
    assert rejected["error"]["code"] == (-32603 if change == "unauthenticated" else -32602)
    assert rejected["error"]["data"]["commitState"] == "not_started"
    assert rejected["error"]["data"]["mutationStarted"] is False
    if change == "unauthenticated":
        assert rejected["error"]["data"]["failurePhase"] == "gateway_http_rejection"
    assert not any(method == "tools/call" for method, _ in calls)


def test_explicit_preflight_retains_both_layer_checks(session):
    _, calls, _, _, _, bridge = session
    bridge.preflight()
    assert [(method, params.get("exposureLayer")) for method, params in calls] == [
        ("tools/list", "planning"), ("tools/list", "execution")]


@pytest.mark.parametrize("session", ["planning", "execution"], indirect=True)
def test_catalog_request_tracks_session_exposure(session):
    request, calls, _, _, _, _ = session
    request("tools/list", {})
    exposure = next(params["exposureLayer"] for method, params in calls if method == "tools/list")
    calls.clear()
    assert not request("tools/call", {"name": TOOL, "arguments": {}}).get("error")
    assert all(params["exposureLayer"] == exposure
               for method, params in calls if method == "tools/list")


@pytest.mark.parametrize("fault", ["offline", "malformed"])
def test_manifest_failure_retains_local_controls_and_prevents_domain_call(session, fault):
    request, calls, state, _, _, _ = session
    state["fault"] = fault
    listed = request("tools/list", {})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "vrcforge_bridge_preflight" in names
    assert TOOL not in names
    calls.clear()
    error = request("tools/call", {"name": TOOL, "arguments": {}})["error"]
    assert error["code"] == -32603
    assert error["data"]["failurePhase"] == "tool_catalogue_lookup"
    assert error["data"]["commitState"] == "not_started"
    assert error["data"]["mutationStarted"] is False
    assert not any(method == "tools/call" for method, _ in calls)
