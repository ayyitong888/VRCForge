from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import dashboard_server
from approved_unity_execution import current_approved_unity_execution
from agent_gateway import AgentGateway
from atomic_reference_rename import (
    APPROVAL_PREVIEW_SCHEMA,
    PLAN_DIGEST_SCHEMA,
    RESULT_SCHEMA,
    TOOL_NAME,
    AtomicReferenceRenameError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_plan_digest,
    normalize_request,
    validate_authoritative_apply_result,
    validate_apply_result,
)


PROJECT_PATH = "D:/DisposableUnityProject"
SCENE_PATH = "Assets/VRCForge/Generated/AtomicRenameProbe.unity"
AVATAR_PATH = "Avatar"
OUTFIT_NAMING_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "skill-packages"
    / "outfit-naming-helper"
    / "workflows"
    / "outfit-naming-helper.json"
)


def object_request() -> dict:
    return {
        "operationKind": "game_object",
        "scenePath": SCENE_PATH,
        "avatarPath": AVATAR_PATH,
        "targetObjectPath": "Avatar/Wardrobe/OldHat",
        "newName": "NewHat",
    }


def parameter_request() -> dict:
    return {
        "operationKind": "parameter",
        "scenePath": SCENE_PATH,
        "avatarPath": AVATAR_PATH,
        "oldParameterName": "Wardrobe_OldHat",
        "newParameterName": "Wardrobe_NewHat",
    }


def wrapper(request: dict) -> dict:
    return {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "arguments": deepcopy(request),
    }


@pytest.mark.parametrize(
    "rename_request",
    [pytest.param(object_request(), id="game-object"), pytest.param(parameter_request(), id="parameter")],
)
def test_outfit_naming_workflow_resolves_to_exact_atomic_wrapper(rename_request: dict) -> None:
    workflow = json.loads(OUTFIT_NAMING_WORKFLOW.read_text(encoding="utf-8"))
    step = workflow["steps"][0]
    request = step["request"]

    assert len(workflow["steps"]) == 1
    assert step["tool"] == "vrcforge_request_apply"
    assert step["writes"] is True
    assert request["targetTool"] == "vrcforge_unity_mcp_write"
    template = deepcopy(request["argumentsByOperation"][rename_request["operationKind"]])
    assert template["toolName"] == TOOL_NAME
    assert template["projectPath"] == "$projectPath"
    for key, value in template["arguments"].items():
        if key == "operationKind":
            assert value == rename_request[key]
        else:
            assert value == f"${key}"

    resolved = deepcopy(template)
    resolved["projectPath"] = PROJECT_PATH
    resolved["arguments"] = deepcopy(rename_request)
    assert resolved == build_wrapper_arguments({"projectPath": PROJECT_PATH, **rename_request})


def test_outfit_naming_wrapper_rejects_an_unregistered_nested_tool() -> None:
    workflow = json.loads(OUTFIT_NAMING_WORKFLOW.read_text(encoding="utf-8"))
    request = workflow["steps"][0]["request"]
    assert set(request["argumentsByOperation"]) == {"game_object", "parameter"}

    result = dashboard_server.unity_mcp_write_sync(
        {
            "projectPath": PROJECT_PATH,
            "toolName": "vrc_unregistered_reference_rename",
            "arguments": object_request(),
        }
    )
    assert result["ok"] is False
    assert "static write allowlist" in result["error"]


def preview_payload(request: dict) -> dict:
    is_object = request["operationKind"] == "game_object"
    if is_object:
        operation = {
            "kind": "game_object",
            "before": "Avatar/Wardrobe/OldHat",
            "after": "Avatar/Wardrobe/NewHat",
        }
        target = {
            "kind": "game_object",
            "objectPath": "Avatar/Wardrobe/OldHat",
            "objectId": "GlobalObjectId_V1-2-100-0-0",
            "parentObjectId": "GlobalObjectId_V1-2-101-0-0",
            "newObjectPath": "Avatar/Wardrobe/NewHat",
            "identityDigest": "a" * 64,
        }
        references = [
            {
                "kind": "hierarchy_object",
                "assetPath": SCENE_PATH,
                "objectId": "GlobalObjectId_V1-2-100-0-0",
                "propertyPath": "m_Name",
                "before": "Avatar/Wardrobe/OldHat",
                "after": "Avatar/Wardrobe/NewHat",
            },
            {
                "kind": "animation_binding",
                "assetPath": "Assets/Avatar/FX/Outfits.anim",
                "objectId": "GlobalObjectId_V1-1-200-0-0",
                "propertyPath": "bindings[0].path",
                "before": "Avatar/Wardrobe/OldHat/Charm",
                "after": "Avatar/Wardrobe/NewHat/Charm",
            },
        ]
    else:
        operation = {
            "kind": "parameter",
            "before": "Wardrobe_OldHat",
            "after": "Wardrobe_NewHat",
        }
        target = {
            "kind": "parameter",
            "oldParameterName": "Wardrobe_OldHat",
            "newParameterName": "Wardrobe_NewHat",
            "definitionAssetGuid": "b" * 32,
            "identityDigest": "c" * 64,
        }
        references = [
            {
                "kind": "expression_parameter",
                "assetPath": "Assets/Avatar/Expressions/Parameters.asset",
                "objectId": "GlobalObjectId_V1-1-300-0-0",
                "propertyPath": "parameters.Array.data[0].name",
                "before": "Wardrobe_OldHat",
                "after": "Wardrobe_NewHat",
            },
            {
                "kind": "animator_condition",
                "assetPath": "Assets/Avatar/FX/Controller.controller",
                "objectId": "GlobalObjectId_V1-1-301-0-0",
                "propertyPath": "layers[0].conditions[0].parameter",
                "before": "Wardrobe_OldHat",
                "after": "Wardrobe_NewHat",
            },
        ]
    references.sort(key=lambda item: (item["assetPath"], item["objectId"], item["propertyPath"], item["kind"]))
    asset_paths = sorted({reference["assetPath"] for reference in references})
    assets = []
    for index, path in enumerate(asset_paths):
        assets.append(
            {
                "assetPath": path,
                "assetGuid": f"{index + 1:032x}",
                "fileDigest": f"{index + 1:064x}",
                "fileLength": 1024 + index,
                "targetFileDigest": f"{index + 101:064x}",
                "targetFileLength": 1024 + index,
                "metaDigest": f"{index + 11:064x}",
                "fileIdentity": f"{index + 21:064x}",
                "mutationCount": sum(1 for reference in references if reference["assetPath"] == path),
                "rawReplacementCount": sum(
                    1 for reference in references if reference["assetPath"] == path
                ),
            }
        )
    payload = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "projectPath": PROJECT_PATH,
        "operation": operation,
        "scene": {
            "path": SCENE_PATH,
            "guid": "d" * 32,
            "handle": 23,
            "fileDigestBefore": "e" * 64,
            "fileDigestAfter": "e" * 64,
            "fileIdentity": "f" * 64,
            "metaDigestBefore": "1" * 64,
            "metaDigestAfter": "1" * 64,
            "metaIdentity": "2" * 64,
            "dirtyBefore": False,
            "dirtyAfter": False,
        },
        "avatar": {
            "path": AVATAR_PATH,
            "objectId": "GlobalObjectId_V1-2-90-0-0",
            "descriptorType": "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor",
        },
        "target": target,
        "scan": {
            "assemblySetDigest": "3" * 64,
            "assetInventoryDigest": "4" * 64,
            "objectCount": 42,
            "assetCount": len(assets),
            "knownReferenceCount": len(references),
            "unknownReferenceCount": 0,
            "unresolvedReferenceCount": 0,
        },
        "assets": assets,
        "references": references,
        "beforeStateDigest": "5" * 64,
        "targetStateDigest": "6" * 64,
        "planDigestSchema": PLAN_DIGEST_SCHEMA,
    }
    _refresh_semantic_evidence(payload)
    payload["planDigest"] = compute_plan_digest(
        {
            "operation": payload["operation"],
            "scene": payload["scene"],
            "avatar": payload["avatar"],
            "target": payload["target"],
            "scan": payload["scan"],
            "assets": payload["assets"],
            "references": payload["references"],
            "beforeStateDigest": payload["beforeStateDigest"],
            "targetStateDigest": payload["targetStateDigest"],
        }
    )
    return payload


_PLAN_KEYS = (
    "operation",
    "scene",
    "avatar",
    "target",
    "scan",
    "assets",
    "references",
    "beforeStateDigest",
    "targetStateDigest",
)


def _plan_from_payload(payload: dict) -> dict:
    return {key: deepcopy(payload[key]) for key in _PLAN_KEYS}


def _recompute_readback_digest(payload: dict) -> None:
    _refresh_semantic_evidence(payload["readback"])
    payload["readback"]["planDigest"] = compute_plan_digest(
        _plan_from_payload(payload["readback"])
    )


def _sha256_fields(*fields: str) -> str:
    value = "".join(f"{len(field.encode('utf-16-le')) // 2}:{field}" for field in fields)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refresh_semantic_evidence(plan: dict) -> None:
    target = plan["target"]
    if plan["operation"]["kind"] == "game_object":
        target["identityDigest"] = _sha256_fields(
            "vrcforge.atomic_object_target.v1",
            plan["scene"]["guid"],
            plan["scene"]["fileIdentity"],
            target["objectId"],
            target["parentObjectId"],
            target["objectPath"],
            target["newObjectPath"],
        )
    else:
        definition = next(
            reference
            for reference in plan["references"]
            if reference["kind"] == "expression_parameter"
        )
        definition_asset = next(
            asset for asset in plan["assets"] if asset["assetPath"] == definition["assetPath"]
        )
        target["definitionAssetGuid"] = definition_asset["assetGuid"]
        target["identityDigest"] = _sha256_fields(
            "vrcforge.atomic_parameter_target.v1",
            target["definitionAssetGuid"],
            definition["objectId"],
            target["oldParameterName"],
            target["newParameterName"],
        )

    common = [
        "vrcforge.atomic_reference_state.v1",
        plan["operation"]["kind"],
        plan["scene"]["guid"],
        plan["scene"]["fileIdentity"],
        plan["avatar"]["objectId"],
        target["identityDigest"],
        plan["scan"]["assemblySetDigest"],
        plan["scan"]["assetInventoryDigest"],
    ]
    before_fields = list(common)
    after_fields = list(common)
    for reference in plan["references"]:
        identity = [
            reference["kind"],
            reference["assetPath"],
            reference["objectId"],
            reference["propertyPath"],
        ]
        before_fields.extend([*identity, reference["before"]])
        after_fields.extend([*identity, reference["after"]])
    plan["beforeStateDigest"] = _sha256_fields(*before_fields)
    plan["targetStateDigest"] = _sha256_fields(*after_fields)


def approved_apply_payload(request: dict) -> tuple[dict, dict]:
    preview = preview_payload(request)
    _refresh_semantic_evidence(preview)
    preview["planDigest"] = compute_plan_digest(_plan_from_payload(preview))
    approved_wrapper, _ = bind_authoritative_preview(wrapper(request), preview)
    approved = _plan_from_payload(preview)
    reverse = deepcopy(approved)
    reverse["operation"] = {
        "kind": approved["operation"]["kind"],
        "before": approved["operation"]["after"],
        "after": approved["operation"]["before"],
    }
    if request["operationKind"] == "game_object":
        reverse["target"].update(
            {
                "objectPath": approved["target"]["newObjectPath"],
                "newObjectPath": approved["target"]["objectPath"],
                "identityDigest": "7" * 64,
            }
        )
    else:
        reverse["target"].update(
            {
                "oldParameterName": approved["target"]["newParameterName"],
                "newParameterName": approved["target"]["oldParameterName"],
                "identityDigest": "7" * 64,
            }
        )
    reverse["references"] = [
        {**reference, "before": reference["after"], "after": reference["before"]}
        for reference in approved["references"]
    ]
    reverse["assets"] = [
        {
            **asset,
            "fileDigest": asset["targetFileDigest"],
            "fileLength": asset["targetFileLength"],
            "targetFileDigest": asset["fileDigest"],
            "targetFileLength": asset["fileLength"],
            "fileIdentity": f"{index + 201:064x}",
        }
        for index, asset in enumerate(approved["assets"])
    ]
    reverse["scan"]["assetInventoryDigest"] = "9" * 64
    scene_asset = next(
        (asset for asset in reverse["assets"] if asset["assetPath"] == SCENE_PATH),
        None,
    )
    scene_after_digest = (
        scene_asset["fileDigest"] if scene_asset else approved["scene"]["fileDigestBefore"]
    )
    scene_after_identity = (
        scene_asset["fileIdentity"] if scene_asset else approved["scene"]["fileIdentity"]
    )
    reverse["scene"].update(
        {
            "fileDigestBefore": scene_after_digest,
            "fileDigestAfter": scene_after_digest,
            "fileIdentity": scene_after_identity,
        }
    )
    _refresh_semantic_evidence(reverse)
    reverse["planDigestSchema"] = PLAN_DIGEST_SCHEMA
    reverse["planDigest"] = compute_plan_digest(_plan_from_payload(reverse))

    result = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "mutationCount": len(approved["references"]),
        "projectPath": approved_wrapper["projectPath"],
        "operation": deepcopy(approved["operation"]),
        "scene": {
            "path": approved["scene"]["path"],
            "guid": approved["scene"]["guid"],
            "handle": approved["scene"]["handle"],
            "fileDigestBefore": approved["scene"]["fileDigestBefore"],
            "fileDigestAfter": scene_after_digest,
            "fileIdentityBefore": approved["scene"]["fileIdentity"],
            "fileIdentityAfter": scene_after_identity,
            "metaDigestBefore": approved["scene"]["metaDigestBefore"],
            "metaDigestAfter": approved["scene"]["metaDigestBefore"],
            "metaIdentityBefore": approved["scene"]["metaIdentity"],
            "metaIdentityAfter": approved["scene"]["metaIdentity"],
            "dirtyBefore": False,
            "dirtyAfter": False,
        },
        "avatar": deepcopy(approved["avatar"]),
        "target": deepcopy(approved["target"]),
        "references": deepcopy(approved["references"]),
        "approvedPlan": approved,
        "beforeStateDigest": approved["beforeStateDigest"],
        "targetStateDigest": approved["targetStateDigest"],
        "planDigestSchema": PLAN_DIGEST_SCHEMA,
        "planDigest": preview["planDigest"],
        "readback": reverse,
        "readbackExact": True,
        "checkpointRestoreRequired": False,
    }
    return approved_wrapper, result


def _tamper_readback_reference(payload: dict) -> None:
    payload["readback"]["references"][0]["propertyPath"] += ".forged"
    _recompute_readback_digest(payload)


def _tamper_readback_asset_guid(payload: dict) -> None:
    payload["readback"]["assets"][0]["assetGuid"] = "f" * 32
    _recompute_readback_digest(payload)


def _tamper_readback_asset_digest(payload: dict) -> None:
    payload["readback"]["assets"][0]["fileDigest"] = payload["approvedPlan"]["assets"][0][
        "fileDigest"
    ]
    _recompute_readback_digest(payload)


def _tamper_readback_target_digest(payload: dict) -> None:
    payload["readback"]["assets"][0]["targetFileDigest"] = "f" * 64
    _recompute_readback_digest(payload)


def _tamper_readback_target_length(payload: dict) -> None:
    payload["readback"]["assets"][0]["targetFileLength"] += 1
    _recompute_readback_digest(payload)


def _tamper_readback_file_length(payload: dict) -> None:
    payload["readback"]["assets"][0]["fileLength"] += 1
    _recompute_readback_digest(payload)


def _tamper_readback_inventory(payload: dict) -> None:
    payload["readback"]["scan"]["assetInventoryDigest"] = payload["approvedPlan"]["scan"][
        "assetInventoryDigest"
    ]
    _recompute_readback_digest(payload)


def _tamper_readback_assembly(payload: dict) -> None:
    payload["readback"]["scan"]["assemblySetDigest"] = "f" * 64
    _recompute_readback_digest(payload)


def _tamper_readback_scene_meta(payload: dict) -> None:
    payload["readback"]["scene"]["metaDigestBefore"] = "f" * 64
    payload["readback"]["scene"]["metaDigestAfter"] = "f" * 64
    _recompute_readback_digest(payload)


def _tamper_readback_target_identity(payload: dict) -> None:
    if payload["readback"]["operation"]["kind"] == "game_object":
        payload["readback"]["target"]["objectId"] = "GlobalObjectId_V1-2-999-0-0"
    else:
        definition = next(
            reference
            for reference in payload["readback"]["references"]
            if reference["kind"] == "expression_parameter"
        )
        definition_asset = next(
            asset
            for asset in payload["readback"]["assets"]
            if asset["assetPath"] == definition["assetPath"]
        )
        definition_asset["assetGuid"] = "f" * 32
    _recompute_readback_digest(payload)


def _tamper_readback_state_digest(payload: dict) -> None:
    payload["readback"]["beforeStateDigest"] = "f" * 64
    payload["readback"]["planDigest"] = compute_plan_digest(
        _plan_from_payload(payload["readback"])
    )


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_request_schema_is_fixed_and_preview_discards_caller_preconditions(rename_case: dict) -> None:
    assert normalize_request(rename_case) == rename_case
    poisoned = {
        **rename_case,
        "preview": False,
        "saveScene": True,
        "expectedPlanDigest": "0" * 64,
        "expectedProjectPath": "D:/OtherProject",
    }

    preview = build_preview_arguments(poisoned)

    assert preview == {**rename_case, "preview": True, "saveScene": False}
    assert not any(key.startswith("expected") for key in preview)


@pytest.mark.parametrize(
    ("request_factory", "field", "value"),
    [
        (object_request, "methodName", "RenameAnything"),
        (object_request, "oldParameterName", "cross_kind"),
        (parameter_request, "targetObjectPath", "Avatar/Hat"),
        (parameter_request, "propertyPath", "arbitrary.property"),
        (parameter_request, "scanRoot", "Assets"),
    ],
)
def test_unknown_or_cross_kind_request_fields_fail_closed(request_factory, field: str, value: object) -> None:
    request = request_factory()
    request[field] = value

    with pytest.raises(AtomicReferenceRenameError, match="unsupported fields"):
        normalize_request(request)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda request: request.update(targetObjectPath="Avatar"),
        lambda request: request.update(targetObjectPath="Other/Hat"),
        lambda request: request.update(newName="../Hat"),
        lambda request: request.update(newName="OldHat"),
        lambda request: request.update(scenePath="Packages/Scene.unity"),
    ],
)
def test_object_request_rejects_ambiguous_or_out_of_scope_values(mutator) -> None:
    request = object_request()
    mutator(request)

    with pytest.raises(AtomicReferenceRenameError):
        normalize_request(request)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda request: request.update(oldParameterName="has spaces"),
        lambda request: request.update(newParameterName="a/b"),
        lambda request: request.update(newParameterName="Wardrobe_OldHat"),
        lambda request: request.update(operationKind="serialized_string"),
    ],
)
def test_parameter_request_rejects_unbounded_or_ambiguous_values(mutator) -> None:
    request = parameter_request()
    mutator(request)

    with pytest.raises(AtomicReferenceRenameError):
        normalize_request(request)


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_flat_inputs_are_normalized_into_supervised_wrapper(rename_case: dict) -> None:
    wrapped = build_wrapper_arguments(
        {
            "projectPath": PROJECT_PATH,
            **rename_case,
            "maliciousExtra": {"approved": True},
        }
    )

    assert wrapped == {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "arguments": rename_case,
    }


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_authoritative_preview_binds_complete_scan_and_plan(rename_case: dict) -> None:
    payload = preview_payload(rename_case)

    canonical, approval = bind_authoritative_preview(wrapper(rename_case), payload)

    arguments = canonical["arguments"]
    assert arguments["preview"] is False
    assert arguments["saveScene"] is True
    assert arguments["expectedSceneGuid"] == "d" * 32
    assert arguments["expectedAvatarObjectId"] == "GlobalObjectId_V1-2-90-0-0"
    assert arguments["expectedAssemblySetDigest"] == "3" * 64
    assert arguments["expectedAssetInventoryDigest"] == "4" * 64
    assert arguments["expectedBeforeStateDigest"] == payload["beforeStateDigest"]
    assert arguments["expectedTargetStateDigest"] == payload["targetStateDigest"]
    assert arguments["expectedPlanDigest"] == payload["planDigest"]
    assert approval["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert approval["mutationCount"] == 2
    assert approval["referenceCount"] == 2
    assert approval["assets"] == payload["assets"]
    assert sum(approval["referenceCountsByKind"].values()) == 2
    assert approval["rollbackRequired"] is True


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_authoritative_preview_rejects_unknown_top_level_fields(rename_case: dict) -> None:
    payload = preview_payload(rename_case)
    payload["maliciousExtra"] = {"approved": True}

    with pytest.raises(AtomicReferenceRenameError, match="result shape"):
        bind_authoritative_preview(wrapper(rename_case), payload)


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_authoritative_preview_discards_unknown_wrapper_fields(rename_case: dict) -> None:
    wrapped = {**wrapper(rename_case), "maliciousExtra": {"approved": True}}

    canonical, _ = bind_authoritative_preview(wrapped, preview_payload(rename_case))

    assert set(canonical) == {"projectPath", "toolName", "arguments"}


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update(changed=True),
        lambda payload: payload.update(mutationCount=1),
        lambda payload: payload["scene"].update(fileDigestAfter="7" * 64),
        lambda payload: payload["scene"].update(dirtyAfter=True),
        lambda payload: payload["scan"].update(unknownReferenceCount=1),
        lambda payload: payload["scan"].update(unresolvedReferenceCount=1),
        lambda payload: payload["scan"].update(assetCount=99),
        lambda payload: payload.update(planDigest="8" * 64),
    ],
)
def test_preview_mutation_unresolved_reference_or_digest_drift_fails_closed(rename_case: dict, mutator) -> None:
    payload = preview_payload(rename_case)
    mutator(payload)

    with pytest.raises(AtomicReferenceRenameError):
        bind_authoritative_preview(wrapper(rename_case), payload)


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_apply_result_binds_the_approved_plan_and_exact_reverse_readback(rename_case: dict) -> None:
    approved_wrapper, payload = approved_apply_payload(rename_case)

    result = validate_apply_result(approved_wrapper, payload)

    assert result["verified"] is True
    assert result["saved"] is True
    assert result["changed"] is True
    assert result["readbackExact"] is True
    assert result["checkpointRestoreRequired"] is False
    assert result["planDigest"] == approved_wrapper["arguments"]["expectedPlanDigest"]
    assert result["readback"]["planDigest"] == payload["readback"]["planDigest"]


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
def test_authoritative_apply_adapter_accepts_only_nested_approved_arguments(
    rename_case: dict,
) -> None:
    approved_wrapper, payload = approved_apply_payload(rename_case)

    result = validate_authoritative_apply_result(
        approved_wrapper["arguments"],
        payload,
    )

    assert result["planDigest"] == approved_wrapper["arguments"]["expectedPlanDigest"]


def test_authoritative_apply_adapter_rejects_missing_project_binding() -> None:
    approved_wrapper, payload = approved_apply_payload(parameter_request())
    arguments = deepcopy(approved_wrapper["arguments"])
    arguments.pop("expectedProjectPath")

    with pytest.raises(AtomicReferenceRenameError, match="projectPath"):
        validate_authoritative_apply_result(arguments, payload)


def test_authoritative_apply_adapter_rejects_forged_receipt() -> None:
    approved_wrapper, payload = approved_apply_payload(object_request())
    payload["readbackExact"] = False

    with pytest.raises(AtomicReferenceRenameError, match="readbackExact"):
        validate_authoritative_apply_result(approved_wrapper["arguments"], payload)


@pytest.mark.parametrize("field", ["identityDigest", "beforeStateDigest", "targetStateDigest"])
def test_preview_recomputes_semantic_evidence_before_accepting_plan_digest(field: str) -> None:
    request = object_request()
    payload = preview_payload(request)
    if field == "identityDigest":
        payload["target"][field] = "f" * 64
    else:
        payload[field] = "f" * 64
    payload["planDigest"] = compute_plan_digest(_plan_from_payload(payload))

    with pytest.raises(AtomicReferenceRenameError, match="target identity|state evidence"):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize("rename_case", [object_request(), parameter_request()])
@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(lambda payload: payload.update(verified=False), id="verified"),
        pytest.param(lambda payload: payload.update(saved=False), id="saved"),
        pytest.param(lambda payload: payload.update(changed=False), id="changed"),
        pytest.param(lambda payload: payload.update(readbackExact=False), id="readback-exact"),
        pytest.param(
            lambda payload: payload.update(checkpointRestoreRequired=True),
            id="checkpoint-restore-required",
        ),
        pytest.param(
            lambda payload: payload.update(projectPath="D:/OtherUnityProject"),
            id="project-path",
        ),
        pytest.param(
            lambda payload: payload.update(mutationCount=payload["mutationCount"] + 1),
            id="mutation-count",
        ),
        pytest.param(
            lambda payload: payload["approvedPlan"]["target"].update(identityDigest="f" * 64),
            id="approved-target",
        ),
        pytest.param(
            lambda payload: payload["scene"].update(metaDigestAfter="f" * 64),
            id="scene-meta",
        ),
        pytest.param(
            lambda payload: payload["scene"].update(fileDigestAfter="f" * 64),
            id="scene-target-bytes",
        ),
        pytest.param(
            lambda payload: payload["references"][0].update(propertyPath="forged.path"),
            id="reference-projection",
        ),
        pytest.param(lambda payload: payload.update(planDigest="f" * 64), id="plan-digest"),
        pytest.param(
            lambda payload: payload["readback"].update(planDigest="f" * 64),
            id="readback-plan-digest",
        ),
        pytest.param(_tamper_readback_reference, id="readback-reference"),
        pytest.param(_tamper_readback_asset_guid, id="readback-asset-guid"),
        pytest.param(_tamper_readback_asset_digest, id="readback-asset-digest"),
        pytest.param(_tamper_readback_target_digest, id="readback-target-digest"),
        pytest.param(_tamper_readback_target_length, id="readback-target-length"),
        pytest.param(_tamper_readback_file_length, id="readback-file-length"),
        pytest.param(_tamper_readback_inventory, id="readback-inventory"),
        pytest.param(_tamper_readback_assembly, id="readback-assembly"),
        pytest.param(_tamper_readback_scene_meta, id="readback-scene-meta"),
        pytest.param(_tamper_readback_target_identity, id="readback-target-identity"),
        pytest.param(_tamper_readback_state_digest, id="readback-state-digest"),
        pytest.param(lambda payload: payload.update(unexpected=True), id="unexpected-field"),
    ],
)
def test_apply_result_field_replacement_fails_closed(rename_case: dict, mutator) -> None:
    approved_wrapper, payload = approved_apply_payload(rename_case)
    mutator(payload)

    with pytest.raises(AtomicReferenceRenameError):
        validate_apply_result(approved_wrapper, payload)


@pytest.mark.parametrize("restored", [True, False])
def test_apply_result_never_accepts_a_failure_or_restore_receipt(restored: bool) -> None:
    approved_wrapper, _ = approved_apply_payload(object_request())
    failure = {
        "schema": RESULT_SCHEMA,
        "mutationStarted": True,
        "restored": restored,
        "cleanupVerified": restored,
        "cleanupRequired": not restored,
        "checkpointRestoreRequired": not restored,
        "operationState": "restored" if restored else "checkpoint_restore_required",
    }

    with pytest.raises(AtomicReferenceRenameError, match="shape"):
        validate_apply_result(approved_wrapper, failure)


def test_object_descendant_binding_is_prefix_migrated_but_unrelated_path_is_rejected() -> None:
    request = object_request()
    payload = preview_payload(request)
    descendant = next(item for item in payload["references"] if item["kind"] == "animation_binding")
    assert descendant["before"].endswith("OldHat/Charm")

    canonical, _ = bind_authoritative_preview(wrapper(request), payload)
    assert canonical["arguments"]["expectedPlanDigest"] == payload["planDigest"]

    descendant["before"] = "Avatar/Wardrobe/OtherHat/Charm"
    descendant["after"] = "Avatar/Wardrobe/NewHat/Charm"
    payload["planDigest"] = compute_plan_digest(
        {
            "operation": payload["operation"],
            "scene": payload["scene"],
            "avatar": payload["avatar"],
            "target": payload["target"],
            "scan": payload["scan"],
            "assets": payload["assets"],
            "references": payload["references"],
            "beforeStateDigest": payload["beforeStateDigest"],
            "targetStateDigest": payload["targetStateDigest"],
        }
    )
    with pytest.raises(AtomicReferenceRenameError, match="reference values drifted"):
        bind_authoritative_preview(wrapper(request), payload)


def test_duplicate_or_unsorted_assets_and_references_fail_closed() -> None:
    request = parameter_request()
    payload = preview_payload(request)
    payload["references"] = list(reversed(payload["references"]))
    with pytest.raises(AtomicReferenceRenameError, match="must be sorted"):
        bind_authoritative_preview(wrapper(request), payload)

    payload = preview_payload(request)
    payload["assets"].append(deepcopy(payload["assets"][0]))
    payload["scan"]["assetCount"] += 1
    with pytest.raises(AtomicReferenceRenameError, match="contains duplicates"):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize(
    ("rename_case", "invalid_kind"),
    [
        (object_request(), "expression_parameter"),
        (parameter_request(), "animation_binding"),
    ],
)
def test_reference_kinds_must_match_the_operation(rename_case: dict, invalid_kind: str) -> None:
    payload = preview_payload(rename_case)
    payload["references"][0]["kind"] = invalid_kind

    with pytest.raises(AtomicReferenceRenameError, match="does not match the operation"):
        bind_authoritative_preview(wrapper(rename_case), payload)


def test_asset_inventory_must_cover_each_reference_exactly() -> None:
    request = parameter_request()
    payload = preview_payload(request)
    payload["assets"][0]["mutationCount"] = 2
    payload["assets"][0]["rawReplacementCount"] = 2
    payload["assets"][1]["mutationCount"] = 0

    with pytest.raises(AtomicReferenceRenameError, match="asset mutationCount"):
        bind_authoritative_preview(wrapper(request), payload)

    payload = preview_payload(request)
    payload["assets"][0]["assetPath"] = "Assets/Avatar/Expressions/Other.asset"

    with pytest.raises(AtomicReferenceRenameError, match="asset coverage drifted"):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda asset: asset.pop("fileLength"),
        lambda asset: asset.pop("targetFileDigest"),
        lambda asset: asset.update(targetFileDigest=asset["fileDigest"]),
        lambda asset: asset.update(targetFileLength=0),
        lambda asset: asset.update(rawReplacementCount=0),
    ],
)
def test_asset_exact_target_evidence_fails_closed(mutator) -> None:
    request = object_request()
    payload = preview_payload(request)
    mutator(payload["assets"][0])

    with pytest.raises(AtomicReferenceRenameError):
        bind_authoritative_preview(wrapper(request), payload)


def test_impossible_target_length_fails_even_with_a_recomputed_plan_digest() -> None:
    request = parameter_request()
    payload = preview_payload(request)
    payload["assets"][0]["targetFileLength"] += 1
    payload["planDigest"] = compute_plan_digest(_plan_from_payload(payload))

    with pytest.raises(AtomicReferenceRenameError, match="target length"):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize("field", ["assetGuid", "fileIdentity"])
def test_asset_inventory_rejects_duplicate_identity(field: str) -> None:
    request = parameter_request()
    payload = preview_payload(request)
    payload["assets"][1][field] = payload["assets"][0][field]

    with pytest.raises(AtomicReferenceRenameError, match="duplicate GUIDs|aliased files"):
        bind_authoritative_preview(wrapper(request), payload)


def test_negative_nonzero_unity_scene_handle_is_preserved() -> None:
    request = parameter_request()
    payload = preview_payload(request)
    payload["scene"]["handle"] = -1322
    payload["planDigest"] = compute_plan_digest(
        {
            "operation": payload["operation"],
            "scene": payload["scene"],
            "avatar": payload["avatar"],
            "target": payload["target"],
            "scan": payload["scan"],
            "assets": payload["assets"],
            "references": payload["references"],
            "beforeStateDigest": payload["beforeStateDigest"],
            "targetStateDigest": payload["targetStateDigest"],
        }
    )

    canonical, _ = bind_authoritative_preview(wrapper(request), payload)

    assert canonical["arguments"]["expectedSceneHandle"] == -1322


def test_caller_secret_and_forged_plan_fields_never_cross_preview_boundary() -> None:
    request = parameter_request()
    poisoned = {
        **request,
        "expectedPlanDigest": "0" * 64,
        "expectedTargetStateDigest": "1" * 64,
        "preview": False,
        "saveScene": True,
    }

    preview = build_preview_arguments(poisoned)

    assert set(preview) == set(request) | {"preview", "saveScene"}
    assert preview["preview"] is True
    assert preview["saveScene"] is False


def test_unity_tool_uses_only_the_fixed_reference_matrix() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")

    for reference_kind in (
        "hierarchy_object",
        "animation_binding",
        "avatar_mask_transform",
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
    ):
        assert f'"{reference_kind}"' in source
    assert '"content.globalParam"' in source
    assert '"content.driveGlobalParam"' in source
    assert '"content.globalParams"' in source
    assert "serializedPropertyPath" not in source
    assert 'ReadRequiredText(parameters, "typeName"' not in source
    assert "BindingFlags" not in source


def test_unity_tool_allows_only_clean_readonly_package_subscenes() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")

    assert 'scenePath.StartsWith("Assets/", StringComparison.Ordinal)' in source
    assert 'scenePath.StartsWith("Packages/", StringComparison.Ordinal)' in source
    assert "isProjectScene =" in source
    assert "&& !scene.isSubScene" in source
    assert "isReadOnlyPackageSubScene =" in source
    assert "&& scene.isSubScene" in source
    assert "isProjectScene || isReadOnlyPackageSubScene" in source
    assert "AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath)" in source
    assert "|| scene.isDirty" in source


def test_unity_tool_checks_loaded_assets_without_forcing_every_file_to_load() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")
    guard_start = source.index("private static void RequireNoDirtyProjectAssets")
    guard_end = source.index("private static bool IsProjectOwnedAssetPath", guard_start)
    guard = source[guard_start:guard_end]

    assert "new HashSet<string>(paths, StringComparer.Ordinal)" in guard
    assert "Resources.FindObjectsOfTypeAll<UnityEngine.Object>()" in guard
    assert "projectPathSet.Contains(path)" in guard
    assert "AssetDatabase.Contains(asset)" in guard
    assert "EditorUtility.IsPersistent(asset)" in guard
    assert "AssetDatabase.IsNativeAsset(asset) && EditorUtility.IsDirty(asset)" in guard
    assert "AssetDatabase.LoadAllAssetsAtPath(path)" not in guard


def test_unity_tool_allows_only_empty_unregistered_gitkeep_placeholders() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")
    verify_start = source.index("private static void VerifyRegisteredAssetsInFileSystem")
    verify_end = source.index("private static FileSystemEntry ReadStableAssetsFileEvidence", verify_start)
    verify = source[verify_start:verify_end]

    assert "registeredOnly.Any()" in verify
    assert "unregistered.Any(entry => !IsAllowedUnregisteredPlaceholder(entry))" in verify
    assert 'entry.Path.EndsWith("/.gitkeep", StringComparison.Ordinal)' in verify
    assert "entry.Length == 0" in verify
    assert "entry.Digest == Sha256Bytes(new byte[0])" in verify


def test_unity_tool_binds_complete_evidence_and_exact_restore() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")

    for contract in (
        "ComputeAssemblySetDigest",
        "ComputePackageSetDigest",
        "BuildInventory",
        "expectedAssetInventoryDigest",
        "expectedAssemblySetDigest",
        "expectedPlanDigest",
        "CaptureBackups",
        "RestoreFailedApply",
        "RestoredStateMatches",
        "RestoredInventoryMatches",
        "RequireNoDirtyProjectAssets",
        "RequirePlannedAssetsClean",
        "SavePlannedAssets",
        "VerifyExactInventoryDelta",
        "VerifyExactFileSystemDelta",
        "CaptureAssetsFileSystemInventory",
        "VerifyRegisteredAssetsInFileSystem",
        "ComputeExpectedTargetFile",
        "VerifyReverseReadback",
        "CountRawToken",
        "checkpointRestoreRequired",
    ):
        assert contract in source
    assert "FileMode.Create" in source
    assert "AssetDatabase.SaveAssetIfDirty(asset)" in source
    assert "AssetDatabase.SaveAssets()" not in source
    assert "AssetDatabase.Refresh(" not in source
    assert "An unapproved project asset changed" in source
    assert "ForceSynchronousImport" in source
    assert "old-count" in source
    assert "colliding-count" in source

    apply_start = source.index("private static object Apply")
    restore_start = source.index("private static bool RestoreFailedApply")
    apply_precondition = source[apply_start:restore_start]
    restore_end = source.index("private static JObject BuildApplyPayload")
    restore_contract = source[restore_start:restore_end]
    scene_match_start = source.index("private static bool SceneEvidenceMatches")
    scene_match_end = source.index("private static string NormalizeScenePath")
    scene_match = source[scene_match_start:scene_match_end]

    assert "immediate.Scene.FileIdentity != snapshot.Scene.FileIdentity" in apply_precondition
    assert "left.FileIdentity == right.FileIdentity" in scene_match
    assert "RestoredAssetEvidenceMatches(currentAfter, backup.Evidence)" in restore_contract
    assert "current.File.LinkCount == 1" in restore_contract
    assert "current.Meta.Identity == expected.Meta.Identity" in restore_contract
    assert "scene.FileIdentity != snapshot.Scene.FileIdentity" not in restore_contract
    assert "left.FileIdentity != right.FileIdentity" not in restore_contract
    restored_scene_start = source.index("private static bool RestoredSceneMatches")
    restored_scene_end = source.index("private static bool RestoredTargetMatches", restored_scene_start)
    restored_scene = source[restored_scene_start:restored_scene_end]
    assert "left.Handle == right.Handle" not in restored_scene
    assert "left.Guid == right.Guid" in restored_scene
    assert "left.FileDigest == right.FileDigest" in restored_scene
    assert "left.MetaIdentity == right.MetaIdentity" in restored_scene
    assert "expected.AssetInventoryDigest != actual.AssetInventoryDigest" not in restore_contract
    assert "expected.BeforeStateDigest != actual.BeforeStateDigest" not in restore_contract
    assert "current.File.Identity == expected.File.Identity" not in restore_contract
    assert "WriteExactFile(absolute, backup.FileBytes, true)" in restore_contract
    assert 'WriteExactFile(absolute + ".meta", backup.MetaBytes, false)' in restore_contract
    exact_write_start = source.index("private static void WriteExactFile")
    exact_write_end = source.index("private static JObject BuildApplyPayload", exact_write_start)
    exact_write = source[exact_write_start:exact_write_end]
    assert "MaxExactWriteAttempts" in exact_write
    assert "ExactWriteRetryMilliseconds" in exact_write
    assert "code != 32 && code != 33" in exact_write
    assert "FileMode.CreateNew" in exact_write
    assert "FileOptions.WriteThrough" in exact_write
    assert "File.Replace(temporaryPath, path, null)" in exact_write
    assert "File.Delete(temporaryPath)" in exact_write
    assert '" (assetIndex=" + index.ToString(CultureInfo.InvariantCulture)' in source
    assert '",fields=" + string.Join(",", mismatches)' in source
    for mismatch_code in (
        "baseline",
        "path",
        "guid",
        "meta_digest",
        "mutation_count",
        "replacement_count",
        "target_digest",
        "target_length",
        "reverse_digest",
        "reverse_length",
        "baseline_length",
        "meta_identity",
    ):
        assert f'mismatches.Add("{mismatch_code}")' in source


def test_unity_tool_projects_animation_binding_hash_bytes_before_approval() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")
    projection_start = source.index("private static byte[] ProjectAnimationBindingPathHashes")
    projection_end = source.index("private static byte[] ReplaceBytesExact", projection_start)
    projection = source[projection_start:projection_end]

    assert 'item.Kind == "animation_binding"' in projection
    assert "Animator.StringToHash(beforePath)" in projection
    assert "Animator.StringToHash(afterPath)" in projection
    assert 'Encoding.UTF8.GetBytes("path: " + replacement.Before)' in projection
    assert 'Encoding.UTF8.GetBytes("path: " + replacement.After)' in projection
    assert "group.Count()" in projection
    assert "path hash collides with its target" in projection


def test_unity_tool_mutates_only_the_serialized_animator_parameter_name() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "AtomicReferenceRenameTool.cs"
    ).read_text(encoding="utf-8")
    scan_start = source.index("private static void ScanAnimatorControllerParameters")
    scan_end = source.index("private static void ScanStateMachine", scan_start)
    scan = source[scan_start:scan_end]

    assert '"m_AnimatorParameters"' in scan
    assert '.FindPropertyRelative("m_Name")' in scan
    assert "serializedName.stringValue != parameter.name" in scan
    assert "AddSerializedStringReference(" in scan
    assert "controller.parameters = current" not in scan


def test_disposable_fixture_covers_success_failure_and_cleanup() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "primitive_basis"
        / "atomic_reference_rename"
        / "AtomicReferenceRenameFixtureProbe.cs"
    ).read_text(encoding="utf-8")

    for scenario in (
        "VerifyObjectLifecycle",
        "VerifyPlannedDirtyAssetFailsClosed",
        "VerifyUnapprovedDirtyAssetFailsClosed",
        "VerifyPreexistingUnregisteredReferenceFailsClosed",
        "VerifyUnapprovedConcurrentWriteRequiresCheckpoint",
        "VerifyPlannedConcurrentWriteIsRejectedAndRestored",
        "VerifyUnregisteredFileSystemResidueRequiresCheckpoint",
        "VerifyParameterLifecycle",
        "VerifyUnknownReferenceFailsClosed",
        "VerifyStaleApprovalFailsClosed",
        "VerifyPartialMutationRestore",
        "RequireNoRawToken",
        "ReferenceSignature",
        "ResetEvidenceOutput",
        "WriteFreshEvidence",
        "CleanupEvidenceOutputBestEffort",
        "FileMode.CreateNew",
        "File.Move(temporaryPath, finalPath)",
        "ContractDigest",
        "body identity must not be contractual",
        'Evidence["objectRequest"]',
        'Evidence["parameterRequest"]',
        'Evidence["partialRestore"]',
        'Evidence["unapprovedDirtyAsset"]',
        'Evidence["plannedDirtyAssets"]',
        'Evidence["preexistingUnregisteredReference"]',
        'Evidence["unapprovedConcurrentWrite"]',
        'Evidence["plannedConcurrentWrite"]',
        'Evidence["unregisteredRawResidue"]',
        "residueCount",
        "VRCFORGE_ATOMIC_REFERENCE_RENAME_PROBE_OK",
    ):
        assert scenario in fixture
    assert '"planned concurrent-write scene finalize"' in fixture


def test_dashboard_fastapi_preview_request_approval_and_apply_are_one_bound_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = json.loads(OUTFIT_NAMING_WORKFLOW.read_text(encoding="utf-8"))
    workflow_request = workflow["steps"][0]["request"]
    project = tmp_path / "UnityProject"
    for root in ("Assets", "Packages", "ProjectSettings"):
        (project / root).mkdir(parents=True, exist_ok=True)
    project_path = str(project.resolve())
    request = object_request()
    params = {"projectPath": project_path, **request}
    preview = preview_payload(request)
    preview["projectPath"] = project_path
    canonical, approval_preview = bind_authoritative_preview(
        build_wrapper_arguments(params),
        preview,
    )
    _fixture_wrapper, apply = approved_apply_payload(request)
    apply["projectPath"] = project_path

    gateway = AgentGateway(
        tmp_path / "gateway" / "config.json",
        tmp_path / "gateway" / "audit",
    )
    gateway.checkpoint_prepare_handler = lambda _root: {"ok": True}
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    dashboard_server.register_agent_gateway_tools()
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "approval"
    gateway.save_config(config)
    headers = {"Authorization": f"Bearer {config.token}"}
    preview_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": preview},
    )
    apply_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": apply},
    )

    def invoke_with_bound_execution(_settings, tool_name, arguments, **_kwargs):
        plan = current_approved_unity_execution()
        if plan is None:
            return preview_result
        claim = plan.claim(tool_name, arguments, project_path)
        claim.complete()
        return apply_result

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch(
            "dashboard_server.invoke_unity_mcp",
            side_effect=invoke_with_bound_execution,
        ) as invoke,
    ):
        client = TestClient(dashboard_server.app)
        try:
            preview_response = client.post(
                "/api/agent/tool/vrcforge_preview_atomic_reference_rename",
                headers=headers,
                json={"agent_name": "atomic-contract", "params": params},
            )
            assert preview_response.status_code == 200
            assert preview_response.json()["result"]["preview"] == approval_preview

            request_response = client.post(
                "/api/agent/tool/vrcforge_request_apply",
                headers=headers,
                json={
                    "agent_name": "atomic-contract",
                    "params": {
                        "target_tool": workflow_request["targetTool"],
                        "arguments": build_wrapper_arguments(params),
                        "preview": {"spoofed": True},
                        "reason": "Verify one complete reference migration.",
                    },
                },
            )
            assert request_response.status_code == 200
            pending = request_response.json()["result"]
            assert pending["status"] == "pending"
            assert pending["approval"]["requiresExplicitApproval"] is True
            assert pending["approval"]["autoApprovalBlocked"] is True
            assert "spoofed" not in pending["approval"]["preview"]
            stored = gateway._approvals[pending["approval"]["id"]]
            assert stored["arguments"] == canonical

            approval_id = pending["approval"]["id"]
            apply_response = client.post(
                f"/api/app/agent/approvals/{approval_id}/approve",
                json={"expectedProjectRoot": project_path, "globalOnly": False},
            )
            assert apply_response.status_code == 200
            execution = apply_response.json()["execution"]
            assert execution["ok"] is True
            assert execution["status"] == "applied"
            assert execution["result"]["ok"] is True
        finally:
            client.close()

    assert [call.args[1] for call in invoke.call_args_list] == [TOOL_NAME] * 4
    preview_calls = [call for call in invoke.call_args_list if call.args[2].get("preview") is True]
    assert preview_calls
    assert all(call.kwargs.get("execution_context") == {"lane": "app_preview"} for call in preview_calls)
    assert not any(
        key.startswith("expected")
        for key in invoke.call_args_list[-2].args[2]
    )
    assert invoke.call_args_list[-1].args[2]["expectedPlanDigest"] == canonical["arguments"]["expectedPlanDigest"]


def test_dashboard_write_handler_rejects_a_forged_atomic_apply_receipt() -> None:
    approved_wrapper, payload = approved_apply_payload(parameter_request())
    payload["readback"]["assets"][0]["targetFileDigest"] = "f" * 64
    _recompute_readback_digest(payload)
    forged_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": payload},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=forged_result),
    ):
        result = dashboard_server.unity_mcp_write_sync(approved_wrapper)

    assert result == {
        "ok": False,
        "toolName": TOOL_NAME,
        "error": "Atomic reference rename apply returned an invalid verification receipt.",
    }
