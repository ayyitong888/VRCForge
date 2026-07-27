from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


RESULT_SCHEMA = "vrcforge.atomic_reference_rename.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.atomic_reference_rename_approval.v1"
PLAN_DIGEST_SCHEMA = "vrcforge.atomic_reference_rename_plan.v1"
TOOL_NAME = "vrc_atomic_reference_rename"

_COMMON_KEYS = {"operationKind", "scenePath", "avatarPath"}
_OBJECT_KEYS = {"targetObjectPath", "newName"}
_PARAMETER_KEYS = {"oldParameterName", "newParameterName"}
_CONTROL_KEYS = {"preview", "saveScene"}
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_PARAMETER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_OBJECT_NAME_PATTERN = re.compile(r"^[^/\\\x00-\x1f]{1,128}$")
_OBJECT_REFERENCE_KINDS = {
    "hierarchy_object",
    "animation_binding",
    "avatar_mask_transform",
}
_PARAMETER_REFERENCE_KINDS = {
    "expression_parameter",
    "expression_menu_parameter",
    "animator_parameter",
    "animator_condition",
    "animator_state_parameter",
    "blend_tree_parameter",
    "state_behaviour_parameter",
    "contact_parameter",
    "physbone_parameter",
    "registered_component_parameter",
}
_REFERENCE_KINDS = _OBJECT_REFERENCE_KINDS | _PARAMETER_REFERENCE_KINDS
_MAX_ASSETS = 4096
_MAX_REFERENCES = 16384
_MAX_RAW_REPLACEMENTS = _MAX_REFERENCES * 2
_MAX_ASSET_BYTES = 64 * 1024 * 1024
_PREVIEW_RESULT_KEYS = {
    "schema",
    "ok",
    "preview",
    "verified",
    "changed",
    "saved",
    "mutationCount",
    "projectPath",
    "operation",
    "scene",
    "avatar",
    "target",
    "scan",
    "assets",
    "references",
    "beforeStateDigest",
    "targetStateDigest",
    "planDigestSchema",
    "planDigest",
}


class AtomicReferenceRenameError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise AtomicReferenceRenameError("Atomic rename parameters are required.")
    nested = params.get("arguments")
    if not isinstance(nested, dict):
        nested = params.get("params")
    if not isinstance(nested, dict):
        allowed = _COMMON_KEYS | _OBJECT_KEYS | _PARAMETER_KEYS | _CONTROL_KEYS
        nested = {key: deepcopy(value) for key, value in params.items() if key in allowed}
    normalized = normalize_request(_without_expected(nested))
    return {
        "projectPath": deepcopy(params.get("projectPath")),
        "toolName": TOOL_NAME,
        "arguments": normalized,
    }


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_request(_without_expected(arguments))
    normalized["preview"] = True
    normalized["saveScene"] = False
    return normalized


def normalize_request(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise AtomicReferenceRenameError("Atomic rename arguments are required.")
    operation_kind = _choice(
        arguments.get("operationKind"),
        label="operationKind",
        choices={"game_object", "parameter"},
    )
    operation_keys = _OBJECT_KEYS if operation_kind == "game_object" else _PARAMETER_KEYS
    allowed = _COMMON_KEYS | operation_keys | _CONTROL_KEYS
    if any(key not in allowed for key in arguments):
        raise AtomicReferenceRenameError("Atomic rename arguments contain unsupported fields.")
    if any(key not in arguments for key in _COMMON_KEYS | operation_keys):
        raise AtomicReferenceRenameError("Atomic rename arguments are incomplete.")

    normalized: dict[str, Any] = {
        "operationKind": operation_kind,
        "scenePath": _safe_asset_path(arguments.get("scenePath"), suffix=".unity"),
        "avatarPath": _safe_hierarchy_path(arguments.get("avatarPath"), label="avatarPath"),
    }
    if operation_kind == "game_object":
        target_path = _safe_hierarchy_path(
            arguments.get("targetObjectPath"),
            label="targetObjectPath",
        )
        avatar_path = normalized["avatarPath"]
        if target_path == avatar_path or not target_path.startswith(f"{avatar_path}/"):
            raise AtomicReferenceRenameError("The object target must be below the selected avatar.")
        new_name = _object_name(arguments.get("newName"))
        if target_path.rsplit("/", 1)[-1] == new_name:
            raise AtomicReferenceRenameError("The object name is unchanged.")
        normalized.update({"targetObjectPath": target_path, "newName": new_name})
        return normalized

    old_name = _parameter_name(arguments.get("oldParameterName"), label="oldParameterName")
    new_name = _parameter_name(arguments.get("newParameterName"), label="newParameterName")
    if old_name == new_name:
        raise AtomicReferenceRenameError("The parameter name is unchanged.")
    normalized.update({"oldParameterName": old_name, "newParameterName": new_name})
    return normalized


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict):
        raise AtomicReferenceRenameError("Atomic rename wrapper arguments are required.")
    if wrapper_arguments.get("toolName", TOOL_NAME) != TOOL_NAME:
        raise AtomicReferenceRenameError("Atomic rename tool name is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    requested = normalize_request(_without_expected(nested))
    project_path = _canonical_project_path(wrapper_arguments.get("projectPath"))

    result = _require_dict(payload, "preview result")
    if set(result) != _PREVIEW_RESULT_KEYS or result.get("schema") != RESULT_SCHEMA:
        raise AtomicReferenceRenameError("Atomic rename preview result shape is invalid.")
    for key, expected in (("ok", True), ("preview", True), ("verified", True)):
        if result.get(key) is not expected:
            raise AtomicReferenceRenameError(f"Atomic rename preview {key} is invalid.")
    for key in ("changed", "saved"):
        if _strict_bool(result.get(key), label=key):
            raise AtomicReferenceRenameError(f"Atomic rename preview reported {key}.")
    if _bounded_int(result.get("mutationCount"), label="mutationCount", minimum=0, maximum=0) != 0:
        raise AtomicReferenceRenameError("Atomic rename preview reported a mutation.")
    actual_project = _canonical_project_path(result.get("projectPath"))
    if os.path.normcase(actual_project) != os.path.normcase(project_path):
        raise AtomicReferenceRenameError("Atomic rename preview changed the selected project.")

    operation = _canonical_operation(result.get("operation"), requested)
    scene = _canonical_scene(result.get("scene"), requested["scenePath"])
    avatar = _canonical_avatar(result.get("avatar"), requested["avatarPath"])
    target = _canonical_target(result.get("target"), requested)
    scan = _canonical_scan(result.get("scan"))
    assets = _canonical_assets(result.get("assets"))
    references = _canonical_references(result.get("references"), requested)
    if scan["assetCount"] != len(assets):
        raise AtomicReferenceRenameError("Atomic rename asset inventory count drifted.")
    if scan["knownReferenceCount"] != len(references):
        raise AtomicReferenceRenameError("Atomic rename reference count drifted.")
    if scan["unknownReferenceCount"] or scan["unresolvedReferenceCount"]:
        raise AtomicReferenceRenameError("Atomic rename preview contains unresolved references.")
    if sum(asset["mutationCount"] for asset in assets) != len(references):
        raise AtomicReferenceRenameError("Atomic rename mutation inventory drifted.")
    reference_counts_by_asset: dict[str, int] = {}
    for reference in references:
        asset_path = reference["assetPath"]
        reference_counts_by_asset[asset_path] = reference_counts_by_asset.get(asset_path, 0) + 1
    asset_counts = {asset["assetPath"]: asset["mutationCount"] for asset in assets}
    if asset_counts != reference_counts_by_asset:
        raise AtomicReferenceRenameError("Atomic rename asset coverage drifted.")

    before_digest = _lower_hex(
        result.get("beforeStateDigest"),
        label="beforeStateDigest",
        pattern=_DIGEST_PATTERN,
    )
    target_digest = _lower_hex(
        result.get("targetStateDigest"),
        label="targetStateDigest",
        pattern=_DIGEST_PATTERN,
    )
    if before_digest == target_digest:
        raise AtomicReferenceRenameError("Atomic rename target state is unchanged.")
    if result.get("planDigestSchema") != PLAN_DIGEST_SCHEMA:
        raise AtomicReferenceRenameError("Atomic rename plan digest schema is invalid.")
    plan_digest = _lower_hex(
        result.get("planDigest"),
        label="planDigest",
        pattern=_DIGEST_PATTERN,
    )
    canonical_plan = {
        "operation": operation,
        "scene": scene,
        "avatar": avatar,
        "target": target,
        "scan": scan,
        "assets": assets,
        "references": references,
        "beforeStateDigest": before_digest,
        "targetStateDigest": target_digest,
    }
    _validate_plan_coverage(canonical_plan)
    if plan_digest != compute_plan_digest(canonical_plan):
        raise AtomicReferenceRenameError("Atomic rename plan digest is invalid.")

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
            "expectedAvatarObjectId": avatar["objectId"],
            "expectedTargetIdentityDigest": target["identityDigest"],
            "expectedAssemblySetDigest": scan["assemblySetDigest"],
            "expectedAssetInventoryDigest": scan["assetInventoryDigest"],
            "expectedBeforeStateDigest": before_digest,
            "expectedTargetStateDigest": target_digest,
            "expectedPlanDigest": plan_digest,
        }
    )
    canonical_wrapper = {
        "projectPath": project_path,
        "toolName": TOOL_NAME,
        "arguments": canonical_nested,
    }

    approval = {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "toolName": TOOL_NAME,
        "projectPath": project_path,
        "operation": operation,
        "target": target,
        "scene": scene,
        "avatar": avatar,
        "scan": scan,
        "assets": deepcopy(assets),
        "assetCount": len(assets),
        "referenceCount": len(references),
        "referenceCountsByKind": _reference_counts(references),
        "beforeStateDigest": before_digest,
        "afterStateDigest": target_digest,
        "planDigest": plan_digest,
        "mutationCount": len(references),
        "rollbackRequired": True,
    }
    return canonical_wrapper, approval


def validate_apply_result(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(wrapper_arguments, dict):
        raise AtomicReferenceRenameError("Atomic rename wrapper arguments are required.")
    if wrapper_arguments.get("toolName") != TOOL_NAME:
        raise AtomicReferenceRenameError("Atomic rename tool name is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        raise AtomicReferenceRenameError("Atomic rename apply arguments are required.")
    requested = normalize_request(_without_expected(nested))
    if nested.get("preview") is not False or nested.get("saveScene") is not True:
        raise AtomicReferenceRenameError("Atomic rename apply controls are invalid.")
    project_path = _canonical_project_path(wrapper_arguments.get("projectPath"))
    expected_project = _canonical_project_path(nested.get("expectedProjectPath"))
    if os.path.normcase(project_path) != os.path.normcase(expected_project):
        raise AtomicReferenceRenameError("Atomic rename apply project binding is invalid.")

    expected = {
        "sceneGuid": _lower_hex(
            nested.get("expectedSceneGuid"), label="expectedSceneGuid", pattern=_GUID_PATTERN
        ),
        "sceneHandle": _nonzero_int32(
            nested.get("expectedSceneHandle"), label="expectedSceneHandle"
        ),
        "sceneFileDigest": _lower_hex(
            nested.get("expectedSceneFileDigest"),
            label="expectedSceneFileDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneFileIdentity": _lower_hex(
            nested.get("expectedSceneFileIdentity"),
            label="expectedSceneFileIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneMetaDigest": _lower_hex(
            nested.get("expectedSceneMetaDigest"),
            label="expectedSceneMetaDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneMetaIdentity": _lower_hex(
            nested.get("expectedSceneMetaIdentity"),
            label="expectedSceneMetaIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "avatarObjectId": _global_object_id(
            nested.get("expectedAvatarObjectId"), label="expectedAvatarObjectId"
        ),
        "targetIdentityDigest": _lower_hex(
            nested.get("expectedTargetIdentityDigest"),
            label="expectedTargetIdentityDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "assemblySetDigest": _lower_hex(
            nested.get("expectedAssemblySetDigest"),
            label="expectedAssemblySetDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "assetInventoryDigest": _lower_hex(
            nested.get("expectedAssetInventoryDigest"),
            label="expectedAssetInventoryDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "beforeStateDigest": _lower_hex(
            nested.get("expectedBeforeStateDigest"),
            label="expectedBeforeStateDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "targetStateDigest": _lower_hex(
            nested.get("expectedTargetStateDigest"),
            label="expectedTargetStateDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "planDigest": _lower_hex(
            nested.get("expectedPlanDigest"),
            label="expectedPlanDigest",
            pattern=_DIGEST_PATTERN,
        ),
    }

    result = _require_dict(payload, "apply result")
    result_keys = {
        "schema",
        "ok",
        "preview",
        "verified",
        "changed",
        "saved",
        "mutationCount",
        "projectPath",
        "operation",
        "scene",
        "avatar",
        "target",
        "references",
        "approvedPlan",
        "beforeStateDigest",
        "targetStateDigest",
        "planDigestSchema",
        "planDigest",
        "readback",
        "readbackExact",
        "checkpointRestoreRequired",
    }
    if set(result) != result_keys or result.get("schema") != RESULT_SCHEMA:
        raise AtomicReferenceRenameError("Atomic rename apply result shape is invalid.")
    for key, value in (
        ("ok", True),
        ("preview", False),
        ("verified", True),
        ("changed", True),
        ("saved", True),
        ("readbackExact", True),
        ("checkpointRestoreRequired", False),
    ):
        if _strict_bool(result.get(key), label=key) is not value:
            raise AtomicReferenceRenameError(f"Atomic rename apply {key} is invalid.")
    actual_project = _canonical_project_path(result.get("projectPath"))
    if os.path.normcase(actual_project) != os.path.normcase(project_path):
        raise AtomicReferenceRenameError("Atomic rename apply changed the selected project.")

    approved_plan = _canonical_result_plan(result.get("approvedPlan"), requested)
    _validate_plan_coverage(approved_plan)
    if approved_plan["scene"]["guid"] != expected["sceneGuid"]:
        raise AtomicReferenceRenameError("Atomic rename approved scene GUID drifted.")
    if approved_plan["scene"]["handle"] != expected["sceneHandle"]:
        raise AtomicReferenceRenameError("Atomic rename approved scene handle drifted.")
    for key, plan_key in (
        ("sceneFileDigest", "fileDigestBefore"),
        ("sceneFileIdentity", "fileIdentity"),
        ("sceneMetaDigest", "metaDigestBefore"),
        ("sceneMetaIdentity", "metaIdentity"),
    ):
        if approved_plan["scene"][plan_key] != expected[key]:
            raise AtomicReferenceRenameError("Atomic rename approved scene evidence drifted.")
    if approved_plan["avatar"]["objectId"] != expected["avatarObjectId"]:
        raise AtomicReferenceRenameError("Atomic rename approved avatar drifted.")
    if approved_plan["target"]["identityDigest"] != expected["targetIdentityDigest"]:
        raise AtomicReferenceRenameError("Atomic rename approved target drifted.")
    if approved_plan["scan"]["assemblySetDigest"] != expected["assemblySetDigest"]:
        raise AtomicReferenceRenameError("Atomic rename approved assembly set drifted.")
    if approved_plan["scan"]["assetInventoryDigest"] != expected["assetInventoryDigest"]:
        raise AtomicReferenceRenameError("Atomic rename approved asset inventory drifted.")
    if approved_plan["beforeStateDigest"] != expected["beforeStateDigest"]:
        raise AtomicReferenceRenameError("Atomic rename approved before-state drifted.")
    if approved_plan["targetStateDigest"] != expected["targetStateDigest"]:
        raise AtomicReferenceRenameError("Atomic rename approved target-state drifted.")
    if compute_plan_digest(approved_plan) != expected["planDigest"]:
        raise AtomicReferenceRenameError("Atomic rename approved plan digest is invalid.")

    operation = _canonical_operation(result.get("operation"), requested)
    avatar = _canonical_avatar(result.get("avatar"), requested["avatarPath"])
    target = _canonical_target(result.get("target"), requested)
    if operation != approved_plan["operation"] or avatar != approved_plan["avatar"]:
        raise AtomicReferenceRenameError("Atomic rename apply identity projection drifted.")
    if target != approved_plan["target"]:
        raise AtomicReferenceRenameError("Atomic rename apply target projection drifted.")
    references = _canonical_references(result.get("references"), requested)
    if references != approved_plan["references"]:
        raise AtomicReferenceRenameError("Atomic rename apply reference projection drifted.")
    mutation_count = _bounded_int(
        result.get("mutationCount"),
        label="mutationCount",
        minimum=1,
        maximum=_MAX_REFERENCES,
    )
    if mutation_count != len(references):
        raise AtomicReferenceRenameError("Atomic rename apply mutation count drifted.")
    before_digest = _lower_hex(
        result.get("beforeStateDigest"), label="beforeStateDigest", pattern=_DIGEST_PATTERN
    )
    target_digest = _lower_hex(
        result.get("targetStateDigest"), label="targetStateDigest", pattern=_DIGEST_PATTERN
    )
    plan_digest = _lower_hex(
        result.get("planDigest"), label="planDigest", pattern=_DIGEST_PATTERN
    )
    if (
        before_digest != expected["beforeStateDigest"]
        or target_digest != expected["targetStateDigest"]
        or result.get("planDigestSchema") != PLAN_DIGEST_SCHEMA
        or plan_digest != expected["planDigest"]
    ):
        raise AtomicReferenceRenameError("Atomic rename apply approval digest drifted.")

    scene_will_change = any(
        asset["assetPath"] == requested["scenePath"] for asset in approved_plan["assets"]
    )
    scene = _canonical_apply_scene(result.get("scene"), requested["scenePath"], expected)
    if scene_will_change:
        approved_scene_asset = next(
            asset
            for asset in approved_plan["assets"]
            if asset["assetPath"] == requested["scenePath"]
        )
        if scene["fileDigestAfter"] != approved_scene_asset["targetFileDigest"]:
            raise AtomicReferenceRenameError(
                "Atomic rename apply scene does not match its exact target bytes."
            )
    elif (
        scene["fileDigestAfter"] != scene["fileDigestBefore"]
        or scene["fileIdentityAfter"] != scene["fileIdentityBefore"]
    ):
        raise AtomicReferenceRenameError("Atomic rename apply changed an out-of-plan scene.")

    readback = _canonical_apply_readback(
        result.get("readback"),
        requested=requested,
        approved=approved_plan,
        expected=expected,
        apply_scene=scene,
    )
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "mutationCount": mutation_count,
        "projectPath": actual_project,
        "operation": operation,
        "scene": scene,
        "avatar": avatar,
        "target": target,
        "references": references,
        "approvedPlan": approved_plan,
        "beforeStateDigest": before_digest,
        "targetStateDigest": target_digest,
        "planDigestSchema": PLAN_DIGEST_SCHEMA,
        "planDigest": plan_digest,
        "readback": readback,
        "readbackExact": True,
        "checkpointRestoreRequired": False,
    }


def validate_authoritative_apply_result(
    approved_arguments: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    nested = _require_dict(approved_arguments, "approved arguments")
    project_path = _canonical_project_path(nested.get("expectedProjectPath"))
    return validate_apply_result(
        {
            "projectPath": project_path,
            "toolName": TOOL_NAME,
            "arguments": deepcopy(nested),
        },
        payload,
    )


def compute_plan_digest(plan: dict[str, Any]) -> str:
    payload = {"schema": PLAN_DIGEST_SCHEMA, **deepcopy(plan)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical_result_plan(value: Any, requested: dict[str, Any]) -> dict[str, Any]:
    plan = _require_dict(value, "atomic rename plan")
    expected_keys = {
        "operation",
        "scene",
        "avatar",
        "target",
        "scan",
        "assets",
        "references",
        "beforeStateDigest",
        "targetStateDigest",
    }
    if set(plan) != expected_keys:
        raise AtomicReferenceRenameError("Atomic rename plan shape is invalid.")
    return {
        "operation": _canonical_operation(plan.get("operation"), requested),
        "scene": _canonical_scene(plan.get("scene"), requested["scenePath"]),
        "avatar": _canonical_avatar(plan.get("avatar"), requested["avatarPath"]),
        "target": _canonical_target(plan.get("target"), requested),
        "scan": _canonical_scan(plan.get("scan")),
        "assets": _canonical_assets(plan.get("assets")),
        "references": _canonical_references(plan.get("references"), requested),
        "beforeStateDigest": _lower_hex(
            plan.get("beforeStateDigest"),
            label="beforeStateDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "targetStateDigest": _lower_hex(
            plan.get("targetStateDigest"),
            label="targetStateDigest",
            pattern=_DIGEST_PATTERN,
        ),
    }


def _validate_plan_coverage(plan: dict[str, Any]) -> None:
    scan = plan["scan"]
    assets = plan["assets"]
    references = plan["references"]
    if plan["beforeStateDigest"] == plan["targetStateDigest"]:
        raise AtomicReferenceRenameError("Atomic rename target state is unchanged.")
    if scan["assetCount"] != len(assets):
        raise AtomicReferenceRenameError("Atomic rename asset inventory count drifted.")
    if scan["knownReferenceCount"] != len(references):
        raise AtomicReferenceRenameError("Atomic rename reference count drifted.")
    if scan["unknownReferenceCount"] or scan["unresolvedReferenceCount"]:
        raise AtomicReferenceRenameError("Atomic rename plan contains unresolved references.")
    if sum(asset["mutationCount"] for asset in assets) != len(references):
        raise AtomicReferenceRenameError("Atomic rename mutation inventory drifted.")
    expected_counts: dict[str, int] = {}
    for reference in references:
        path = reference["assetPath"]
        expected_counts[path] = expected_counts.get(path, 0) + 1
    actual_counts = {asset["assetPath"]: asset["mutationCount"] for asset in assets}
    if actual_counts != expected_counts:
        raise AtomicReferenceRenameError("Atomic rename asset coverage drifted.")
    operation = plan["operation"]
    if operation["kind"] == "game_object":
        before_token = operation["before"].rsplit("/", 1)[-1]
        after_token = operation["after"].rsplit("/", 1)[-1]
    else:
        before_token = operation["before"]
        after_token = operation["after"]
    byte_delta = len(after_token.encode("utf-8")) - len(before_token.encode("utf-8"))
    for asset in assets:
        expected_target_length = (
            asset["fileLength"] + byte_delta * asset["rawReplacementCount"]
        )
        if asset["targetFileLength"] != expected_target_length:
            raise AtomicReferenceRenameError(
                "Atomic rename asset target length does not match its exact replacement plan."
            )
    _validate_target_evidence(plan)
    if plan["beforeStateDigest"] != _compute_state_digest(plan, after=False):
        raise AtomicReferenceRenameError("Atomic rename before-state evidence is invalid.")
    if plan["targetStateDigest"] != _compute_state_digest(plan, after=True):
        raise AtomicReferenceRenameError("Atomic rename target-state evidence is invalid.")


def _validate_target_evidence(plan: dict[str, Any]) -> None:
    operation = plan["operation"]
    target = plan["target"]
    references = plan["references"]
    if operation["kind"] == "game_object":
        definitions = [reference for reference in references if reference["kind"] == "hierarchy_object"]
        if len(definitions) != 1:
            raise AtomicReferenceRenameError("Atomic rename object target coverage is invalid.")
        definition = definitions[0]
        if (
            definition["assetPath"] != plan["scene"]["path"]
            or definition["objectId"] != target["objectId"]
            or definition["propertyPath"] != "m_Name"
        ):
            raise AtomicReferenceRenameError("Atomic rename object target evidence drifted.")
        expected_identity = _sha256_fields(
            "vrcforge.atomic_object_target.v1",
            plan["scene"]["guid"],
            plan["scene"]["fileIdentity"],
            target["objectId"],
            target["parentObjectId"],
            target["objectPath"],
            target["newObjectPath"],
        )
    else:
        definitions = [
            reference for reference in references if reference["kind"] == "expression_parameter"
        ]
        if len(definitions) != 1:
            raise AtomicReferenceRenameError("Atomic rename parameter target coverage is invalid.")
        definition = definitions[0]
        definition_assets = [
            asset for asset in plan["assets"] if asset["assetPath"] == definition["assetPath"]
        ]
        if (
            len(definition_assets) != 1
            or definition_assets[0]["assetGuid"] != target["definitionAssetGuid"]
        ):
            raise AtomicReferenceRenameError("Atomic rename parameter target evidence drifted.")
        expected_identity = _sha256_fields(
            "vrcforge.atomic_parameter_target.v1",
            target["definitionAssetGuid"],
            definition["objectId"],
            target["oldParameterName"],
            target["newParameterName"],
        )
    if target["identityDigest"] != expected_identity:
        raise AtomicReferenceRenameError("Atomic rename target identity is invalid.")


def _compute_state_digest(plan: dict[str, Any], *, after: bool) -> str:
    fields = [
        "vrcforge.atomic_reference_state.v1",
        plan["operation"]["kind"],
        plan["scene"]["guid"],
        plan["scene"]["fileIdentity"],
        plan["avatar"]["objectId"],
        plan["target"]["identityDigest"],
        plan["scan"]["assemblySetDigest"],
        plan["scan"]["assetInventoryDigest"],
    ]
    for reference in plan["references"]:
        fields.extend(
            [
                reference["kind"],
                reference["assetPath"],
                reference["objectId"],
                reference["propertyPath"],
                reference["after"] if after else reference["before"],
            ]
        )
    return _sha256_fields(*fields)


def _sha256_fields(*fields: str) -> str:
    value = "".join(f"{len(field.encode('utf-16-le')) // 2}:{field}" for field in fields)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_apply_scene(
    value: Any,
    requested_path: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    scene = _require_dict(value, "apply scene receipt")
    expected_keys = {
        "path",
        "guid",
        "handle",
        "fileDigestBefore",
        "fileDigestAfter",
        "fileIdentityBefore",
        "fileIdentityAfter",
        "metaDigestBefore",
        "metaDigestAfter",
        "metaIdentityBefore",
        "metaIdentityAfter",
        "dirtyBefore",
        "dirtyAfter",
    }
    if set(scene) != expected_keys:
        raise AtomicReferenceRenameError("Atomic rename apply scene receipt shape is invalid.")
    canonical = {
        "path": _safe_asset_path(scene.get("path"), suffix=".unity"),
        "guid": _lower_hex(scene.get("guid"), label="scene guid", pattern=_GUID_PATTERN),
        "handle": _nonzero_int32(scene.get("handle"), label="scene handle"),
        "fileDigestBefore": _lower_hex(
            scene.get("fileDigestBefore"), label="scene file digest", pattern=_DIGEST_PATTERN
        ),
        "fileDigestAfter": _lower_hex(
            scene.get("fileDigestAfter"), label="scene file digest", pattern=_DIGEST_PATTERN
        ),
        "fileIdentityBefore": _lower_hex(
            scene.get("fileIdentityBefore"), label="scene file identity", pattern=_DIGEST_PATTERN
        ),
        "fileIdentityAfter": _lower_hex(
            scene.get("fileIdentityAfter"), label="scene file identity", pattern=_DIGEST_PATTERN
        ),
        "metaDigestBefore": _lower_hex(
            scene.get("metaDigestBefore"), label="scene meta digest", pattern=_DIGEST_PATTERN
        ),
        "metaDigestAfter": _lower_hex(
            scene.get("metaDigestAfter"), label="scene meta digest", pattern=_DIGEST_PATTERN
        ),
        "metaIdentityBefore": _lower_hex(
            scene.get("metaIdentityBefore"), label="scene meta identity", pattern=_DIGEST_PATTERN
        ),
        "metaIdentityAfter": _lower_hex(
            scene.get("metaIdentityAfter"), label="scene meta identity", pattern=_DIGEST_PATTERN
        ),
        "dirtyBefore": _strict_bool(scene.get("dirtyBefore"), label="dirtyBefore"),
        "dirtyAfter": _strict_bool(scene.get("dirtyAfter"), label="dirtyAfter"),
    }
    if canonical["path"] != requested_path:
        raise AtomicReferenceRenameError("Atomic rename apply scene path drifted.")
    if canonical["guid"] != expected["sceneGuid"] or canonical["handle"] != expected["sceneHandle"]:
        raise AtomicReferenceRenameError("Atomic rename apply scene identity drifted.")
    if (
        canonical["fileDigestBefore"] != expected["sceneFileDigest"]
        or canonical["fileIdentityBefore"] != expected["sceneFileIdentity"]
        or canonical["metaDigestBefore"] != expected["sceneMetaDigest"]
        or canonical["metaDigestAfter"] != expected["sceneMetaDigest"]
        or canonical["metaIdentityBefore"] != expected["sceneMetaIdentity"]
        or canonical["metaIdentityAfter"] != expected["sceneMetaIdentity"]
        or canonical["dirtyBefore"]
        or canonical["dirtyAfter"]
    ):
        raise AtomicReferenceRenameError("Atomic rename apply scene evidence drifted.")
    return canonical


def _reverse_request(requested: dict[str, Any]) -> dict[str, Any]:
    if requested["operationKind"] == "game_object":
        return normalize_request(
            {
                "operationKind": "game_object",
                "scenePath": requested["scenePath"],
                "avatarPath": requested["avatarPath"],
                "targetObjectPath": _renamed_object_path(
                    requested["targetObjectPath"], requested["newName"]
                ),
                "newName": requested["targetObjectPath"].rsplit("/", 1)[-1],
            }
        )
    return normalize_request(
        {
            "operationKind": "parameter",
            "scenePath": requested["scenePath"],
            "avatarPath": requested["avatarPath"],
            "oldParameterName": requested["newParameterName"],
            "newParameterName": requested["oldParameterName"],
        }
    )


def _canonical_apply_readback(
    value: Any,
    *,
    requested: dict[str, Any],
    approved: dict[str, Any],
    expected: dict[str, Any],
    apply_scene: dict[str, Any],
) -> dict[str, Any]:
    readback = _require_dict(value, "apply readback")
    expected_keys = {
        "operation",
        "scene",
        "avatar",
        "target",
        "scan",
        "assets",
        "references",
        "beforeStateDigest",
        "targetStateDigest",
        "planDigestSchema",
        "planDigest",
    }
    if set(readback) != expected_keys:
        raise AtomicReferenceRenameError("Atomic rename apply readback shape is invalid.")
    reverse_requested = _reverse_request(requested)
    canonical_plan = _canonical_result_plan(
        {key: readback[key] for key in expected_keys if key not in {"planDigestSchema", "planDigest"}},
        reverse_requested,
    )
    _validate_plan_coverage(canonical_plan)
    if readback.get("planDigestSchema") != PLAN_DIGEST_SCHEMA:
        raise AtomicReferenceRenameError("Atomic rename readback plan schema is invalid.")
    readback_digest = _lower_hex(
        readback.get("planDigest"), label="readback planDigest", pattern=_DIGEST_PATTERN
    )
    if readback_digest != compute_plan_digest(canonical_plan):
        raise AtomicReferenceRenameError("Atomic rename readback plan digest is invalid.")
    if canonical_plan["scan"]["assemblySetDigest"] != expected["assemblySetDigest"]:
        raise AtomicReferenceRenameError("Atomic rename readback assembly set drifted.")
    if canonical_plan["scan"]["assetInventoryDigest"] == expected["assetInventoryDigest"]:
        raise AtomicReferenceRenameError("Atomic rename readback asset inventory did not change.")
    if canonical_plan["scan"]["objectCount"] != approved["scan"]["objectCount"]:
        raise AtomicReferenceRenameError("Atomic rename readback object inventory drifted.")
    if canonical_plan["avatar"] != approved["avatar"]:
        raise AtomicReferenceRenameError("Atomic rename readback avatar drifted.")
    if requested["operationKind"] == "game_object":
        stable_target_keys = ("objectId", "parentObjectId")
    else:
        stable_target_keys = ("definitionAssetGuid",)
    if any(
        canonical_plan["target"][key] != approved["target"][key]
        for key in stable_target_keys
    ):
        raise AtomicReferenceRenameError("Atomic rename readback target identity drifted.")

    approved_references = approved["references"]
    reverse_references = canonical_plan["references"]
    if len(approved_references) != len(reverse_references):
        raise AtomicReferenceRenameError("Atomic rename readback reference count drifted.")
    for before, after in zip(approved_references, reverse_references, strict=True):
        for key in ("kind", "assetPath", "objectId", "propertyPath"):
            if before[key] != after[key]:
                raise AtomicReferenceRenameError("Atomic rename readback reference identity drifted.")
        if before["before"] != after["after"] or before["after"] != after["before"]:
            raise AtomicReferenceRenameError("Atomic rename readback reference value drifted.")

    approved_assets = approved["assets"]
    reverse_assets = canonical_plan["assets"]
    if len(approved_assets) != len(reverse_assets):
        raise AtomicReferenceRenameError("Atomic rename readback asset count drifted.")
    for before, after in zip(approved_assets, reverse_assets, strict=True):
        for key in (
            "assetPath",
            "assetGuid",
            "metaDigest",
            "mutationCount",
            "rawReplacementCount",
        ):
            if before[key] != after[key]:
                raise AtomicReferenceRenameError("Atomic rename readback asset identity drifted.")
        if (
            after["fileDigest"] != before["targetFileDigest"]
            or after["fileLength"] != before["targetFileLength"]
            or after["targetFileDigest"] != before["fileDigest"]
            or after["targetFileLength"] != before["fileLength"]
        ):
            raise AtomicReferenceRenameError(
                "Atomic rename readback asset bytes do not match the exact projection."
            )

    scene = canonical_plan["scene"]
    if (
        scene["guid"] != apply_scene["guid"]
        or scene["handle"] != apply_scene["handle"]
        or scene["fileDigestBefore"] != apply_scene["fileDigestAfter"]
        or scene["fileIdentity"] != apply_scene["fileIdentityAfter"]
        or scene["metaDigestBefore"] != apply_scene["metaDigestAfter"]
        or scene["metaIdentity"] != apply_scene["metaIdentityAfter"]
    ):
        raise AtomicReferenceRenameError("Atomic rename readback scene evidence drifted.")
    return {
        **canonical_plan,
        "planDigestSchema": PLAN_DIGEST_SCHEMA,
        "planDigest": readback_digest,
    }


def _canonical_operation(value: Any, requested: dict[str, Any]) -> dict[str, str]:
    operation = _require_dict(value, "operation")
    if set(operation) != {"kind", "before", "after"}:
        raise AtomicReferenceRenameError("Atomic rename operation shape is invalid.")
    kind = _choice(operation.get("kind"), label="operation kind", choices={"game_object", "parameter"})
    before = _bounded_text(operation.get("before"), label="operation before", maximum=512)
    after = _bounded_text(operation.get("after"), label="operation after", maximum=512)
    if kind != requested["operationKind"]:
        raise AtomicReferenceRenameError("Atomic rename operation kind drifted.")
    if kind == "game_object":
        expected_before = requested["targetObjectPath"]
        expected_after = _renamed_object_path(expected_before, requested["newName"])
    else:
        expected_before = requested["oldParameterName"]
        expected_after = requested["newParameterName"]
    if before != expected_before or after != expected_after:
        raise AtomicReferenceRenameError("Atomic rename operation values drifted.")
    return {"kind": kind, "before": before, "after": after}


def _canonical_scene(value: Any, requested_path: str) -> dict[str, Any]:
    scene = _require_dict(value, "scene")
    expected_keys = {
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
    if set(scene) != expected_keys:
        raise AtomicReferenceRenameError("Atomic rename scene receipt shape is invalid.")
    canonical = {
        "path": _safe_asset_path(scene.get("path"), suffix=".unity"),
        "guid": _lower_hex(scene.get("guid"), label="scene guid", pattern=_GUID_PATTERN),
        "handle": _nonzero_int32(scene.get("handle"), label="scene handle"),
        "fileDigestBefore": _lower_hex(scene.get("fileDigestBefore"), label="scene file digest", pattern=_DIGEST_PATTERN),
        "fileDigestAfter": _lower_hex(scene.get("fileDigestAfter"), label="scene file digest", pattern=_DIGEST_PATTERN),
        "fileIdentity": _lower_hex(scene.get("fileIdentity"), label="scene file identity", pattern=_DIGEST_PATTERN),
        "metaDigestBefore": _lower_hex(scene.get("metaDigestBefore"), label="scene meta digest", pattern=_DIGEST_PATTERN),
        "metaDigestAfter": _lower_hex(scene.get("metaDigestAfter"), label="scene meta digest", pattern=_DIGEST_PATTERN),
        "metaIdentity": _lower_hex(scene.get("metaIdentity"), label="scene meta identity", pattern=_DIGEST_PATTERN),
        "dirtyBefore": _strict_bool(scene.get("dirtyBefore"), label="scene dirtyBefore"),
        "dirtyAfter": _strict_bool(scene.get("dirtyAfter"), label="scene dirtyAfter"),
    }
    if canonical["path"] != requested_path:
        raise AtomicReferenceRenameError("Atomic rename scene path drifted.")
    if canonical["fileDigestBefore"] != canonical["fileDigestAfter"]:
        raise AtomicReferenceRenameError("Atomic rename preview changed the scene file.")
    if canonical["metaDigestBefore"] != canonical["metaDigestAfter"]:
        raise AtomicReferenceRenameError("Atomic rename preview changed scene metadata.")
    if canonical["dirtyBefore"] or canonical["dirtyAfter"]:
        raise AtomicReferenceRenameError("Atomic rename requires a clean saved scene.")
    return canonical


def _canonical_avatar(value: Any, requested_path: str) -> dict[str, str]:
    avatar = _require_dict(value, "avatar")
    if set(avatar) != {"path", "objectId", "descriptorType"}:
        raise AtomicReferenceRenameError("Atomic rename avatar receipt shape is invalid.")
    path = _safe_hierarchy_path(avatar.get("path"), label="avatar path")
    if path != requested_path:
        raise AtomicReferenceRenameError("Atomic rename avatar path drifted.")
    descriptor_type = _bounded_text(avatar.get("descriptorType"), label="descriptor type", maximum=256)
    if descriptor_type != "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor":
        raise AtomicReferenceRenameError("Atomic rename avatar descriptor type is invalid.")
    return {
        "path": path,
        "objectId": _global_object_id(avatar.get("objectId"), label="avatar object id"),
        "descriptorType": descriptor_type,
    }


def _canonical_target(value: Any, requested: dict[str, Any]) -> dict[str, str]:
    target = _require_dict(value, "target")
    kind = requested["operationKind"]
    if kind == "game_object":
        expected_keys = {"kind", "objectPath", "objectId", "parentObjectId", "newObjectPath", "identityDigest"}
    else:
        expected_keys = {"kind", "oldParameterName", "newParameterName", "definitionAssetGuid", "identityDigest"}
    if set(target) != expected_keys or target.get("kind") != kind:
        raise AtomicReferenceRenameError("Atomic rename target receipt shape is invalid.")
    identity = _lower_hex(target.get("identityDigest"), label="target identity", pattern=_DIGEST_PATTERN)
    if kind == "game_object":
        object_path = _safe_hierarchy_path(target.get("objectPath"), label="target object path")
        new_path = _safe_hierarchy_path(target.get("newObjectPath"), label="new object path")
        if object_path != requested["targetObjectPath"] or new_path != _renamed_object_path(object_path, requested["newName"]):
            raise AtomicReferenceRenameError("Atomic rename object target drifted.")
        return {
            "kind": kind,
            "objectPath": object_path,
            "objectId": _global_object_id(target.get("objectId"), label="target object id"),
            "parentObjectId": _global_object_id(target.get("parentObjectId"), label="parent object id"),
            "newObjectPath": new_path,
            "identityDigest": identity,
        }
    old_name = _parameter_name(target.get("oldParameterName"), label="oldParameterName")
    new_name = _parameter_name(target.get("newParameterName"), label="newParameterName")
    if old_name != requested["oldParameterName"] or new_name != requested["newParameterName"]:
        raise AtomicReferenceRenameError("Atomic rename parameter target drifted.")
    return {
        "kind": kind,
        "oldParameterName": old_name,
        "newParameterName": new_name,
        "definitionAssetGuid": _lower_hex(target.get("definitionAssetGuid"), label="definition asset guid", pattern=_GUID_PATTERN),
        "identityDigest": identity,
    }


def _canonical_scan(value: Any) -> dict[str, Any]:
    scan = _require_dict(value, "scan")
    expected = {
        "assemblySetDigest",
        "assetInventoryDigest",
        "objectCount",
        "assetCount",
        "knownReferenceCount",
        "unknownReferenceCount",
        "unresolvedReferenceCount",
    }
    if set(scan) != expected:
        raise AtomicReferenceRenameError("Atomic rename scan receipt shape is invalid.")
    return {
        "assemblySetDigest": _lower_hex(scan.get("assemblySetDigest"), label="assembly set digest", pattern=_DIGEST_PATTERN),
        "assetInventoryDigest": _lower_hex(scan.get("assetInventoryDigest"), label="asset inventory digest", pattern=_DIGEST_PATTERN),
        "objectCount": _bounded_int(scan.get("objectCount"), label="objectCount", minimum=1, maximum=1_000_000),
        "assetCount": _bounded_int(scan.get("assetCount"), label="assetCount", minimum=1, maximum=_MAX_ASSETS),
        "knownReferenceCount": _bounded_int(scan.get("knownReferenceCount"), label="knownReferenceCount", minimum=1, maximum=_MAX_REFERENCES),
        "unknownReferenceCount": _bounded_int(scan.get("unknownReferenceCount"), label="unknownReferenceCount", minimum=0, maximum=_MAX_REFERENCES),
        "unresolvedReferenceCount": _bounded_int(scan.get("unresolvedReferenceCount"), label="unresolvedReferenceCount", minimum=0, maximum=_MAX_REFERENCES),
    }


def _canonical_assets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_ASSETS:
        raise AtomicReferenceRenameError("Atomic rename asset inventory is invalid.")
    result: list[dict[str, Any]] = []
    for raw in value:
        item = _require_dict(raw, "asset receipt")
        if set(item) != {
            "assetPath",
            "assetGuid",
            "fileDigest",
            "fileLength",
            "targetFileDigest",
            "targetFileLength",
            "metaDigest",
            "fileIdentity",
            "mutationCount",
            "rawReplacementCount",
        }:
            raise AtomicReferenceRenameError("Atomic rename asset receipt shape is invalid.")
        canonical = {
            "assetPath": _safe_asset_path(item.get("assetPath")),
            "assetGuid": _lower_hex(item.get("assetGuid"), label="asset guid", pattern=_GUID_PATTERN),
            "fileDigest": _lower_hex(item.get("fileDigest"), label="asset file digest", pattern=_DIGEST_PATTERN),
            "fileLength": _bounded_int(
                item.get("fileLength"),
                label="asset fileLength",
                minimum=1,
                maximum=_MAX_ASSET_BYTES,
            ),
            "targetFileDigest": _lower_hex(
                item.get("targetFileDigest"),
                label="asset target file digest",
                pattern=_DIGEST_PATTERN,
            ),
            "targetFileLength": _bounded_int(
                item.get("targetFileLength"),
                label="asset targetFileLength",
                minimum=1,
                maximum=_MAX_ASSET_BYTES,
            ),
            "metaDigest": _lower_hex(item.get("metaDigest"), label="asset meta digest", pattern=_DIGEST_PATTERN),
            "fileIdentity": _lower_hex(item.get("fileIdentity"), label="asset file identity", pattern=_DIGEST_PATTERN),
            "mutationCount": _bounded_int(item.get("mutationCount"), label="asset mutationCount", minimum=1, maximum=_MAX_REFERENCES),
            "rawReplacementCount": _bounded_int(
                item.get("rawReplacementCount"),
                label="asset rawReplacementCount",
                minimum=1,
                maximum=_MAX_RAW_REPLACEMENTS,
            ),
        }
        if canonical["fileDigest"] == canonical["targetFileDigest"]:
            raise AtomicReferenceRenameError("Atomic rename asset target bytes are unchanged.")
        if canonical["rawReplacementCount"] < canonical["mutationCount"]:
            raise AtomicReferenceRenameError(
                "Atomic rename raw replacement coverage is incomplete."
            )
        result.append(canonical)
    if len({item["assetPath"] for item in result}) != len(result):
        raise AtomicReferenceRenameError("Atomic rename asset inventory contains duplicates.")
    if len({item["assetGuid"] for item in result}) != len(result):
        raise AtomicReferenceRenameError("Atomic rename asset inventory contains duplicate GUIDs.")
    if len({item["fileIdentity"] for item in result}) != len(result):
        raise AtomicReferenceRenameError("Atomic rename asset inventory contains aliased files.")
    if result != sorted(result, key=lambda item: (item["assetPath"], item["assetGuid"])):
        raise AtomicReferenceRenameError("Atomic rename asset inventory must be sorted.")
    return result


def _canonical_references(value: Any, requested: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_REFERENCES:
        raise AtomicReferenceRenameError("Atomic rename reference plan is invalid.")
    expected_before = requested.get("targetObjectPath") or requested.get("oldParameterName")
    expected_after = (
        _renamed_object_path(requested["targetObjectPath"], requested["newName"])
        if requested["operationKind"] == "game_object"
        else requested["newParameterName"]
    )
    result: list[dict[str, str]] = []
    for raw in value:
        item = _require_dict(raw, "reference")
        if set(item) != {"kind", "assetPath", "objectId", "propertyPath", "before", "after"}:
            raise AtomicReferenceRenameError("Atomic rename reference shape is invalid.")
        kind = _choice(item.get("kind"), label="reference kind", choices=_REFERENCE_KINDS)
        allowed_kinds = (
            _OBJECT_REFERENCE_KINDS
            if requested["operationKind"] == "game_object"
            else _PARAMETER_REFERENCE_KINDS
        )
        if kind not in allowed_kinds:
            raise AtomicReferenceRenameError("Atomic rename reference kind does not match the operation.")
        before = _bounded_text(item.get("before"), label="reference before", maximum=512)
        after = _bounded_text(item.get("after"), label="reference after", maximum=512)
        if requested["operationKind"] == "game_object":
            if before != expected_before and not before.startswith(f"{expected_before}/"):
                raise AtomicReferenceRenameError("Atomic rename reference values drifted.")
            expected_reference_after = f"{expected_after}{before[len(expected_before):]}"
            if after != expected_reference_after:
                raise AtomicReferenceRenameError("Atomic rename reference values drifted.")
        elif before != expected_before or after != expected_after:
            raise AtomicReferenceRenameError("Atomic rename reference values drifted.")
        result.append(
            {
                "kind": kind,
                "assetPath": _safe_asset_path(item.get("assetPath")),
                "objectId": _global_object_id(item.get("objectId"), label="reference object id"),
                "propertyPath": _bounded_text(item.get("propertyPath"), label="property path", maximum=512),
                "before": before,
                "after": after,
            }
        )
    sort_key = lambda item: (item["assetPath"], item["objectId"], item["propertyPath"], item["kind"])
    if result != sorted(result, key=sort_key):
        raise AtomicReferenceRenameError("Atomic rename reference plan must be sorted.")
    if len({sort_key(item) for item in result}) != len(result):
        raise AtomicReferenceRenameError("Atomic rename reference plan contains duplicates.")
    return result


def _reference_counts(references: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reference in references:
        kind = reference["kind"]
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _without_expected(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise AtomicReferenceRenameError("Atomic rename arguments are required.")
    return {
        key: deepcopy(value)
        for key, value in arguments.items()
        if not key.startswith("expected") and key not in _CONTROL_KEYS
    }


def _canonical_project_path(value: Any) -> str:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise AtomicReferenceRenameError("projectPath must be absolute.")
    try:
        return str(path.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise AtomicReferenceRenameError("projectPath is invalid.") from exc


def _safe_asset_path(value: Any, *, suffix: str | None = None) -> str:
    text = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if (
        not text.startswith("Assets/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AtomicReferenceRenameError("Asset path is invalid.")
    if suffix and not text.lower().endswith(suffix):
        raise AtomicReferenceRenameError("Asset path type is invalid.")
    if len(text) > 512:
        raise AtomicReferenceRenameError("Asset path is too long.")
    return text


def _safe_hierarchy_path(value: Any, *, label: str) -> str:
    text = str(value or "").replace("\\", "/").strip(" /")
    parts = text.split("/") if text else []
    if not parts or len(text) > 512 or any(part in {"", ".", ".."} for part in parts):
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    if any(_OBJECT_NAME_PATTERN.fullmatch(part) is None for part in parts):
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return "/".join(parts)


def _object_name(value: Any) -> str:
    text = str(value or "").strip()
    if text in {".", ".."} or _OBJECT_NAME_PATTERN.fullmatch(text) is None:
        raise AtomicReferenceRenameError("newName is invalid.")
    return text


def _parameter_name(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if _PARAMETER_PATTERN.fullmatch(text) is None:
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return text


def _renamed_object_path(path: str, new_name: str) -> str:
    parent = path.rsplit("/", 1)[0]
    return f"{parent}/{new_name}"


def _choice(value: Any, *, label: str, choices: set[str]) -> str:
    text = str(value or "").strip().lower()
    if text not in choices:
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return text


def _bounded_text(value: Any, *, label: str, maximum: int) -> str:
    text = str(value or "")
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return text


def _global_object_id(value: Any, *, label: str) -> str:
    text = _bounded_text(value, label=label, maximum=256)
    if not text.startswith("GlobalObjectId_V1-"):
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return text


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AtomicReferenceRenameError(f"{label} must be an object.")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise AtomicReferenceRenameError(f"{label} must be a boolean.")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return value


def _nonzero_int32(value: Any, *, label: str) -> int:
    result = _bounded_int(value, label=label, minimum=-(2**31), maximum=2**31 - 1)
    if result == 0:
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return result


def _lower_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value or "").strip().lower()
    if pattern.fullmatch(text) is None:
        raise AtomicReferenceRenameError(f"{label} is invalid.")
    return text
