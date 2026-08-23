from __future__ import annotations

import io
import json

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import (
    LATEST_PROTOCOL_VERSION,
    McpStandardRouter,
    run_negotiated_stdio_loop,
    run_standard_stdio_loop,
)


def _router() -> McpStandardRouter:
    return McpStandardRouter(
        lambda: [
            {"name": "echo", "description": "Echo a value", "inputSchema": {"type": "object"}},
            {"name": "status"},
        ],
        lambda name, arguments: {"ok": True, "name": name, "arguments": arguments},
        server_name="VRCForge",
        server_version="1.5.1",
    )


def test_deepseek_harness_sdk_initialize_list_and_call() -> None:
    router = _router()
    initialized = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "dsh-mcp-client", "version": "0.1.0-rc.5"},
            },
        }
    )
    assert initialized["result"] == {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "VRCForge", "version": "1.5.1"},
        "instructions": "Use VRCForge tools for supervised avatar work. Project writes remain subject to VRCForge approval and checkpoint policy.",
    }

    listed = router.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["echo", "status"]
    assert "When to use:" in listed["result"]["tools"][0]["description"]
    assert "When NOT to use:" in listed["result"]["tools"][0]["description"]

    called = router.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"value": "ok"}}}
    )
    structured = called["result"]["structuredContent"]
    assert {key: structured[key] for key in ("ok", "name", "arguments")} == {
        "ok": True,
        "name": "echo",
        "arguments": {"value": "ok"},
    }
    assert structured["outcome"]["status"] == "ok"
    assert called["result"]["isError"] is False


def test_standard_stdio_ignores_initialized_notification_and_survives_bad_input() -> None:
    router = _router()
    source = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "dsh", "version": "1"}}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
                "not-json",
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}),
                "",
            ]
        )
    )
    sink = io.StringIO()
    assert run_standard_stdio_loop(router, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert len(output) == 3
    assert output[0]["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION
    assert output[1]["error"]["code"] == -32700
    assert output[2]["result"] == {}


def test_standard_stdio_notifies_client_when_tool_blocks_change() -> None:
    revision = {"value": 0}

    def call_tool(name, _arguments):
        assert name == "load_block"
        revision["value"] += 1
        return {"ok": True, "toolListChanged": True}

    router = McpStandardRouter(
        lambda: [{"name": "load_block"}],
        call_tool,
        tool_list_revision=lambda: revision["value"],
    )
    source = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "blocks", "version": "1"}}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "load_block", "arguments": {}}}),
                "",
            ]
        )
    )
    sink = io.StringIO()

    assert run_standard_stdio_loop(router, input_stream=source, output_stream=sink) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["result"]["capabilities"] == {"tools": {"listChanged": True}}
    assert output[1]["result"]["structuredContent"]["toolListChanged"] is True
    assert output[2] == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}


def test_standard_profile_is_explicit_and_fails_closed() -> None:
    router = _router()
    before_initialize = router.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert before_initialize["error"]["code"] == -32002
    assert before_initialize["error"]["data"]["schema"] == "vrcforge.external_tool_error.v1"
    assert before_initialize["error"]["data"]["failureLayer"] == "external_mcp_protocol"
    assert before_initialize["error"]["data"]["mutationStarted"] is False
    assert before_initialize["error"]["data"]["protocolProfile"] == "mcp-1x"

    unsupported = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "2026-07-28", "capabilities": {}, "clientInfo": {"name": "bad", "version": "1"}},
        }
    )
    assert unsupported["error"]["code"] == -32602
    assert unsupported["error"]["data"]["schema"] == "vrcforge.external_tool_error.v1"
    assert unsupported["error"]["data"]["requested"] == "2026-07-28"
    assert unsupported["error"]["data"]["supported"] == [
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
        "2024-10-07",
    ]


def test_unknown_or_unlisted_tool_never_reaches_callback() -> None:
    calls: list[str] = []
    router = McpStandardRouter(
        lambda: [{"name": "read"}],
        lambda name, _arguments: calls.append(name) or {"ok": True},
    )
    router.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "dsh", "version": "1"}},
        }
    )
    response = router.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "write", "arguments": {}}}
    )
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["schema"] == "vrcforge.external_tool_error.v1"
    assert response["error"]["data"]["toolRoutingStarted"] is False
    assert calls == []


def test_auto_profile_selects_standard_for_dsh_and_freezes_the_connection() -> None:
    custom = Mcp2026Router(lambda _params: [], lambda _name, _arguments: {})
    standard = _router()
    source = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "dsh", "version": "1"}}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION, "io.modelcontextprotocol/clientCapabilities": {}}}}),
                "",
            ]
        )
    )
    sink = io.StringIO()
    diagnostics = io.StringIO()

    assert run_negotiated_stdio_loop(custom, standard, input_stream=source, output_stream=sink, diagnostic_stream=diagnostics) == 0
    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION
    assert output[1]["error"]["code"] == -32601
    assert json.loads(diagnostics.getvalue())["selectedProfile"] == "mcp-1x"


def test_auto_profile_selects_vrcforge_2026_when_client_advertises_it() -> None:
    custom = Mcp2026Router(lambda _params: [], lambda _name, _arguments: {})
    standard = _router()
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    source = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": meta}}) + "\n")
    sink = io.StringIO()
    diagnostics = io.StringIO()

    assert run_negotiated_stdio_loop(custom, standard, input_stream=source, output_stream=sink, diagnostic_stream=diagnostics) == 0
    response = json.loads(sink.getvalue())
    assert response["result"]["supportedVersions"] == [PROTOCOL_VERSION]
    assert json.loads(diagnostics.getvalue())["selectedProfile"] == "vrcforge-2026"


def test_auto_profile_rejection_reports_exact_v2_metadata_location_without_routing() -> None:
    custom = Mcp2026Router(lambda _params: [], lambda _name, _arguments: {})
    standard = _router()
    source = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"protocolVersion": PROTOCOL_VERSION},
            }
        )
        + "\n"
    )
    sink = io.StringIO()

    assert run_negotiated_stdio_loop(custom, standard, input_stream=source, output_stream=sink) == 0
    error = json.loads(sink.getvalue())["error"]
    data = error["data"]
    assert error["code"] == -32022
    assert data["protocolProfile"] == "unnegotiated"
    assert data["protocolNamespace"] == "io.modelcontextprotocol/protocolVersion"
    assert data["protocolMetadataLocation"] == "params._meta"
    assert data["protocolVersion"] == PROTOCOL_VERSION
    assert data["fallbackProtocolNamespace"] == "initialize.params.protocolVersion"
    assert data["receivedMethod"] == "server/discover"
    assert data["receivedProtocolVersion"] is None
    assert data["toolRoutingStarted"] is False
    assert data["mutationStarted"] is False
    assert data["commitState"] == "not_started"
