"""Approval-time Core call plans for deterministic Unity workflow handlers.

This module deliberately has no dashboard, filesystem, vault, or Unity dependency.
It may only freeze calls whose complete Core arguments are known from the approved
handler arguments.  Callers must keep runtime-discovery workflows out of this
module until their readback facts are part of the approved request.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from typing import Any

from wardrobe_outfit_workflow_service import build_add_wardrobe_outfit_request


WorkflowPlan = list[tuple[str, dict[str, Any]]]


WORKFLOW_EXECUTION_PLAN_TARGETS = frozenset(
    {
        "vrcforge_setup_outfit",
        "vrcforge_add_wardrobe_outfit",
        "vrcforge_manage_wardrobe",
        "vrcforge_add_outfit_part",
        "vrcforge_add_modular_avatar_component",
        "vrcforge_create_wardrobe",
        "vrcforge_restore_safe_backup",
    }
)


_RUNTIME_FACT_WORKFLOWS: dict[str, str] = {
    "vrcforge_add_outfit": "resolved prefab asset identity and wardrobe scan/readback",
    "vrcforge_import_outfit_package": "frozen outfit-import queue and verified source file identities",
    "vrcforge_import_chat_image": "resolved vault attachment identity and destination file identity",
    "vrcforge_import_chat_archive": "resolved vault archive guard result and frozen extraction/import queue",
    "vrcforge_install_vpm_package": "selected package-manager executable, package resolution, and package-state readback",
    "vrcforge_configure_optimizer_component": "component inspection/readback and the resolved optimizer property set",
}


def build_workflow_execution_plan(target_name: str, arguments: Mapping[str, Any]) -> WorkflowPlan:
    """Return the exact ordered Core calls for one approved workflow.

    Unknown targets and workflows whose calls depend on execution-time facts fail
    closed.  Returned dictionaries are newly allocated and contain no approval
    capability or caller-owned mutable object.
    """
    if not isinstance(target_name, str) or not target_name:
        raise ValueError("workflow target name is required")
    if not isinstance(arguments, Mapping):
        raise ValueError("workflow arguments must be an object")
    params = dict(arguments)
    if target_name in _RUNTIME_FACT_WORKFLOWS:
        raise ValueError(
            f"{target_name} needs runtime readback before an exact Core plan can be frozen: "
            f"{_RUNTIME_FACT_WORKFLOWS[target_name]}."
        )
    builders = {
        "vrcforge_setup_outfit": _setup_outfit,
        "vrcforge_add_wardrobe_outfit": _add_wardrobe_outfit,
        "vrcforge_manage_wardrobe": _manage_wardrobe,
        "vrcforge_add_outfit_part": _add_outfit_part,
        "vrcforge_add_modular_avatar_component": _add_modular_avatar_component,
        "vrcforge_create_wardrobe": _create_wardrobe,
        "vrcforge_restore_safe_backup": _restore_safe_backup,
    }
    builder = builders.get(target_name)
    if builder is None:
        raise ValueError(f"{target_name} has no deterministic Core workflow plan")
    return builder(params)


def _text(params: dict[str, Any], *names: str, default: str = "") -> str:
    for name in names:
        value = params.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _bool(params: dict[str, Any], *names: str, default: bool) -> bool:
    for name in names:
        if name in params:
            value = params[name]
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
    return default


def _path_list(params: dict[str, Any], *names: str) -> list[str]:
    result: list[str] = []
    for name in names:
        value = params.get(name)
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
    return result


def _setup_outfit(params: dict[str, Any]) -> WorkflowPlan:
    return [("vrc_setup_outfit", {
        "avatarPath": _text(params, "avatar_path", "avatarPath"),
        "outfitPath": _text(params, "outfit_path", "outfitPath"),
        "confirmSetup": True,
        "saveScene": bool(params.get("save_scene", params.get("saveScene", True))),
    })]


def _add_wardrobe_outfit(params: dict[str, Any]) -> WorkflowPlan:
    return [
        (
            "vrc_add_wardrobe_outfit",
            build_add_wardrobe_outfit_request(params, False),
        )
    ]


def _add_outfit_part(params: dict[str, Any]) -> WorkflowPlan:
    request: dict[str, Any] = {
        "avatarPath": _text(params, "avatar_path", "avatarPath"),
        "parameterName": _text(params, "parameter_name", "parameterName"),
        "partName": _text(params, "part_name", "partName", "display_name", "displayName"),
        "objectPaths": _path_list(params, "object_paths", "objectPaths", "on_object_paths", "onObjectPaths"),
        "preview": False,
    }
    for source in ("value", "outfit_value", "outfitValue"):
        if source in params:
            request["value"] = int(params[source])
            break
    _optional_text(request, params, "partParameterName", "part_parameter_name", "partParameterName", "bool_parameter_name", "boolParameterName")
    _optional_text(request, params, "subMenuName", "sub_menu_name", "subMenuName")
    _optional_text(request, params, "clipOutputDir", "clip_output_dir", "clipOutputDir")
    _optional_python_bool(request, params, "addMenuToggle", "add_menu_toggle", "addMenuToggle")
    _optional_python_bool(request, params, "setObjectsDefaultOff", "set_objects_default_off", "setObjectsDefaultOff")
    _optional_python_bool(request, params, "defaultOn", "default_on", "defaultOn")
    _optional_python_bool(request, params, "writeDefaults", "write_defaults", "writeDefaults")
    return [("vrc_add_outfit_part", request)]


def _add_modular_avatar_component(params: dict[str, Any]) -> WorkflowPlan:
    if any(key in params for key in ("primitiveLive", "primitive_live", "expectedSceneIdentity", "expected_scene_identity")):
        raise ValueError("vrcforge_add_modular_avatar_component needs frozen live-instance identity/readback.")
    request: dict[str, Any] = {
        "gameObjectPath": _text(params, "game_object_path", "gameObjectPath", "target_path", "targetPath"),
        "componentType": _text(params, "component_type", "componentType"),
        "preview": False,
        "saveScene": _bool(params, "save_scene", "saveScene", default=False),
    }
    _optional_text(request, params, "avatarPath", "avatar_path", "avatarPath")
    _optional_python_bool(request, params, "allowDuplicate", "allow_duplicate", "allowDuplicate")
    for key in ("references", "fields"):
        if isinstance(params.get(key), dict) and params[key]:
            request[key] = deepcopy(params[key])
    return [("vrc_add_modular_avatar_component", request)]


def _manage_wardrobe(params: dict[str, Any]) -> WorkflowPlan:
    request: dict[str, Any] = {
        "action": _text(params, "action"),
        "avatarPath": _text(params, "avatar_path", "avatarPath"),
        "parameterName": _text(params, "parameter_name", "parameterName", "wardrobe_parameter", "wardrobeParameter"),
        "preview": False,
    }
    for destination, *sources in (("outfitName", "outfit_name", "outfitName"), ("targetName", "target_name", "targetName"), ("stateName", "state_name", "stateName"), ("controlName", "control_name", "controlName"), ("newName", "new_name", "newName"), ("newOutfitName", "new_outfit_name", "newOutfitName"), ("assetDir", "asset_dir", "assetDir"), ("clipOutputDir", "clip_output_dir", "clipOutputDir")):
        _optional_text(request, params, destination, *sources)
    for source, target in (("target_value", "targetValue"), ("targetValue", "targetValue"), ("outfit_value", "outfitValue"), ("outfitValue", "outfitValue"), ("value", "value")):
        if params.get(source) is not None:
            request[target] = int(params[source])
            break
    order_values = _int_list(params, "order_values", "orderValues")
    if order_values:
        request["orderValues"] = order_values
    target_values = _int_list(params, "target_values", "targetValues", "values")
    if target_values:
        request["targetValues"] = target_values
    _optional_bool(request, params, "deleteObjects", "delete_objects", "deleteObjects", default=False)
    _optional_bool(request, params, "deactivateObjects", "deactivate_objects", "deactivateObjects", default=True)
    _optional_bool(request, params, "deleteGeneratedAssets", "delete_generated_assets", "deleteGeneratedAssets", default=False)
    _optional_bool(request, params, "confirmDeleteWardrobe", "confirm_delete_wardrobe", "confirmDeleteWardrobe", default=False)
    return [("vrc_manage_wardrobe", request)]


def _create_wardrobe(params: dict[str, Any]) -> WorkflowPlan:
    avatar = _text(params, "avatar_path", "avatarPath")
    parameter = _text(params, "parameter_name", "parameterName", "wardrobe_parameter", "wardrobeParameter", default="Clothes")
    asset_dir = _text(params, "asset_dir", "assetDir", "clip_output_dir", "clipOutputDir", default="Assets/VRCForge/Generated/Wardrobe")
    menu_name = _text(params, "menu_name", "menuName", "sub_menu_name", "subMenuName", default="Wardrobe")
    control = _text(params, "default_control_name", "defaultControlName", default="Default")
    layer = _text(params, "layer_name", "layerName", default=parameter)
    common = {"avatarPath": avatar, "assetDir": asset_dir}
    return [
        ("vrc_ensure_expression_parameter", {**common, "parameterName": parameter, "valueType": "Int", "defaultValue": 0.0, "saved": _bool(params, "saved", default=True), "networkSynced": _bool(params, "network_synced", "networkSynced", default=True), "preview": False}),
        ("vrc_ensure_animator_state", {**common, "layerName": layer, "stateName": control, "parameterName": parameter, "parameterType": "Int", "conditionMode": "Equals", "threshold": 0.0, "writeDefaults": _bool(params, "write_defaults", "writeDefaults", default=True), "preview": False}),
        ("vrc_ensure_expression_menu_control", {**common, "menuPath": menu_name, "controlName": control, "controlType": "Toggle", "parameterName": parameter, "controlValue": 0.0, "preview": False}),
    ]


def _restore_safe_backup(params: dict[str, Any]) -> WorkflowPlan:
    request: dict[str, Any] = {
        "backupPath": _text(params, "backup_path", "backupPath"),
        "backupId": _text(params, "backup_id", "backupId"),
        "assetPaths": _path_list(params, "asset_paths", "assetPaths"),
        "confirmRestore": True,
        "allowProjectMismatch": bool(
            params.get("allow_project_mismatch")
            or params.get("allowProjectMismatch")
            or False
        ),
        "allowOverwriteChanged": bool(
            params.get("allow_overwrite_changed")
            or params.get("allowOverwriteChanged")
            or False
        ),
        "refreshAssets": True,
    }
    _optional_text(request, params, "backupRoot", "backup_root", "backupRoot")
    return [("vrc_restore_safe_backup", request)]


def _optional_text(target: dict[str, Any], params: dict[str, Any], destination: str, *sources: str) -> None:
    if any(source in params for source in sources):
        value = _text(params, *sources)
        if value:
            target[destination] = value


def _int_list(params: dict[str, Any], *names: str) -> list[int]:
    result: list[int] = []
    for name in names:
        raw = params.get(name)
        if raw is None:
            continue
        values = raw if isinstance(raw, (list, tuple)) else str(raw).replace(";", ",").replace(" ", ",").split(",")
        for item in values:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value not in result:
                result.append(value)
    return result


def _optional_bool(target: dict[str, Any], params: dict[str, Any], destination: str, *sources: str, default: bool = False) -> None:
    if any(source in params for source in sources):
        target[destination] = _bool(params, *sources, default=default)


def _optional_python_bool(
    target: dict[str, Any],
    params: dict[str, Any],
    destination: str,
    *sources: str,
) -> None:
    """Mirror legacy builders whose JSON values use Python truthiness."""
    if not any(source in params for source in sources):
        return
    for source in sources:
        if source in params:
            target[destination] = bool(params[source])
            return
