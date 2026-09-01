from __future__ import annotations

from pathlib import Path

import pytest

from agent_gateway import AgentGateway


def _gateway(tmp_path: Path, execution_mode: str) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = execution_mode
    config.roslyn_risk_acknowledged = execution_mode in {
        "roslyn_full_auto",
        "full_auto",
        "full_permission",
    }
    gateway.save_config(config)
    return gateway


@pytest.mark.parametrize(
    "tool_name",
    [
        "vrcforge_save_current_scene",
        "vrcforge_restore_shader_tuning",
        "vrcforge_undo_blendshapes",
        "vrcforge_external_high_risk_unity_write",
    ],
)
@pytest.mark.parametrize(
    "execution_mode",
    ["roslyn_full_auto", "full_auto", "full_permission"],
)
def test_external_full_permission_executes_local_unity_writes_without_confirmation(
    tmp_path: Path,
    tool_name: str,
    execution_mode: str,
) -> None:
    gateway = _gateway(tmp_path, execution_mode)
    executed: list[dict[str, object]] = []
    gateway.approval_transactions.register_write_handler(
        tool_name,
        "Save or update local Unity project state.",
        "high",
        lambda arguments: executed.append(arguments) or {"ok": True},
        manual_approval_resolver=lambda _arguments, _preview: (
            "The handler normally requires user confirmation."
        ),
    )
    gateway.register_external_mcp_unity_tool(tool_name, "avatar")

    result = gateway.call_external_mcp_tool(tool_name, {"value": "local edit"})

    assert result["status"] == "executed"
    assert "confirmation" not in result
    assert executed == [{"value": "local edit"}]


@pytest.mark.parametrize("execution_mode", ["approval", "auto"])
@pytest.mark.parametrize(
    "tool_name",
    [
        "vrcforge_save_current_scene",
        "vrcforge_restore_shader_tuning",
        "vrcforge_external_high_risk_unity_write",
    ],
)
def test_external_other_modes_preserve_high_risk_user_confirmation(
    tmp_path: Path,
    tool_name: str,
    execution_mode: str,
) -> None:
    gateway = _gateway(tmp_path, execution_mode)
    executed: list[dict[str, object]] = []
    gateway.approval_transactions.register_write_handler(
        tool_name,
        "Update local Unity project state.",
        "high",
        lambda arguments: executed.append(arguments) or {"ok": True},
    )
    gateway.register_external_mcp_unity_tool(tool_name, "avatar")

    result = gateway.call_external_mcp_tool(tool_name, {"value": "local edit"})

    assert result["status"] == "user_confirmation_required"
    assert result["confirmation"]["targetTool"] == tool_name
    assert executed == []


@pytest.mark.parametrize(
    ("tool_name", "reason_fragment"),
    [
        ("vrcforge_restore_checkpoint", "Checkpoint restore always requires"),
        ("vrcforge_build_and_upload_avatar", "VRChat avatar upload always requires"),
    ],
)
@pytest.mark.parametrize(
    "execution_mode",
    ["approval", "auto", "roslyn_full_auto", "full_auto", "full_permission"],
)
def test_checkpoint_restore_and_irreversible_upload_always_require_confirmation(
    tmp_path: Path,
    tool_name: str,
    reason_fragment: str,
    execution_mode: str,
) -> None:
    gateway = _gateway(tmp_path, execution_mode)
    executed: list[dict[str, object]] = []
    gateway.approval_transactions.register_write_handler(
        tool_name,
        "Execute checkpoint restoration or irreversible remote publication.",
        "high",
        lambda arguments: executed.append(arguments) or {"ok": True},
    )
    gateway.register_external_mcp_unity_tool(tool_name, "avatar")

    result = gateway.call_external_mcp_tool(tool_name, {"value": "protected"})

    upload_in_full_permission = (
        tool_name == "vrcforge_build_and_upload_avatar"
        and execution_mode in {"roslyn_full_auto", "full_auto", "full_permission"}
    )
    if upload_in_full_permission:
        assert result["status"] == "executed"
        assert "confirmation" not in result
        assert executed == [{"value": "protected"}]
    else:
        assert result["status"] == "user_confirmation_required"
        assert reason_fragment in result["reason"]
        assert executed == []


@pytest.mark.parametrize(
    "tool_name",
    [
        "vrcforge_restore_checkpoint",
        "vrcforge_rollback_parameters",
        "vrcforge_rollback_project_lifecycle",
        "vrcforge_rollback_project_catalog_registration",
    ],
)
def test_full_permission_keeps_all_rollback_tools_manual(
    tmp_path: Path,
    tool_name: str,
) -> None:
    gateway = _gateway(tmp_path, "roslyn_full_auto")
    executed: list[dict[str, object]] = []
    gateway.approval_transactions.register_write_handler(
        tool_name,
        "Receipt-bound rollback.",
        "high",
        lambda arguments: executed.append(arguments) or {"ok": True},
    )
    gateway.register_external_mcp_unity_tool(tool_name, "project")

    result = gateway.call_external_mcp_tool(tool_name, {"value": "rollback"})

    assert result["status"] == "user_confirmation_required"
    assert executed == []


def test_reload_confirmation_policy_does_not_claim_asset_or_editor_state_rollback() -> None:
    import dashboard_server

    gateway = dashboard_server.AGENT_GATEWAY
    handler = gateway._write_handlers["vrcforge_confirm_unity_reload_dialog"]

    policy = gateway.approval_transactions._write_handler_rollback_policy(handler)

    assert handler.pre_write_checkpoint_required is False
    assert policy["kind"] == "irreversible_ephemeral_editor_reload"
    assert policy["required"] is False
    assert policy["approvalRequired"] is True
    assert policy["preWriteCheckpointRequired"] is False
    assert policy["checkpointScope"] == []
    assert policy["restoreTool"] == ""
    assert policy["postRestoreValidationRequired"] is False
    assert "unsaved" in policy["note"].casefold()
    assert "cannot be restored" in policy["note"].casefold()
