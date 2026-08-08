import random

import pytest

import dashboard_server as dashboard
import unity_execution_plans_workflows as workflow_plans
from unity_execution_plans_workflows import build_workflow_execution_plan
from wardrobe_outfit_workflow_service import (
    build_add_modular_avatar_component_request,
    build_add_outfit_part_request,
    build_add_wardrobe_outfit_request,
    build_setup_outfit_request,
)


def test_create_wardrobe_freezes_the_three_exact_core_calls() -> None:
    plan = build_workflow_execution_plan("vrcforge_create_wardrobe", {"avatarPath": "Avatar", "parameterName": "Clothes"})
    assert [name for name, _ in plan] == ["vrc_ensure_expression_parameter", "vrc_ensure_animator_state", "vrc_ensure_expression_menu_control"]
    assert plan[0][1]["parameterName"] == "Clothes"
    assert plan[1][1]["layerName"] == "Clothes"
    assert plan[2][1]["menuPath"] == "Wardrobe"


def test_create_wardrobe_is_workflow_only_not_a_generic_core_write() -> None:
    assert "vrc_create_wardrobe" not in dashboard.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    assert [name for name, _ in build_workflow_execution_plan(
        "vrcforge_create_wardrobe", {"avatarPath": "Avatar", "parameterName": "Clothes"}
    )] == [
        "vrc_ensure_expression_parameter",
        "vrc_ensure_animator_state",
        "vrc_ensure_expression_menu_control",
    ]


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
        ("vrc_setup_outfit", build_setup_outfit_request(params, True))
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
            build_add_wardrobe_outfit_request,
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
            build_add_outfit_part_request,
        ),
        (
            "vrcforge_add_modular_avatar_component",
            {
                "gameObjectPath": "Avatar/Hat",
                "componentType": "MenuInstaller",
                "saveScene": "false",
                "allowDuplicate": "false",
            },
            build_add_modular_avatar_component_request,
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


@pytest.mark.parametrize(
    ("flag_arguments", "expected_flag"),
    [
        ({"camel": None}, None),
        ({"snake": None}, None),
        ({"snake": None, "camel": True}, False),
        ({"snake": False, "camel": True}, False),
        ({"camel": "false"}, True),
    ],
)
def test_add_wardrobe_plan_reuses_authoritative_builder_for_null_alias_and_path_parity(
    flag_arguments: dict[str, object],
    expected_flag: bool | None,
) -> None:
    flag_aliases = (
        ("add_menu_toggle", "addMenuToggle"),
        ("set_objects_default_off", "setObjectsDefaultOff"),
        ("sub_menu_overflow", "subMenuOverflow"),
        ("write_defaults", "writeDefaults"),
    )
    params: dict[str, object] = {
        "avatarPath": "Avatar",
        "parameterName": "Clothes",
        "outfitName": "Hoodie",
        "object_paths": ("Avatar/A", "Avatar/A", "Avatar/B"),
        "objectPaths": ["Avatar/B", "Avatar/C"],
        "off_object_paths": ["Avatar/Off", "Avatar/Off"],
        "value": None,
    }
    for snake, camel in flag_aliases:
        if "snake" in flag_arguments:
            params[snake] = flag_arguments["snake"]
        if "camel" in flag_arguments:
            params[camel] = flag_arguments["camel"]

    plan = build_workflow_execution_plan("vrcforge_add_wardrobe_outfit", params)

    assert plan == [
        ("vrc_add_wardrobe_outfit", build_add_wardrobe_outfit_request(params, False))
    ]
    assert len(plan) == 1
    arguments = plan[0][1]
    assert arguments["objectPaths"] == ["Avatar/A", "Avatar/B", "Avatar/C"]
    assert arguments["offObjectPaths"] == ["Avatar/Off"]
    assert "value" not in arguments
    for _snake, camel in flag_aliases:
        if expected_flag is None:
            assert camel not in arguments
        else:
            assert arguments[camel] is expected_flag


@pytest.mark.parametrize(
    ("value_arguments", "expected_value"),
    [
        ({"value": None}, None),
        ({"value": None, "outfitValue": 2}, 2),
        ({"outfit_value": None, "outfitValue": 2}, None),
        ({"outfitValue": "2"}, 2),
        ({"value": 0, "outfitValue": 2}, 0),
    ],
)
def test_add_outfit_part_plan_preserves_value_alias_null_and_fallback_parity(
    value_arguments: dict[str, object],
    expected_value: int | None,
) -> None:
    params: dict[str, object] = {
        "parameterName": "Clothes",
        "partName": "Hat",
        "objectPaths": ["Avatar/Hat"],
        **value_arguments,
    }

    plan = build_workflow_execution_plan("vrcforge_add_outfit_part", params)

    assert plan == [
        ("vrc_add_outfit_part", build_add_outfit_part_request(params, False))
    ]
    assert len(plan) == 1
    if expected_value is None:
        assert "value" not in plan[0][1]
    else:
        assert plan[0][1]["value"] == expected_value


@pytest.mark.parametrize(
    ("flag_arguments", "expected_flag"),
    [
        ({"camel": None}, None),
        ({"snake": None}, None),
        ({"snake": None, "camel": True}, False),
        ({"snake": False, "camel": True}, False),
        ({"camel": "false"}, True),
    ],
)
def test_add_outfit_part_plan_preserves_flag_null_precedence_and_truthiness(
    flag_arguments: dict[str, object],
    expected_flag: bool | None,
) -> None:
    flag_aliases = (
        ("add_menu_toggle", "addMenuToggle"),
        ("set_objects_default_off", "setObjectsDefaultOff"),
        ("default_on", "defaultOn"),
        ("write_defaults", "writeDefaults"),
    )
    params: dict[str, object] = {
        "parameterName": "Clothes",
        "partName": "Hat",
        "value": 2,
        "objectPaths": ["Avatar/Hat"],
    }
    for snake, camel in flag_aliases:
        if "snake" in flag_arguments:
            params[snake] = flag_arguments["snake"]
        if "camel" in flag_arguments:
            params[camel] = flag_arguments["camel"]

    plan = build_workflow_execution_plan("vrcforge_add_outfit_part", params)

    assert plan == [
        ("vrc_add_outfit_part", build_add_outfit_part_request(params, False))
    ]
    assert len(plan) == 1
    for _snake, camel in flag_aliases:
        if expected_flag is None:
            assert camel not in plan[0][1]
        else:
            assert plan[0][1][camel] is expected_flag


def test_add_outfit_part_plan_preserves_falsey_text_fallback_and_path_coercion() -> None:
    params: dict[str, object] = {
        "avatar_path": "",
        "avatarPath": "Avatar",
        "parameter_name": 0,
        "parameterName": "Clothes",
        "part_name": "",
        "partName": "",
        "display_name": False,
        "displayName": "Hat",
        "part_parameter_name": "",
        "partParameterName": "HatToggle",
        "sub_menu_name": "",
        "subMenuName": "More",
        "clip_output_dir": 0,
        "clipOutputDir": "Assets/Clips",
        "value": 2,
        "object_paths": "Avatar/A;Avatar/B",
        "objectPaths": ("Avatar/Tuple", "Avatar/Tuple"),
        "on_object_paths": ["Avatar/List", "Avatar/List"],
        "onObjectPaths": 42,
    }

    plan = build_workflow_execution_plan("vrcforge_add_outfit_part", params)

    assert plan == [
        ("vrc_add_outfit_part", build_add_outfit_part_request(params, False))
    ]
    assert len(plan) == 1
    arguments = plan[0][1]
    assert arguments["avatarPath"] == "Avatar"
    assert arguments["parameterName"] == "Clothes"
    assert arguments["partName"] == "Hat"
    assert arguments["partParameterName"] == "HatToggle"
    assert arguments["subMenuName"] == "More"
    assert arguments["clipOutputDir"] == "Assets/Clips"
    assert arguments["objectPaths"] == [
        "Avatar/A;Avatar/B",
        "Avatar/Tuple",
        "Avatar/List",
        "42",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        (True, True),
        (False, False),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("off", False),
        ("unexpected", False),
    ],
)
def test_modular_component_plan_preserves_save_scene_normalization(
    value: object,
    expected: bool,
) -> None:
    params = {
        "gameObjectPath": "Avatar/Outfit",
        "componentType": "MergeArmature",
        "saveScene": value,
    }
    plan = build_workflow_execution_plan(
        "vrcforge_add_modular_avatar_component",
        params,
    )

    assert plan == [
        (
            "vrc_add_modular_avatar_component",
            build_add_modular_avatar_component_request(params, False),
        )
    ]
    assert plan[0][1]["saveScene"] is expected


@pytest.mark.parametrize(
    ("allow_arguments", "expected"),
    [
        ({"allowDuplicate": None}, None),
        ({"allow_duplicate": None}, None),
        ({"allow_duplicate": None, "allowDuplicate": True}, False),
        ({"allow_duplicate": False, "allowDuplicate": True}, False),
        ({"allowDuplicate": "false"}, True),
    ],
)
def test_modular_component_plan_preserves_allow_duplicate_null_and_falsey_aliases(
    allow_arguments: dict[str, object],
    expected: bool | None,
) -> None:
    params = {
        "gameObjectPath": "Avatar/Outfit",
        "componentType": "MergeArmature",
        **allow_arguments,
    }
    plan = build_workflow_execution_plan(
        "vrcforge_add_modular_avatar_component",
        params,
    )

    assert len(plan) == 1
    assert plan == [
        (
            "vrc_add_modular_avatar_component",
            build_add_modular_avatar_component_request(params, False),
        )
    ]
    if expected is None:
        assert "allowDuplicate" not in plan[0][1]
    else:
        assert plan[0][1]["allowDuplicate"] is expected


def test_modular_component_plan_preserves_paths_component_and_dict_projection() -> None:
    params = {
        "game_object_path": "",
        "gameObjectPath": 0,
        "target_path": False,
        "targetPath": "Avatar/Outfit",
        "component_type": "",
        "componentType": "MergeArmature",
        "avatar_path": "",
        "avatarPath": "Avatar",
        "references": {"mergeTarget": "Armature"},
        "fields": {"nested": {"enabled": True}},
    }
    plan = build_workflow_execution_plan(
        "vrcforge_add_modular_avatar_component",
        params,
    )

    assert plan == [
        (
            "vrc_add_modular_avatar_component",
            build_add_modular_avatar_component_request(params, False),
        )
    ]
    assert plan[0][1] == {
        "gameObjectPath": "Avatar/Outfit",
        "componentType": "MergeArmature",
        "preview": False,
        "saveScene": False,
        "avatarPath": "Avatar",
        "references": {"mergeTarget": "Armature"},
        "fields": {"nested": {"enabled": True}},
    }


def test_modular_component_plan_deep_copies_nested_reference_and_field_payloads() -> None:
    references = {"mergeTarget": {"path": "Armature"}}
    fields = {"nested": {"enabled": True}}
    plan = build_workflow_execution_plan(
        "vrcforge_add_modular_avatar_component",
        {
            "gameObjectPath": "Avatar/Outfit",
            "componentType": "MergeArmature",
            "references": references,
            "fields": fields,
        },
    )

    references["mergeTarget"]["path"] = "Changed"
    fields["nested"]["enabled"] = False

    assert plan[0][1]["references"] == {
        "mergeTarget": {"path": "Armature"}
    }
    assert plan[0][1]["fields"] == {"nested": {"enabled": True}}


@pytest.mark.parametrize(
    ("guard_key", "guard_value"),
    [
        (guard_key, guard_value)
        for guard_key in (
            "primitiveLive",
            "primitive_live",
            "expectedSceneIdentity",
            "expected_scene_identity",
        )
        for guard_value in (None, False)
    ],
)
def test_modular_component_plan_rejects_live_identity_guard_arguments(
    guard_key: str,
    guard_value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow_plans,
        "build_add_modular_avatar_component_request",
        lambda params, _preview: builder_calls.append(params) or {},
    )

    with pytest.raises(ValueError, match="frozen live-instance identity/readback"):
        build_workflow_execution_plan(
            "vrcforge_add_modular_avatar_component",
            {
                "gameObjectPath": "Avatar/Outfit",
                "componentType": "MergeArmature",
                guard_key: guard_value,
            },
        )
    assert builder_calls == []


def test_modular_component_randomized_plan_parity_uses_one_canonical_call() -> None:
    rng = random.Random(0xC5)
    text_values: list[object] = [None, "", False, 0, "Avatar/Outfit", " MergeArmature "]
    bool_values: list[object] = [None, True, False, 0, 1, "yes", "false", "unknown"]
    aliases = (
        "game_object_path",
        "gameObjectPath",
        "target_path",
        "targetPath",
        "component_type",
        "componentType",
        "avatar_path",
        "avatarPath",
    )

    for _ in range(200):
        params: dict[str, object] = {}
        for alias in aliases:
            if rng.choice((True, False)):
                params[alias] = rng.choice(text_values)
        for alias in ("save_scene", "saveScene", "allow_duplicate", "allowDuplicate"):
            if rng.choice((True, False)):
                params[alias] = rng.choice(bool_values)
        params["references"] = rng.choice(
            [None, [], {}, {"target": "Armature"}, {"nested": {"value": 1}}]
        )
        params["fields"] = rng.choice(
            [None, "bad", {}, {"prefix": ""}, {"nested": {"enabled": True}}]
        )

        plan = build_workflow_execution_plan(
            "vrcforge_add_modular_avatar_component",
            params,
        )

        assert plan == [
            (
                "vrc_add_modular_avatar_component",
                build_add_modular_avatar_component_request(params, False),
            )
        ]
        assert len(plan) == 1
        assert plan[0][1]["preview"] is False


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
