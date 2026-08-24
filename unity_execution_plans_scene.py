"""Pure approved-execution plans for simple Unity scene write handlers.

Keep these argument projections byte-for-byte compatible with the legacy
dashboard handlers.  The gateway freezes the returned calls at approval time.
"""

from __future__ import annotations

from typing import Any


SCENE_EXECUTION_PLAN_TARGETS = frozenset(
    {
        "vrcforge_add_component",
        "vrcforge_remove_component",
        "vrcforge_set_property",
        "vrcforge_gesture_manager_set_parameter",
        "vrcforge_gesture_manager_enter_play_mode",
        "vrcforge_select_scene_object",
        "vrcforge_set_play_mode",
        "vrcforge_create_gameobject",
        "vrcforge_rename_gameobject",
        "vrcforge_reparent_gameobject",
        "vrcforge_delete_gameobject",
        "vrcforge_set_gameobject_active",
        "vrcforge_instantiate_prefab",
        "vrcforge_duplicate_scene_object",
        "vrcforge_unpack_prefab",
        "vrcforge_toggle_scene_object",
        "vrcforge_remap_skinned_mesh_bone",
        "vrcforge_ensure_expression_parameter",
        "vrcforge_ensure_expression_menu_control",
        "vrcforge_ensure_animator_state",
        "vrcforge_write_avatar_descriptor",
        "vrcforge_write_animation_curve",
        "vrcforge_manage_expression_parameters",
        "vrcforge_manage_expression_menu",
        "vrcforge_manage_fx_animator",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    value = str(value).strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _component_target(params: dict[str, Any]) -> tuple[str, str]:
    return (
        _text(params.get("game_object_path") or params.get("gameObjectPath") or params.get("object_path") or params.get("objectPath")),
        _text(params.get("component_type") or params.get("componentType")),
    )


def _gameobject_target(params: dict[str, Any]) -> str:
    return _text(params.get("game_object_path") or params.get("gameObjectPath") or params.get("object_path") or params.get("objectPath"))


_AVATAR_KEYS = (
    "action", "avatarPath", "clipPath", "bindingPath", "objectPath", "componentType", "propertyName", "sourceBindingPath", "sourceComponentType", "sourcePropertyName", "deleteSource", "overwriteExisting", "constantFloat", "keys", "parameterName", "newName", "orderNames", "valueType", "defaultValue", "saved", "networkSynced", "menuPath", "controlName", "controlIndex", "controlType", "controlFloat", "value", "iconAssetPath", "subMenuAssetPath", "createSubMenu", "subParameters", "assetDir", "controllerPath", "fxControllerPath", "layerName", "stateName", "destinationStateName", "transitionIndex", "hasExitTime", "exitTime", "duration", "canTransitionToSelf", "conditions", "parameterType", "conditionMode", "threshold", "writeDefaults", "motionClipPath", "speed", "viewPosition", "lipSync", "visemeSkinnedMeshPath", "visemeBlendShapes", "expressionParametersPath", "expressionsMenuPath", "baseAnimationLayers", "specialAnimationLayers", "eyeLookSettingsSourceAvatarPath", "eyeLookEnabled",
)
_AVATAR_ALIASES = {
    "avatarPath": ("avatar_path",), "clipPath": ("clip_path",), "bindingPath": ("binding_path",), "componentType": ("component_type",), "propertyName": ("property_name",), "sourceBindingPath": ("source_binding_path",), "sourceComponentType": ("source_component_type",), "sourcePropertyName": ("source_property_name",), "deleteSource": ("delete_source",), "overwriteExisting": ("overwrite_existing",), "constantFloat": ("constant_float",), "parameterName": ("parameter_name",), "newName": ("new_name",), "orderNames": ("order_names",), "valueType": ("value_type",), "defaultValue": ("default_value",), "networkSynced": ("network_synced",), "menuPath": ("menu_path",), "controlName": ("control_name",), "controlIndex": ("control_index",), "controlType": ("control_type",), "controlFloat": ("control_float", "control_value"), "iconAssetPath": ("icon_asset_path",), "subMenuAssetPath": ("sub_menu_asset_path",), "createSubMenu": ("create_sub_menu",), "subParameters": ("sub_parameters",), "assetDir": ("asset_dir",), "controllerPath": ("controller_path",), "fxControllerPath": ("fx_controller_path",), "layerName": ("layer_name",), "stateName": ("state_name",), "destinationStateName": ("destination_state_name",), "transitionIndex": ("transition_index",), "hasExitTime": ("has_exit_time",), "exitTime": ("exit_time",), "canTransitionToSelf": ("can_transition_to_self",), "parameterType": ("parameter_type",), "conditionMode": ("condition_mode",), "writeDefaults": ("write_defaults",), "motionClipPath": ("motion_clip_path",), "viewPosition": ("view_position",), "visemeSkinnedMeshPath": ("viseme_skinned_mesh_path",), "visemeBlendShapes": ("viseme_blend_shapes",), "expressionParametersPath": ("expression_parameters_path",), "expressionsMenuPath": ("expressions_menu_path",), "baseAnimationLayers": ("base_animation_layers",), "specialAnimationLayers": ("special_animation_layers",), "eyeLookSettingsSourceAvatarPath": ("eye_look_settings_source_avatar_path",), "eyeLookEnabled": ("eye_look_enabled",),
}


def _avatar_primitive(params: dict[str, Any]) -> dict[str, Any]:
    request = {key: params[key] for key in _AVATAR_KEYS if key in params}
    for canonical, aliases in _AVATAR_ALIASES.items():
        if canonical not in request:
            for alias in aliases:
                if alias in params:
                    request[canonical] = params[alias]
                    break
    request["preview"] = False
    return request


def build_scene_execution_plan(target_name: str, arguments: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Map one dashboard write target to its exact non-preview Core invocation."""
    params = dict(arguments or {})
    if target_name == "vrcforge_gesture_manager_set_parameter":
        request = {
            "avatarPath": _text(params.get("avatar_path") or params.get("avatarPath")),
            "parameterName": _text(params.get("parameter_name") or params.get("parameterName")),
            "value": params.get("value"),
        }
        return [("vrc_gesture_manager_set_parameter", request)]
    if target_name == "vrcforge_gesture_manager_enter_play_mode":
        return [("vrc_gesture_manager_enter_play_mode", {
            "avatarPath": _text(params.get("avatar_path") or params.get("avatarPath")),
        })]
    if target_name == "vrcforge_select_scene_object":
        return [("vrc_select_scene_object", {
            "gameObjectPath": _text(
                params.get("game_object_path")
                or params.get("gameObjectPath")
                or params.get("object_path")
                or params.get("objectPath")
            ),
        })]
    if target_name == "vrcforge_set_play_mode":
        return [("vrc_set_play_mode", {"isPlaying": params.get("is_playing", params.get("isPlaying"))})]
    if target_name in {"vrcforge_add_component", "vrcforge_remove_component", "vrcforge_set_property"}:
        path, component = _component_target(params)
        if target_name == "vrcforge_add_component":
            return [("vrc_add_component", {"gameObjectPath": path, "componentType": component, "preview": False})]
        request = {"gameObjectPath": path, "componentType": component, "componentIndex": int(params.get("component_index", params.get("componentIndex", 0)) or 0), "preview": False}
        if target_name == "vrcforge_set_property":
            request["propertyPath"] = _text(params.get("property_path") or params.get("propertyPath"))
            request["value"] = params.get("value")
            return [("vrc_set_property", request)]
        return [("vrc_remove_component", request)]

    gameobject_tools = {
        "vrcforge_rename_gameobject": ("vrc_rename_gameobject", lambda: {"gameObjectPath": _gameobject_target(params), "newName": _text(params.get("new_name") or params.get("newName")), "preview": False}),
        "vrcforge_reparent_gameobject": ("vrc_reparent_gameobject", lambda: {"gameObjectPath": _gameobject_target(params), "newParentPath": _text(params.get("new_parent_path") or params.get("newParentPath")), "worldPositionStays": bool(params.get("world_position_stays", params.get("worldPositionStays", True))), "preview": False}),
        "vrcforge_delete_gameobject": (
            "vrc_delete_gameobject",
            lambda: {
                "gameObjectPath": _gameobject_target(params),
                "globalObjectId": _text(params.get("global_object_id") or params.get("globalObjectId")),
                "preview": False,
            },
        ),
        "vrcforge_set_gameobject_active": ("vrc_set_gameobject_active", lambda: {"gameObjectPath": _gameobject_target(params), "active": bool(params.get("active", params.get("isActive"))), "preview": False}),
        "vrcforge_unpack_prefab": ("vrc_unpack_prefab", lambda: {"gameObjectPath": _gameobject_target(params), "mode": _text(params.get("mode") or "outermost"), "preview": False}),
    }
    if target_name in gameobject_tools:
        tool, factory = gameobject_tools[target_name]
        return [(tool, factory())]
    if target_name == "vrcforge_create_gameobject":
        return [("vrc_create_gameobject", {"name": _text(params.get("name")), "parentPath": _text(params.get("parent_path") or params.get("parentPath")), "preview": False})]
    if target_name == "vrcforge_instantiate_prefab":
        return [("vrc_instantiate_prefab", {"assetPath": _text(params.get("asset_path") or params.get("assetPath")), "guid": _text(params.get("guid")), "parentPath": _text(params.get("parent_path") or params.get("parentPath")), "name": _text(params.get("name")), "worldPositionStays": bool(params.get("world_position_stays", params.get("worldPositionStays", True))), "preview": False})]
    if target_name == "vrcforge_duplicate_scene_object":
        nested = params.get("arguments") if isinstance(params.get("arguments"), dict) else params
        request = {
            key: nested[key]
            for key in (
                "sourceScenePath", "sourceObjectPath", "targetParentScenePath",
                "targetParentPath", "targetName", "preserveWorldTransform",
                "expectedProjectPath", "expectedSourceSceneGuid", "expectedSourceSceneHandle",
                "expectedSourceObjectId", "expectedSourceHierarchyDigest", "expectedSourceSceneFileDigest",
                "expectedSourceSceneFileIdentity", "expectedSourceSceneMetaDigest", "expectedSourceSceneMetaIdentity",
                "expectedTargetSceneGuid", "expectedTargetSceneHandle", "expectedTargetParentObjectId",
                "expectedTargetParentHierarchyDigest", "expectedTargetSceneFileDigest", "expectedTargetSceneFileIdentity",
                "expectedTargetSceneMetaDigest", "expectedTargetSceneMetaIdentity", "expectedDestinationPath",
                "expectedPreviewDigest",
            )
            if key in nested
        }
        request.update({"preview": False, "saveScene": True, "overwrite": False})
        return [("vrc_duplicate_scene_object", request)]
    if target_name == "vrcforge_toggle_scene_object":
        return [("vrc_toggle_scene_object", {"objectPath": _text(params.get("object_path") or params.get("objectPath")), "active": bool(params.get("active")), "saveAssets": True})]
    if target_name == "vrcforge_remap_skinned_mesh_bone":
        return [("vrc_remap_skinned_mesh_bone", {
            "gameObjectPath": _gameobject_target(params),
            "componentIndex": int(params.get("component_index", params.get("componentIndex", 0)) or 0),
            "boneIndex": int(params.get("bone_index", params.get("boneIndex", 0)) or 0),
            "expectedCurrentBonePath": _text(params.get("expected_current_bone_path") or params.get("expectedCurrentBonePath")),
            "targetBonePath": _text(params.get("target_bone_path") or params.get("targetBonePath")),
            "expectedMeshName": _text(params.get("expected_mesh_name") or params.get("expectedMeshName")),
            "preview": False,
        })]

    if target_name == "vrcforge_ensure_expression_parameter":
        request = {"avatarPath": _text(params.get("avatar_path") or params.get("avatarPath")), "parameterName": _text(params.get("parameter_name") or params.get("parameterName")), "valueType": _text(params.get("value_type") or params.get("valueType") or "Int") or "Int", "defaultValue": float(params.get("default_value", params.get("defaultValue", 0)) or 0), "saved": _bool(params.get("saved"), True), "networkSynced": _bool(params.get("network_synced", params.get("networkSynced")), True), "preview": False}
        if _text(params.get("asset_dir") or params.get("assetDir")):
            request["assetDir"] = _text(params.get("asset_dir") or params.get("assetDir"))
        return [("vrc_ensure_expression_parameter", request)]
    if target_name == "vrcforge_ensure_expression_menu_control":
        request = {"avatarPath": _text(params.get("avatar_path") or params.get("avatarPath")), "menuPath": _text(params.get("menu_path") or params.get("menuPath")), "controlName": _text(params.get("control_name") or params.get("controlName")), "controlType": _text(params.get("control_type") or params.get("controlType") or "Toggle") or "Toggle", "parameterName": _text(params.get("parameter_name") or params.get("parameterName")), "controlValue": float(params.get("control_value", params.get("controlValue", 0)) or 0), "preview": False}
        if _text(params.get("asset_dir") or params.get("assetDir")):
            request["assetDir"] = _text(params.get("asset_dir") or params.get("assetDir"))
        return [("vrc_ensure_expression_menu_control", request)]
    if target_name == "vrcforge_ensure_animator_state":
        request = {"avatarPath": _text(params.get("avatar_path") or params.get("avatarPath")), "layerName": _text(params.get("layer_name") or params.get("layerName")), "stateName": _text(params.get("state_name") or params.get("stateName")), "parameterName": _text(params.get("parameter_name") or params.get("parameterName")), "parameterType": _text(params.get("parameter_type") or params.get("parameterType") or "Int") or "Int", "conditionMode": _text(params.get("condition_mode") or params.get("conditionMode") or "Equals") or "Equals", "threshold": float(params.get("threshold", 0) or 0), "writeDefaults": _bool(params.get("write_defaults", params.get("writeDefaults")), True), "preview": False}
        if _text(params.get("asset_dir") or params.get("assetDir")):
            request["assetDir"] = _text(params.get("asset_dir") or params.get("assetDir"))
        return [("vrc_ensure_animator_state", request)]

    primitive_tools = {"vrcforge_write_avatar_descriptor": "vrc_write_avatar_descriptor", "vrcforge_write_animation_curve": "vrc_write_animation_curve", "vrcforge_manage_expression_parameters": "vrc_manage_expression_parameters", "vrcforge_manage_expression_menu": "vrc_manage_expression_menu", "vrcforge_manage_fx_animator": "vrc_manage_fx_animator"}
    if target_name in primitive_tools:
        return [(primitive_tools[target_name], _avatar_primitive(params))]
    raise ValueError(f"Unsupported scene execution-plan target: {target_name}")
