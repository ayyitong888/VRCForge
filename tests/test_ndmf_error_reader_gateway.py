from types import SimpleNamespace
from unittest.mock import patch

import dashboard_server
from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS
from internal_tool_blocks import internal_tool_block_for_name
from provider_tool_phases import PHASE_BY_TOOL


TOOL = "vrcforge_get_ndmf_errors"


def test_ndmf_reader_is_exposed_as_one_shared_read_only_diagnostic():
    gateway = dashboard_server.AGENT_GATEWAY
    assert TOOL in EXTERNAL_MCP_READ_TOOL_BLOCKS["diagnostics"]
    assert internal_tool_block_for_name(TOOL, "unity") == "diagnostics_build/compile_logs"
    assert PHASE_BY_TOOL[TOOL] == PHASE_BY_TOOL["vrcforge_get_compile_errors"]
    assert PHASE_BY_TOOL["unity_get_ndmf_errors"] == PHASE_BY_TOOL["unity_get_compile_errors"]
    external = {item["name"]: item for item in gateway.build_external_mcp_tools("planning", tool_blocks=["*"])}
    descriptor = external[TOOL]
    assert descriptor["permission"] == "ReadOnly"
    assert descriptor["write"] is False
    assert "last report currently retained" in descriptor["description"]
    assert "Show NDMF Console" in descriptor["description"]
    assert descriptor["inputSchema"] == gateway.shared_agent_tool_descriptor(TOOL, write=False)["inputSchema"]
    assert descriptor["inputSchema"]["required"] == ["projectPath"]


def test_registered_ndmf_reader_routes_once_and_preserves_real_error_payload():
    payload = {"ndmfErrorCount": 1, "ndmfErrors": [{
        "message": "InvalidOperationException: NDMF fixture build failed",
        "severity": "InternalError", "avatar": "Fixture", "pass": "Optimize",
    }]}
    with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()) as load, \
            patch("dashboard_server.invoke_unity_mcp", return_value=dashboard_server.McpResult(
                exit_code=0, stdout="ok", stderr="", payload={"data": payload},
            )) as invoke:
        actual = dashboard_server.AGENT_GATEWAY._tools[TOOL].handler({"projectPath": "D:/Fixture"})
    assert actual == payload
    assert invoke.call_count == 1
    assert invoke.call_args.args[1:] == ("vrc_get_ndmf_errors", {})
    assert load.call_count == 1


def test_registered_ndmf_reader_preserves_empty_last_report_payload():
    payload = {"ndmfErrorCount": 0, "ndmfErrors": []}
    with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), \
            patch("dashboard_server.invoke_unity_mcp", return_value=dashboard_server.McpResult(
                exit_code=0, stdout="ok", stderr="", payload={"data": payload},
            )):
        actual = dashboard_server.AGENT_GATEWAY._tools[TOOL].handler({"projectPath": "D:/Fixture"})
    assert actual == payload
