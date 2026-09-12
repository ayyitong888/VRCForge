from __future__ import annotations

import re
import json
from copy import deepcopy
from pathlib import PurePosixPath
import os
from typing import Any


TOOL_NAME = "vrc_set_renderer_material_slot"
GATEWAY_TOOL_NAME = "vrcforge_set_renderer_material_slot"
RESULT_SCHEMA = "vrcforge.renderer_material_slot.v1"
REQUEST_KEYS = (
    "assignments",
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
    if "assignments" in wrapper:
        _batch_assignments(wrapper)
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
    if "assignments" in arguments:
        _batch_assignments(arguments)
    preview = {key: deepcopy(arguments[key]) for key in REQUEST_KEYS if key in arguments}
    preview["preview"] = True
    return preview


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any], payload: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        raise RendererMaterialSlotError("Renderer material-slot arguments are required.")
    if "assignments" in nested:
        return _bind_batch(wrapper_arguments, payload)
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
    if "assignments" in arguments:
        return _validate_batch_apply(arguments, payload)
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


def _batch_assignments(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    if any(key in arguments for key in ("rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath")):
        raise RendererMaterialSlotError("assignments cannot be combined with single-slot fields.")
    rows = arguments.get("assignments")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise RendererMaterialSlotError("assignments requires 1..256 entries.")
    if len(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 512 * 1024:
        raise RendererMaterialSlotError("assignments exceeds 512 KiB.")
    seen = set()
    for row in rows:
        row = _object(row, "assignment")
        if set(row) != {"rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath"}:
            raise RendererMaterialSlotError("Assignment requires exact renderer/slot/material fields.")
        _scene_path(row["rendererPath"], "rendererPath")
        identity = _hex(row["rendererComponentId"], "rendererComponentId", _DIGEST)
        slot = _integer(row["slotIndex"], "slotIndex", 0, 1024)
        _asset_path(row["newMaterialAssetPath"], "newMaterialAssetPath", ".mat")
        if (identity, slot) in seen:
            raise RendererMaterialSlotError("Duplicate renderer/slot assignment.")
        seen.add((identity, slot))
    return rows


def _validate_batch_plan(rows: list[dict[str, Any]], value: Any) -> dict[str, Any]:
    plan = _object(value, "batch plan")
    if set(plan) != {"scene", "beforeRenderers", "afterRenderers", "assignments"}:
        raise RendererMaterialSlotError("Batch plan fields are invalid.")
    scene = _object(plan["scene"], "scene")
    _asset_path(scene.get("scenePath"), "scenePath", ".unity")
    _hex(scene.get("sceneGuid"), "sceneGuid", _GUID)
    for field in ("sceneFileDigest", "sceneMetaDigest", "sceneMetaIdentity"):
        _hex(scene.get(field), field, _DIGEST)
    if _integer(scene.get("sceneHandle"), "sceneHandle", -2147483648, 2147483647) == 0:
        raise RendererMaterialSlotError("sceneHandle must be nonzero.")
    before = plan["beforeRenderers"]
    if not isinstance(before, list) or not before or len(before) > len(rows):
        raise RendererMaterialSlotError("Batch renderer snapshots are invalid.")
    expected = deepcopy(before)
    by_id = {}
    for renderer in expected:
        renderer = _object(renderer, "renderer snapshot")
        identity = _hex(renderer.get("rendererComponentId"), "rendererComponentId", _DIGEST)
        if identity in by_id: raise RendererMaterialSlotError("Duplicate renderer snapshot.")
        _scene_path(renderer.get("rendererPath"), "rendererPath")
        _text(renderer.get("rendererComponentType"), "rendererComponentType", 512)
        _integer(renderer.get("rendererComponentIndex"), "rendererComponentIndex", 0, 1024)
        slots = renderer.get("slots")
        if not isinstance(slots, list) or not all(isinstance(slot, str) and slot for slot in slots):
            raise RendererMaterialSlotError("Renderer slots must be complete identity arrays.")
        by_id[identity] = renderer
    evidence = plan["assignments"]
    if not isinstance(evidence, list) or len(evidence) != len(rows):
        raise RendererMaterialSlotError("Batch assignment evidence count differs.")
    touched = set()
    for requested, actual in zip(rows, evidence):
        actual = _object(actual, "assignment evidence")
        if any(actual.get(key) != requested[key] for key in ("rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath")):
            raise RendererMaterialSlotError("Batch preview changed a requested assignment.")
        renderer = by_id.get(actual["rendererComponentId"])
        if renderer is None or renderer["rendererPath"] != actual["rendererPath"]:
            raise RendererMaterialSlotError("Batch renderer does not match assignment.")
        touched.add(actual["rendererComponentId"])
        slot = actual["slotIndex"]
        if slot >= len(renderer["slots"]): raise RendererMaterialSlotError("Batch slot is out of range.")
        old = None if actual.get("beforeMaterial") is None else _material(actual["beforeMaterial"], "beforeMaterial")
        old_identity = "null" if old is None else old["assetPath"] + "|" + old["assetGuid"]
        if renderer["slots"][slot] != old_identity: raise RendererMaterialSlotError("Before material differs from renderer slot.")
        new = _material(actual.get("newMaterial"), "newMaterial")
        if new["assetPath"] != actual["newMaterialAssetPath"]: raise RendererMaterialSlotError("New material differs from request.")
        new_identity = new["assetPath"] + "|" + new["assetGuid"]
        if new_identity == old_identity: raise RendererMaterialSlotError("Batch includes an unchanged slot.")
        renderer["slots"][slot] = new_identity
    if touched != set(by_id) or expected != plan["afterRenderers"]:
        raise RendererMaterialSlotError("Batch final arrays changed untouched slots or renderer identity.")
    return deepcopy(plan)


def _bind_batch(wrapper: dict[str, Any], payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = wrapper["arguments"]
    rows = _batch_assignments(nested)
    result = _object(payload, "batch preview")
    if result.get("schema") != RESULT_SCHEMA or result.get("batch") is not True or result.get("preview") is not True or result.get("verified") is not True:
        raise RendererMaterialSlotError("Batch preview schema is invalid.")
    if any(result.get(key) is not False for key in ("mutationStarted", "applied", "committed", "sceneSaved", "persistedReadback")):
        raise RendererMaterialSlotError("Batch preview reported a mutation.")
    plan = _validate_batch_plan(rows, result.get("plan"))
    target = _object(wrapper.get("executionTarget"), "batch ExecutionTarget")
    if target.get("scope") not in ("scene", "avatar"):
        raise RendererMaterialSlotError("Batch requires a scene or avatar ExecutionTarget envelope.")
    scene = _object(target.get("scene"), "ExecutionTarget scene")
    expected_path = os.path.normcase(os.path.abspath(os.path.join(str(wrapper.get("projectPath") or ""), plan["scene"]["scenePath"])))
    actual_path = os.path.normcase(os.path.abspath(str(scene.get("absolutePath") or scene.get("path") or "")))
    if actual_path != expected_path or scene.get("guid") != plan["scene"]["sceneGuid"] or scene.get("digest") != plan["scene"]["sceneFileDigest"]:
        raise RendererMaterialSlotError("Batch scene differs from its ExecutionTarget namespace.")
    if target["scope"] == "avatar":
        root = str(_object(target.get("avatar"), "ExecutionTarget avatar").get("exactHierarchyPath") or "")
        if not root or any(row["rendererPath"] != root and not row["rendererPath"].startswith(root + "/") for row in rows):
            raise RendererMaterialSlotError("Batch renderer lies outside the bound avatar namespace.")
    for envelope in (wrapper, nested):
        if "expectedBatchPlan" in envelope and envelope["expectedBatchPlan"] != plan:
            raise RendererMaterialSlotError("Explicit expectedBatchPlan differs from fresh preview.")
    canonical = deepcopy(wrapper)
    canonical["arguments"] = {"assignments": deepcopy(rows), "preview": False, "expectedBatchPlan": plan,
        "expectedProjectPath": nested.get("expectedProjectPath") or wrapper.get("projectPath")}
    if len(json.dumps(canonical["arguments"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 512 * 1024:
        raise RendererMaterialSlotError("Sealed renderer batch exceeds 512 KiB.")
    return canonical, {"schema": "vrcforge.renderer_material_slot_approval.v1", "toolName": TOOL_NAME, "batch": True,
        "assignmentCount": len(rows), "rendererCount": len(plan["beforeRenderers"]), "plan": plan, "rollbackRequired": True, "freshReadbackRequired": True}


def _validate_batch_apply(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    rows = _batch_assignments(arguments)
    expected = _validate_batch_plan(rows, arguments.get("expectedBatchPlan"))
    result = _object(payload, "batch apply")
    if result.get("schema") != RESULT_SCHEMA or result.get("batch") is not True or result.get("preview") is not False:
        raise RendererMaterialSlotError("Batch apply schema is invalid.")
    if any(result.get(key) is not True for key in ("verified", "mutationStarted", "applied", "committed", "sceneSaved", "persistedReadback", "commitStateKnown")) or result.get("commitState") != "committed":
        raise RendererMaterialSlotError("Batch apply lacks verified persistence.")
    if result.get("plan") != expected or result.get("assignmentCount") != len(rows) or result.get("rendererCount") != len(expected["beforeRenderers"]):
        raise RendererMaterialSlotError("Batch apply differs from sealed plan.")
    readback = _object(result.get("readback"), "batch readback")
    scene = deepcopy(_object(readback.get("scene"), "readback scene"))
    digest = _hex(scene.get("sceneFileDigest"), "sceneFileDigest", _DIGEST)
    if digest == expected["scene"]["sceneFileDigest"]: raise RendererMaterialSlotError("Batch scene digest did not change.")
    scene["sceneFileDigest"] = expected["scene"]["sceneFileDigest"]
    if scene != expected["scene"] or readback.get("renderers") != expected["afterRenderers"]:
        raise RendererMaterialSlotError("Batch independent readback differs from expected arrays or scene identity.")
    return result
