import random

import pytest

import dashboard_server as dashboard
import unity_execution_plans_workflows as workflow_plans
from unity_execution_plans_workflows import build_workflow_execution_plan
from wardrobe_outfit_workflow_service import (
    build_add_modular_avatar_component_request,
    build_add_outfit_part_request,
    build_add_wardrobe_outfit_request,
    build_create_wardrobe_core_calls,
    build_create_wardrobe_request,
    build_manage_wardrobe_request,
    build_setup_outfit_request,
)


def _legacy_manage_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _legacy_manage_int_list(
    params: dict[str, object],
    *keys: str,
) -> list[int]:
    result: list[int] = []
    for key in keys:
        raw = params.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple)):
            for item in raw:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value not in result:
                    result.append(value)
            continue
        for part in str(raw).replace(";", ",").replace(" ", ",").split(","):
            if not part.strip():
                continue
            try:
                value = int(part.strip())
            except ValueError:
                continue
            if value not in result:
                result.append(value)
    return result


def _legacy_manage_wardrobe_request(
    params: dict[str, object],
    preview: bool,
) -> dict[str, object]:
    request: dict[str, object] = {
        "action": str(params.get("action") or "").strip(),
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "parameterName": str(
            params.get("parameter_name")
            or params.get("parameterName")
            or params.get("wardrobe_parameter")
            or params.get("wardrobeParameter")
            or ""
        ).strip(),
        "preview": preview,
    }
    for source_key, target_key in (
        ("outfit_name", "outfitName"),
        ("outfitName", "outfitName"),
        ("target_name", "targetName"),
        ("targetName", "targetName"),
        ("state_name", "stateName"),
        ("stateName", "stateName"),
        ("control_name", "controlName"),
        ("controlName", "controlName"),
        ("new_name", "newName"),
        ("newName", "newName"),
        ("new_outfit_name", "newOutfitName"),
        ("newOutfitName", "newOutfitName"),
        ("asset_dir", "assetDir"),
        ("assetDir", "assetDir"),
        ("clip_output_dir", "clipOutputDir"),
        ("clipOutputDir", "clipOutputDir"),
    ):
        value = str(params.get(source_key) or "").strip()
        if value:
            request[target_key] = value
    for source_key, target_key in (
        ("target_value", "targetValue"),
        ("targetValue", "targetValue"),
        ("outfit_value", "outfitValue"),
        ("outfitValue", "outfitValue"),
        ("value", "value"),
    ):
        if params.get(source_key) is not None:
            request[target_key] = int(params[source_key])
            break
    order_values = _legacy_manage_int_list(params, "order_values", "orderValues")
    if order_values:
        request["orderValues"] = order_values
    target_values = _legacy_manage_int_list(
        params,
        "target_values",
        "targetValues",
        "values",
    )
    if target_values:
        request["targetValues"] = target_values
    for source_key, target_key, default in (
        ("delete_objects", "deleteObjects", False),
        ("deleteObjects", "deleteObjects", False),
        ("deactivate_objects", "deactivateObjects", True),
        ("deactivateObjects", "deactivateObjects", True),
        ("delete_generated_assets", "deleteGeneratedAssets", False),
        ("deleteGeneratedAssets", "deleteGeneratedAssets", False),
        ("confirm_delete_wardrobe", "confirmDeleteWardrobe", False),
        ("confirmDeleteWardrobe", "confirmDeleteWardrobe", False),
    ):
        if params.get(source_key) is not None:
            request[target_key] = _legacy_manage_bool(
                params[source_key],
                default,
            )
    return request


def _legacy_create_wardrobe_request(
    params: dict[str, object],
    preview: bool,
) -> dict[str, object]:
    request: dict[str, object] = {
        "avatarPath": str(
            params.get("avatar_path") or params.get("avatarPath") or ""
        ).strip(),
        "parameterName": str(
            params.get("parameter_name")
            or params.get("parameterName")
            or params.get("wardrobe_parameter")
            or params.get("wardrobeParameter")
            or "Clothes"
        ).strip(),
        "preview": preview,
    }
    for destination, names in (
        ("menuName", ("menu_name", "menuName", "sub_menu_name", "subMenuName")),
        ("defaultControlName", ("default_control_name", "defaultControlName")),
        ("layerName", ("layer_name", "layerName")),
        ("assetDir", ("asset_dir", "assetDir", "clip_output_dir", "clipOutputDir")),
    ):
        value = str(next((params[name] for name in names if params.get(name)), "")).strip()
        if value:
            request[destination] = value
    if (
        params.get("write_defaults") is not None
        or params.get("writeDefaults") is not None
    ):
        request["writeDefaults"] = _legacy_manage_bool(
            params.get("write_defaults", params.get("writeDefaults")),
            True,
        )
    if params.get("saved") is not None:
        request["saved"] = _legacy_manage_bool(params.get("saved"), True)
    if (
        params.get("network_synced") is not None
        or params.get("networkSynced") is not None
    ):
        request["networkSynced"] = _legacy_manage_bool(
            params.get("network_synced", params.get("networkSynced")),
            True,
        )
    return request


def _legacy_create_wardrobe_calls(
    params: dict[str, object],
    preview: bool,
) -> list[tuple[str, dict[str, object]]]:
    request = _legacy_create_wardrobe_request(params, preview)
    avatar = request["avatarPath"]
    parameter = request["parameterName"]
    asset_dir = request.get("assetDir", "Assets/VRCForge/Generated/Wardrobe")
    menu = str(request.get("menuName") or "Wardrobe").strip() or "Wardrobe"
    control = str(request.get("defaultControlName") or "Default").strip() or "Default"
    layer = str(request.get("layerName") or parameter).strip() or str(parameter)
    common = {"avatarPath": avatar, "assetDir": asset_dir}
    return [
        (
            "vrc_ensure_expression_parameter",
            {
                **common,
                "parameterName": parameter,
                "valueType": "Int",
                "defaultValue": 0.0,
                "saved": bool(request.get("saved", True)),
                "networkSynced": bool(request.get("networkSynced", True)),
                "preview": preview,
            },
        ),
        (
            "vrc_ensure_animator_state",
            {
                **common,
                "layerName": layer,
                "stateName": control,
                "parameterName": parameter,
                "parameterType": "Int",
                "conditionMode": "Equals",
                "threshold": 0.0,
                "writeDefaults": bool(request.get("writeDefaults", True)),
                "preview": preview,
            },
        ),
        (
            "vrc_ensure_expression_menu_control",
            {
                **common,
                "menuPath": menu,
                "controlName": control,
                "controlType": "Toggle",
                "parameterName": parameter,
                "controlValue": 0.0,
                "preview": preview,
            },
        ),
    ]


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


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"parameterName": "Clothes", "menuName": False}, (2, "menuPath", "Wardrobe")),
        ({"parameterName": "Clothes", "defaultControlName": 0}, (1, "stateName", "Default")),
        ({"parameterName": "Clothes", "layerName": False}, (1, "layerName", "Clothes")),
        (
            {
                "parameterName": "Clothes",
                "assetDir": 0,
                "clipOutputDir": "Assets/Fallback",
            },
            (0, "assetDir", "Assets/Fallback"),
        ),
        ({"parameterName": "Clothes", "avatarPath": 0}, (0, "avatarPath", "")),
    ],
)
def test_create_wardrobe_plan_uses_exact_handler_falsey_and_alias_semantics(
    params: dict[str, object],
    expected: tuple[int, str, object],
) -> None:
    legacy_request = _legacy_create_wardrobe_request(params, False)
    legacy_calls = _legacy_create_wardrobe_calls(params, False)

    assert build_create_wardrobe_request(params, False) == legacy_request
    assert build_create_wardrobe_core_calls(params, False) == legacy_calls
    assert build_workflow_execution_plan("vrcforge_create_wardrobe", params) == legacy_calls
    index, key, value = expected
    assert legacy_calls[index][1][key] == value


def test_create_wardrobe_whitespace_parameter_fails_before_any_frozen_write() -> None:
    params = {"parameter_name": " ", "parameterName": "Clothes"}
    assert build_create_wardrobe_request(params, False)["parameterName"] == ""
    with pytest.raises(
        ValueError,
        match="parameterName is required for wardrobe creation",
    ):
        build_workflow_execution_plan("vrcforge_create_wardrobe", params)


def test_create_wardrobe_randomized_legacy_builder_and_plan_parity() -> None:
    rng = random.Random(0xC7)
    values: list[object] = [
        None,
        "",
        " ",
        False,
        0,
        True,
        1,
        "Clothes",
        " Wardrobe ",
        [],
        {},
    ]
    bool_values: list[object] = [
        None,
        True,
        False,
        0,
        1,
        0.5,
        "yes",
        "false",
        "unknown",
    ]
    text_keys = (
        "avatar_path",
        "avatarPath",
        "parameter_name",
        "parameterName",
        "wardrobe_parameter",
        "wardrobeParameter",
        "menu_name",
        "menuName",
        "sub_menu_name",
        "subMenuName",
        "default_control_name",
        "defaultControlName",
        "layer_name",
        "layerName",
        "asset_dir",
        "assetDir",
        "clip_output_dir",
        "clipOutputDir",
    )
    bool_keys = (
        "write_defaults",
        "writeDefaults",
        "saved",
        "network_synced",
        "networkSynced",
    )

    for _ in range(2500):
        params: dict[str, object] = {}
        for key in text_keys:
            if rng.choice((True, False)):
                params[key] = rng.choice(values)
        for key in bool_keys:
            if rng.choice((True, False)):
                params[key] = rng.choice(bool_values)

        legacy_request = _legacy_create_wardrobe_request(params, False)
        assert build_create_wardrobe_request(params, False) == legacy_request
        if not legacy_request["parameterName"]:
            with pytest.raises(ValueError):
                build_workflow_execution_plan("vrcforge_create_wardrobe", params)
            continue
        legacy_calls = _legacy_create_wardrobe_calls(params, False)
        assert build_create_wardrobe_core_calls(params, False) == legacy_calls
        assert build_workflow_execution_plan("vrcforge_create_wardrobe", params) == legacy_calls


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
    expected = build_manage_wardrobe_request(params, False)
    assert build_workflow_execution_plan("vrcforge_manage_wardrobe", params) == [("vrc_manage_wardrobe", expected)]


def test_manage_wardrobe_canonical_builder_preserves_falsey_and_late_alias_overwrite() -> None:
    params = {
        "action": "rename_outfit",
        "wardrobeParameter": False,
        "control_name": 0,
        "new_name": ["Legacy"],
        "newName": "Coat",
        "delete_objects": True,
        "deleteObjects": False,
        "deactivate_objects": False,
        "deactivateObjects": "unknown",
        "target_value": None,
        "targetValue": 3,
        "order_values": "3; 2 3 bad",
        "orderValues": (4, "2"),
        "target_values": [3, "bad", 3],
        "targetValues": "4, 5",
    }

    expected = _legacy_manage_wardrobe_request(params, False)
    actual = build_manage_wardrobe_request(params, False)
    plan = build_workflow_execution_plan("vrcforge_manage_wardrobe", params)

    assert actual == expected
    assert plan == [("vrc_manage_wardrobe", expected)]
    assert len(plan) == 1
    assert actual["parameterName"] == ""
    assert "controlName" not in actual
    assert actual["newName"] == "Coat"
    assert actual["deleteObjects"] is False
    assert actual["deactivateObjects"] is True
    assert actual["targetValue"] == 3
    assert actual["orderValues"] == [3, 2, 4]
    assert actual["targetValues"] == [3, 4, 5]


def test_manage_wardrobe_randomized_legacy_builder_and_plan_parity() -> None:
    rng = random.Random(0xC6)
    text_values: list[object] = [
        None,
        "",
        False,
        0,
        " Clothes ",
        ["Legacy"],
    ]
    int_values: list[object] = [None, 0, 1, -2, "3", "bad", [], {}]
    bool_values: list[object] = [
        None,
        True,
        False,
        0,
        1,
        0.5,
        "yes",
        "false",
        "unknown",
    ]
    int_list_values: list[object] = [
        None,
        [],
        [1, "2", "bad", 1],
        (3, "4", 3),
        "5; 6 5 bad",
        7,
    ]
    text_keys = (
        "action",
        "avatar_path",
        "avatarPath",
        "parameter_name",
        "parameterName",
        "wardrobe_parameter",
        "wardrobeParameter",
        "outfit_name",
        "outfitName",
        "target_name",
        "targetName",
        "state_name",
        "stateName",
        "control_name",
        "controlName",
        "new_name",
        "newName",
        "new_outfit_name",
        "newOutfitName",
        "asset_dir",
        "assetDir",
        "clip_output_dir",
        "clipOutputDir",
    )
    int_keys = (
        "target_value",
        "targetValue",
        "outfit_value",
        "outfitValue",
        "value",
    )
    bool_keys = (
        "delete_objects",
        "deleteObjects",
        "deactivate_objects",
        "deactivateObjects",
        "delete_generated_assets",
        "deleteGeneratedAssets",
        "confirm_delete_wardrobe",
        "confirmDeleteWardrobe",
    )
    int_list_keys = (
        "order_values",
        "orderValues",
        "target_values",
        "targetValues",
        "values",
    )

    for _ in range(1000):
        params: dict[str, object] = {}
        for key in text_keys:
            if rng.choice((True, False)):
                params[key] = rng.choice(text_values)
        for key in int_keys:
            if rng.choice((True, False)):
                params[key] = rng.choice(int_values)
        for key in bool_keys:
            if rng.choice((True, False)):
                params[key] = rng.choice(bool_values)
        for key in int_list_keys:
            if rng.choice((True, False)):
                params[key] = rng.choice(int_list_values)

        def capture(call):
            try:
                return ("ok", call())
            except (TypeError, ValueError) as exc:
                return ("error", type(exc), str(exc))

        expected = capture(lambda: _legacy_manage_wardrobe_request(params, False))
        actual = capture(lambda: build_manage_wardrobe_request(params, False))
        plan = capture(
            lambda: build_workflow_execution_plan("vrcforge_manage_wardrobe", params)
        )

        assert actual == expected
        if expected[0] == "error":
            assert plan == expected
        else:
            assert plan == (
                "ok",
                [("vrc_manage_wardrobe", expected[1])],
            )
            assert len(plan[1]) == 1


def test_create_wardrobe_plan_has_shared_builder_parity_and_passes_preview_false_to_all_three_calls() -> None:
    params = {"avatarPath": "Avatar", "parameterName": "Clothes", "writeDefaults": "no"}
    expected = build_create_wardrobe_core_calls(params, False)
    rebuilt = [
        dashboard.build_ensure_expression_parameter_request(expected[0][1], False),
        dashboard.build_ensure_animator_state_request(expected[1][1], False),
        dashboard.build_ensure_expression_menu_control_request(expected[2][1], False),
    ]
    assert build_workflow_execution_plan("vrcforge_create_wardrobe", params) == expected
    assert len(expected) == 3
    assert all(arguments["preview"] is False for _name, arguments in expected)
    assert [arguments for _name, arguments in expected] == rebuilt


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
