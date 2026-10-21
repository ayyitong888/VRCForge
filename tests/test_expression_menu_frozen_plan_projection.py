from unity_execution_plans_scene import build_scene_execution_plan
from unity_mcp_core_client import canonical_arguments_sha256
from dashboard_server import _avatar_primitive_request


def test_expression_menu_approved_asset_scope_reaches_core_and_frozen_plan() -> None:
    approved_arguments = {
        "action": "delete",
        "avatarPath": "TestAvatar",
        "controlName": "Clothes",
        "expectedPlanAssetPaths": ["Assets/Expressions/TestMenu.asset"],
        "executionTarget": {"schema": "vrcforge.execution_target.v1", "ref": "opaque"},
    }

    frozen_call = build_scene_execution_plan(
        "vrcforge_manage_expression_menu", approved_arguments
    )[0][1]
    runtime_core_call = _avatar_primitive_request(approved_arguments, preview=False)

    assert runtime_core_call["expectedPlanAssetPaths"] == ["Assets/Expressions/TestMenu.asset"]
    assert frozen_call["expectedPlanAssetPaths"] == runtime_core_call["expectedPlanAssetPaths"]
    assert canonical_arguments_sha256(runtime_core_call) == canonical_arguments_sha256(frozen_call)


def test_new_root_scene_scope_remains_sealed_through_preparation_and_checkpoint(tmp_path, monkeypatch):
    import pytest
    import dashboard_server as server
    from agent_gateway import AgentGatewayError
    paths = ["Assets/Main.unity", "Assets/Menu.asset", "Assets/New/SubMenu.asset"]
    current = list(paths)
    def preview(*_args, **_kwargs):
        return {"ok": True, "preview": True, "plan": {"action": "create", "plannedAssetPaths": list(current)}}
    monkeypatch.setattr(server, "manage_expression_menu_sync", preview)
    registered = server.AGENT_GATEWAY._write_handlers["vrcforge_manage_expression_menu"]
    prepared, _ = registered.request_preparer({"action": "create", "projectPath": str(tmp_path)}, None)
    assert prepared["expectedPlanAssetPaths"] == paths
    calls = registered.approved_execution_plan_builder(prepared)
    assert calls[0][1]["expectedPlanAssetPaths"] == paths
    import unity_planned_checkpoint
    captured = []
    monkeypatch.setattr(unity_planned_checkpoint, "prepare_planned_asset_checkpoint",
        lambda _root, exact, **_kwargs: captured.append(exact) or {"ok": True})
    assert registered.checkpoint_prepare_handler(tmp_path, {**prepared, "_checkpointTargetTool": "vrcforge_manage_expression_menu"})["ok"]
    assert captured == [paths]
    current.remove("Assets/Main.unity")
    refused = registered.checkpoint_prepare_handler(tmp_path, {**prepared, "_checkpointTargetTool": "vrcforge_manage_expression_menu"})
    assert refused["ok"] is False and len(captured) == 1
    with pytest.raises(AgentGatewayError):
        registered.request_preparer({"expectedPlanAssetPaths": paths}, None)
