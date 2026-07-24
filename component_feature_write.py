from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


RESULT_SCHEMA = "vrcforge.component_feature_write.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.component_feature_write_approval.v1"
FEATURE_DIGEST_SCHEMA = "vrcforge.component_feature_state.v1"
PREVIEW_DIGEST_SCHEMA = "vrcforge.component_feature_preview.v1"
COMPATIBILITY_DIGEST_SCHEMA = "vrcforge.component_feature_compatibility.v1"
TOOL_NAME = "vrc_create_component_feature"

EXPECTED_COMPATIBILITY = {
    "packageName": "com.vrcfury.vrcfury",
    "packageVersion": "1.1334.0",
    "packageFileCount": 1255,
    "packageTotalBytes": 1_999_565,
    "packageTreeDigest": "d58d5db6083852bb0f5b495248794026b753b494dd88c8f6523e0019ff1a0f59",
    "apiAssemblyName": "com.vrcfury.api",
    "apiAssemblyVersion": "0.0.0.0",
    "apiAssemblyPublicKeyToken": "",
    "apiAssemblySignatureState": "unsigned",
    "runtimeAssemblyName": "VRCFury",
    "runtimeAssemblyVersion": "0.0.0.0",
    "runtimeAssemblyPublicKeyToken": "",
    "runtimeAssemblySignatureState": "unsigned",
    "apiSignatureDigest": "71dc4faf929c8da61b8969e2b23a00636ac0aa5a53e9a67f73274213d4a417b1",
}

_ASSEMBLY_DIGEST_KEYS = {"apiAssemblyDigest", "runtimeAssemblyDigest"}
_COMPATIBILITY_KEYS = (
    "packageName",
    "packageVersion",
    "packageFileCount",
    "packageTotalBytes",
    "packageTreeDigest",
    "apiAssemblyName",
    "apiAssemblyVersion",
    "apiAssemblyPublicKeyToken",
    "apiAssemblySignatureState",
    "apiAssemblyDigest",
    "runtimeAssemblyName",
    "runtimeAssemblyVersion",
    "runtimeAssemblyPublicKeyToken",
    "runtimeAssemblySignatureState",
    "runtimeAssemblyDigest",
    "apiSignatureDigest",
)

_COMMON_KEYS = {"scenePath", "gameObjectPath", "featureKind"}
_TOGGLE_KEYS = {
    "menuPath",
    "targetObjectPaths",
    "slider",
    "defaultOn",
    "saved",
    "globalParameter",
}
_ARMATURE_KEYS = {"linkFromPath", "linkTargets", "recursive", "align"}
_CONTROL_KEYS = {"preview", "saveScene"}
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_GLOBAL_PARAMETER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_HUMANOID_BONES = {
    "Hips",
    "LeftUpperLeg",
    "RightUpperLeg",
    "LeftLowerLeg",
    "RightLowerLeg",
    "LeftFoot",
    "RightFoot",
    "Spine",
    "Chest",
    "UpperChest",
    "Neck",
    "Head",
    "LeftShoulder",
    "RightShoulder",
    "LeftUpperArm",
    "RightUpperArm",
    "LeftLowerArm",
    "RightLowerArm",
    "LeftHand",
    "RightHand",
    "LeftToes",
    "RightToes",
    "LeftEye",
    "RightEye",
    "Jaw",
    "LeftThumbProximal",
    "LeftThumbIntermediate",
    "LeftThumbDistal",
    "LeftIndexProximal",
    "LeftIndexIntermediate",
    "LeftIndexDistal",
    "LeftMiddleProximal",
    "LeftMiddleIntermediate",
    "LeftMiddleDistal",
    "LeftRingProximal",
    "LeftRingIntermediate",
    "LeftRingDistal",
    "LeftLittleProximal",
    "LeftLittleIntermediate",
    "LeftLittleDistal",
    "RightThumbProximal",
    "RightThumbIntermediate",
    "RightThumbDistal",
    "RightIndexProximal",
    "RightIndexIntermediate",
    "RightIndexDistal",
    "RightMiddleProximal",
    "RightMiddleIntermediate",
    "RightMiddleDistal",
    "RightRingProximal",
    "RightRingIntermediate",
    "RightRingDistal",
    "RightLittleProximal",
    "RightLittleIntermediate",
    "RightLittleDistal",
}


class ComponentFeatureWriteError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ComponentFeatureWriteError("Component feature parameters are required.")
    wrapper = deepcopy(params)
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {
            key: deepcopy(value)
            for key, value in wrapper.items()
            if key in _COMMON_KEYS | _TOGGLE_KEYS | _ARMATURE_KEYS | _CONTROL_KEYS
            or key.startswith("expected")
        }
    normalized = normalize_request(_without_expected(nested))
    project_path = wrapper.get("projectPath")
    wrapper = {
        key: deepcopy(value)
        for key, value in wrapper.items()
        if key not in _COMMON_KEYS | _TOGGLE_KEYS | _ARMATURE_KEYS | _CONTROL_KEYS
        and not key.startswith("expected")
        and key not in {"params", "tool_name", "arguments"}
    }
    wrapper["toolName"] = TOOL_NAME
    if project_path is not None:
        wrapper["projectPath"] = project_path
    wrapper["arguments"] = normalized
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_request(_without_expected(arguments))
    normalized["preview"] = True
    normalized["saveScene"] = False
    return normalized


def normalize_request(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ComponentFeatureWriteError("Component feature arguments are required.")
    feature_kind = _choice(
        arguments.get("featureKind"),
        label="featureKind",
        choices={"toggle", "armature_link"},
    )
    feature_keys = _TOGGLE_KEYS if feature_kind == "toggle" else _ARMATURE_KEYS
    allowed = _COMMON_KEYS | feature_keys | _CONTROL_KEYS
    unexpected = sorted(key for key in arguments if key not in allowed)
    if unexpected:
        raise ComponentFeatureWriteError("Component feature arguments contain unsupported fields.")
    missing = sorted((_COMMON_KEYS | feature_keys) - set(arguments))
    if missing:
        raise ComponentFeatureWriteError("Component feature arguments are incomplete.")

    normalized: dict[str, Any] = {
        "scenePath": _safe_scene_path(arguments.get("scenePath")),
        "gameObjectPath": _safe_hierarchy_path(
            arguments.get("gameObjectPath"),
            label="gameObjectPath",
        ),
        "featureKind": feature_kind,
    }
    if feature_kind == "toggle":
        target_paths = _unique_path_list(
            arguments.get("targetObjectPaths"),
            label="targetObjectPaths",
            minimum=1,
            maximum=32,
        )
        global_parameter = _optional_text(
            arguments.get("globalParameter"),
            label="globalParameter",
            max_length=128,
        )
        if global_parameter and _GLOBAL_PARAMETER_PATTERN.fullmatch(global_parameter) is None:
            raise ComponentFeatureWriteError("globalParameter is invalid.")
        normalized.update(
            {
                "menuPath": _safe_menu_path(arguments.get("menuPath")),
                "targetObjectPaths": target_paths,
                "slider": _strict_bool(arguments.get("slider"), label="slider"),
                "defaultOn": _strict_bool(arguments.get("defaultOn"), label="defaultOn"),
                "saved": _strict_bool(arguments.get("saved"), label="saved"),
                "globalParameter": global_parameter,
            }
        )
        return normalized

    links = arguments.get("linkTargets")
    if not isinstance(links, list) or not 1 <= len(links) <= 8:
        raise ComponentFeatureWriteError("linkTargets must be a bounded non-empty array.")
    normalized_links: list[dict[str, str]] = []
    seen_links: set[tuple[str, str, str]] = set()
    for raw in links:
        if not isinstance(raw, dict) or set(raw) != {"targetKind", "target", "offset"}:
            raise ComponentFeatureWriteError("Each armature target must match its fixed schema.")
        target_kind = _choice(
            raw.get("targetKind"),
            label="targetKind",
            choices={"humanoid_bone", "game_object", "relative_path"},
        )
        raw_target = raw.get("target")
        if target_kind == "humanoid_bone":
            target = _bounded_text(raw_target, label="target", max_length=128)
            if target not in _HUMANOID_BONES:
                raise ComponentFeatureWriteError("The humanoid bone target is unsupported.")
        else:
            target = _safe_hierarchy_path(raw_target, label="target")
        offset = _optional_relative_path(raw.get("offset"), label="offset")
        if target_kind == "relative_path" and offset:
            raise ComponentFeatureWriteError("relative_path targets cannot include a second offset.")
        key = (target_kind, target, offset)
        if key in seen_links:
            raise ComponentFeatureWriteError("Duplicate armature targets are not supported.")
        seen_links.add(key)
        normalized_links.append(
            {"targetKind": target_kind, "target": target, "offset": offset}
        )
    normalized.update(
        {
            "linkFromPath": _safe_hierarchy_path(
                arguments.get("linkFromPath"),
                label="linkFromPath",
            ),
            "linkTargets": normalized_links,
            "recursive": _strict_bool(arguments.get("recursive"), label="recursive"),
            "align": _strict_bool(arguments.get("align"), label="align"),
        }
    )
    return normalized


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict):
        raise ComponentFeatureWriteError("Component feature wrapper arguments are required.")
    if wrapper_arguments.get("toolName", TOOL_NAME) != TOOL_NAME:
        raise ComponentFeatureWriteError("Component feature tool name is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    requested = normalize_request(_without_expected(nested))
    project_path = _canonical_project_path(wrapper_arguments.get("projectPath"))

    result = _require_dict(payload, "preview result")
    expected_result_keys = {
        "schema",
        "ok",
        "preview",
        "verified",
        "changed",
        "saved",
        "mutationCount",
        "projectPath",
        "compatibility",
        "compatibilityDigestSchema",
        "compatibilityDigest",
        "scene",
        "host",
        "before",
        "target",
        "featureDigestSchema",
        "beforeFeatureDigest",
        "targetFeatureDigest",
        "wouldChange",
        "previewDigest",
    }
    if set(result) != expected_result_keys:
        raise ComponentFeatureWriteError("Component feature preview shape is invalid.")
    if result.get("schema") != RESULT_SCHEMA:
        raise ComponentFeatureWriteError("Component feature preview schema is invalid.")
    for key, expected in (("ok", True), ("preview", True), ("verified", True)):
        if result.get(key) is not expected:
            raise ComponentFeatureWriteError(f"Component feature preview {key} is invalid.")
    for key in ("changed", "saved"):
        if _strict_bool(result.get(key), label=key):
            raise ComponentFeatureWriteError(f"Component feature preview reported {key}.")
    if _bounded_int(result.get("mutationCount"), label="mutationCount", minimum=0, maximum=0) != 0:
        raise ComponentFeatureWriteError("Component feature preview reported a mutation.")
    actual_project = _canonical_project_path(result.get("projectPath"))
    if os.path.normcase(actual_project) != os.path.normcase(project_path):
        raise ComponentFeatureWriteError("Component feature preview changed the selected project.")

    compatibility = _canonical_compatibility(result.get("compatibility"))
    compatibility_digest = _lower_hex(
        result.get("compatibilityDigest"),
        label="compatibilityDigest",
        pattern=_DIGEST_PATTERN,
    )
    if result.get("compatibilityDigestSchema") != COMPATIBILITY_DIGEST_SCHEMA:
        raise ComponentFeatureWriteError("Component feature compatibility digest schema is invalid.")
    if compatibility_digest != compute_compatibility_digest(compatibility):
        raise ComponentFeatureWriteError("Component feature compatibility digest is invalid.")

    scene = _canonical_scene(result.get("scene"))
    if scene["path"] != requested["scenePath"]:
        raise ComponentFeatureWriteError("Component feature preview changed the selected scene.")
    if scene["fileDigestAfter"] != scene["fileDigestBefore"]:
        raise ComponentFeatureWriteError("Component feature preview changed the scene file.")
    if scene["metaDigestAfter"] != scene["metaDigestBefore"]:
        raise ComponentFeatureWriteError("Component feature preview changed the scene metadata.")
    if scene["dirtyBefore"] or scene["dirtyAfter"]:
        raise ComponentFeatureWriteError("Component feature preview requires a clean saved scene.")

    host = _canonical_host(result.get("host"))
    if host["objectPath"] != requested["gameObjectPath"]:
        raise ComponentFeatureWriteError("Component feature preview changed the selected object.")
    if host["componentType"] != "VF.Model.VRCFury":
        raise ComponentFeatureWriteError("Component feature component type is invalid.")
    if host["existingFeatureCount"] != 0:
        raise ComponentFeatureWriteError("Component feature CreateNew target already exists.")

    before = _canonical_before(result.get("before"), requested["featureKind"])
    target = _canonical_target(result.get("target"), requested)
    if result.get("featureDigestSchema") != FEATURE_DIGEST_SCHEMA:
        raise ComponentFeatureWriteError("Component feature digest schema is invalid.")
    before_digest = _lower_hex(
        result.get("beforeFeatureDigest"),
        label="beforeFeatureDigest",
        pattern=_DIGEST_PATTERN,
    )
    target_digest = _lower_hex(
        result.get("targetFeatureDigest"),
        label="targetFeatureDigest",
        pattern=_DIGEST_PATTERN,
    )
    if before_digest != compute_feature_digest(before):
        raise ComponentFeatureWriteError("Component feature before digest is invalid.")
    if target_digest != compute_feature_digest(target):
        raise ComponentFeatureWriteError("Component feature target digest is invalid.")
    if not _strict_bool(result.get("wouldChange"), label="wouldChange"):
        raise ComponentFeatureWriteError("Component feature CreateNew preview must change the target.")

    preview_digest = _lower_hex(
        result.get("previewDigest"),
        label="previewDigest",
        pattern=_DIGEST_PATTERN,
    )
    if preview_digest != compute_preview_digest(result):
        raise ComponentFeatureWriteError("Component feature preview digest is invalid.")

    canonical_nested = deepcopy(requested)
    canonical_nested.update(
        {
            "preview": False,
            "saveScene": True,
            "expectedProjectPath": project_path,
            "expectedSceneGuid": scene["guid"],
            "expectedSceneHandle": scene["handle"],
            "expectedSceneFileDigest": scene["fileDigestBefore"],
            "expectedSceneFileIdentity": scene["fileIdentity"],
            "expectedSceneMetaDigest": scene["metaDigestBefore"],
            "expectedSceneMetaIdentity": scene["metaIdentity"],
            "expectedHostObjectId": host["objectId"],
            "expectedComponentType": host["componentType"],
            "expectedComponentIndex": host["componentIndex"],
            "expectedComponentIdentitySeed": host["componentIdentitySeed"],
            "expectedBeforeFeatureDigest": before_digest,
            "expectedTargetFeatureDigest": target_digest,
            "expectedCompatibilityDigest": compatibility_digest,
            "expectedPreviewDigest": preview_digest,
        }
    )
    canonical_wrapper = {
        key: deepcopy(value)
        for key, value in wrapper_arguments.items()
        if key not in {"params", "tool_name", "arguments"}
    }
    canonical_wrapper["projectPath"] = project_path
    canonical_wrapper["toolName"] = TOOL_NAME
    canonical_wrapper["arguments"] = canonical_nested

    approval = {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "toolName": TOOL_NAME,
        "projectPath": project_path,
        "target": {
            "scene": scene,
            "host": host,
            "compatibility": compatibility,
            "compatibilityDigest": compatibility_digest,
        },
        "change": {
            "before": before,
            "after": target,
            "beforeFeatureDigest": before_digest,
            "afterFeatureDigest": target_digest,
            "wouldChange": True,
            "createNew": True,
        },
        "featureDigestSchema": FEATURE_DIGEST_SCHEMA,
        "mutationCount": 1,
        "rollbackRequired": True,
        "previewDigest": preview_digest,
    }
    return canonical_wrapper, approval


def compute_feature_digest(state: dict[str, Any]) -> str:
    if not isinstance(state, dict):
        raise ComponentFeatureWriteError("Component feature state must be an object.")
    feature_kind = _choice(
        state.get("featureKind"),
        label="featureKind",
        choices={"toggle", "armature_link"},
    )
    if state.get("present") is False:
        if set(state) != {"present", "featureKind"}:
            raise ComponentFeatureWriteError("Absent component feature state is invalid.")
        return _framed_digest([FEATURE_DIGEST_SCHEMA, "absent", feature_kind])
    if state.get("present") is not True:
        raise ComponentFeatureWriteError("Component feature state presence is invalid.")
    values = [FEATURE_DIGEST_SCHEMA, "present", feature_kind]
    if feature_kind == "toggle":
        target = _canonical_toggle_target(state)
        values.extend(
            (
                target["menuPath"],
                _bool_token(target["slider"]),
                _bool_token(target["defaultOn"]),
                _bool_token(target["saved"]),
                target["globalParameter"],
                str(len(target["targets"])),
            )
        )
        for item in target["targets"]:
            values.extend((item["objectPath"], item["objectId"]))
        return _framed_digest(values)
    target = _canonical_armature_target(state)
    values.extend(
        (
            target["linkFrom"]["objectPath"],
            target["linkFrom"]["objectId"],
            _bool_token(target["recursive"]),
            _bool_token(target["align"]),
            str(len(target["links"])),
        )
    )
    for item in target["links"]:
        values.extend(
            (
                item["targetKind"],
                item["target"],
                item["objectId"],
                item["offset"],
            )
        )
    return _framed_digest(values)


def compute_compatibility_digest(compatibility: dict[str, Any]) -> str:
    canonical = _canonical_compatibility(compatibility)
    values = [COMPATIBILITY_DIGEST_SCHEMA]
    for key in _COMPATIBILITY_KEYS:
        values.extend((key, str(canonical[key])))
    return _framed_digest(values)


def compute_preview_digest(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ComponentFeatureWriteError("Component feature preview must be an object.")
    committed = {
        "schema": payload.get("schema"),
        "projectPath": payload.get("projectPath"),
        "compatibility": payload.get("compatibility"),
        "compatibilityDigestSchema": payload.get("compatibilityDigestSchema"),
        "compatibilityDigest": payload.get("compatibilityDigest"),
        "scene": payload.get("scene"),
        "host": payload.get("host"),
        "before": payload.get("before"),
        "target": payload.get("target"),
        "featureDigestSchema": payload.get("featureDigestSchema"),
        "beforeFeatureDigest": payload.get("beforeFeatureDigest"),
        "targetFeatureDigest": payload.get("targetFeatureDigest"),
        "wouldChange": payload.get("wouldChange"),
    }
    serialized = json.dumps(
        committed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _framed_digest([PREVIEW_DIGEST_SCHEMA, serialized])


def _canonical_compatibility(value: Any) -> dict[str, Any]:
    raw = _require_dict(value, "compatibility")
    if set(raw) != set(EXPECTED_COMPATIBILITY) | _ASSEMBLY_DIGEST_KEYS:
        raise ComponentFeatureWriteError("Component feature compatibility evidence is incomplete.")
    canonical: dict[str, Any] = {}
    for key in _COMPATIBILITY_KEYS:
        actual = raw.get(key)
        if key in _ASSEMBLY_DIGEST_KEYS:
            canonical[key] = _lower_hex(actual, label=key, pattern=_DIGEST_PATTERN)
            continue
        expected = EXPECTED_COMPATIBILITY[key]
        if isinstance(expected, int):
            actual = _bounded_int(actual, label=key, minimum=0, maximum=10_000_000_000)
        else:
            if not isinstance(actual, str):
                raise ComponentFeatureWriteError(f"{key} must be text.")
            actual = actual.strip()
        if actual != expected:
            raise ComponentFeatureWriteError("The component feature compatibility version is unsupported.")
        canonical[key] = actual
    return canonical


def _canonical_scene(value: Any) -> dict[str, Any]:
    raw = _require_dict(value, "scene")
    required = {
        "path",
        "guid",
        "handle",
        "fileDigestBefore",
        "fileDigestAfter",
        "fileIdentity",
        "metaDigestBefore",
        "metaDigestAfter",
        "metaIdentity",
        "dirtyBefore",
        "dirtyAfter",
    }
    if set(raw) != required:
        raise ComponentFeatureWriteError("Component feature scene evidence is incomplete.")
    handle = _bounded_int(
        raw.get("handle"),
        label="sceneHandle",
        minimum=-2_147_483_648,
        maximum=2_147_483_647,
    )
    if handle == 0:
        raise ComponentFeatureWriteError("sceneHandle is out of range.")
    return {
        "path": _safe_scene_path(raw.get("path")),
        "guid": _lower_hex(raw.get("guid"), label="sceneGuid", pattern=_GUID_PATTERN),
        "handle": handle,
        "fileDigestBefore": _lower_hex(raw.get("fileDigestBefore"), label="fileDigestBefore", pattern=_DIGEST_PATTERN),
        "fileDigestAfter": _lower_hex(raw.get("fileDigestAfter"), label="fileDigestAfter", pattern=_DIGEST_PATTERN),
        "fileIdentity": _lower_hex(raw.get("fileIdentity"), label="fileIdentity", pattern=_DIGEST_PATTERN),
        "metaDigestBefore": _lower_hex(raw.get("metaDigestBefore"), label="metaDigestBefore", pattern=_DIGEST_PATTERN),
        "metaDigestAfter": _lower_hex(raw.get("metaDigestAfter"), label="metaDigestAfter", pattern=_DIGEST_PATTERN),
        "metaIdentity": _lower_hex(raw.get("metaIdentity"), label="metaIdentity", pattern=_DIGEST_PATTERN),
        "dirtyBefore": _strict_bool(raw.get("dirtyBefore"), label="dirtyBefore"),
        "dirtyAfter": _strict_bool(raw.get("dirtyAfter"), label="dirtyAfter"),
    }


def _canonical_host(value: Any) -> dict[str, Any]:
    raw = _require_dict(value, "host")
    required = {
        "objectPath",
        "objectId",
        "componentType",
        "componentIndex",
        "componentIdentitySeed",
        "existingFeatureCount",
    }
    if set(raw) != required:
        raise ComponentFeatureWriteError("Component feature host evidence is incomplete.")
    return {
        "objectPath": _safe_hierarchy_path(raw.get("objectPath"), label="objectPath"),
        "objectId": _global_object_id(raw.get("objectId"), label="objectId"),
        "componentType": _bounded_text(raw.get("componentType"), label="componentType", max_length=512),
        "componentIndex": _bounded_int(raw.get("componentIndex"), label="componentIndex", minimum=0, maximum=64),
        "componentIdentitySeed": _lower_hex(raw.get("componentIdentitySeed"), label="componentIdentitySeed", pattern=_DIGEST_PATTERN),
        "existingFeatureCount": _bounded_int(raw.get("existingFeatureCount"), label="existingFeatureCount", minimum=0, maximum=64),
    }


def _canonical_before(value: Any, feature_kind: str) -> dict[str, Any]:
    raw = _require_dict(value, "before")
    if raw != {"present": False, "featureKind": feature_kind}:
        raise ComponentFeatureWriteError("Component feature before state must be absent.")
    return dict(raw)


def _canonical_target(value: Any, requested: dict[str, Any]) -> dict[str, Any]:
    if requested["featureKind"] == "toggle":
        target = _canonical_toggle_target(value)
        if (
            target["menuPath"] != requested["menuPath"]
            or target["slider"] != requested["slider"]
            or target["defaultOn"] != requested["defaultOn"]
            or target["saved"] != requested["saved"]
            or target["globalParameter"] != requested["globalParameter"]
            or [item["objectPath"] for item in target["targets"]]
            != requested["targetObjectPaths"]
        ):
            raise ComponentFeatureWriteError("Component feature preview changed the requested toggle.")
        return target
    target = _canonical_armature_target(value)
    if (
        target["linkFrom"]["objectPath"] != requested["linkFromPath"]
        or target["recursive"] != requested["recursive"]
        or target["align"] != requested["align"]
        or [
            {key: item[key] for key in ("targetKind", "target", "offset")}
            for item in target["links"]
        ]
        != requested["linkTargets"]
    ):
        raise ComponentFeatureWriteError("Component feature preview changed the requested armature link.")
    return target


def _canonical_toggle_target(value: Any) -> dict[str, Any]:
    raw = _require_dict(value, "toggle target")
    required = {
        "present",
        "featureKind",
        "menuPath",
        "slider",
        "defaultOn",
        "saved",
        "globalParameter",
        "targets",
    }
    if set(raw) != required or raw.get("present") is not True or raw.get("featureKind") != "toggle":
        raise ComponentFeatureWriteError("Toggle target state is invalid.")
    targets = raw.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 32:
        raise ComponentFeatureWriteError("Toggle target evidence is invalid.")
    canonical_targets = []
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for item in targets:
        item = _require_dict(item, "toggle target item")
        if set(item) != {"objectPath", "objectId"}:
            raise ComponentFeatureWriteError("Toggle target item is invalid.")
        path = _safe_hierarchy_path(item.get("objectPath"), label="objectPath")
        object_id = _global_object_id(item.get("objectId"), label="objectId")
        if path in seen_paths or object_id in seen_ids:
            raise ComponentFeatureWriteError("Toggle target identities must be unique.")
        seen_paths.add(path)
        seen_ids.add(object_id)
        canonical_targets.append({"objectPath": path, "objectId": object_id})
    return {
        "present": True,
        "featureKind": "toggle",
        "menuPath": _safe_menu_path(raw.get("menuPath")),
        "slider": _strict_bool(raw.get("slider"), label="slider"),
        "defaultOn": _strict_bool(raw.get("defaultOn"), label="defaultOn"),
        "saved": _strict_bool(raw.get("saved"), label="saved"),
        "globalParameter": _optional_text(raw.get("globalParameter"), label="globalParameter", max_length=128),
        "targets": canonical_targets,
    }


def _canonical_armature_target(value: Any) -> dict[str, Any]:
    raw = _require_dict(value, "armature target")
    required = {"present", "featureKind", "linkFrom", "links", "recursive", "align"}
    if set(raw) != required or raw.get("present") is not True or raw.get("featureKind") != "armature_link":
        raise ComponentFeatureWriteError("Armature-link target state is invalid.")
    link_from = _require_dict(raw.get("linkFrom"), "linkFrom")
    if set(link_from) != {"objectPath", "objectId"}:
        raise ComponentFeatureWriteError("Armature-link source identity is invalid.")
    canonical_from = {
        "objectPath": _safe_hierarchy_path(link_from.get("objectPath"), label="objectPath"),
        "objectId": _global_object_id(link_from.get("objectId"), label="objectId"),
    }
    links = raw.get("links")
    if not isinstance(links, list) or not 1 <= len(links) <= 8:
        raise ComponentFeatureWriteError("Armature-link target evidence is invalid.")
    canonical_links = []
    for item in links:
        item = _require_dict(item, "armature link item")
        if set(item) != {"targetKind", "target", "objectId", "offset"}:
            raise ComponentFeatureWriteError("Armature-link target item is invalid.")
        target_kind = _choice(item.get("targetKind"), label="targetKind", choices={"humanoid_bone", "game_object", "relative_path"})
        target = _bounded_text(item.get("target"), label="target", max_length=2048)
        object_id = item.get("objectId")
        if not isinstance(object_id, str):
            raise ComponentFeatureWriteError("Armature-link object identity must be text.")
        object_id = object_id.strip()
        if target_kind == "game_object":
            target = _safe_hierarchy_path(target, label="target")
            object_id = _global_object_id(object_id, label="objectId")
        elif object_id:
            raise ComponentFeatureWriteError("Non-object armature targets cannot include an object identity.")
        if target_kind == "humanoid_bone" and target not in _HUMANOID_BONES:
            raise ComponentFeatureWriteError("The humanoid bone target is unsupported.")
        if target_kind == "relative_path":
            target = _safe_hierarchy_path(target, label="target")
        offset = _optional_relative_path(item.get("offset"), label="offset")
        if target_kind == "relative_path" and offset:
            raise ComponentFeatureWriteError("relative_path targets cannot include a second offset.")
        canonical_links.append(
            {"targetKind": target_kind, "target": target, "objectId": object_id, "offset": offset}
        )
    return {
        "present": True,
        "featureKind": "armature_link",
        "linkFrom": canonical_from,
        "links": canonical_links,
        "recursive": _strict_bool(raw.get("recursive"), label="recursive"),
        "align": _strict_bool(raw.get("align"), label="align"),
    }


def _without_expected(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ComponentFeatureWriteError("Component feature arguments are required.")
    return {
        key: deepcopy(value)
        for key, value in arguments.items()
        if not key.startswith("expected")
    }


def _canonical_project_path(value: Any) -> str:
    raw = _bounded_text(value, label="projectPath", max_length=32_768).replace("\\", "/")
    if not re.match(r"^(?:[A-Za-z]:/|//[^/]+/[^/]+/)", raw):
        raise ComponentFeatureWriteError("projectPath must be absolute.")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ComponentFeatureWriteError("projectPath is invalid.")
    return path.as_posix()


def _safe_scene_path(value: Any) -> str:
    raw = _bounded_text(value, label="scenePath", max_length=2048)
    if "\\" in raw or raw.startswith("/") or raw.endswith("/"):
        raise ComponentFeatureWriteError("scenePath is outside Assets/.")
    path = PurePosixPath(raw)
    if (
        len(path.parts) < 2
        or path.parts[0] != "Assets"
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() != ".unity"
    ):
        raise ComponentFeatureWriteError("scenePath must select a saved scene under Assets/.")
    return path.as_posix()


def _safe_hierarchy_path(value: Any, *, label: str) -> str:
    raw = _bounded_text(value, label=label, max_length=2048)
    if "\\" in raw or raw.startswith("/") or raw.endswith("/") or "//" in raw:
        raise ComponentFeatureWriteError(f"{label} is invalid.")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ComponentFeatureWriteError(f"{label} is invalid.")
    return "/".join(parts)


def _safe_menu_path(value: Any) -> str:
    return _safe_hierarchy_path(value, label="menuPath")


def _optional_relative_path(value: Any, *, label: str) -> str:
    parsed = _optional_text(value, label=label, max_length=512)
    return "" if not parsed else _safe_hierarchy_path(parsed, label=label)


def _unique_path_list(value: Any, *, label: str, minimum: int, maximum: int) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ComponentFeatureWriteError(f"{label} must be a bounded array.")
    paths = [_safe_hierarchy_path(item, label=label) for item in value]
    if len(paths) != len(set(paths)):
        raise ComponentFeatureWriteError(f"{label} must be unique.")
    return paths


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComponentFeatureWriteError(f"{label} must be an object.")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ComponentFeatureWriteError(f"{label} must be a boolean.")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ComponentFeatureWriteError(f"{label} is out of range.")
    return value


def _bounded_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ComponentFeatureWriteError(f"{label} must be text.")
    parsed = value.strip()
    if (
        not parsed
        or _utf16_length(parsed) > max_length
        or any(ord(character) < 32 for character in parsed)
    ):
        raise ComponentFeatureWriteError(f"{label} is invalid.")
    return parsed


def _optional_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ComponentFeatureWriteError(f"{label} must be text.")
    parsed = value.strip()
    if _utf16_length(parsed) > max_length or any(ord(character) < 32 for character in parsed):
        raise ComponentFeatureWriteError(f"{label} is invalid.")
    return parsed


def _choice(value: Any, *, label: str, choices: set[str]) -> str:
    parsed = _bounded_text(value, label=label, max_length=128)
    if parsed not in choices:
        raise ComponentFeatureWriteError(f"{label} is unsupported.")
    return parsed


def _lower_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    parsed = _bounded_text(value, label=label, max_length=64).lower()
    if pattern.fullmatch(parsed) is None:
        raise ComponentFeatureWriteError(f"{label} is invalid.")
    return parsed


def _global_object_id(value: Any, *, label: str) -> str:
    parsed = _bounded_text(value, label=label, max_length=512)
    if not parsed.startswith("GlobalObjectId_V1-"):
        raise ComponentFeatureWriteError(f"{label} is invalid.")
    return parsed


def _bool_token(value: bool) -> str:
    return "true" if value else "false"


def _framed_digest(values: list[str]) -> str:
    framed = "".join(f"{_utf16_length(value)}:{value}" for value in values)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _utf16_length(value: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ComponentFeatureWriteError("Component feature data contains invalid Unicode.") from exc
