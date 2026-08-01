from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs").read_text(
    encoding="utf-8-sig"
)


def test_standard_mcp_tool_call_can_only_queue_explicit_read_only_tools() -> None:
    assert 'string.Equals(method, "tools/call"' in SERVER
    assert "descriptor.Permission != VRCForgeToolPermission.ReadOnly" in SERVER
    assert "return QueueInvocation(toolName, arguments);" in SERVER
    assert "This tool requires the VRCForge FastAPI approval and checkpoint lane." in SERVER
    assert "var descriptor = registry.GetRequired(pending.ToolName);" in SERVER
    assert SERVER.count("descriptor.Permission != VRCForgeToolPermission.ReadOnly") == 2


def test_no_file_bootstrap_or_private_approved_execution_rpc_exists() -> None:
    assert "vrcforge/execution/register" not in SERVER
    assert "vrcforge/execute-approved" not in SERVER
    assert "executionGrant" not in SERVER
    assert "one-time-hmac-bootstrap" not in SERVER
    assert "unity-mcp-authority" not in SERVER
    assert '"read-only-direct-writes-rejected"' in SERVER


def test_screenshot_and_parameter_preview_cannot_bypass_read_only_permission() -> None:
    assert "vrc_capture_scene_view" not in SERVER
    assert "statusOnly" not in SERVER
    assert "restoreView" not in SERVER
    assert "PreviewCapableTools" not in SERVER
    assert "BoundedScanTools" not in SERVER
