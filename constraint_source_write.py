from __future__ import annotations

import hashlib
import math
import os
import re
import struct
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


RESULT_SCHEMA = "vrcforge.constraint_source_write.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.constraint_source_write_approval.v1"
SOURCES_DIGEST_SCHEMA = "vrcforge.constraint_sources_digest.v1"
COMPONENT_DIGEST_SCHEMA = "vrcforge.constraint_component.v1"
TOOL_NAME = "vrc_set_constraint_sources"
MAX_SOURCES = 64

REQUEST_ARGUMENT_KEYS = (
    "scenePath",
    "gameObjectPath",
    "constraintKind",
    "componentIndex",
    "sources",
)

_CONSTRAINT_TYPES = {
    "position": "VRC.SDK3.Dynamics.Constraint.Components.VRCPositionConstraint",
    "rotation": "VRC.SDK3.Dynamics.Constraint.Components.VRCRotationConstraint",
    "scale": "VRC.SDK3.Dynamics.Constraint.Components.VRCScaleConstraint",
    "parent": "VRC.SDK3.Dynamics.Constraint.Components.VRCParentConstraint",
    "aim": "VRC.SDK3.Dynamics.Constraint.Components.VRCAimConstraint",
    "look_at": "VRC.SDK3.Dynamics.Constraint.Components.VRCLookAtConstraint",
}
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_FLOAT_BITS_PATTERN = re.compile(r"^[0-9a-f]{8}$")


class ConstraintSourceWriteError(ValueError):
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
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    preview = deepcopy(arguments or {})
    for key in tuple(preview):
        if key.startswith("expected"):
            preview.pop(key, None)
    preview["preview"] = True
    preview["saveScene"] = False
    return preview


def normalize_request(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ConstraintSourceWriteError("Constraint source arguments are required.")
    allowed = set(REQUEST_ARGUMENT_KEYS) | {"preview", "saveScene"}
    unexpected = [key for key in arguments if key not in allowed and not key.startswith("expected")]
    if unexpected:
        raise ConstraintSourceWriteError("Constraint source arguments contain unsupported fields.")

    scene_path = _safe_scene_path(arguments.get("scenePath"))
    game_object_path = _safe_hierarchy_path(arguments.get("gameObjectPath"), label="gameObjectPath")
    kind = _choice(arguments.get("constraintKind"), label="constraintKind", choices=set(_CONSTRAINT_TYPES))
    component_index = _bounded_int(
        arguments.get("componentIndex"),
        label="componentIndex",
        minimum=0,
        maximum=31,
    )
    raw_sources = arguments.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
        raise ConstraintSourceWriteError("sources must be a bounded array.")

    sources: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, dict) or set(raw) != {"sourcePath", "weight"}:
            raise ConstraintSourceWriteError("Each source must contain only sourcePath and weight.")
        source_path = _safe_hierarchy_path(raw.get("sourcePath"), label="sourcePath")
        if source_path in seen_paths:
            raise ConstraintSourceWriteError("Duplicate source paths are not supported.")
        seen_paths.add(source_path)
        weight, weight_bits = _canonical_float32(raw.get("weight"), label="weight")
        if not 0.0 <= weight <= 1.0:
            raise ConstraintSourceWriteError("weight is out of range.")
        sources.append(
            {
                "sourcePath": source_path,
                "weight": weight,
                "weightBits": weight_bits,
            }
        )

    return {
        "scenePath": scene_path,
        "gameObjectPath": game_object_path,
        "constraintKind": kind,
        "componentIndex": component_index,
        "sources": sources,
    }


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict):
        raise ConstraintSourceWriteError("Constraint source wrapper arguments are required.")
    if wrapper_arguments.get("toolName", TOOL_NAME) != TOOL_NAME:
        raise ConstraintSourceWriteError("Constraint source tool name is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    if not isinstance(nested, dict):
        raise ConstraintSourceWriteError("Constraint source arguments are required.")

    requested = normalize_request(nested)
    project_path = _canonical_project_path(wrapper_arguments.get("projectPath"))
    result = _require_dict(payload, "preview result")
    if result.get("schema") != RESULT_SCHEMA:
        raise ConstraintSourceWriteError("Constraint source preview schema is invalid.")
    for key, expected in (("ok", True), ("preview", True), ("verified", True)):
        if result.get(key) is not expected:
            raise ConstraintSourceWriteError(f"Constraint source preview {key} is invalid.")
    for key in ("changed", "saved", "sceneDirtyBefore", "sceneDirtyAfter"):
        if _strict_bool(result.get(key), label=key):
            raise ConstraintSourceWriteError(f"Constraint source preview reported {key}.")

    actual_project = _canonical_project_path(result.get("projectPath"))
    if os.path.normcase(actual_project) != os.path.normcase(project_path):
        raise ConstraintSourceWriteError("Constraint source preview changed the selected project.")
    scene_path = _safe_scene_path(result.get("scenePath"))
    if scene_path != requested["scenePath"]:
        raise ConstraintSourceWriteError("Constraint source preview changed the selected scene.")
    scene_guid = _lower_hex(result.get("sceneGuid"), label="sceneGuid", pattern=_GUID_PATTERN)
    scene_handle = _strict_int(result.get("sceneHandle"), label="sceneHandle")
    if scene_handle == 0 or not -(2**31) <= scene_handle <= 2**31 - 1:
        raise ConstraintSourceWriteError("sceneHandle is invalid.")
    scene_file_digest = _lower_hex(
        result.get("sceneFileDigestBefore"),
        label="sceneFileDigestBefore",
        pattern=_DIGEST_PATTERN,
    )
    scene_file_after = _lower_hex(
        result.get("sceneFileDigestAfter"),
        label="sceneFileDigestAfter",
        pattern=_DIGEST_PATTERN,
    )
    scene_file_identity = _lower_hex(
        result.get("sceneFileIdentity"),
        label="sceneFileIdentity",
        pattern=_DIGEST_PATTERN,
    )
    scene_meta_digest = _lower_hex(
        result.get("sceneMetaDigestBefore"),
        label="sceneMetaDigestBefore",
        pattern=_DIGEST_PATTERN,
    )
    scene_meta_after = _lower_hex(
        result.get("sceneMetaDigestAfter"),
        label="sceneMetaDigestAfter",
        pattern=_DIGEST_PATTERN,
    )
    scene_meta_identity = _lower_hex(
        result.get("sceneMetaIdentity"),
        label="sceneMetaIdentity",
        pattern=_DIGEST_PATTERN,
    )
    if _strict_int(result.get("sceneFileLinkCount"), label="sceneFileLinkCount") != 1:
        raise ConstraintSourceWriteError("The scene file must have exactly one filesystem link.")
    if _strict_int(result.get("sceneMetaLinkCount"), label="sceneMetaLinkCount") != 1:
        raise ConstraintSourceWriteError("The scene metadata must have exactly one filesystem link.")
    if scene_file_after != scene_file_digest or scene_meta_after != scene_meta_digest:
        raise ConstraintSourceWriteError("Constraint source preview changed the saved scene.")

    game_object_path = _safe_hierarchy_path(result.get("gameObjectPath"), label="gameObjectPath")
    constraint_kind = _choice(
        result.get("constraintKind"),
        label="constraintKind",
        choices=set(_CONSTRAINT_TYPES),
    )
    component_type = _bounded_text(result.get("componentType"), label="componentType", max_length=512)
    component_index = _bounded_int(
        result.get("componentIndex"),
        label="componentIndex",
        minimum=0,
        maximum=31,
    )
    component_id = _lower_hex(result.get("componentId"), label="componentId", pattern=_DIGEST_PATTERN)
    component_global_id = _bounded_text(
        result.get("componentGlobalId"),
        label="componentGlobalId",
        max_length=512,
    )
    expected_component_id = compute_component_id(
        scene_guid=scene_guid,
        component_global_id=component_global_id,
        game_object_path=game_object_path,
        component_type=component_type,
        component_index=component_index,
    )
    if component_id != expected_component_id:
        raise ConstraintSourceWriteError("Constraint component identity digest is invalid.")
    if (
        game_object_path != requested["gameObjectPath"]
        or constraint_kind != requested["constraintKind"]
        or component_type != _CONSTRAINT_TYPES[constraint_kind]
        or component_index != requested["componentIndex"]
    ):
        raise ConstraintSourceWriteError("Constraint source preview changed the selected component.")

    before_sources = _canonical_evidence_sources(result.get("beforeSources"), label="beforeSources")
    target_sources = _canonical_evidence_sources(result.get("targetSources"), label="targetSources")
    if len(target_sources) != len(requested["sources"]):
        raise ConstraintSourceWriteError("Constraint source preview changed the requested source count.")
    for requested_item, target_item in zip(requested["sources"], target_sources, strict=True):
        if (
            requested_item["sourcePath"] != target_item["sourcePath"]
            or requested_item["weightBits"] != target_item["weightBits"]
        ):
            raise ConstraintSourceWriteError("Constraint source preview changed source order or weight.")

    if result.get("sourcesDigestSchema") != SOURCES_DIGEST_SCHEMA:
        raise ConstraintSourceWriteError("Constraint source digest schema is invalid.")
    before_digest = _lower_hex(
        result.get("beforeSourcesDigest"),
        label="beforeSourcesDigest",
        pattern=_DIGEST_PATTERN,
    )
    target_digest = _lower_hex(
        result.get("targetSourcesDigest"),
        label="targetSourcesDigest",
        pattern=_DIGEST_PATTERN,
    )
    if before_digest != compute_sources_digest(before_sources):
        raise ConstraintSourceWriteError("Constraint source before digest is invalid.")
    if target_digest != compute_sources_digest(target_sources):
        raise ConstraintSourceWriteError("Constraint source target digest is invalid.")
    would_change = _strict_bool(result.get("wouldChange"), label="wouldChange")
    if would_change != (before_digest != target_digest):
        raise ConstraintSourceWriteError("Constraint source wouldChange is inconsistent.")

    canonical_nested = deepcopy(nested)
    for key in tuple(canonical_nested):
        if key.startswith("expected"):
            canonical_nested.pop(key, None)
    canonical_nested.update(
        {
            "scenePath": scene_path,
            "gameObjectPath": game_object_path,
            "constraintKind": constraint_kind,
            "componentIndex": component_index,
            "sources": [
                {"sourcePath": item["sourcePath"], "weight": item["weight"]}
                for item in requested["sources"]
            ],
            "preview": False,
            "saveScene": True,
            "expectedProjectPath": project_path,
            "expectedScenePath": scene_path,
            "expectedSceneGuid": scene_guid,
            "expectedSceneHandle": scene_handle,
            "expectedSceneFileDigest": scene_file_digest,
            "expectedSceneFileIdentity": scene_file_identity,
            "expectedSceneMetaDigest": scene_meta_digest,
            "expectedSceneMetaIdentity": scene_meta_identity,
            "expectedGameObjectPath": game_object_path,
            "expectedConstraintKind": constraint_kind,
            "expectedComponentType": component_type,
            "expectedComponentIndex": component_index,
            "expectedComponentId": component_id,
            "expectedComponentGlobalId": component_global_id,
            "expectedBeforeSourcesDigest": before_digest,
            "expectedTargetSourcesDigest": target_digest,
        }
    )
    canonical_wrapper = deepcopy(wrapper_arguments)
    canonical_wrapper.pop("params", None)
    canonical_wrapper.pop("tool_name", None)
    canonical_wrapper["projectPath"] = project_path
    canonical_wrapper["toolName"] = TOOL_NAME
    canonical_wrapper["arguments"] = canonical_nested

    approval = {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "toolName": TOOL_NAME,
        "projectPath": project_path,
        "target": {
            "scenePath": scene_path,
            "sceneGuid": scene_guid,
            "sceneFileDigest": scene_file_digest,
            "sceneFileIdentity": scene_file_identity,
            "sceneMetaDigest": scene_meta_digest,
            "sceneMetaIdentity": scene_meta_identity,
            "gameObjectPath": game_object_path,
            "constraintKind": constraint_kind,
            "componentType": component_type,
            "componentIndex": component_index,
            "componentId": component_id,
            "componentGlobalId": component_global_id,
        },
        "change": {
            "before": before_sources,
            "after": target_sources,
            "beforeSourcesDigest": before_digest,
            "afterSourcesDigest": target_digest,
            "wouldChange": would_change,
        },
        "sourcesDigestSchema": SOURCES_DIGEST_SCHEMA,
        "rollbackRequired": True,
    }
    return canonical_wrapper, approval


def compute_sources_digest(sources: list[dict[str, Any]]) -> str:
    canonical = _canonical_evidence_sources(sources, label="sources")
    fields = [SOURCES_DIGEST_SCHEMA, str(len(canonical))]
    for item in canonical:
        fields.extend((item["sourcePath"], item["sourceObjectId"], item["weightBits"]))
    framed = "".join(f"{_utf16_length(value)}:{value}" for value in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def compute_component_id(
    *,
    scene_guid: str,
    component_global_id: str,
    game_object_path: str,
    component_type: str,
    component_index: int,
) -> str:
    fields = [
        COMPONENT_DIGEST_SCHEMA,
        _lower_hex(scene_guid, label="sceneGuid", pattern=_GUID_PATTERN),
        _bounded_text(component_global_id, label="componentGlobalId", max_length=512),
        _safe_hierarchy_path(game_object_path, label="gameObjectPath"),
        _bounded_text(component_type, label="componentType", max_length=512),
        str(_bounded_int(component_index, label="componentIndex", minimum=0, maximum=31)),
    ]
    framed = "".join(f"{_utf16_length(value)}:{value}" for value in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _canonical_evidence_sources(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_SOURCES:
        raise ConstraintSourceWriteError(f"{label} must be a bounded array.")
    canonical: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "sourcePath",
            "sourceObjectId",
            "weight",
            "weightBits",
        }:
            raise ConstraintSourceWriteError(f"{label} contains an invalid source item.")
        source_path = _safe_hierarchy_path(raw.get("sourcePath"), label="sourcePath")
        object_id = _bounded_text(raw.get("sourceObjectId"), label="sourceObjectId", max_length=512)
        weight, computed_bits = _canonical_float32(raw.get("weight"), label="weight")
        weight_bits = _lower_hex(
            raw.get("weightBits"),
            label="weightBits",
            pattern=_FLOAT_BITS_PATTERN,
        )
        if not 0.0 <= weight <= 1.0 or weight_bits != computed_bits:
            raise ConstraintSourceWriteError(f"{label} contains an invalid source weight.")
        if source_path in seen_paths or object_id in seen_ids:
            raise ConstraintSourceWriteError(f"{label} contains duplicate source identities.")
        seen_paths.add(source_path)
        seen_ids.add(object_id)
        canonical.append(
            {
                "sourcePath": source_path,
                "sourceObjectId": object_id,
                "weight": weight,
                "weightBits": weight_bits,
            }
        )
    return canonical


def _canonical_project_path(value: Any) -> str:
    raw = _bounded_text(value, label="projectPath", max_length=32_768)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ConstraintSourceWriteError("projectPath must be absolute.")
    try:
        return str(path.resolve(strict=False))
    except OSError as exc:
        raise ConstraintSourceWriteError("projectPath is invalid.") from exc


def _safe_scene_path(value: Any) -> str:
    raw = _bounded_text(value, label="scenePath", max_length=2048)
    if "\\" in raw or raw.startswith("/") or raw.endswith("/"):
        raise ConstraintSourceWriteError("scenePath is outside Assets/.")
    path = PurePosixPath(raw)
    if (
        len(path.parts) < 2
        or path.parts[0] != "Assets"
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() != ".unity"
    ):
        raise ConstraintSourceWriteError("scenePath must select a saved scene under Assets/.")
    return path.as_posix()


def _safe_hierarchy_path(value: Any, *, label: str) -> str:
    raw = _bounded_text(value, label=label, max_length=2048)
    if "\\" in raw or raw.startswith("/") or raw.endswith("/") or "//" in raw:
        raise ConstraintSourceWriteError(f"{label} is invalid.")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ConstraintSourceWriteError(f"{label} is invalid.")
    return "/".join(parts)


def _canonical_float32(value: Any, *, label: str) -> tuple[float, str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConstraintSourceWriteError(f"{label} must be numeric.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ConstraintSourceWriteError(f"{label} must be finite.")
    try:
        packed = struct.pack(">f", numeric)
    except (OverflowError, struct.error) as exc:
        raise ConstraintSourceWriteError(f"{label} is out of range.") from exc
    return struct.unpack(">f", packed)[0], packed.hex()


def _choice(value: Any, *, label: str, choices: set[str]) -> str:
    parsed = _bounded_text(value, label=label, max_length=128).lower()
    if parsed not in choices:
        raise ConstraintSourceWriteError(f"{label} is unsupported.")
    return parsed


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConstraintSourceWriteError(f"{label} must be an object.")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConstraintSourceWriteError(f"{label} must be a boolean.")
    return value


def _strict_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConstraintSourceWriteError(f"{label} must be an integer.")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    parsed = _strict_int(value, label=label)
    if not minimum <= parsed <= maximum:
        raise ConstraintSourceWriteError(f"{label} is out of range.")
    return parsed


def _bounded_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ConstraintSourceWriteError(f"{label} must be text.")
    parsed = value.strip()
    if not parsed or len(parsed) > max_length or any(ord(character) < 32 for character in parsed):
        raise ConstraintSourceWriteError(f"{label} is invalid.")
    return parsed


def _lower_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    parsed = _bounded_text(value, label=label, max_length=64).lower()
    if pattern.fullmatch(parsed) is None:
        raise ConstraintSourceWriteError(f"{label} is invalid.")
    return parsed


def _utf16_length(value: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ConstraintSourceWriteError("Constraint source data contains invalid Unicode.") from exc
