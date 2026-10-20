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
