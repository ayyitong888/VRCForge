import pytest

import dashboard_server as dashboard
from unity_execution_plans_workflows import build_workflow_execution_plan


def test_create_wardrobe_freezes_the_three_exact_core_calls() -> None:
    plan = build_workflow_execution_plan("vrcforge_create_wardrobe", {"avatarPath": "Avatar", "parameterName": "Clothes"})
    assert [name for name, _ in plan] == ["vrc_ensure_expression_parameter", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control"]
    assert plan[0][1]["parameterName"] == "Clothes"
    assert plan[1][1]["layerName"] == "Clothes"
    assert plan[2][1]["menuPath"] == "Wardrobe"


@pytest.mark.parametrize(("target", "tool"), [
    ("vrcforge_add_wardrobe_outfit", "vrc_add_wardrobe_outfit"),
    ("vrcforge_manage_wardrobe", "vrc_manage_wardrobe"),
    ("vrcforge_add_outfit_part", "vrc_add_outfit_part"),
    ("vrcforge_add_modular_avatar_component", "vrc_add_modular_avatar_component"),
    ("vrcforge_restore_safe_backup", "vrc_restore_safe_backup"),
])
def test_deterministic_workflows_freeze_one_core_call(target: str, tool: str) -> None:
    plan = build_workflow_execution_plan(target, {"avatarPath": "Avatar", "parameterName": "Clothes"})
    assert [name for name, _ in plan] == [tool]
    assert plan[0][1]["preview"] is False if "preview" in plan[0][1] else True


@pytest.mark.parametrize("target", [
    "vrcforge_add_outfit", "vrcforge_import_outfit_package", "vrcforge_import_chat_image",
    "vrcforge_import_chat_archive", "vrcforge_install_vpm_package", "vrcforge_configure_optimizer_component",
])
def test_runtime_dependent_workflows_fail_closed_with_missing_fact(target: str) -> None:
    with pytest.raises(ValueError, match="runtime readback"):
        build_workflow_execution_plan(target, {})


def test_setup_outfit_freezes_only_the_initial_write_call() -> None:
    params = {"avatarPath": "Avatar", "outfitPath": "Avatar/Outfit", "saveScene": "false"}
    assert build_workflow_execution_plan("vrcforge_setup_outfit", params) == [
        ("vrc_setup_outfit", dashboard.build_setup_outfit_request(params, True))
    ]


def test_manage_wardrobe_plan_has_shared_builder_parity_for_aliases_lists_and_default_bool_semantics() -> None:
    params = {
        "action": "reorder", "avatarPath": "Avatar", "wardrobeParameter": "Clothes",
        "targetValue": "2", "orderValues": "3; 2,3", "targetValues": [2, "4"],
        "deactivateObjects": "not-a-bool", "deleteObjects": "yes", "newOutfitName": "Renamed",
    }
    expected = dashboard.build_manage_wardrobe_request(params, False)
    assert build_workflow_execution_plan("vrcforge_manage_wardrobe", params) == [("vrc_manage_wardrobe", expected)]


def test_create_wardrobe_plan_has_shared_builder_parity_and_passes_preview_false_to_all_three_calls() -> None:
    params = {"avatarPath": "Avatar", "parameterName": "Clothes", "writeDefaults": "no"}
    request = dashboard.build_create_wardrobe_request(params, False)
    expected = []
    for name, arguments in zip(
        ("vrc_ensure_expression_parameter", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control"),
        dashboard._create_wardrobe_primitive_args(request),
    ):
        if name == "vrc_ensure_expression_parameter":
            expected_args = dashboard.build_ensure_expression_parameter_request(arguments, False)
        elif name == "vrc_ensure_animator_state":
            expected_args = dashboard.build_ensure_animator_state_request(arguments, False)
        else:
            expected_args = dashboard.build_ensure_expression_menu_control_request(arguments, False)
        expected.append((name, expected_args))
    assert build_workflow_execution_plan("vrcforge_create_wardrobe", params) == expected


@pytest.mark.parametrize(
    ("target", "params", "dashboard_builder"),
    [
        (
            "vrcforge_add_wardrobe_outfit",
            {
                "parameterName": "Clothes",
                "outfitName": "Outfit",
                "objectPaths": ["Avatar/Outfit"],
                "addMenuToggle": "false",
                "setObjectsDefaultOff": "false",
                "subMenuOverflow": "false",
                "writeDefaults": "false",
            },
            dashboard.build_add_wardrobe_outfit_request,
        ),
        (
            "vrcforge_add_outfit_part",
            {
                "parameterName": "Clothes",
                "partName": "Hat",
                "objectPaths": ["Avatar/Hat"],
                "value": 1,
                "addMenuToggle": "false",
                "setObjectsDefaultOff": "false",
                "defaultOn": "false",
                "writeDefaults": "false",
            },
            dashboard.build_add_outfit_part_request,
        ),
        (
            "vrcforge_add_modular_avatar_component",
            {
                "gameObjectPath": "Avatar/Hat",
                "componentType": "MenuInstaller",
                "saveScene": "false",
                "allowDuplicate": "false",
            },
            dashboard.build_add_modular_avatar_component_request,
        ),
    ],
)
def test_workflow_plan_preserves_existing_python_truthiness(
    target: str,
    params: dict,
    dashboard_builder,
) -> None:
    assert build_workflow_execution_plan(target, params) == [
        (build_workflow_execution_plan(target, params)[0][0], dashboard_builder(params, False))
    ]


def test_restore_plan_preserves_existing_python_truthiness() -> None:
    params = {
        "backupId": "b1",
        "allowProjectMismatch": "false",
        "allowOverwriteChanged": "false",
    }
    assert build_workflow_execution_plan("vrcforge_restore_safe_backup", params) == [
        ("vrc_restore_safe_backup", dashboard.build_safe_backup_restore_request(params, True))
    ]


def test_restore_plan_is_confirmation_bound_and_does_not_retain_caller_mutability() -> None:
    paths = ["Assets/A.prefab"]
    plan = build_workflow_execution_plan("vrcforge_restore_safe_backup", {"backupId": "b1", "assetPaths": paths})
    paths.append("Assets/other.prefab")
    assert plan == [("vrc_restore_safe_backup", {"backupPath": "", "backupId": "b1", "assetPaths": ["Assets/A.prefab"], "confirmRestore": True, "allowProjectMismatch": False, "allowOverwriteChanged": False, "refreshAssets": True})]
