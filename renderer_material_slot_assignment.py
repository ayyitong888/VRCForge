from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


TOOL_NAME = "vrc_set_renderer_material_slot"
GATEWAY_TOOL_NAME = "vrcforge_set_renderer_material_slot"
RESULT_SCHEMA = "vrcforge.renderer_material_slot.v1"
REQUEST_KEYS = (
    "rendererPath",
    "rendererComponentId",
    "slotIndex",
    "newMaterialAssetPath",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GUID = re.compile(r"^[0-9a-f]{32}$")


class RendererMaterialSlotError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in REQUEST_KEYS if key in wrapper}
    for key in REQUEST_KEYS:
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise RendererMaterialSlotError("Renderer material-slot arguments are required.")
    preview = {key: deepcopy(arguments[key]) for key in REQUEST_KEYS if key in arguments}
    preview["preview"] = True
    return preview


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any], payload: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        raise RendererMaterialSlotError("Renderer material-slot arguments are required.")
    result = _object(payload, "preview result")
    if result.get("schema") != RESULT_SCHEMA or result.get("preview") is not True:
        raise RendererMaterialSlotError("Renderer material-slot preview schema is invalid.")
    for key in ("mutationStarted", "applied", "committed", "sceneSaved", "persistedReadback"):
        if result.get(key) is not False:
            raise RendererMaterialSlotError(f"Preview field {key} must be false.")

    requested_path = _scene_path(nested.get("rendererPath"), "rendererPath")
    actual_path = _scene_path(result.get("rendererPath"), "rendererPath")
    if actual_path != requested_path and not actual_path.endswith("/" + requested_path):
        raise RendererMaterialSlotError("Preview changed the requested renderer path.")
    requested_slot = _integer(nested.get("slotIndex"), "slotIndex", 0, 1024)
    if _integer(result.get("slotIndex"), "slotIndex", 0, 1024) != requested_slot:
        raise RendererMaterialSlotError("Preview changed the requested material slot.")

    component_id = _hex(result.get("rendererComponentId"), "rendererComponentId", _DIGEST)
    caller_component_id = str(nested.get("rendererComponentId") or "").strip().lower()
    if caller_component_id and _hex(caller_component_id, "rendererComponentId", _DIGEST) != component_id:
        raise RendererMaterialSlotError("Preview changed the requested renderer identity.")
    component_type = _text(result.get("rendererComponentType"), "rendererComponentType", 512)
    component_index = _integer(result.get("rendererComponentIndex"), "rendererComponentIndex", 0, 1024)

    scene_path = _asset_path(result.get("scenePath"), "scenePath", ".unity")
    scene_guid = _hex(result.get("sceneGuid"), "sceneGuid", _GUID)
    scene_handle = _integer(result.get("sceneHandle"), "sceneHandle", -2_147_483_648, 2_147_483_647)
    if scene_handle == 0:
        raise RendererMaterialSlotError("sceneHandle must be nonzero.")
    scene_digest = _hex(result.get("sceneFileDigest"), "sceneFileDigest", _DIGEST)

    before = _material(result.get("beforeMaterial"), "beforeMaterial")
    new_material = _material(result.get("newMaterial"), "newMaterial")
    requested_new_path = _asset_path(nested.get("newMaterialAssetPath"), "newMaterialAssetPath", ".mat")
    new_path = _asset_path(result.get("newMaterialAssetPath"), "newMaterialAssetPath", ".mat")
    new_guid = _hex(result.get("newMaterialAssetGuid"), "newMaterialAssetGuid", _GUID)
    new_digest = _hex(result.get("newMaterialFileDigest"), "newMaterialFileDigest", _DIGEST)
    if new_path != requested_new_path or new_material["assetPath"] != new_path or new_material["assetGuid"] != new_guid:
        raise RendererMaterialSlotError("Preview changed the requested new material.")
    if before["assetPath"] == new_path and before["assetGuid"] == new_guid:
        raise RendererMaterialSlotError("The requested slot already uses the new material.")

    canonical_nested = deepcopy(nested)
    canonical_nested.update(
        {
            "rendererPath": actual_path,
            "rendererComponentId": component_id,
            "slotIndex": requested_slot,
            "newMaterialAssetPath": new_path,
            "preview": False,
            "expectedScenePath": scene_path,
            "expectedSceneGuid": scene_guid,
            "expectedSceneHandle": scene_handle,
            "expectedSceneFileDigest": scene_digest,
            "expectedRendererComponentType": component_type,
            "expectedRendererComponentIndex": component_index,
            "expectedBeforeMaterialAssetPath": before["assetPath"],
            "expectedBeforeMaterialGuid": before["assetGuid"],
            "expectedBeforeMaterialFileDigest": before["fileDigest"],
            "expectedBeforeMaterialShader": before["shader"],
            "expectedBeforeMaterialRenderQueue": before["renderQueue"],
            "expectedNewMaterialGuid": new_guid,
            "expectedNewMaterialFileDigest": new_digest,
        }
    )
    canonical_wrapper = deepcopy(wrapper_arguments)
    canonical_wrapper.pop("params", None)
    canonical_wrapper.pop("tool_name", None)
    canonical_wrapper["toolName"] = TOOL_NAME
    canonical_wrapper["arguments"] = canonical_nested
    approval = {
        "schema": "vrcforge.renderer_material_slot_approval.v1",
        "toolName": TOOL_NAME,
        "target": {
            "scenePath": scene_path,
            "sceneGuid": scene_guid,
            "sceneHandle": scene_handle,
            "sceneFileDigest": scene_digest,
            "rendererPath": actual_path,
            "rendererComponentId": component_id,
            "rendererComponentType": component_type,
            "rendererComponentIndex": component_index,
            "slotIndex": requested_slot,
        },
        "change": {"beforeMaterial": before, "newMaterial": {**new_material, "fileDigest": new_digest}},
        "rollbackRequired": True,
        "freshReadbackRequired": True,
    }
    return canonical_wrapper, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    result = _object(payload, "apply result")
    if result.get("schema") != RESULT_SCHEMA or result.get("preview") is not False:
        raise RendererMaterialSlotError("Renderer material-slot apply schema is invalid.")
    for key in ("mutationStarted", "applied", "committed", "sceneSaved", "persistedReadback"):
        if result.get(key) is not True:
            raise RendererMaterialSlotError(f"Apply field {key} must be true.")
    if _hex(result.get("rendererComponentId"), "rendererComponentId", _DIGEST) != _hex(arguments.get("rendererComponentId"), "rendererComponentId", _DIGEST):
        raise RendererMaterialSlotError("Apply renderer identity does not match approval.")
    if _integer(result.get("slotIndex"), "slotIndex", 0, 1024) != _integer(arguments.get("slotIndex"), "slotIndex", 0, 1024):
        raise RendererMaterialSlotError("Apply slot does not match approval.")
    if _asset_path(result.get("newMaterialAssetPath"), "newMaterialAssetPath", ".mat") != _asset_path(arguments.get("newMaterialAssetPath"), "newMaterialAssetPath", ".mat"):
        raise RendererMaterialSlotError("Apply material does not match approval.")
    if _hex(result.get("sceneFileDigest"), "sceneFileDigest", _DIGEST) == _hex(arguments.get("expectedSceneFileDigest"), "expectedSceneFileDigest", _DIGEST):
        raise RendererMaterialSlotError("Apply did not produce a fresh saved-scene digest.")
    return result


def _material(value: Any, label: str) -> dict[str, Any]:
    item = _object(value, label)
    path = _asset_path(item.get("assetPath"), f"{label}.assetPath", ".mat")
    return {
        "name": _text(item.get("name"), f"{label}.name", 512),
        "shader": _text(item.get("shader"), f"{label}.shader", 512),
        "renderQueue": _integer(item.get("renderQueue"), f"{label}.renderQueue", -1, 5000),
        "assetPath": path,
        "assetGuid": _hex(item.get("assetGuid"), f"{label}.assetGuid", _GUID),
        "fileDigest": _hex(item.get("fileDigest"), f"{label}.fileDigest", _DIGEST),
    }


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RendererMaterialSlotError(f"{label} must be an object.")
    return value


def _text(value: Any, label: str, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        raise RendererMaterialSlotError(f"{label} is invalid.")
    return text


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RendererMaterialSlotError(f"{label} is invalid.")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    if not pattern.fullmatch(text):
        raise RendererMaterialSlotError(f"{label} is invalid.")
    return text


def _scene_path(value: Any, label: str) -> str:
    text = _text(value, label, 2048).replace("\\", "/").strip("/")
    if any(part in {"", ".", ".."} for part in text.split("/")):
        raise RendererMaterialSlotError(f"{label} is invalid.")
    return text


def _asset_path(value: Any, label: str, suffix: str) -> str:
    text = _text(value, label, 2048).replace("\\", "/")
    parts = text.split("/")
    if text.startswith("/") or text.endswith("/") or any(part in {"", ".", ".."} for part in parts) or parts[0] != "Assets":
        raise RendererMaterialSlotError(f"{label} is outside Assets.")
    path = PurePosixPath(*parts).as_posix()
    if not path.lower().endswith(suffix):
        raise RendererMaterialSlotError(f"{label} has an unsupported file type.")
    return path
