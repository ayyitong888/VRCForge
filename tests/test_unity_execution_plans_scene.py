import pytest

from unity_execution_plans_scene import build_scene_execution_plan


@pytest.mark.parametrize(("target", "arguments", "expected"), [
    ("vrcforge_add_component", {"objectPath": "Root", "componentType": "Box"}, ("vrc_add_component", {"gameObjectPath": "Root", "componentType": "Box", "preview": False})),
    ("vrcforge_remove_component", {"game_object_path": "Root", "component_type": "Box", "component_index": "2"}, ("vrc_remove_component", {"gameObjectPath": "Root", "componentType": "Box", "componentIndex": 2, "preview": False})),
    ("vrcforge_set_property", {"gameObjectPath": "Root", "componentType": "Box", "property_path": "size.x", "value": 3}, ("vrc_set_property", {"gameObjectPath": "Root", "componentType": "Box", "propertyPath": "size.x", "componentIndex": 0, "preview": False, "value": 3})),
    ("vrcforge_create_gameobject", {"name": " Child ", "parent_path": "Root"}, ("vrc_create_gameobject", {"name": "Child", "parentPath": "Root", "preview": False})),
    ("vrcforge_rename_gameobject", {"objectPath": "Root", "new_name": "New"}, ("vrc_rename_gameobject", {"gameObjectPath": "Root", "newName": "New", "preview": False})),
    ("vrcforge_reparent_gameobject", {"gameObjectPath": "A", "new_parent_path": "B", "worldPositionStays": False}, ("vrc_reparent_gameobject", {"gameObjectPath": "A", "newParentPath": "B", "worldPositionStays": False, "preview": False})),
    ("vrcforge_delete_gameobject", {"gameObjectPath": "A"}, ("vrc_delete_gameobject", {"gameObjectPath": "A", "preview": False})),
    ("vrcforge_set_gameobject_active", {"object_path": "A", "isActive": False}, ("vrc_set_gameobject_active", {"gameObjectPath": "A", "active": False, "preview": False})),
    ("vrcforge_instantiate_prefab", {"asset_path": "Assets/X.prefab", "world_position_stays": False}, ("vrc_instantiate_prefab", {"assetPath": "Assets/X.prefab", "guid": "", "parentPath": "", "name": "", "worldPositionStays": False, "preview": False})),
    ("vrcforge_unpack_prefab", {"gameObjectPath": "A"}, ("vrc_unpack_prefab", {"gameObjectPath": "A", "mode": "outermost", "preview": False})),
    ("vrcforge_toggle_scene_object", {"object_path": "A", "active": True}, ("vrc_toggle_scene_object", {"objectPath": "A", "active": True, "saveAssets": True})),
])
def test_scene_handler_argument_projection(target, arguments, expected):
    assert build_scene_execution_plan(target, arguments) == [expected]


def test_expression_builders_preserve_aliases_defaults_and_non_preview():
    tool, args = build_scene_execution_plan("vrcforge_ensure_expression_parameter", {"avatar_path": "A", "parameter_name": "P", "value_type": "", "default_value": "2.5", "saved": "off", "network_synced": "yes", "asset_dir": "Assets/G"})[0]
    assert (tool, args) == ("vrc_ensure_expression_parameter", {"avatarPath": "A", "parameterName": "P", "valueType": "Int", "defaultValue": 2.5, "saved": False, "networkSynced": True, "preview": False, "assetDir": "Assets/G"})
    tool, args = build_scene_execution_plan("vrcforge_ensure_animator_state", {"avatarPath": "A", "layerName": "FX", "stateName": "On", "parameterName": "P", "write_defaults": "false"})[0]
    assert tool == "vrc_ensure_animator_state"
    assert args["preview"] is False and args["writeDefaults"] is False and args["parameterType"] == "Int"


@pytest.mark.parametrize("target,tool", [
    ("vrcforge_write_avatar_descriptor", "vrc_write_avatar_descriptor"),
    ("vrcforge_write_animation_curve", "vrc_write_animation_curve"),
    ("vrcforge_manage_expression_parameters", "vrc_manage_expression_parameters"),
    ("vrcforge_manage_expression_menu", "vrc_manage_expression_menu"),
    ("vrcforge_manage_fx_animator", "vrc_manage_fx_animator"),
])
def test_avatar_primitive_builders_copy_canonical_or_alias_and_force_non_preview(target, tool):
    result_tool, args = build_scene_execution_plan(target, {"avatar_path": "A", "property_name": "m_Value", "preview": True})[0]
    assert result_tool == tool
    assert args == {"avatarPath": "A", "propertyName": "m_Value", "preview": False}


def test_unknown_target_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        build_scene_execution_plan("vrcforge_not_a_scene_write", {})
