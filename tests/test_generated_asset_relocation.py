from __future__ import annotations

import pytest
from unittest.mock import patch
from agent_gateway import AgentGateway

import dashboard_server
from generated_asset_relocation import (
    GeneratedAssetRelocationError,
    RESULT_SCHEMA,
    OPERATION,
    bind_authoritative_preview,
    build_execution_plan,
    build_wrapper_arguments,
    validate_apply_result,
)


def _row(name: str = "Generated.asset") -> dict[str, str]:
    return {
        "sourceAssetPath": f"Assets/VRCForge/Generated/{name}",
        "destinationAssetPath": f"Assets/VRCForgeGenerated/{name}",
        "expectedGuid": "a" * 32,
        "expectedAssetSha256": "b" * 64,
        "expectedMetaSha256": "c" * 64,
    }


def test_registered_relocation_handler_is_high_risk_and_supervised() -> None:
    service = dashboard_server.AGENT_GATEWAY.approval_transactions
    assert "vrcforge_relocate_generated_assets" in service.registered_write_target_names()
    handler = service._ports.state.write_handlers["vrcforge_relocate_generated_assets"]
    assert handler.requires_approved_execution_context is True
    assert handler.request_preparer is not None
    assert handler.approved_execution_plan_builder is build_execution_plan
    visible = next(
        item
        for item in service.visible_write_targets()
        if item["name"] == "vrcforge_relocate_generated_assets"
    )
    assert visible["riskLevel"] == "high"


def test_wrapper_binds_exact_rows_and_defaults_to_preview() -> None:
    wrapper = build_wrapper_arguments(
        {
            "projectPath": "C:/Projects/Avatar",
            "entries": [_row()],
        }
    )
    assert wrapper["toolName"] == "vrc_relocate_generated_assets"
    assert wrapper["projectPath"] == "C:/Projects/Avatar"
    assert wrapper["arguments"]["preview"] is True
    assert wrapper["arguments"]["entries"] == [_row()]


def test_registered_relocation_write_dispatches_through_static_allowlist() -> None:
    project = "C:/Projects/Avatar"
    wrapper = build_wrapper_arguments({"projectPath": project, "entries": [_row()], "preview": False})
    receipt = {
        "schema": RESULT_SCHEMA, "operation": OPERATION,
        "ok": True, "verified": True, "preview": False, "changed": True,
        "mutationStarted": True, "committed": True, "checkpointRecoveryRequired": False,
        "commitState": "committed", "persistenceState": "persisted",
        "readbackState": "verified", "cleanupState": "complete", "mutationCount": 1,
        "expectedProjectPath": project, "entries": [_row()],
    }
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_relocate_generated_assets"]
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=dashboard_server.McpResult(0, "", "", {"data": receipt})) as invoke,
    ):
        result = handler.handler(wrapper)
    invoke.assert_called_once()
    assert invoke.call_args.args[1:3] == ("vrc_relocate_generated_assets", wrapper["arguments"])
    assert invoke.call_args.kwargs["preserve_tool_error"] is True
    assert result["ok"] is True


def test_wrapper_rejects_out_of_scope_or_malformed_rows() -> None:
    with pytest.raises(GeneratedAssetRelocationError):
        build_wrapper_arguments(
            {
                "expectedProjectPath": "C:/Projects/Avatar",
                "entries": [{**_row(), "sourceAssetPath": "Assets/Other/Generated.asset"}],
            }
        )
    with pytest.raises(GeneratedAssetRelocationError):
        build_wrapper_arguments(
            {
                "projectPath": "C:/Projects/Avatar",
                "entries": [_row("A.asset"), _row("a.asset")],
            }
        )
    with pytest.raises(GeneratedAssetRelocationError):
        build_wrapper_arguments(
            {
                "expectedProjectPath": "C:/Projects/Avatar",
                "entries": [{**_row(), "expectedGuid": "bad"}],
            }
        )


def test_approved_plan_freezes_rows_and_forces_apply() -> None:
    plan = build_execution_plan(
        {
            "projectPath": "C:/Projects/Avatar",
            "expectedProjectPath": "C:/Projects/Avatar",
            "entries": [_row()],
            "preview": True,
        }
    )
    assert plan[0][0] == "vrc_relocate_generated_assets"
    assert plan[0][1]["preview"] is False
    assert plan[0][1]["entries"] == [_row()]


def test_authoritative_preview_binds_exact_inventory_to_apply() -> None:
    project = "C:/Projects/Avatar"
    wrapper = build_wrapper_arguments(
        {"projectPath": project, "expectedProjectPath": project, "entries": [_row()]}
    )
    preview = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "verified": True,
        "preview": True,
        "changed": False,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "mutationCount": 0,
        "expectedProjectPath": project,
        "entries": [_row()],
    }
    canonical, approval = bind_authoritative_preview(wrapper, preview)
    assert canonical["arguments"]["preview"] is False
    assert canonical["arguments"]["entries"] == [_row()]
    assert approval["mutationCount"] == 1


def test_apply_result_requires_persisted_verified_core_receipt() -> None:
    project = "C:/Projects/Avatar"
    arguments = {"expectedProjectPath": project, "entries": [_row()], "preview": False}
    result = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "verified": True,
        "preview": False,
        "changed": True,
        "mutationStarted": True,
        "committed": True,
        "checkpointRecoveryRequired": False,
        "commitState": "committed",
        "persistenceState": "persisted",
        "readbackState": "verified",
        "cleanupState": "complete",
        "mutationCount": 1,
        "expectedProjectPath": project,
        "entries": [_row()],
    }
    assert validate_apply_result(arguments, result)["readbackState"] == "verified"
    with pytest.raises(GeneratedAssetRelocationError):
        validate_apply_result(arguments, {**result, "checkpointRecoveryRequired": True})
    with pytest.raises(GeneratedAssetRelocationError):
        bind_authoritative_preview(
            build_wrapper_arguments({"projectPath": project, "entries": [_row()]}),
            {key: value for key, value in result.items() if key not in {"mutationStarted", "committed", "commitState"}},
        )


def test_dashboard_authoritative_preparer_calls_core_preview_and_freezes_request(tmp_path) -> None:
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True)
    params = {
        "projectPath": str(project),
        "expectedProjectPath": str(project),
        "entries": [_row()],
    }
    preview = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "verified": True,
        "preview": True,
        "changed": False,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "mutationCount": 0,
        "expectedProjectPath": str(project.resolve()),
        "entries": [_row()],
    }
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": preview},
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        prepared, approval = dashboard_server.prepare_unity_mcp_write_request(
            build_wrapper_arguments(params), None
        )
    assert prepared["arguments"]["preview"] is False
    assert approval["mutationCount"] == 1
    assert invoke.call_args.args[1] == "vrc_relocate_generated_assets"
    assert invoke.call_args.args[2]["preview"] is True


def test_gateway_prepare_and_execute_keeps_approval_checkpoint_boundary(tmp_path, monkeypatch) -> None:
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True)
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    service = gateway.approval_transactions
    service.register_write_handler(
        "vrcforge_relocate_generated_assets",
        "relocate generated assets",
        "high",
        lambda _args: {"ok": True},
        request_preparer=lambda params, preview: dashboard_server.prepare_unity_mcp_write_request(
            build_wrapper_arguments({"projectPath": str(project), **params}), preview
        ),
        requires_approved_execution_context=True,
        checkpoint_prepare_handler=lambda _approval, _args: {
            "ok": True,
            "status": "ready",
            "id": "relocation-checkpoint",
            "projectRoot": str(project),
        },
        approved_execution_plan_builder=build_execution_plan,
    )
    preview = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "verified": True,
        "preview": True,
        "changed": False,
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "mutationCount": 0,
        "expectedProjectPath": str(project.resolve()),
        "entries": [_row()],
    }
    fake_preview_result = dashboard_server.McpResult(0, "", "", {"data": preview})
    with patch("dashboard_server.load_dashboard_settings"), patch(
        "dashboard_server.invoke_unity_mcp", return_value=fake_preview_result
    ):
        prepared = service.prepare_external_mcp_write(
            "vrcforge_relocate_generated_assets",
            {"projectPath": str(project), "expectedProjectPath": str(project), "entries": [_row()]},
        )
    assert prepared["requiresUserConfirmation"] is True
    assert prepared["approvedUnityExecutionPlan"]["calls"][0]["toolName"] == "vrc_relocate_generated_assets"
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda _self, _approval, _args: {
            "ok": True,
            "status": "ready",
            "id": "relocation-checkpoint",
            "projectRoot": str(project),
        },
    )
    monkeypatch.setattr(
        type(service),
        "_call_external_mcp_write_handler",
        lambda *_args: {"ok": True, "status": "applied", "verified": True, "committed": True},
    )
    executed = service.execute_prepared_external_mcp_write(prepared)
    assert executed["ok"] is True
