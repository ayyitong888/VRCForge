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
from dashboard_foundation import read_vrcforge_version


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


@pytest.mark.parametrize(
    "method,params",
    [
        ("server/discover", {}),
        ("tools/list", {}),
        ("prompts/list", {}),
        ("resources/list", {}),
        ("resources/templates/list", {}),
        ("resources/read", {"uri": "test://resource"}),
    ],
)
def test_cacheable_results_declare_private_immediately_stale_policy(method, params):
    # MCP 2026-07-28 CacheableResult, also enforced by strict external hosts.
    routed = Mcp2026Router(
        lambda _params: [], lambda _name, _arguments: {"ok": True},
        resource_list=lambda _params: {"resources": [], "nextCursor": "page-2"},
        resource_templates=lambda _params: {"resourceTemplates": []},
        resource_read=lambda uri: {"contents": [{"uri": uri, "text": "content"}]},
        prompt_list=lambda _params: {"prompts": []},
    )
    response, status = routed.handle(_request(method, params))
    assert status == 200
    result = response["result"]
    assert result["resultType"] == "complete"
    assert type(result.get("ttlMs")) is int
    assert result["ttlMs"] == 0
    assert result.get("cacheScope") == "private"
    if method == "resources/list":
        assert result["nextCursor"] == "page-2"


def test_non_cacheable_results_do_not_advertise_caching(router):
    response, status = router.handle(_request("tools/call", {"name": "echo", "arguments": {}}))
    assert status == 200
    assert "ttlMs" not in response["result"]
    assert "cacheScope" not in response["result"]


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


def test_default_router_server_info_uses_app_version() -> None:
    router = Mcp2026Router(lambda _params: [], lambda _name, _arguments: {"ok": True})

    response, status = router.handle(_request("server/discover"))

    assert status == 200
    assert response["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == read_vrcforge_version()


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
    assert output[0]["result"]["ttlMs"] == 0
    assert output[0]["result"]["cacheScope"] == "private"
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


def test_actual_list_avatars_ambiguity_returns_known_projects_and_selection_action(tmp_path):
    from fastapi.testclient import TestClient

    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    projects = [tmp_path / "project-a", tmp_path / "project-b"]
    for project in projects:
        gateway._register_runtime_project_scope(str(project))
    calls = []
    name = "vrcforge_list_avatars"
    gateway.register_tool(name, "List avatars from the selected Unity project.", "read/debug", lambda args: calls.append(args))
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
    assert data["details"]["knownProjectPaths"] == sorted(gateway._external_mcp_project_paths)  # noqa: SLF001
    assert "choose the exact intended projectpath" in data["details"]["nextAction"].casefold()
    assert "ask the user to select it in the VRCForge App project picker" in data["details"]["nextAction"]


def test_actual_project_catalog_status_without_path_runs_with_multiple_scopes(tmp_path):
    from fastapi.testclient import TestClient

    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway._register_runtime_project_scope(str(tmp_path / "project-a"))
    gateway._register_runtime_project_scope(str(tmp_path / "project-b"))
    calls = []
    name = "vrcforge_project_catalog_registration_status"
    gateway.register_tool(
        name, "Read global project catalogue availability.", "read/debug",
        lambda args: calls.append(args) or {"ok": True, "scope": "catalogue_availability", "projectPath": "", "catalogs": []},
    )
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

    assert response.status_code == 200, response.text
    assert calls == [{}]


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


def test_execution_target_session_mismatch_returns_same_project_recovery_call() -> None:
    calls = []

    def fail(tool, arguments):
        calls.append((tool, arguments))
        raise AgentGatewayError(
            "ExecutionTarget reference belongs to another session.",
            status_code=409,
            cause_code="execution_target_session_mismatch",
            failure_layer="execution_target",
            failure_phase="before_unity_core_call",
            operation_kind="write",
            tool=tool,
            tool_routing_started=False,
            mutation_started=False,
            committed=False,
            commit_state="not_started",
        )

    router = Mcp2026Router(
        lambda _params: [{"name": "vrcforge_set_property", "_meta": {"permission": "Write"}}],
        fail,
    )
    response, status = router.handle(_request("tools/call", {
        "name": "vrcforge_set_property",
        "arguments": {
            "projectPath": "D:/Unity/Avatar",
            "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": "vrcforge-target-stale"},
        },
    }))

    assert status == 409
    assert response["error"]["code"] == -32602
    data = response["error"]["data"]
    assert data["errorCode"] == "execution_target_session_mismatch"
    assert data["recoverable"] is True
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["committed"] is False
    assert data["recoveryToolCall"] == {
        "name": "vrcforge_list_execution_targets",
        "arguments": {"projectPath": "D:/Unity/Avatar", "scope": "component"},
    }
    assert data["recoveryBindingTool"] == "vrcforge_bind_execution_target"
    assert calls


@pytest.mark.parametrize("include_project_path", [True, False])
def test_execution_target_session_mismatch_http_replay_returns_recoverable_409(
    tmp_path, monkeypatch, include_project_path: bool,
) -> None:
    from fastapi.testclient import TestClient
    import execution_target

    project = tmp_path.resolve()
    target = {
        "schema": execution_target.EXECUTION_TARGET_SCHEMA,
        "scope": "component",
        "project": {"root": str(project), "projectId": "fixture-project"},
        "editor": {"unityPid": 1234, "processStartTime": "start", "coreInstanceId": "core"},
        "scene": {"absolutePath": str(project / "Assets" / "Main.unity"), "guid": "scene-guid", "revision": "1", "digest": "a" * 64},
        "avatar": {"globalObjectId": "avatar-id"},
        "object": {"globalObjectId": "object-id"},
        "component": {"globalObjectId": "component-id", "type": "SkinnedMeshRenderer"},
    }
    target["namespace"] = execution_target.canonical_namespace(target)
    monkeypatch.setattr(execution_target, "validate_runtime_execution_target", lambda value, **_kwargs: dict(value))
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    binding = gateway._execution_target_bindings.bind(
        target, project_root=str(project), session_context="previous-session",
    )
    reference = binding["executionTarget"]
    name = "vrcforge_set_property"

    def call(tool, arguments):
        assert tool == name
        if not include_project_path:
            return gateway.resolve_execution_target_reference(arguments["executionTarget"])
        return gateway.resolve_execution_target_reference(
            arguments["executionTarget"],
            project_root=arguments["projectPath"],
            required_scope="component",
        )

    router = Mcp2026Router(
        lambda _params: [{"name": name, "_meta": {"permission": "Write"}}],
        call,
    )
    app = create_asgi_app(
        router,
        bearer_validator=lambda token: token == "fixture-token",
        route_path="/mcp",
        request_context=gateway.resource_context,
    )
    with TestClient(app) as client:
        discovery = client.post(
            "/mcp",
            headers={"Authorization": "Bearer fixture-token", "Accept": "application/json, text/event-stream",
                     "Mcp-Method": "server/discover", "MCP-Protocol-Version": PROTOCOL_VERSION},
            json=_request("server/discover"),
        )
        session_id = discovery.headers["Mcp-Session-Id"]
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer fixture-token", "Accept": "application/json, text/event-stream",
                     "Mcp-Method": "tools/call", "Mcp-Name": name,
                     "Mcp-Session-Id": session_id, "MCP-Protocol-Version": PROTOCOL_VERSION},
            json=_request("tools/call", {"name": name, "arguments": {
                **({"projectPath": str(project)} if include_project_path else {}),
                "executionTarget": reference,
            }}),
        )

    assert response.status_code == 409, response.text
    data = response.json()["error"]["data"]
    assert data["errorCode"] == "execution_target_session_mismatch"
    if include_project_path:
        assert data["recoveryToolCall"] == {
            "name": "vrcforge_list_execution_targets",
            "arguments": {"projectPath": str(project), "scope": "component"},
        }
    else:
        assert "recoveryToolCall" not in data
        assert data["recoveryTool"] == "vrcforge_list_execution_targets"
        assert data["requiredInput"] == "arguments.projectPath"
        assert "Provide arguments.projectPath" in data["nextAction"]
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["committed"] is False


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


def _connection_headers(method, **extra):
    return {
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": PROTOCOL_VERSION,
        "mcp-method": method,
        "authorization": "Bearer good",
        "content-type": "application/json",
        **extra,
    }


def test_http_connection_projection_uses_session_idle_lifetime(router, monkeypatch):
    from types import SimpleNamespace
    import agent_mcp_2026

    clock = [1000.0]
    monkeypatch.setattr(agent_mcp_2026, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))

    async def exercise():
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good")
        status = app.state.mcp_connection_status
        assert status() == {"connected": False, "sessionCount": 0,
                            "activeRequestCount": 0, "idleTimeoutSeconds": 900, "bridgeConnectionCount": 0, "lastConnectedAt": None}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            discovered = await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover"))
            assert discovered.status_code == 200
            assert status()["sessionCount"] == 1
            clock[0] += 91
            assert status()["connected"] is True
            clock[0] += 809
            assert status()["connected"] is False
            assert status()["sessionCount"] == 0
            # Reading status does not extend idle lifetime.
            expired = await client.post("/", json=_request("tools/list"), headers=_connection_headers("tools/list", **{"Mcp-Session-Id": discovered.headers["Mcp-Session-Id"]}))
            assert expired.status_code == 404
            assert status()["connected"] is False
            again = await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover"))
            assert status()["connected"] is True
            deleted = await client.delete("/", headers={"authorization": "Bearer good", "Mcp-Session-Id": again.headers["Mcp-Session-Id"]})
            assert deleted.status_code == 204
            assert status()["connected"] is False
            assert create_asgi_app(router).state.mcp_connection_status()["connected"] is False

    asyncio.run(exercise())


def test_http_bad_or_sessionless_requests_do_not_claim_connection(router):
    async def exercise():
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/")).status_code == 405
            assert (await client.post("/", content=b"invalid", headers=_connection_headers("server/discover"))).status_code == 400
            assert (await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover", authorization="Bearer bad"))).status_code == 401
            assert (await client.post("/", json=_request("tools/list"), headers=_connection_headers("tools/list", **{"Mcp-Session-Id": "missing"}))).status_code == 404
            assert (await client.post("/", json=_request("tools/list"), headers=_connection_headers("tools/list"))).status_code == 200
            assert app.state.mcp_connection_status()["connected"] is False
            assert app.state.mcp_connection_status()["sessionCount"] == 0

    asyncio.run(exercise())


def test_http_active_request_remains_connected_beyond_idle_timeout(monkeypatch):
    from types import SimpleNamespace
    import agent_mcp_2026

    clock = [1000.0]
    monkeypatch.setattr(agent_mcp_2026, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def call_tool(_name, _arguments):
            entered.set()
            await release.wait()
            return {"ok": True}

        router = Mcp2026Router(lambda _params: [{"name": "wait"}], call_tool)
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            discovered = await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover"))
            pending = asyncio.create_task(client.post("/", json=_request("tools/call", {"name": "wait", "arguments": {}}), headers=_connection_headers("tools/call", **{"mcp-name": "wait", "Mcp-Session-Id": discovered.headers["Mcp-Session-Id"]})))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                clock[0] += 901
                assert app.state.mcp_connection_status() == {"connected": True, "sessionCount": 1,
                                                          "activeRequestCount": 1, "idleTimeoutSeconds": 900, "bridgeConnectionCount": 0, "lastConnectedAt": 1000000}
            finally:
                release.set()
            assert (await pending).status_code == 200
            assert app.state.mcp_connection_status()["connected"] is True
            assert app.state.mcp_connection_status()["activeRequestCount"] == 0
            clock[0] += 900
            assert app.state.mcp_connection_status()["connected"] is False

    asyncio.run(exercise())


def test_http_connection_events_only_follow_successful_session_lifecycle(router, monkeypatch):
    from types import SimpleNamespace
    import agent_mcp_2026

    clock = [1000.0]
    monkeypatch.setattr(agent_mcp_2026, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))

    async def exercise():
        events = []
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good",
                              on_connection_changed=lambda: events.append(app.state.mcp_connection_status()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.post("/", content=b"invalid", headers=_connection_headers("server/discover"))
            assert bad.status_code == 400 and events == []
            first = await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover"))
            first_id = first.headers["Mcp-Session-Id"]
            assert len(events) == 1 and events[-1]["lastConnectedAt"] == 1000000
            clock[0] += 1
            assert (await client.post("/", json=_request("tools/list"), headers=_connection_headers("tools/list", **{"Mcp-Session-Id": first_id}))).status_code == 200
            assert len(events) == 1 and app.state.mcp_connection_status()["lastConnectedAt"] == 1000000
            second = await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover"))
            assert len(events) == 2 and events[-1]["lastConnectedAt"] == 1001000
            assert events[-1]["sessionCount"] == 2
            assert (await client.delete("/", headers={"authorization": "Bearer good", "Mcp-Session-Id": second.headers["Mcp-Session-Id"]})).status_code == 204
            assert len(events) == 3 and events[-1]["lastConnectedAt"] == 1000000
            assert (await client.delete("/", headers={"authorization": "Bearer good", "Mcp-Session-Id": first_id})).status_code == 204
            assert len(events) == 4 and events[-1]["lastConnectedAt"] is None
            assert events[-1]["connected"] is False
            assert (await client.delete("/", headers={"authorization": "Bearer good", "Mcp-Session-Id": first_id})).status_code == 404
            assert len(events) == 4

    asyncio.run(exercise())


def test_http_connection_notification_failure_does_not_fail_discovery(router, caplog):
    def broken_notification():
        raise RuntimeError("fixture connection notification failure")

    async def exercise():
        app = create_asgi_app(router, bearer_validator=lambda token: token == "good",
                              on_connection_changed=broken_notification)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/", json=_request("server/discover"), headers=_connection_headers("server/discover"))
            assert response.status_code == 200
            assert app.state.mcp_connection_status()["connected"] is True
    asyncio.run(exercise())
    assert "MCP connection notification failed" in caplog.text


def test_bridge_websocket_tracks_real_lifetime_without_http_session(router, monkeypatch):
    from starlette.testclient import TestClient

    events = []
    app = create_asgi_app(router, bearer_validator=lambda token: token == "good", route_path="/mcp",
                          on_connection_changed=lambda: events.append(app.state.mcp_connection_status()))
    with TestClient(app) as client:
        with client.websocket_connect("/mcp/connection", headers={"Authorization": "Bearer good"}) as ws:
            assert ws.receive_json() == {"connected": True}
            status = app.state.mcp_connection_status()
            assert status["connected"] is True
            assert status["bridgeConnectionCount"] == 1
            assert status["sessionCount"] == 0
            assert status["activeRequestCount"] == 0
            assert isinstance(status["lastConnectedAt"], int)
            assert len(events) == 1
            now = time.monotonic()
            monkeypatch.setattr("agent_mcp_2026.time.monotonic", lambda: now + 901)
            assert app.state.mcp_connection_status()["connected"] is True
    assert app.state.mcp_connection_status()["connected"] is False
    assert app.state.mcp_connection_status()["bridgeConnectionCount"] == 0
    assert app.state.mcp_connection_status()["lastConnectedAt"] is None
    assert len(events) == 2


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer bad"},
    {"Authorization": "Bearer good", "Host": "remote.example"},
    {"Authorization": "Bearer good", "Origin": "https://remote.example"},
])
def test_bridge_websocket_rejects_unauthenticated_or_nonloopback(router, headers):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    events = []
    app = create_asgi_app(router, bearer_validator=lambda token: token == "good",
                          on_connection_changed=lambda: events.append(True))
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/connection", headers=headers):
                pytest.fail("Unauthorized connection was accepted")
    assert events == []
    assert app.state.mcp_connection_status()["connected"] is False


def test_bridge_websocket_has_no_tool_message_protocol(router):
    from starlette.testclient import TestClient

    app = create_asgi_app(router, bearer_validator=lambda token: token == "good")
    with TestClient(app) as client:
        with client.websocket_connect("/connection", headers={"Authorization": "Bearer good"}) as ws:
            assert ws.receive_json() == {"connected": True}
            ws.send_json(_request("tools/list"))
            assert ws.receive() == {"type": "websocket.close", "code": 1008, "reason": ""}
    assert app.state.mcp_connection_status()["bridgeConnectionCount"] == 0
