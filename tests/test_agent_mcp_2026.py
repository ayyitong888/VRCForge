from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import time

import httpx
import pytest

from agent_mcp_2026 import PROTOCOL_VERSION, Mcp2026Router, create_asgi_app, run_stdio_loop
from agent_gateway import AgentGateway, AgentGatewayError


def _meta(**extra):
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        **extra,
    }


def _request(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {"_meta": _meta(), **(params or {})}}


@pytest.fixture
def router():
    return Mcp2026Router(
        lambda _params: [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}, {"name": "generic"}],
        lambda name, arguments: {"ok": True, "echo": f"{name}:{arguments.get('value', '')}"},
        server_name="VRCForge",
        server_version="1.4.0",
    )


def test_discover_list_and_call_are_strict_2026(router):
    discover, status = router.handle(_request("server/discover"))
    assert status == 200
    assert discover["result"]["supportedVersions"] == [PROTOCOL_VERSION]
    assert discover["result"]["capabilities"] == {"tools": {}}
    assert discover["result"]["resultType"] == "complete"
    assert discover["result"]["_meta"]["io.modelcontextprotocol/serverInfo"] == {"name": "VRCForge", "version": "1.4.0"}

    listed, status = router.handle(_request("tools/list"))
    assert status == 200
    assert listed["result"]["tools"][1]["inputSchema"] == {"type": "object", "additionalProperties": True}
    assert "When to use:" in listed["result"]["tools"][0]["description"]
    assert "When NOT to use:" in listed["result"]["tools"][0]["description"]
    assert "Negative example:" in listed["result"]["tools"][0]["description"]

    called, status = router.handle(_request("tools/call", {"name": "echo", "arguments": {"value": "ok"}}))
    assert status == 200
    structured = called["result"]["structuredContent"]
    assert {key: structured[key] for key in ("ok", "echo")} == {
        "ok": True,
        "echo": "echo:ok",
    }
    assert structured["outcome"]["status"] == "ok"
    assert json.loads(called["result"]["content"][0]["text"]) == called["result"]["structuredContent"]
    assert called["result"]["isError"] is False


def test_nested_mcp_catalogue_preserves_write_permission_annotation() -> None:
    routed = Mcp2026Router(
        lambda _params: [
            {
                "name": "write_from_upstream_mcp",
                "description": "Write one approved object.",
                "inputSchema": {"type": "object"},
                "_meta": {"permission": "Write", "toolBlock": "avatar"},
            }
        ],
        lambda _name, _arguments: {"ok": True},
    )

    listed, status = routed.handle(_request("tools/list"))

    assert status == 200
    tool = listed["result"]["tools"][0]
    assert tool["annotations"]["readOnlyHint"] is False
    assert tool["_meta"]["permission"] == "Write"


def test_call_uses_execution_catalogue_when_client_does_not_repeat_layer() -> None:
    observed_layers = []

    def list_tools(params):
        layer = params.get("exposureLayer", "planning")
        observed_layers.append(layer)
        if layer == "execution":
            return [{"name": "write_request"}]
        return [{"name": "read_status"}]

    routed = Mcp2026Router(
        list_tools,
        lambda name, arguments: {"ok": True, "name": name, "arguments": arguments},
    )
    listed, listed_status = routed.handle(_request("tools/list"))
    called, called_status = routed.handle(
        _request("tools/call", {"name": "write_request", "arguments": {"value": 1}})
    )

    assert listed_status == 200
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["read_status"]
    assert called_status == 200
    assert called["result"]["structuredContent"]["name"] == "write_request"
    assert observed_layers == ["planning", "execution"]


def test_2026_stdio_notifies_client_when_tool_blocks_change() -> None:
    revision = {"value": 0}

    def call_tool(name, _arguments):
        assert name == "load_block"
        revision["value"] += 1
        return {"ok": True, "toolListChanged": True}

    routed = Mcp2026Router(
        lambda _params: [{"name": "load_block"}],
        call_tool,
        tool_list_revision=lambda: revision["value"],
    )
    source = io.StringIO(
        "\n".join(
            [
                json.dumps(_request("server/discover", request_id=1)),
                json.dumps(_request("tools/call", {"name": "load_block", "arguments": {}}, request_id=2)),
                "",
            ]
        )
    )
    sink = io.StringIO()

    assert run_stdio_loop(routed, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["result"]["capabilities"] == {"tools": {"listChanged": True}}
    assert output[1]["result"]["structuredContent"]["toolListChanged"] is True
    assert output[1]["result"]["structuredContent"]["operationId"].startswith("mcpread_")
    assert output[1]["result"]["structuredContent"]["operationStatus"] == "success"
    assert output[1]["result"]["structuredContent"]["resources"]["status"] == "unavailable"
    assert output[1]["result"]["structuredContent"]["promptSkillProvenance"]["status"] == "unavailable"
    assert output[2] == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}


@pytest.mark.parametrize(
    "message, code",
    [
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, -32020),
        (_request("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2025-11-25", "io.modelcontextprotocol/clientCapabilities": {}}}), -32022),
        (_request("tools/list", {"_meta": _meta(**{"io.modelcontextprotocol/clientInfo": {"name": "", "version": "1"}})}), -32602),
        (_request("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION}}), -32021),
        (_request("initialize"), -32601),
        (_request("unknown"), -32601),
        (_request("tools/call", {"name": "", "arguments": []}), -32602),
        (_request("tools/call", {"name": "hidden", "arguments": {}}), -32602),
        (_request("tools/list", {"exposureLayer": "legacy"}), -32602),
        (_request("tools/list", {"exposureLayer": 1}), -32602),
    ],
)
def test_router_rejects_legacy_and_bad_shapes(router, message, code):
    response, status = router.handle(message)
    assert response["error"]["code"] == code
    assert response["error"]["data"]["schema"] == "vrcforge.external_tool_error.v1"
    assert response["error"]["data"]["protocolNamespace"] == (
        "io.modelcontextprotocol/protocolVersion"
    )
    assert response["error"]["data"]["protocolVersion"] == "2026-07-28"
    assert response["error"]["data"]["mutationStarted"] is False
    assert status == (404 if code == -32601 else 400)


def test_http_transport_enforces_headers_origin_bearer_and_body(router):
    async def exercise():
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good", max_body_bytes=400)
        transport = httpx.ASGITransport(app=app)
        headers = {
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": PROTOCOL_VERSION,
            "mcp-method": "tools/call",
            "mcp-name": "echo",
            "authorization": "Bearer good",
            "origin": "http://127.0.0.1:1234",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/", json=_request("tools/call", {"name": "echo", "arguments": {}}), headers=headers)
            assert response.status_code == 200
            assert response.json()["result"]["resultType"] == "complete"

            for bad_headers, expected_status, expected_code in (
                ({**headers, "origin": "https://evil.example"}, 403, -32600),
                ({**headers, "authorization": "Bearer bad"}, 401, -32000),
                ({**headers, "mcp-name": "other"}, 400, -32020),
                ({**headers, "accept": "application/json"}, 400, -32020),
            ):
                rejected = await client.post("/", json=_request("tools/call", {"name": "echo", "arguments": {}}), headers=bad_headers)
                assert rejected.status_code == expected_status
                assert rejected.json()["error"]["code"] == expected_code
            unknown_headers = {key: value for key, value in headers.items() if key != "mcp-name"}
            unknown = await client.post("/", json=_request("other"), headers={**unknown_headers, "mcp-method": "other"})
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == -32601
            assert (await client.get("/")).status_code == 405
            assert (await client.get("/")).headers["allow"] == "POST"
            assert (await client.delete("/")).json()["error"]["code"] == -32601
            batch = await client.post("/", content=b"[]", headers={**headers, "mcp-method": "tools/list"})
            assert batch.status_code == 400
            assert batch.json()["error"]["code"] == -32600
            too_large = await client.post("/", content=b"{" + b"x" * 1000, headers={**headers, "mcp-method": "tools/list"})
            assert too_large.status_code == 400
            non_finite = await client.post(
                "/",
                content=json.dumps(_request("tools/call", {"name": "echo", "arguments": {"value": float("nan")}})).encode(),
                headers=headers,
            )
            assert non_finite.status_code == 400
            assert non_finite.json()["error"]["code"] == -32700

            duplicate_cases = (
                ([*headers.items(), ("authorization", "Bearer good")], 401),
                ([*headers.items(), ("mcp-protocol-version", PROTOCOL_VERSION)], 400),
                ([*headers.items(), ("mcp-method", "tools/call")], 400),
                ([*headers.items(), ("mcp-name", "echo")], 400),
                ([*headers.items(), ("origin", "http://localhost")], 400),
                ([*headers.items(), ("host", "test"), ("host", "evil.example")], 400),
            )
            for duplicate_headers, expected_status in duplicate_cases:
                duplicate = await client.post(
                    "/",
                    content=json.dumps(_request("tools/call", {"name": "echo", "arguments": {}})).encode(),
                    headers=duplicate_headers,
                )
                assert duplicate.status_code == expected_status

            extra_name = await client.post(
                "/",
                json=_request("tools/list"),
                headers={**headers, "mcp-method": "tools/list"},
            )
            assert extra_name.status_code == 400
            assert extra_name.json()["error"]["code"] == -32020

    asyncio.run(exercise())


def test_http_concurrent_calls_offload_catalogue_without_bypassing_exposure():
    def slow_catalogue(_params):
        time.sleep(0.20)
        return [{"name": "echo", "inputSchema": {"type": "object"}}]

    async def call_tool(name, arguments):
        return {"ok": True, "name": name, "value": arguments.get("value")}

    router = Mcp2026Router(
        slow_catalogue,
        call_tool,
        tool_call_catalogue=slow_catalogue,
        server_name="VRCForge",
        server_version="1.8.0",
    )

    async def exercise():
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good")
        transport = httpx.ASGITransport(app=app)
        base_headers = {
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": PROTOCOL_VERSION,
            "mcp-method": "tools/call",
            "mcp-name": "echo",
            "authorization": "Bearer good",
            "origin": "http://127.0.0.1:1234",
            "content-type": "application/json",
        }
        loop_progress = asyncio.Event()
        probe_delay = None
        launch_at = None

        async def probe_loop():
            nonlocal probe_delay
            await asyncio.sleep(0.03)
            probe_delay = time.perf_counter() - launch_at
            loop_progress.set()

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = client.post(
                "/",
                json=_request("tools/call", {"name": "echo", "arguments": {"value": 1}}, request_id=1),
                headers=base_headers,
            )
            second = client.post(
                "/",
                json=_request("tools/call", {"name": "echo", "arguments": {"value": 2}}, request_id=2),
                headers=base_headers,
            )
            launch_at = time.perf_counter()
            responses = await asyncio.gather(first, second, probe_loop())
            assert loop_progress.is_set()
            assert probe_delay is not None and probe_delay < 0.15
            assert [response.status_code for response in responses[:2]] == [200, 200]
            assert [response.json()["result"]["structuredContent"]["value"] for response in responses[:2]] == [1, 2]

            hidden_headers = {**base_headers, "mcp-name": "hidden"}
            hidden = await client.post(
                "/",
                json=_request("tools/call", {"name": "hidden", "arguments": {}}, request_id=3),
                headers=hidden_headers,
            )
            assert hidden.status_code == 400
            assert hidden.json()["error"]["code"] == -32602

    asyncio.run(exercise())


def test_stdio_is_newline_json_without_length_prefix(router):
    source = io.StringIO(json.dumps(_request("tools/list")) + "\n" + "not-json\n")
    sink = io.StringIO()
    assert run_stdio_loop(router, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["result"]["resultType"] == "complete"
    assert output[1]["error"]["code"] == -32700
    assert "Content-Length" not in sink.getvalue()


def test_stdio_rejects_oversized_frame_then_continues(router):
    valid = json.dumps(_request("tools/list"))
    source = io.StringIO("{" + "x" * 600 + "\n" + valid + "\n")
    sink = io.StringIO()
    assert run_stdio_loop(router, input_stream=source, output_stream=sink, max_line_bytes=512) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["error"]["code"] == -32700
    assert output[1]["result"]["resultType"] == "complete"


@pytest.mark.parametrize("bad_value", [object(), float("nan")])
def test_tool_result_must_be_strict_json(bad_value):
    invalid_router = Mcp2026Router(
        lambda _params: [{"name": "bad"}],
        lambda _name, _arguments: {"value": bad_value},
    )
    response, status = invalid_router.handle(_request("tools/call", {"name": "bad", "arguments": {}}))
    assert status == 500
    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "Internal MCP server error"


def test_prompt_provenance_rejection_is_a_pre_routing_validation_error():
    def reject(_name, _arguments):
        raise AgentGatewayError(
            "Prompt/Skill provenance rejected this Tool call: declared Tool set mismatch",
            status_code=409,
            cause_code="prompt_skill_provenance_mismatch",
        )

    router = Mcp2026Router(
        lambda _params: [{"name": "vrcforge_get_asset_info"}],
        reject,
    )
    response, status = router.handle(
        _request(
            "tools/call",
            {
                "name": "vrcforge_get_asset_info",
                "arguments": {"promptSkillProvenance": {"contentHash": "stale"}},
            },
        )
    )

    assert status == 409
    assert response["error"]["code"] == -32602
    data = response["error"]["data"]
    assert data["errorCode"] == "prompt_skill_provenance_mismatch"
    assert data["failurePhase"] == "prompt_skill_provenance_validation"
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["committed"] is False
    assert data["commitState"] == "not_started"
    assert data["commitStateKnown"] is True
    assert data["recovery"]["required"] is False
    assert "http_500" not in json.dumps(data)


def test_actual_ambiguous_project_guard_returns_http409_without_recovery(tmp_path):
    from fastapi.testclient import TestClient

    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway._register_runtime_project_scope(str(tmp_path / "project-a"))
    gateway._register_runtime_project_scope(str(tmp_path / "project-b"))
    calls = []
    name = "vrcforge_avatar_encryption_scan"
    gateway.register_tool(name, "Read encryption candidates.", "read/debug", lambda args: calls.append(args))
    router = Mcp2026Router(
        lambda _params: [{"name": name}],
        lambda tool, arguments: gateway.call_external_mcp_tool(tool, arguments),
    )
    with TestClient(create_asgi_app(router)) as client:
        response = client.post(
            "/",
            headers={"Accept": "application/json, text/event-stream", "Mcp-Method": "tools/call", "Mcp-Name": name, "MCP-Protocol-Version": PROTOCOL_VERSION},
            json=_request("tools/call", {"name": name, "arguments": {}}),
        )
    assert response.status_code == 409, response.text
    data = response.json()["error"]["data"]
    assert calls == []
    assert data["errorCode"] == "external_mcp_project_scope_ambiguous"
    assert data["failurePhase"] == "external_mcp_project_scope_validation"
    assert data["operationKind"] == "read"
    assert data["tool"] == name
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["committed"] is False
    assert data["commitState"] == "not_started"
    assert data["recovery"]["required"] is False
    assert data["checkpointRecoveryRequired"] is False
    assert "arguments.projectPath" in data["nextAction"]


@pytest.mark.parametrize("started", [None, True])
def test_project_scope_error_without_explicit_no_route_proof_stays_unknown(started):
    def fail(_tool, _arguments):
        raise AgentGatewayError(
            "Project scope rejection without pre-routing proof",
            status_code=409,
            cause_code="external_mcp_project_scope_ambiguous",
            failure_phase="external_mcp_project_scope_validation",
            tool_routing_started=started,
            mutation_started=started,
        )
    router = Mcp2026Router(lambda _params: [{"name": "read"}], fail)
    response, status = router.handle(_request("tools/call", {"name": "read", "arguments": {}}))
    assert status == 500
    assert response["error"]["data"]["commitState"] == "unknown"


def test_actual_820_preparation_rejection_repairs_outer_no_write_state():
    fixture = Path(__file__).parent / "fixtures" / "actual820_preparation_rejection.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    callback_result = recorded

    router = Mcp2026Router(
        lambda _params: [{"name": "vrcforge_start_runtime_observation", "inputSchema": {"type": "object"}, "_meta": {"permission": "Write"}}],
        lambda _name, _arguments: callback_result,
    )
    response, status = router.handle(
        _request(
            "tools/call",
            {
                "name": "vrcforge_start_runtime_observation",
                "arguments": {"width": 576},
            },
        )
    )

    assert status == 200
    structured = response["result"]["structuredContent"]
    assert structured["errorDetails"]["errorCode"] == "external_write_preparation_rejected"
    assert structured["errorDetails"]["failurePhase"] == "before_write_handler"
    assert structured["mutationStarted"] is False
    assert structured["committed"] is False
    assert structured["commitState"] == "not_started"
    assert structured["persistenceState"] == "not_applicable"
    assert structured["outcome"]["mutationStarted"] is False
    assert structured["outcome"]["commitState"] == "not_started"


def test_routed_unknown_callback_result_keeps_unknown_outer_state():
    callback_result = {
        "ok": False,
        "status": "failed",
        "tool": "vrcforge_start_runtime_observation",
        "error": "The routed operation timed out.",
        "operationStatus": "failed",
        "toolRoutingStarted": True,
        "mutationStarted": None,
        "committed": None,
        "commitState": "unknown",
        "persistenceState": "unknown",
        "outcome": {
            "schema": "vrcforge.tool_result.v1",
            "success": False,
            "status": "failed",
            "summary": "The routed operation timed out.",
            "errorCode": "external_timeout",
            "failureLayer": "unity_core",
            "failurePhase": "after_route",
            "toolRoutingStarted": True,
            "mutationStarted": None,
            "committed": None,
            "commitState": "unknown",
            "commitStateKnown": False,
        },
    }
    router = Mcp2026Router(
        lambda _params: [{"name": "vrcforge_start_runtime_observation", "inputSchema": {"type": "object"}, "_meta": {"permission": "Write"}}],
        lambda _name, _arguments: callback_result,
    )
    response, status = router.handle(
        _request("tools/call", {"name": "vrcforge_start_runtime_observation", "arguments": {}})
    )

    assert status == 200
    structured = response["result"]["structuredContent"]
    assert structured["toolRoutingStarted"] is True
    assert structured["mutationStarted"] is None
    assert structured["commitState"] == "unknown"
    assert structured["persistenceState"] == "unknown"


def test_routed_gateway_error_keeps_unknown_commit_state():
    def routed_failure(_name, _arguments):
        raise AgentGatewayError(
            "Prompt provenance failure reported after routing",
            status_code=500,
            cause_code="prompt_skill_provenance_mismatch",
            failure_layer="unity_core",
            failure_phase="after_route",
            tool_routing_started=True,
            mutation_started=True,
            committed=None,
            commit_state="unknown",
        )

    router = Mcp2026Router(
        lambda _params: [{"name": "vrcforge_read_avatar"}],
        routed_failure,
    )
    response, status = router.handle(
        _request("tools/call", {"name": "vrcforge_read_avatar", "arguments": {}})
    )

    assert status == 500
    data = response["error"]["data"]
    assert data["errorCode"] == "prompt_skill_provenance_mismatch"
    assert data["toolRoutingStarted"] is True
    assert data["commitState"] == "unknown"
    assert data["commitStateKnown"] is False


def test_tool_result_cycle_is_shaped_and_stdio_survives():
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    invalid_router = Mcp2026Router(
        lambda _params: [{"name": "bad"}, {"name": "good"}],
        lambda name, _arguments: cyclic if name == "bad" else {"ok": True},
    )
    source = io.StringIO(
        json.dumps(_request("tools/call", {"name": "bad", "arguments": {}}))
        + "\n"
        + json.dumps(_request("tools/call", {"name": "good", "arguments": {}}))
        + "\n"
    )
    sink = io.StringIO()
    assert run_stdio_loop(invalid_router, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["error"]["code"] == -32603
    assert output[1]["result"]["structuredContent"]["ok"] is True
    assert output[1]["result"]["structuredContent"]["outcome"]["status"] == "ok"


def test_async_callbacks_work(router):
    async def tool_list(_params):
        return [{"name": "async", "inputSchema": {"type": "object"}}]

    async def tool_call(name, args):
        return {"content": [{"type": "text", "text": name}]}

    async_router = Mcp2026Router(tool_list, tool_call)
    result, status = asyncio.run(async_router.handle_async(_request("tools/list")))
    assert status == 200
    assert result["result"]["tools"][0]["name"] == "async"
