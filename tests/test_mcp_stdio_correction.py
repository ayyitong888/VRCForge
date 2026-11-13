import json

import pytest

from test_external_mcp_tool_selection import LEAF, NAMES, stdio_requester


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_stdio_search_errors_guide_the_next_valid_call(monkeypatch, transport):
    request, bridge = stdio_requester(monkeypatch, transport, "execution", [])
    reply = request("tools/call", name="vrcforge_list_tool_blocks", arguments={"query": ""})
    result = reply["result"]
    assert result["isError"] is True
    receipt = result["structuredContent"]
    assert receipt["recoveryAction"] == "fix_and_retry"
    assert receipt["mutationStarted"] is False
    assert receipt["issues"][0]["path"] == "query"
    assert json.loads(result["content"][0]["text"])["recoveryToolCall"] == receipt["recoveryToolCall"]
    call = receipt["recoveryToolCall"]
    assert request("tools/call", **call)["result"]["isError"] is False
    assert bridge.calls == []


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_stdio_browse_call_loads_full_selected_description(monkeypatch, transport):
    long_description = "Use a live object reference from the current scene. " * 4 + "Trailing scope rule."
    fixture = {"name": NAMES[0], "description": "Read fixture", "_meta": {"toolBlock": LEAF, "permission": "read"},
               "inputSchema": {"type": "object", "properties": {"selector": {"type": "string", "description": long_description}}}}
    request, bridge = stdio_requester(monkeypatch, transport, "execution", [fixture])
    result = request("tools/call", name="vrcforge_load_tool_block", arguments={"block": LEAF, "toolNames": []})["result"]
    assert result["structuredContent"]["changed"] is False
    call = json.loads(result["content"][0]["text"])["nextCall"]
    assert call["arguments"]["toolNames"] == [NAMES[0]]
    loaded = request("tools/call", **call)["result"]["structuredContent"]
    assert loaded["selectedTools"][0]["inputSchema"]["properties"]["selector"]["description"] == long_description
    assert bridge.calls == []
    invocation = loaded["invocationTemplates"][0]
    invocation["arguments"]["arguments"] = {"selector": "fixture"}
    assert request("tools/call", **invocation)["result"]["isError"] is False
    assert bridge.calls == [(NAMES[0], {"selector": "fixture"})]


def test_schema_errors_include_exact_enum_suggested_field_and_type_example():
    from runtime_planner_service import validate_planner_tool_arguments

    schema = {"type": "object", "additionalProperties": False, "properties": {
        "mode": {"enum": ["linear", "constant"]}, "speed": {"type": "number", "minimum": 1},
    }}
    result = validate_planner_tool_arguments(schema, {"mode": "wrong", "speeed": 2, "speed": "1"})
    issues = {issue["path"]: issue for issue in result["issues"]}
    assert issues["mode"]["allowedValues"] == ["linear", "constant"]
    assert issues["speeed"]["suggestedFields"] == ["speed"]
    assert issues["speed"]["example"] == 1


def test_chinese_and_english_queries_use_the_same_material_route():
    from types import SimpleNamespace
    from tool_discovery_search import search_tool_leaves

    records = [SimpleNamespace(name="inspect_material", runtime_name="inspect_material", block="appearance/materials_shaders",
                               description="Inspect a material.", input_schema={}, write=False)]
    chinese = search_tool_leaves("检查材质", records, {})
    english = search_tool_leaves("inspect material", records, {})
    assert chinese["matches"] == english["matches"] and chinese["matches"]


def test_modern_stdio_loading_keeps_the_default_catalogue_stable(monkeypatch):
    fixture = {"name": NAMES[0], "description": "Read fixture", "inputSchema": {"type": "object"},
               "_meta": {"toolBlock": LEAF, "permission": "read"}}
    request, _ = stdio_requester(monkeypatch, "2026", "execution", [fixture])
    before = request()["result"]["tools"]
    loaded = request("tools/call", name="vrcforge_load_tool_block", arguments={"block": LEAF, "toolNames": [NAMES[0]]})
    assert loaded["result"]["structuredContent"]["selectedTools"][0]["name"] == NAMES[0]
    assert loaded["result"]["structuredContent"]["toolListChanged"] is False
    assert request()["result"]["tools"] == before
    assert request("tools/call", name="vrcforge_unload_tool_block", arguments={"block": LEAF})["result"]["isError"] is False
    assert request()["result"]["tools"] == before


def test_modern_rpc_distinguishes_missing_metadata_from_unsupported_version():
    from agent_mcp_2026 import Mcp2026Router
    from test_agent_mcp_2026 import _request

    router = Mcp2026Router(lambda _: [], lambda *_: {})
    for metadata in ({}, {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}):
        response, status = router.handle(_request("tools/list", {"_meta": metadata}))
        assert status == 400 and response["error"]["code"] == -32602
    response, status = router.handle(_request("tools/list", {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2099-01-01", "io.modelcontextprotocol/clientCapabilities": {},
    }}))
    assert status == 400 and response["error"]["data"]["supported"] == ["2026-07-28"]
    assert response["error"]["code"] == -32022


def test_invalid_activation_returns_a_search_call_without_dispatch(monkeypatch):
    request, bridge = stdio_requester(monkeypatch, "2026", "execution", [])
    response = request("tools/call", name="vrcforge_invoke_loaded_read_tool", arguments={
        "activationHandle": "expired", "toolName": NAMES[0], "arguments": {},
    })["result"]
    assert response["isError"] is True
    receipt = response["structuredContent"]
    assert receipt["recoveryAction"] == "rediscover_target"
    assert request("tools/call", **receipt["recoveryToolCall"])["result"]["isError"] is False
    assert bridge.calls == []


def test_modern_stdio_ignores_unmatched_cancel_and_processes_next_frame():
    import io
    from agent_mcp_2026 import Mcp2026Router, run_stdio_loop
    from test_agent_mcp_2026 import _request

    router = Mcp2026Router(lambda _: [], lambda *_: {})
    cancellation = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 42}}
    source = io.StringIO("\n".join(map(json.dumps, [cancellation, _request("tools/list", request_id=43)])) + "\n")
    sink = io.StringIO()
    run_stdio_loop(router, input_stream=source, output_stream=sink)
    assert [json.loads(line)["id"] for line in sink.getvalue().splitlines()] == [43]


@pytest.mark.parametrize("method,field", [("resources/read", "uri"), ("prompts/get", "name")])
def test_http_name_header_roundtrips_from_bridge(monkeypatch, tmp_path, method, field):
    from starlette.testclient import TestClient
    from agent_mcp_2026 import Mcp2026Router, create_asgi_app
    from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge

    value = "vrcforge://材质" if field == "uri" else "材质"
    seen = []
    router = Mcp2026Router(lambda _: [], lambda *_: {},
                           resource_read=lambda uri: seen.append(uri) or {"contents": [{"uri": uri, "text": "ok"}]},
                           prompt_get=lambda name, _: seen.append(name) or {"messages": []})
    bridge = VRCForgeBridge(base_url="http://127.0.0.1:8757", config_path=None, timeout_seconds=1, start_runtime=False)
    with TestClient(create_asgi_app(router, route_path="/mcp"), base_url="http://127.0.0.1") as client:
        def send(_, path, *, payload, extra_headers, response_headers, **kwargs):
            if payload["method"] == method:
                assert extra_headers["Mcp-Name"].startswith("=?base64?")
            response = client.post(path, json=payload, headers=extra_headers)
            response.raise_for_status()
            response_headers.update(response.headers.items())
            if "mcp-session-id" in response.headers:
                response_headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            return response.json()
        monkeypatch.setattr(bridge, "request_json", send)
        bridge._mcp_request(method, {field: value}, token="fixture")
    assert seen == [value]

@pytest.mark.parametrize(
    ("invalid", "field", "hint", "expected"),
    [
        ({"scope": "everything"}, "scope", "allowedValues", ["face", "face_static", "face_unknown", "all"]),
        ({"scpoe": "all"}, "scpoe", "suggestedFields", ["scope"]),
        ({"blendshapeNames": "Ears_on"}, "blendshapeNames", "expected", "array"),
    ],
)
def test_external_blendshape_argument_error_precedes_unity_dispatch(tmp_path, monkeypatch, invalid, field, hint, expected):
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    calls = []

    def read(tool, arguments, *, agent_name):
        calls.append(arguments)
        return {"ok": True}

    monkeypatch.setattr(gateway, "_call_external_mcp_read_tool", read)
    name = "vrcforge_scan_blendshapes"
    gateway.register_tool(name, "Scan blendshapes", "avatar", lambda _: {})
    valid = {"projectPath": str(tmp_path), "scope": "all", "blendshapeNames": ["Ears_on"]}
    result = gateway.call_external_mcp_tool(name, {**valid, **invalid})
    assert result["ok"] is False
    assert result["recoveryAction"] == "fix_and_retry"
    assert result["mutationStarted"] is False
    assert result["toolRoutingStarted"] is False
    issue = next(item for item in result["issues"] if item["path"] == field)
    assert issue[hint] == expected
    if field == "blendshapeNames":
        assert issue["example"] == ["Ears_on"]
    assert calls == []
    assert gateway.call_external_mcp_tool(name, valid)["ok"] is True
    assert calls == [valid]
