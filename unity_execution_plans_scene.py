"""Pure approved-execution plans for simple Unity scene write handlers.

Keep these argument projections byte-for-byte compatible with the legacy
dashboard handlers.  The gateway freezes the returned calls at approval time.
"""

from __future__ import annotations

from typing import Any
from animation_curve_input import expand_animation_curve_sets


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

ASSET_SAVE_CORE_TOOL = "vrc_save_asset"


def build_save_asset_core_arguments(params: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
    """Build the exact one-asset Core request; project scope stays at the App boundary."""
    asset_path = _text(params.get("assetPath") or params.get("asset_path"))
    if not asset_path:
        raise ValueError("assetPath is required and must name one existing project asset.")
    return {"assetPath": asset_path, "preview": bool(preview)}


def build_save_asset_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    """Build the approved static Core call without an extra preview round trip."""
    request: dict[str, Any] = {
        "toolName": ASSET_SAVE_CORE_TOOL,
        "arguments": build_save_asset_core_arguments(params, preview=False),
    }
    for key in ("projectPath", "project_path", "projectRoot", "executionTarget"):
        if key in params:
            request[key] = params[key]
    return request


def _text(value: Any) -> str:
    return str(value or "").strip()


def sibling_index_arguments(params: dict[str, Any]) -> dict[str, int]:
    """Keep the optional exact index identical in direct and approved requests."""
    value = params.get("sibling_index", params.get("siblingIndex"))
    if value is None:
        return {}
    if type(value) is not int or value < 0:
        raise ValueError("siblingIndex must be a non-negative integer.")
    return {"siblingIndex": value}


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


def build_unpack_prefab_request(params: dict[str, Any], *, preview: bool | None = None) -> dict[str, Any]:
    """Use the same object and prefab guards for approval and Core execution."""
    request = {
        "gameObjectPath": _gameobject_target(params),
        "mode": _text(params.get("mode") or "completely"),
        "preview": bool(params.get("preview", False)) if preview is None else preview,
    }
    for key in ("expectedGlobalObjectId", "expectedPrefabGuid", "expectedAssetDependencyHash",
                "expectedScenePath", "approvedObjectReceiptNonce"):
        if key in params:
            request[key] = params[key]
    return request


def build_instantiate_prefab_request(params: dict[str, Any], *, preview: bool | None = None) -> dict[str, Any]:
    """Freeze the exact asset, destination and continuation facts sent to Core."""
    request = {
        "assetPath": _text(params.get("asset_path") or params.get("assetPath")),
        "guid": _text(params.get("guid")),
        "parentPath": _text(params.get("parent_path") or params.get("parentPath")),
        "name": _text(params.get("name")),
        "worldPositionStays": bool(params.get("world_position_stays", params.get("worldPositionStays", True))),
        "preview": bool(params.get("preview", False)) if preview is None else preview,
    }
    for key in ("expectedPrefabGuid", "expectedAssetDependencyHash", "expectedScenePath",
                "expectedParentGlobalObjectId", "expectedResultPath", "approvedObjectReceiptNonce",
                "approvedContinuationTools"):
        if key in params:
            request[key] = params[key]
    return request


_AVATAR_KEYS = (
    "animatorRootPath", "assetOnly",
    "expectedSpeed", "expectedStateDigest",
    "expectedPlanAssetPaths",
    "edits",
    "curves", "clips",
    "sourceStateName",
    "orderedInterruption",
    "action", "avatarPath", "clipPath", "bindingPath", "objectPath", "componentType", "propertyName", "sourceBindingPath", "sourceComponentType", "sourcePropertyName", "deleteSource", "overwriteExisting", "constantFloat", "keys", "parameterName", "newName", "orderNames", "valueType", "defaultValue", "saved", "networkSynced", "menuPath", "controlName", "controlIndex", "controlType", "controlFloat", "value", "iconAssetPath", "subMenuAssetPath", "createSubMenu", "subParameters", "assetDir", "controllerPath", "fxControllerPath", "layerName", "stateName", "destinationStateName", "transitionIndex", "hasExitTime", "exitTime", "duration", "canTransitionToSelf", "interruptionSource", "conditions", "parameterType", "conditionMode", "threshold", "writeDefaults", "motionClipPath", "speed", "timeParameterActive", "timeParameter", "viewPosition", "lipSync", "visemeSkinnedMeshPath", "visemeBlendShapes", "expressionParametersPath", "expressionsMenuPath", "baseAnimationLayers", "specialAnimationLayers", "eyeLookSettingsSourceAvatarPath", "eyeLookEnabled",
)
_AVATAR_ALIASES = {
    "sourceStateName": ("source_state_name",),
    "avatarPath": ("avatar_path",), "clipPath": ("clip_path",), "bindingPath": ("binding_path",), "componentType": ("component_type",), "propertyName": ("property_name",), "sourceBindingPath": ("source_binding_path",), "sourceComponentType": ("source_component_type",), "sourcePropertyName": ("source_property_name",), "deleteSource": ("delete_source",), "overwriteExisting": ("overwrite_existing",), "constantFloat": ("constant_float",), "parameterName": ("parameter_name",), "newName": ("new_name",), "orderNames": ("order_names",), "valueType": ("value_type",), "defaultValue": ("default_value",), "networkSynced": ("network_synced",), "menuPath": ("menu_path",), "controlName": ("control_name",), "controlIndex": ("control_index",), "controlType": ("control_type",), "controlFloat": ("control_float", "control_value"), "iconAssetPath": ("icon_asset_path",), "subMenuAssetPath": ("sub_menu_asset_path",), "createSubMenu": ("create_sub_menu",), "subParameters": ("sub_parameters",), "assetDir": ("asset_dir",), "controllerPath": ("controller_path",), "fxControllerPath": ("fx_controller_path",), "layerName": ("layer_name",), "stateName": ("state_name",), "destinationStateName": ("destination_state_name",), "transitionIndex": ("transition_index",), "hasExitTime": ("has_exit_time",), "exitTime": ("exit_time",), "canTransitionToSelf": ("can_transition_to_self",), "parameterType": ("parameter_type",), "conditionMode": ("condition_mode",), "writeDefaults": ("write_defaults",), "motionClipPath": ("motion_clip_path",), "timeParameterActive": ("time_parameter_active",), "timeParameter": ("time_parameter",), "viewPosition": ("view_position",), "visemeSkinnedMeshPath": ("viseme_skinned_mesh_path",), "visemeBlendShapes": ("viseme_blend_shapes",), "expressionParametersPath": ("expression_parameters_path",), "expressionsMenuPath": ("expressions_menu_path",), "baseAnimationLayers": ("base_animation_layers",), "specialAnimationLayers": ("special_animation_layers",), "eyeLookSettingsSourceAvatarPath": ("eye_look_settings_source_avatar_path",), "eyeLookEnabled": ("eye_look_enabled",),
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


def build_ensure_expression_parameter_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "valueType": str(params.get("value_type") or params.get("valueType") or "Int").strip() or "Int",
        "defaultValue": float(params.get("default_value", params.get("defaultValue", 0)) or 0),
        "saved": _bool(params.get("saved"), True),
        "networkSynced": _bool(params.get("network_synced", params.get("networkSynced")), True),
        "preview": preview,
    }
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    if "expectedCheckpointAssetPaths" in params:
        request["expectedCheckpointAssetPaths"] = params["expectedCheckpointAssetPaths"]
    return request


def build_ensure_expression_menu_control_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "menuPath": str(params.get("menu_path") or params.get("menuPath") or "").strip(),
        "controlName": str(params.get("control_name") or params.get("controlName") or "").strip(),
        "controlType": str(params.get("control_type") or params.get("controlType") or "Toggle").strip() or "Toggle",
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "controlValue": float(params.get("control_value", params.get("controlValue", 0)) or 0),
        "preview": preview,
    }
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    if "expectedCheckpointAssetPaths" in params:
        request["expectedCheckpointAssetPaths"] = params["expectedCheckpointAssetPaths"]
    return request


def build_ensure_animator_state_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "layerName": str(params.get("layer_name") or params.get("layerName") or "").strip(),
        "stateName": str(params.get("state_name") or params.get("stateName") or "").strip(),
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "parameterType": str(params.get("parameter_type") or params.get("parameterType") or "Int").strip() or "Int",
        "conditionMode": str(params.get("condition_mode") or params.get("conditionMode") or "Equals").strip() or "Equals",
        "threshold": float(params.get("threshold", 0) or 0),
        "writeDefaults": _bool(params.get("write_defaults", params.get("writeDefaults")), True),
        "preview": preview,
    }
    if params.get("time_parameter_active", params.get("timeParameterActive")) is not None:
        request["timeParameterActive"] = _bool(
            params.get("time_parameter_active", params.get("timeParameterActive")), False
        )
    if params.get("time_parameter", params.get("timeParameter")) is not None:
        request["timeParameter"] = str(params.get("time_parameter", params.get("timeParameter")) or "").strip()
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    if "expectedCheckpointAssetPaths" in params:
        request["expectedCheckpointAssetPaths"] = params["expectedCheckpointAssetPaths"]
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
            direct = _text(params.get("property_path") or params.get("propertyPath"))
            serialized = _text(params.get("serialized_property_path") or params.get("serializedPropertyPath"))
            if bool(direct) == bool(serialized):
                raise ValueError("Provide exactly one of propertyPath and serializedPropertyPath.")
            request["propertyPath" if direct else "serializedPropertyPath"] = direct or serialized
            request["value"] = params.get("value")
            return [("vrc_set_property", request)]
        return [("vrc_remove_component", request)]

    gameobject_tools = {
        "vrcforge_rename_gameobject": ("vrc_rename_gameobject", lambda: {"gameObjectPath": _gameobject_target(params), "newName": _text(params.get("new_name") or params.get("newName")), "preview": False}),
        "vrcforge_reparent_gameobject": ("vrc_reparent_gameobject", lambda: {"gameObjectPath": _gameobject_target(params), "newParentPath": _text(params.get("new_parent_path") or params.get("newParentPath")), "worldPositionStays": bool(params.get("world_position_stays", params.get("worldPositionStays", True))), "preview": False, **sibling_index_arguments(params)}),
        "vrcforge_delete_gameobject": (
            "vrc_delete_gameobject",
            lambda: {
                "gameObjectPath": _gameobject_target(params),
                "globalObjectId": _text(params.get("global_object_id") or params.get("globalObjectId")),
                "preview": False,
            },
        ),
        "vrcforge_set_gameobject_active": ("vrc_set_gameobject_active", lambda: {"gameObjectPath": _gameobject_target(params), "active": bool(params.get("active", params.get("isActive"))), "preview": False}),
        "vrcforge_unpack_prefab": ("vrc_unpack_prefab", lambda: build_unpack_prefab_request(params, preview=False)),
    }
    if target_name in gameobject_tools:
        tool, factory = gameobject_tools[target_name]
        return [(tool, factory())]
    if target_name == "vrcforge_create_gameobject":
        return [("vrc_create_gameobject", {"name": _text(params.get("name")), "parentPath": _text(params.get("parent_path") or params.get("parentPath")), "preview": False})]
    if target_name == "vrcforge_instantiate_prefab":
        return [("vrc_instantiate_prefab", build_instantiate_prefab_request(params, preview=False))]
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
        save_assets = params.get("save_assets", params.get("saveAssets", True))
        return [("vrc_toggle_scene_object", {"objectPath": _text(params.get("object_path") or params.get("objectPath")), "active": bool(params.get("active")), "saveAssets": save_assets is not False})]
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
        return [("vrc_ensure_expression_parameter", build_ensure_expression_parameter_request(params, False))]
    if target_name == "vrcforge_ensure_expression_menu_control":
        return [("vrc_ensure_expression_menu_control", build_ensure_expression_menu_control_request(params, False))]
    if target_name == "vrcforge_ensure_animator_state":
        return [("vrc_ensure_animator_state", build_ensure_animator_state_request(params, False))]

    primitive_tools = {"vrcforge_write_avatar_descriptor": "vrc_write_avatar_descriptor", "vrcforge_write_animation_curve": "vrc_write_animation_curve", "vrcforge_manage_expression_parameters": "vrc_manage_expression_parameters", "vrcforge_manage_expression_menu": "vrc_manage_expression_menu", "vrcforge_manage_fx_animator": "vrc_manage_fx_animator"}
    if target_name in primitive_tools:
        if target_name == "vrcforge_write_animation_curve":
            params = expand_animation_curve_sets(params)
        return [(primitive_tools[target_name], _avatar_primitive(params))]
    raise ValueError(f"Unsupported scene execution-plan target: {target_name}")
