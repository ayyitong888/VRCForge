from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


ASSIGNMENT_SCHEMA = "vrcforge.material_shader_assignment.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.material_shader_assignment_approval.v1"
IMPACT_DIGEST_SCHEMA = "vrcforge.material_shader_impact.v2"
TOOL_NAME = "vrc_set_material_shader"
MAX_IMPACT_ITEMS = 128
MAX_DEPENDENCY_CANDIDATES = 4096
REQUEST_ARGUMENT_KEYS = (
    "rendererPath",
    "rendererComponentId",
    "materialAssetPath",
    "slotIndex",
    "shaderName",
    "targetShader",
    "shaderAssetPath",
)

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class MaterialShaderAssignmentError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in REQUEST_ARGUMENT_KEYS if key in wrapper}
    for key in REQUEST_ARGUMENT_KEYS:
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper.pop("tool_name", None)
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    preview_arguments = deepcopy(arguments)
    for key in (
        "expectedBeforeShader",
        "expectedBeforeShaderAssetPath",
        "expectedBeforeShaderAssetGuid",
        "expectedMaterialAssetPath",
        "expectedMaterialAssetGuid",
        "expectedMaterialFileDigest",
        "expectedSharedImpactDigest",
        "expectedRendererScenePath",
        "expectedRendererSceneGuid",
        "expectedRendererSceneHandle",
        "expectedRendererComponentId",
        "expectedRendererComponentType",
        "expectedRendererComponentIndex",
        "expectedShaderAssetPath",
        "expectedShaderAssetGuid",
        "expectedProjectPath",
    ):
        preview_arguments.pop(key, None)
    preview_arguments["preview"] = True
    preview_arguments["saveAssets"] = True
    return preview_arguments


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    if not isinstance(nested, dict):
        raise MaterialShaderAssignmentError("Material shader arguments are required.")

    requested_shader = _bounded_text(
        nested.get("shaderName") or nested.get("targetShader"),
        label="shaderName",
        max_length=512,
    )
    result = _require_dict(payload, "preview result")
    if result.get("schema") != ASSIGNMENT_SCHEMA:
        raise MaterialShaderAssignmentError("Material shader preview schema is invalid.")
    for key, expected in (("ok", True), ("preview", True), ("verified", True)):
        if result.get(key) is not expected:
            raise MaterialShaderAssignmentError(f"Material shader preview {key} is invalid.")

    before_shader = _bounded_text(result.get("beforeShader"), label="beforeShader", max_length=512)
    before_shader_asset_path = _optional_asset_path(
        result.get("beforeShaderAssetPath"),
        label="beforeShaderAssetPath",
        roots=("Assets", "Packages"),
    )
    before_shader_asset_guid = str(result.get("beforeShaderAssetGuid") or "").strip().lower()
    if before_shader_asset_path:
        before_shader_asset_guid = _lower_hex(
            before_shader_asset_guid,
            label="beforeShaderAssetGuid",
            pattern=_GUID_PATTERN,
        )
    elif before_shader_asset_guid:
        raise MaterialShaderAssignmentError("beforeShaderAssetGuid requires beforeShaderAssetPath.")
    actual_requested_shader = _bounded_text(
        result.get("requestedShader"),
        label="requestedShader",
        max_length=512,
    )
    if actual_requested_shader != requested_shader:
        raise MaterialShaderAssignmentError("Material shader preview does not match the requested shader.")

    material_path = _safe_asset_path(
        result.get("materialAssetPath"),
        label="materialAssetPath",
        roots=("Assets",),
        suffix=".mat",
    )
    material_guid = _lower_hex(result.get("materialAssetGuid"), label="materialAssetGuid", pattern=_GUID_PATTERN)
    material_digest = _lower_hex(
        result.get("materialFileDigestBefore"),
        label="materialFileDigestBefore",
        pattern=_DIGEST_PATTERN,
    )
    impact_digest = _lower_hex(
        result.get("sharedImpactDigest"),
        label="sharedImpactDigest",
        pattern=_DIGEST_PATTERN,
    )
    impact_display_digest = _lower_hex(
        result.get("sharedImpactDisplayDigest"),
        label="sharedImpactDisplayDigest",
        pattern=_DIGEST_PATTERN,
    )
    impact_tail_digest = _lower_hex(
        result.get("sharedImpactTailDigest"),
        label="sharedImpactTailDigest",
        pattern=_DIGEST_PATTERN,
    )
    if result.get("sharedImpactDigestSchema") != IMPACT_DIGEST_SCHEMA:
        raise MaterialShaderAssignmentError("Shared material impact digest schema is invalid.")
    shader_asset_path = _optional_asset_path(
        result.get("shaderAssetPath"),
        label="shaderAssetPath",
        roots=("Assets", "Packages"),
    )
    shader_asset_guid = str(result.get("shaderAssetGuid") or "").strip().lower()
    if shader_asset_path:
        shader_asset_guid = _lower_hex(
            shader_asset_guid,
            label="shaderAssetGuid",
            pattern=_GUID_PATTERN,
        )
    elif shader_asset_guid:
        raise MaterialShaderAssignmentError("shaderAssetGuid requires shaderAssetPath.")

    renderer_path = _optional_scene_path(result.get("rendererPath"), label="rendererPath")
    slot_index = _bounded_int(result.get("slotIndex"), label="slotIndex", minimum=-1, maximum=1024)
    renderer_scene_path = _optional_asset_path(
        result.get("rendererScenePath"),
        label="rendererScenePath",
        roots=("Assets",),
    )
    if renderer_scene_path and not renderer_scene_path.lower().endswith(".unity"):
        raise MaterialShaderAssignmentError("rendererScenePath has an unsupported file type.")
    renderer_scene_guid = str(result.get("rendererSceneGuid") or "").strip().lower()
    if renderer_scene_path:
        renderer_scene_guid = _lower_hex(
            renderer_scene_guid,
            label="rendererSceneGuid",
            pattern=_GUID_PATTERN,
        )
    elif renderer_scene_guid:
        raise MaterialShaderAssignmentError("rendererSceneGuid requires rendererScenePath.")
    renderer_scene_handle = _bounded_int(
        result.get("rendererSceneHandle"),
        label="rendererSceneHandle",
        minimum=-1,
        maximum=2_147_483_647,
    )
    renderer_component_id = str(result.get("rendererComponentId") or "").strip().lower()
    renderer_component_type = str(result.get("rendererComponentType") or "").strip()
    renderer_component_index = _bounded_int(
        result.get("rendererComponentIndex"),
        label="rendererComponentIndex",
        minimum=-1,
        maximum=1024,
    )
    if renderer_path and slot_index < 0:
        raise MaterialShaderAssignmentError("Renderer previews require a non-negative slotIndex.")
    if not renderer_path and slot_index != -1:
        raise MaterialShaderAssignmentError("Direct material previews must use slotIndex -1.")
    if renderer_path:
        renderer_component_id = _lower_hex(
            renderer_component_id,
            label="rendererComponentId",
            pattern=_DIGEST_PATTERN,
        )
        renderer_component_type = _bounded_text(
            renderer_component_type,
            label="rendererComponentType",
            max_length=512,
        )
        if renderer_component_index < 0 or renderer_scene_handle <= 0:
            raise MaterialShaderAssignmentError("Renderer component identity is incomplete.")
    elif (
        renderer_scene_path
        or renderer_scene_guid
        or renderer_scene_handle != -1
        or renderer_component_id
        or renderer_component_type
        or renderer_component_index != -1
    ):
        raise MaterialShaderAssignmentError("Direct material previews cannot include renderer identity.")

    caller_renderer_path = _optional_scene_path(nested.get("rendererPath"), label="rendererPath")
    caller_material_path = _optional_asset_path(
        nested.get("materialAssetPath"),
        label="materialAssetPath",
        roots=("Assets",),
    )
    if bool(caller_renderer_path) == bool(caller_material_path):
        raise MaterialShaderAssignmentError("Exactly one material selector is required.")
    caller_renderer_component_id = str(nested.get("rendererComponentId") or "").strip().lower()
    if caller_renderer_component_id:
        caller_renderer_component_id = _lower_hex(
            caller_renderer_component_id,
            label="rendererComponentId",
            pattern=_DIGEST_PATTERN,
        )
    if caller_material_path and caller_renderer_component_id:
        raise MaterialShaderAssignmentError("Direct material selectors cannot include rendererComponentId.")
    if caller_material_path and (renderer_path or caller_material_path != material_path):
        raise MaterialShaderAssignmentError("Material shader preview changed the requested material selector.")
    if caller_renderer_path:
        caller_slot_index = _bounded_int(nested.get("slotIndex", 0), label="slotIndex", minimum=0, maximum=1024)
        renderer_matches = renderer_path == caller_renderer_path or renderer_path.endswith("/" + caller_renderer_path)
        if not renderer_matches or slot_index != caller_slot_index:
            raise MaterialShaderAssignmentError("Material shader preview changed the requested renderer selector.")
        if caller_renderer_component_id and caller_renderer_component_id != renderer_component_id:
            raise MaterialShaderAssignmentError("Material shader preview changed the requested renderer component.")
    caller_shader_asset_path = _optional_asset_path(
        nested.get("shaderAssetPath"),
        label="shaderAssetPath",
        roots=("Assets", "Packages"),
    )
    if caller_shader_asset_path and caller_shader_asset_path != shader_asset_path:
        raise MaterialShaderAssignmentError("Material shader preview changed the requested shader asset.")

    shared_impact = _canonical_shared_impact(result.get("sharedImpact"))
    if compute_shared_impact_digest(shared_impact) != impact_display_digest:
        raise MaterialShaderAssignmentError("Shared material impact display digest is invalid.")
    if not shared_impact["listsTruncated"]:
        expected_tail_digest = compute_shared_impact_tail_digest(shared_impact, slots=[], assets=[])
        if expected_tail_digest != impact_tail_digest:
            raise MaterialShaderAssignmentError("Shared material impact tail digest is invalid.")
    expected_impact_digest = compute_shared_impact_commitment(
        shared_impact,
        display_digest=impact_display_digest,
        tail_digest=impact_tail_digest,
    )
    if expected_impact_digest != impact_digest:
        raise MaterialShaderAssignmentError("Shared material impact commitment is invalid.")
    would_change = _strict_bool(result.get("wouldChange"), label="wouldChange")
    if _strict_bool(result.get("changed"), label="changed"):
        raise MaterialShaderAssignmentError("Material shader preview reported a write.")
    if _strict_bool(result.get("saved"), label="saved"):
        raise MaterialShaderAssignmentError("Material shader preview reported a save.")
    material_digest_after = _lower_hex(
        result.get("materialFileDigestAfter"),
        label="materialFileDigestAfter",
        pattern=_DIGEST_PATTERN,
    )
    if material_digest_after != material_digest:
        raise MaterialShaderAssignmentError("Material shader preview changed the material file.")
    after_shader = _bounded_text(result.get("afterShader"), label="afterShader", max_length=512)
    if after_shader != actual_requested_shader:
        raise MaterialShaderAssignmentError("Material shader preview afterShader is invalid.")
    same_shader_identity = (
        before_shader == actual_requested_shader
        and before_shader_asset_path == shader_asset_path
        and before_shader_asset_guid == shader_asset_guid
    )
    if would_change == same_shader_identity:
        raise MaterialShaderAssignmentError("Material shader preview wouldChange is inconsistent.")

    canonical_nested = deepcopy(nested)
    canonical_nested.pop("targetShader", None)
    canonical_nested["shaderName"] = actual_requested_shader
    canonical_nested["preview"] = False
    canonical_nested["saveAssets"] = True
    canonical_nested["expectedBeforeShader"] = before_shader
    canonical_nested["expectedBeforeShaderAssetPath"] = before_shader_asset_path
    canonical_nested["expectedBeforeShaderAssetGuid"] = before_shader_asset_guid
    canonical_nested["expectedMaterialAssetPath"] = material_path
    canonical_nested["expectedMaterialAssetGuid"] = material_guid
    canonical_nested["expectedMaterialFileDigest"] = material_digest
    canonical_nested["expectedSharedImpactDigest"] = impact_digest
    canonical_nested["expectedRendererScenePath"] = renderer_scene_path
    canonical_nested["expectedRendererSceneGuid"] = renderer_scene_guid
    canonical_nested["expectedRendererSceneHandle"] = renderer_scene_handle
    canonical_nested["expectedRendererComponentId"] = renderer_component_id
    canonical_nested["expectedRendererComponentType"] = renderer_component_type
    canonical_nested["expectedRendererComponentIndex"] = renderer_component_index
    canonical_nested["expectedShaderAssetPath"] = shader_asset_path
    canonical_nested["expectedShaderAssetGuid"] = shader_asset_guid
    if shader_asset_path:
        canonical_nested["shaderAssetPath"] = shader_asset_path
    else:
        canonical_nested.pop("shaderAssetPath", None)
    if renderer_path:
        canonical_nested["rendererPath"] = renderer_path
        canonical_nested["rendererComponentId"] = renderer_component_id
        canonical_nested["slotIndex"] = slot_index
        canonical_nested.pop("materialAssetPath", None)
    else:
        canonical_nested["materialAssetPath"] = material_path
        canonical_nested.pop("rendererPath", None)
        canonical_nested.pop("rendererComponentId", None)
        canonical_nested.pop("slotIndex", None)

    canonical_wrapper = deepcopy(wrapper_arguments)
    canonical_wrapper.pop("params", None)
    canonical_wrapper["toolName"] = TOOL_NAME
    canonical_wrapper.pop("tool_name", None)
    canonical_wrapper["arguments"] = canonical_nested

    approval_preview = {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "toolName": TOOL_NAME,
        "target": {
            "rendererPath": renderer_path,
            "rendererScenePath": renderer_scene_path,
            "rendererSceneGuid": renderer_scene_guid,
            "rendererSceneHandle": renderer_scene_handle,
            "rendererComponentId": renderer_component_id,
            "rendererComponentType": renderer_component_type,
            "rendererComponentIndex": renderer_component_index,
            "slotIndex": slot_index,
            "materialAssetPath": material_path,
            "materialAssetGuid": material_guid,
            "materialFileDigest": material_digest,
        },
        "change": {
            "beforeShader": before_shader,
            "beforeShaderAssetPath": before_shader_asset_path,
            "beforeShaderAssetGuid": before_shader_asset_guid,
            "afterShader": actual_requested_shader,
            "shaderAssetPath": shader_asset_path,
            "shaderAssetGuid": shader_asset_guid,
            "wouldChange": would_change,
        },
        "sharedImpact": shared_impact,
        "sharedImpactDigestSchema": IMPACT_DIGEST_SCHEMA,
        "sharedImpactDigest": impact_digest,
        "sharedImpactDisplayDigest": impact_display_digest,
        "sharedImpactTailDigest": impact_tail_digest,
        "rollbackRequired": True,
    }
    return canonical_wrapper, approval_preview


def _canonical_shared_impact(value: Any) -> dict[str, Any]:
    impact = _require_dict(value, "sharedImpact")
    if impact.get("scope") != "loaded_scene_renderers_and_project_scene_prefab_dependencies":
        raise MaterialShaderAssignmentError("Shared material impact scope is invalid.")
    candidate_count = _bounded_int(
        impact.get("dependencyCandidateCount"),
        label="dependencyCandidateCount",
        minimum=0,
        maximum=MAX_DEPENDENCY_CANDIDATES,
    )
    renderer_count = _bounded_int(
        impact.get("loadedRendererSlotCount"),
        label="loadedRendererSlotCount",
        minimum=0,
        maximum=MAX_DEPENDENCY_CANDIDATES,
    )
    dependent_count = _bounded_int(
        impact.get("dependentAssetCount"),
        label="dependentAssetCount",
        minimum=0,
        maximum=MAX_DEPENDENCY_CANDIDATES,
    )
    raw_slots = impact.get("loadedRendererSlots")
    raw_assets = impact.get("dependentAssets")
    if not isinstance(raw_slots, list) or len(raw_slots) > MAX_IMPACT_ITEMS:
        raise MaterialShaderAssignmentError("loadedRendererSlots is invalid.")
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_IMPACT_ITEMS:
        raise MaterialShaderAssignmentError("dependentAssets is invalid.")
    slots: list[dict[str, Any]] = []
    for item in raw_slots:
        slot = _require_dict(item, "loadedRendererSlots item")
        scene_path = _optional_asset_path(
            slot.get("scenePath"),
            label="scenePath",
            roots=("Assets",),
        )
        if scene_path and not scene_path.lower().endswith(".unity"):
            raise MaterialShaderAssignmentError("scenePath has an unsupported file type.")
        scene_guid = str(slot.get("sceneGuid") or "").strip().lower()
        if scene_path:
            scene_guid = _lower_hex(scene_guid, label="sceneGuid", pattern=_GUID_PATTERN)
        elif scene_guid:
            raise MaterialShaderAssignmentError("sceneGuid requires scenePath.")
        slots.append(
            {
                "scenePath": scene_path,
                "sceneGuid": scene_guid,
                "sceneHandle": _bounded_int(
                    slot.get("sceneHandle"),
                    label="sceneHandle",
                    minimum=1,
                    maximum=2_147_483_647,
                ),
                "rendererPath": _bounded_text(
                    slot.get("rendererPath"),
                    label="rendererPath",
                    max_length=1024,
                ),
                "rendererComponentId": _lower_hex(
                    slot.get("rendererComponentId"),
                    label="rendererComponentId",
                    pattern=_DIGEST_PATTERN,
                ),
                "rendererComponentType": _bounded_text(
                    slot.get("rendererComponentType"),
                    label="rendererComponentType",
                    max_length=512,
                ),
                "rendererComponentIndex": _bounded_int(
                    slot.get("rendererComponentIndex"),
                    label="rendererComponentIndex",
                    minimum=0,
                    maximum=1024,
                ),
                "slotIndex": _bounded_int(
                    slot.get("slotIndex"),
                    label="slotIndex",
                    minimum=0,
                    maximum=1024,
                ),
            }
        )
    dependent_assets = [
        _safe_asset_path(
            item,
            label="dependentAssets item",
            roots=("Assets",),
            suffixes=(".prefab", ".unity"),
        )
        for item in raw_assets
    ]
    lists_truncated = _strict_bool(impact.get("listsTruncated"), label="listsTruncated")
    expected_slots = sorted(
        slots,
        key=lambda item: (
            _utf16_sort_key(item["scenePath"]),
            item["sceneHandle"],
            _utf16_sort_key(item["rendererPath"]),
            _utf16_sort_key(item["rendererComponentType"]),
            item["rendererComponentIndex"],
            _utf16_sort_key(item["rendererComponentId"]),
            item["slotIndex"],
        ),
    )
    expected_assets = sorted(dependent_assets, key=_utf16_sort_key)
    slot_identities = {
        (
            item["scenePath"],
            item["sceneGuid"],
            item["sceneHandle"],
            item["rendererPath"],
            item["rendererComponentId"],
            item["rendererComponentType"],
            item["rendererComponentIndex"],
            item["slotIndex"],
        )
        for item in slots
    }
    if slots != expected_slots or len(slot_identities) != len(slots):
        raise MaterialShaderAssignmentError("loadedRendererSlots must be sorted and unique.")
    if dependent_assets != expected_assets or len(set(dependent_assets)) != len(dependent_assets):
        raise MaterialShaderAssignmentError("dependentAssets must be sorted and unique.")
    if dependent_count > candidate_count:
        raise MaterialShaderAssignmentError("dependentAssetCount exceeds dependencyCandidateCount.")
    if len(slots) != min(renderer_count, MAX_IMPACT_ITEMS):
        raise MaterialShaderAssignmentError("loadedRendererSlots length is inconsistent.")
    if len(dependent_assets) != min(dependent_count, MAX_IMPACT_ITEMS):
        raise MaterialShaderAssignmentError("dependentAssets length is inconsistent.")
    if renderer_count < len(slots) or dependent_count < len(dependent_assets):
        raise MaterialShaderAssignmentError("Shared material impact counts are inconsistent.")
    if lists_truncated != (renderer_count > len(slots) or dependent_count > len(dependent_assets)):
        raise MaterialShaderAssignmentError("Shared material impact truncation state is inconsistent.")
    return {
        "scope": impact["scope"],
        "dependencyCandidateCount": candidate_count,
        "loadedRendererSlotCount": renderer_count,
        "loadedRendererSlots": slots,
        "dependentAssetCount": dependent_count,
        "dependentAssets": dependent_assets,
        "listsTruncated": lists_truncated,
    }


def compute_shared_impact_digest(impact: dict[str, Any]) -> str:
    return _compute_impact_partition_digest(
        impact,
        partition="display",
        slots=list(impact["loadedRendererSlots"]),
        assets=list(impact["dependentAssets"]),
    )


def compute_shared_impact_tail_digest(
    impact: dict[str, Any],
    *,
    slots: list[dict[str, Any]],
    assets: list[str],
) -> str:
    return _compute_impact_partition_digest(
        impact,
        partition="tail",
        slots=slots,
        assets=assets,
    )


def _compute_impact_partition_digest(
    impact: dict[str, Any],
    *,
    partition: str,
    slots: list[dict[str, Any]],
    assets: list[str],
) -> str:
    fields = [
        IMPACT_DIGEST_SCHEMA,
        partition,
        str(impact["scope"]),
        str(impact["dependencyCandidateCount"]),
        str(impact["loadedRendererSlotCount"]),
    ]
    for slot in slots:
        fields.extend(
            (
                str(slot["scenePath"]),
                str(slot["sceneGuid"]),
                str(slot["sceneHandle"]),
                str(slot["rendererPath"]),
                str(slot["rendererComponentId"]),
                str(slot["rendererComponentType"]),
                str(slot["rendererComponentIndex"]),
                str(slot["slotIndex"]),
            )
        )
    fields.append(str(impact["dependentAssetCount"]))
    fields.extend(str(path) for path in assets)
    fields.append("true" if impact["listsTruncated"] else "false")
    return _digest_framed_fields(fields)


def compute_shared_impact_commitment(
    impact: dict[str, Any],
    *,
    display_digest: str,
    tail_digest: str,
) -> str:
    return _digest_framed_fields(
        [
            IMPACT_DIGEST_SCHEMA,
            "full",
            str(impact["scope"]),
            str(impact["dependencyCandidateCount"]),
            str(impact["loadedRendererSlotCount"]),
            str(impact["dependentAssetCount"]),
            "true" if impact["listsTruncated"] else "false",
            display_digest,
            tail_digest,
        ]
    )


def _digest_framed_fields(fields: list[str]) -> str:
    framed = "".join(f"{_utf16_length(value)}:{value}" for value in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _utf16_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-16-be")
    except UnicodeEncodeError as exc:
        raise MaterialShaderAssignmentError("Impact paths contain invalid Unicode.") from exc


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaterialShaderAssignmentError(f"{label} must be an object.")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise MaterialShaderAssignmentError(f"{label} must be a boolean.")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MaterialShaderAssignmentError(f"{label} is outside the supported range.")
    return value


def _bounded_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise MaterialShaderAssignmentError(f"{label} must be text.")
    text = value.strip()
    if not text or len(text) > max_length or any(ord(char) < 32 for char in text):
        raise MaterialShaderAssignmentError(f"{label} is invalid.")
    return text


def _lower_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip().lower()):
        raise MaterialShaderAssignmentError(f"{label} is invalid.")
    return value.strip().lower()


def _optional_scene_path(value: Any, *, label: str) -> str:
    if value is None or value == "":
        return ""
    text = _bounded_text(value, label=label, max_length=1024).replace("\\", "/").strip("/")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise MaterialShaderAssignmentError(f"{label} is invalid.")
    return "/".join(parts)


def _optional_asset_path(value: Any, *, label: str, roots: tuple[str, ...]) -> str:
    if value is None or value == "":
        return ""
    return _safe_asset_path(value, label=label, roots=roots)


def _safe_asset_path(
    value: Any,
    *,
    label: str,
    roots: tuple[str, ...],
    suffix: str = "",
    suffixes: tuple[str, ...] = (),
) -> str:
    if not isinstance(value, str):
        raise MaterialShaderAssignmentError(f"{label} must be text.")
    text = value.replace("\\", "/").strip()
    if not text or len(text) > 2048 or text.startswith("/") or text.endswith("/"):
        raise MaterialShaderAssignmentError(f"{label} is invalid.")
    if any(ord(char) < 32 for char in text):
        raise MaterialShaderAssignmentError(f"{label} is invalid.")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts) or parts[0] not in roots:
        raise MaterialShaderAssignmentError(f"{label} is outside the allowed asset roots.")
    path = PurePosixPath(*parts).as_posix()
    lowered = path.lower()
    if suffix and not lowered.endswith(suffix.lower()):
        raise MaterialShaderAssignmentError(f"{label} has an unsupported file type.")
    if suffixes and not any(lowered.endswith(item.lower()) for item in suffixes):
        raise MaterialShaderAssignmentError(f"{label} has an unsupported file type.")
    return path
