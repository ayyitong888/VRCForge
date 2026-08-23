from types import SimpleNamespace
from unittest.mock import patch

import dashboard_server
from agent_gateway import (
    EXTERNAL_MCP_READ_TOOL_BLOCKS,
    EXTERNAL_MCP_WRITE_TOOL_BLOCKS,
    UNITY_READ_TOOL_INPUT_SCHEMAS,
)
from internal_tool_blocks import internal_tool_block_for_name


TOOL_NAME = "vrcforge_read_vrchat_sdk_builder_alerts"


def test_sdk_builder_alert_reader_is_one_shared_lazy_diagnostics_tool() -> None:
    assert TOOL_NAME in EXTERNAL_MCP_READ_TOOL_BLOCKS["diagnostics"]
    assert all(
        TOOL_NAME not in tools
        for tools in EXTERNAL_MCP_WRITE_TOOL_BLOCKS.values()
    )
    assert internal_tool_block_for_name(TOOL_NAME, "unity") == "unity/diagnostics"

    schema = UNITY_READ_TOOL_INPUT_SCHEMAS[TOOL_NAME]
    assert schema["required"] == ["projectPath", "avatarPath"]
    assert schema["additionalProperties"] is False


@patch("dashboard_server.invoke_unity_mcp")
@patch("dashboard_server.load_dashboard_settings")
def test_sdk_builder_alert_reader_forwards_core_result_unchanged(
    mock_load_settings,
    mock_invoke,
) -> None:
    mock_load_settings.return_value = SimpleNamespace()
    core_result = {
        "ok": True,
        "schema": "vrcforge.vrchat_sdk_builder_alerts.v1",
        "exact": True,
        "alerts": [
            {
                "message": "This avatar uses Unity constraints.",
                "blocker": False,
                "selectable": True,
                "autoFixAvailable": True,
            }
        ],
        "mutationStarted": False,
        "writeOccurred": False,
    }
    mock_invoke.return_value = dashboard_server.McpResult(
        exit_code=0,
        stdout="ok",
        stderr="",
        payload={"data": core_result},
    )

    result = dashboard_server.read_vrchat_sdk_builder_alerts_sync(
        {"projectPath": "D:/Project", "avatarPath": "Scene/FinalAvatar"}
    )

    assert result == core_result
    _settings, core_tool, arguments = mock_invoke.call_args.args
    assert core_tool == "vrc_read_vrchat_sdk_builder_alerts"
    assert arguments == {"avatarPath": "Scene/FinalAvatar"}


def test_sdk_builder_alert_reader_rejects_missing_avatar_before_core_routing() -> None:
    with patch("dashboard_server.invoke_unity_mcp") as invoke:
        try:
            dashboard_server.read_vrchat_sdk_builder_alerts_sync(
                {"projectPath": "D:/Project"}
            )
        except RuntimeError as exc:
            assert str(exc) == "avatarPath is required."
        else:
            raise AssertionError("missing avatarPath must fail before Core routing")
        invoke.assert_not_called()
