from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import authoritative_unity_writes as authoritative_writes
import dashboard_server
import parameter_bit_packing as bitpack
from agent_gateway import AgentGateway
from parameter_bit_packing import (
    APPROVAL_SCHEMA,
    RESULT_SCHEMA,
    TOOL_NAME,
    ParameterBitPackingError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_capability_digest,
    compute_apply_receipt_digest,
    compute_preview_digest,
    validate_apply_result,
)


PROJECT_PATH = str(Path("D:/DisposableParameterProject").resolve())


def request_arguments() -> dict:
    return {
        "sourceScenePath": "Assets/Avatar/ParameterFixture.unity",
        "sourceAvatarPath": "Avatar",
        "outputCloneName": "Packed Clone",
    }


def wrapper(project_path: str = PROJECT_PATH) -> dict:
    return {
        "projectPath": project_path,
        "toolName": TOOL_NAME,
        "arguments": request_arguments(),
    }


def exclusions() -> list[dict]:
    rows = [
        {
            "name": "FT/JawOpen",
            "type": "Float",
            "networkSynced": False,
            "reasons": ["face_tracking", "float_or_int", "osc_or_unmapped", "not_network_synced"],
            "stateDigest": "4" * 64,
        },
        {
            "name": "Puppet/X",
            "type": "Float",
            "networkSynced": False,
            "reasons": ["float_or_int", "not_network_synced", "puppet"],
            "stateDigest": "5" * 64,
        },
        {
            "name": "OSC/Raw",
            "type": "Int",
            "networkSynced": False,
            "reasons": ["float_or_int", "not_network_synced", "osc_or_unmapped"],
            "stateDigest": "6" * 64,
        },
    ]
    for row in rows:
        row["reasons"] = sorted(row["reasons"])
    return sorted(rows, key=lambda row: row["name"])


def capability() -> dict:
    value = {
        "packageId": bitpack.PACKAGE_ID,
        "packageVersion": bitpack.PACKAGE_VERSION,
        "packageAuthor": bitpack.PACKAGE_AUTHOR,
        "packageArchiveSha256": bitpack.PACKAGE_ARCHIVE_SHA256,
        "packageTreeSha256": bitpack.PACKAGE_TREE_SHA256,
        "packageFileCount": bitpack.PACKAGE_FILE_COUNT,
        "packageRootIdentityDigest": "7" * 64,
        "callbackAssemblyName": bitpack.CALLBACK_ASSEMBLY_NAME,
        "callbackAssemblyVersion": bitpack.CALLBACK_ASSEMBLY_VERSION,
        "callbackAssemblyPublicKeyToken": bitpack.CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN,
        "callbackAssemblySha256": bitpack.CALLBACK_ASSEMBLY_SHA256,
        "sdkCallbackAssemblyName": bitpack.SDK_CALLBACK_ASSEMBLY_NAME,
        "sdkCallbackAssemblyVersion": bitpack.SDK_CALLBACK_ASSEMBLY_VERSION,
        "sdkCallbackAssemblyPublicKeyToken": bitpack.SDK_CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN,
        "sdkCallbackAssemblySha256": bitpack.SDK_CALLBACK_ASSEMBLY_SHA256,
        "callbackType": bitpack.CALLBACK_TYPE,
        "callbackSignature": bitpack.CALLBACK_SIGNATURE,
        "registeredHookType": bitpack.REGISTERED_HOOK_TYPE,
        "registeredHookCount": 1,
        "callbackRosterCount": bitpack.CALLBACK_ROSTER_COUNT,
        "callbackRosterDigest": bitpack.CALLBACK_ROSTER_DIGEST,
    }
    value["capabilityDigest"] = compute_capability_digest(value)
    return value


def behavior_evidence(*, output: bool = False) -> dict:
    value = {
        "schema": bitpack.BEHAVIOR_EVIDENCE_SCHEMA,
        "portableAvatarDigest": ("8" if output else "1") * 64,
        "orderedParameterDigest": ("9" if output else "2") * 64,
        "parameterCount": 268 if output else 263,
        "menuGraphDigest": "3" * 64,
        "menuRowCount": 24,
        "animatorBehaviorDigest": ("a" if output else "4") * 64,
        "animatorRowCount": 84 if output else 52,
    }
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.BEHAVIOR_EVIDENCE_SCHEMA,
        value["portableAvatarDigest"],
        value["orderedParameterDigest"],
        value["parameterCount"],
        value["menuGraphDigest"],
        value["menuRowCount"],
        value["animatorBehaviorDigest"],
        value["animatorRowCount"],
    )
    return value


def preferences() -> dict:
    value = {
        "schema": bitpack.PREFERENCE_SCHEMA,
        "compressorPresent": False,
        "compressorValue": 0,
        "compressorMode": "missing-default-automatic",
        "alignMobilePresent": False,
        "alignMobileValue": True,
        "readOnly": True,
        "buildTarget": bitpack.DESKTOP_BUILD_TARGET,
        "platformScope": bitpack.PLATFORM_SCOPE,
        "crossPlatformEquivalent": False,
        "localAppDataAccessed": False,
    }
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.PREFERENCE_SCHEMA,
        value["compressorPresent"],
        value["compressorValue"],
        value["alignMobilePresent"],
        value["alignMobileValue"],
        value["buildTarget"],
        value["platformScope"],
        value["crossPlatformEquivalent"],
        value["localAppDataAccessed"],
    )
    return value


def platform_proof() -> dict:
    return {
        "buildTarget": bitpack.DESKTOP_BUILD_TARGET,
        "scope": bitpack.PLATFORM_SCOPE,
        "crossPlatformEquivalent": False,
        "localAppDataAccessed": False,
    }


def behavior_proof(compressed_count: int = 16) -> dict:
    source = behavior_evidence()
    output = behavior_evidence(output=True)
    value = {
        "schema": bitpack.BEHAVIOR_PROOF_SCHEMA,
        "status": "verified",
        "platformScope": bitpack.PLATFORM_SCOPE,
        "crossPlatformEquivalent": False,
        "sourceOrderedParameterDigest": source["orderedParameterDigest"],
        "outputOrderedParameterDigest": output["orderedParameterDigest"],
        "sourceParameterCount": source["parameterCount"],
        "outputParameterCount": output["parameterCount"],
        "sourceMenuGraphDigest": source["menuGraphDigest"],
        "outputMenuGraphDigest": output["menuGraphDigest"],
        "sourceMenuRowCount": source["menuRowCount"],
        "outputMenuRowCount": output["menuRowCount"],
        "sourceAnimatorBehaviorDigest": source["animatorBehaviorDigest"],
        "outputAnimatorBehaviorDigest": output["animatorBehaviorDigest"],
        "sourceAnimatorRowCount": source["animatorRowCount"],
        "outputAnimatorRowCount": output["animatorRowCount"],
        "preservedBehaviorDigest": "b" * 64,
        "codecGraphDigest": "c" * 64,
        "codecMappingDigest": "d" * 64,
        "codecMappingCount": compressed_count,
        "excludedBeforeDigest": "e" * 64,
        "excludedAfterDigest": "e" * 64,
    }
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.BEHAVIOR_PROOF_SCHEMA,
        value["status"],
        value["platformScope"],
        value["crossPlatformEquivalent"],
        value["sourceOrderedParameterDigest"],
        value["outputOrderedParameterDigest"],
        value["sourceParameterCount"],
        value["outputParameterCount"],
        value["sourceMenuGraphDigest"],
        value["outputMenuGraphDigest"],
        value["sourceMenuRowCount"],
        value["outputMenuRowCount"],
        value["sourceAnimatorBehaviorDigest"],
        value["outputAnimatorBehaviorDigest"],
        value["sourceAnimatorRowCount"],
        value["outputAnimatorRowCount"],
        value["preservedBehaviorDigest"],
        value["codecGraphDigest"],
        value["codecMappingDigest"],
        value["codecMappingCount"],
        value["excludedBeforeDigest"],
        value["excludedAfterDigest"],
    )
    return value


def manifest(*, staged: bool) -> dict:
    root = (
        f"{bitpack.GENERATED_ROOT}/Packed Clone"
        if staged
        else f"{bitpack.OUTPUT_KIND_ROOT}/Packed Clone"
    )
    value = {
        "schema": bitpack.OUTPUT_MANIFEST_SCHEMA,
        "rootPath": root,
        "prefabPath": f"{root}/Packed Clone.prefab",
        "entryCount": 4,
        "byteCount": 4096,
        "contentDigest": "1" * 64,
        "handleEvidenceDigest": "2" * 64,
        "guidMapDigest": "3" * 64,
        "dependencyGuidDigest": "4" * 64,
        "referenceClosureDigest": ("5" if staged else "6") * 64,
        "noTemporaryReferences": not staged,
        "reparseFree": True,
        "singleLink": True,
        "handleHashed": True,
        "finalEnumerationVerified": True,
    }
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.OUTPUT_MANIFEST_SCHEMA,
        value["rootPath"],
        value["prefabPath"],
        value["entryCount"],
        value["byteCount"],
        value["contentDigest"],
        value["handleEvidenceDigest"],
        value["guidMapDigest"],
        value["dependencyGuidDigest"],
        value["referenceClosureDigest"],
        value["noTemporaryReferences"],
        value["reparseFree"],
        value["singleLink"],
        value["handleHashed"],
        value["finalEnumerationVerified"],
    )
    return value


def refresh_preference_receipt(value: dict) -> None:
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.PREFERENCE_SCHEMA,
        value["compressorPresent"],
        value["compressorValue"],
        value["alignMobilePresent"],
        value["alignMobileValue"],
        value["buildTarget"],
        value["platformScope"],
        value["crossPlatformEquivalent"],
        value["localAppDataAccessed"],
    )


def refresh_behavior_evidence_receipt(value: dict) -> None:
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.BEHAVIOR_EVIDENCE_SCHEMA,
        value["portableAvatarDigest"],
        value["orderedParameterDigest"],
        value["parameterCount"],
        value["menuGraphDigest"],
        value["menuRowCount"],
        value["animatorBehaviorDigest"],
        value["animatorRowCount"],
    )


def refresh_behavior_proof_receipt(value: dict) -> None:
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.BEHAVIOR_PROOF_SCHEMA,
        value["status"],
        value["platformScope"],
        value["crossPlatformEquivalent"],
        value["sourceOrderedParameterDigest"],
        value["outputOrderedParameterDigest"],
        value["sourceParameterCount"],
        value["outputParameterCount"],
        value["sourceMenuGraphDigest"],
        value["outputMenuGraphDigest"],
        value["sourceMenuRowCount"],
        value["outputMenuRowCount"],
        value["sourceAnimatorBehaviorDigest"],
        value["outputAnimatorBehaviorDigest"],
        value["sourceAnimatorRowCount"],
        value["outputAnimatorRowCount"],
        value["preservedBehaviorDigest"],
        value["codecGraphDigest"],
        value["codecMappingDigest"],
        value["codecMappingCount"],
        value["excludedBeforeDigest"],
        value["excludedAfterDigest"],
    )


def refresh_manifest_receipt(value: dict) -> None:
    value["receiptDigest"] = bitpack._sha256_framed(
        bitpack.OUTPUT_MANIFEST_SCHEMA,
        value["rootPath"],
        value["prefabPath"],
        value["entryCount"],
        value["byteCount"],
        value["contentDigest"],
        value["handleEvidenceDigest"],
        value["guidMapDigest"],
        value["dependencyGuidDigest"],
        value["referenceClosureDigest"],
        value["noTemporaryReferences"],
        value["reparseFree"],
        value["singleLink"],
        value["handleHashed"],
        value["finalEnumerationVerified"],
    )


def preview_payload(project_path: str = PROJECT_PATH) -> dict:
    safe = [f"SafeToggle{i:03d}" for i in range(20)]
    excluded = exclusions()
    source = {
        "scenePath": "Assets/Avatar/ParameterFixture.unity",
        "sceneGuid": "a" * 32,
        "sceneFileDigest": "b" * 64,
        "sceneMetaDigest": "c" * 64,
        "objectPath": "Avatar",
        "globalObjectId": "GlobalObjectId_V1-2-abcdef-12345-0",
        "hierarchyDigest": "d" * 64,
        "sourceStateDigest": "e" * 64,
        "sourceAssetSetDigest": "f" * 64,
        "sourceAssetCount": 5,
        "parameterStateDigest": "1" * 64,
        "controllerStateDigest": "2" * 64,
        "menuStateDigest": "3" * 64,
        "behaviorEvidence": behavior_evidence(),
        "sourceCostBits": 260,
        "parameterCount": 263,
        "safeCandidateNames": safe,
        "safeCandidateDigest": bitpack._name_digest(safe, "vrcforge.safe_parameter_names.v1"),
        "excludedParameters": excluded,
        "excludedDigest": bitpack._excluded_digest(excluded),
        "sourceDirty": False,
        "referencedAssetsDirty": False,
    }
    result = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "callbacksInvoked": False,
        "mutationStarted": False,
        "mutationCount": 0,
        "projectPath": project_path,
        "source": source,
        "capability": capability(),
        "generated": {
            "root": bitpack.GENERATED_ROOT,
            "treeDigestBefore": "0" * 64,
            "contentDigestBefore": "9" * 64,
            "entryCountBefore": 4,
            "byteCountBefore": 1234,
            "backupMaxEntries": bitpack.CACHE_BACKUP_MAX_ENTRIES,
            "backupMaxBytes": bitpack.CACHE_BACKUP_MAX_BYTES,
            "journalSchema": bitpack.CACHE_JOURNAL_SCHEMA,
            "protectedTreeDigestBefore": "b" * 64,
            "protectedEntryCountBefore": 1400,
            "rootIdentityDigestBefore": "c" * 64,
            "rootIdentityCountBefore": 6,
            "exists": True,
            "reparseFree": True,
        },
        "preferences": preferences(),
        "platformProof": platform_proof(),
        "output": {
            "root": bitpack.OUTPUT_ROOT,
            "kindRoot": bitpack.OUTPUT_KIND_ROOT,
            "cloneName": "Packed Clone",
            "sceneName": "VRCForge Parameter Build - Packed Clone",
            "temporaryPrefabPath": f"{bitpack.GENERATED_ROOT}/Packed Clone/Packed Clone.prefab",
            "prefabPath": f"{bitpack.OUTPUT_KIND_ROOT}/Packed Clone/Packed Clone.prefab",
            "treeDigestBefore": "d" * 64,
            "entryCountBefore": 8,
            "rootExistsBefore": True,
            "targetExistsBefore": False,
            "cloneExists": False,
            "sceneCreated": False,
            "prefabExists": False,
        },
    }
    result["previewDigest"] = compute_preview_digest(result)
    return result


def approved(project_path: str = PROJECT_PATH) -> tuple[dict, dict]:
    canonical, approval = bind_authoritative_preview(
        wrapper(project_path),
        preview_payload(project_path),
    )
    return canonical["arguments"], approval


def apply_payload(approved_arguments: dict | None = None) -> dict:
    args = deepcopy(approved_arguments) if approved_arguments is not None else approved()[0]
    safe = [f"SafeToggle{i:03d}" for i in range(20)]
    proof = behavior_proof()
    output_evidence = behavior_evidence(output=True)
    staged_manifest = manifest(staged=True)
    final_manifest = manifest(staged=False)
    result = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "callbacksInvoked": True,
        "mutationStarted": True,
        "restored": False,
        "cleanupRequired": False,
        "checkpointRestoreRequired": False,
        "operationState": "verified",
        "cleanupVerified": True,
        "sceneLoadedAfter": False,
        "temporaryObjectResidue": False,
        "projectPath": args["expectedProjectPath"],
        "previewDigest": args["expectedPreviewDigest"],
        "capability": capability(),
        "preferences": preferences(),
        "platformProof": platform_proof(),
        "behaviorProof": proof,
        "costBeforeBits": 260,
        "costAfterBits": 248,
        "compressedParameterNames": safe[:16],
        "approvedSafeCandidateNames": safe,
        "excludedParameters": exclusions(),
        "sourceUnchanged": True,
        "sourceSceneDirtyAfter": False,
        "sourceStateDigestAfter": args["expectedSourceStateDigest"],
        "sourceAssetSetDigestAfter": args["expectedSourceAssetSetDigest"],
        "source": {
            "scenePath": args["sourceScenePath"],
            "sceneGuid": args["expectedSourceSceneGuid"],
            "sceneFileDigest": args["expectedSourceSceneFileDigest"],
            "sceneMetaDigest": args["expectedSourceSceneMetaDigest"],
            "objectPath": args["sourceAvatarPath"],
            "globalObjectId": args["expectedSourceGlobalObjectId"],
            "hierarchyDigest": args["expectedSourceHierarchyDigest"],
            "sourceStateDigestBefore": args["expectedSourceStateDigest"],
            "sourceStateDigestAfter": args["expectedSourceStateDigest"],
            "sourceAssetSetDigestBefore": args["expectedSourceAssetSetDigest"],
            "sourceAssetSetDigestAfter": args["expectedSourceAssetSetDigest"],
            "sourceAssetCount": args["expectedSourceAssetCount"],
            "parameterStateDigest": args["expectedParameterStateDigest"],
            "parameterCount": args["expectedParameterCount"],
            "controllerStateDigest": args["expectedControllerStateDigest"],
            "menuStateDigest": args["expectedMenuStateDigest"],
            "behaviorEvidence": behavior_evidence(),
            "sourceUnchanged": True,
            "sceneDirtyAfter": False,
        },
        "output": {
            "cloneName": "Packed Clone",
            "sceneName": "VRCForge Parameter Build - Packed Clone",
            "scenePath": "",
            "scenePersistent": False,
            "clonePortableAvatarDigest": output_evidence["portableAvatarDigest"],
            "cloneEvidenceDigest": output_evidence["receiptDigest"],
            "cloneParameterStateDigest": "9" * 64,
            "prefabPath": args["expectedOutputPrefabPath"],
            "prefabGuid": "7" * 32,
            "prefabFileDigest": "6" * 64,
            "prefabMetaDigest": "5" * 64,
            "prefabRootGlobalObjectId": "GlobalObjectId_V1-1-fedcba-54321-0",
            "prefabPortableAvatarDigest": output_evidence["portableAvatarDigest"],
            "prefabOrderedParameterDigest": output_evidence["orderedParameterDigest"],
            "prefabMenuGraphDigest": output_evidence["menuGraphDigest"],
            "prefabAnimatorBehaviorDigest": output_evidence["animatorBehaviorDigest"],
            "prefabEvidenceDigest": output_evidence["receiptDigest"],
            "prefabBehaviorProofDigest": proof["receiptDigest"],
            "prefabParameterStateDigest": "9" * 64,
            "prefabPersistent": True,
            "prefabExistsAfter": True,
            "sceneLoadedAfter": False,
            "temporaryObjectResidue": False,
        },
        "generated": {
            "root": bitpack.GENERATED_ROOT,
            "stagingRoot": bitpack.STAGING_ROOT,
            "stagingRemoved": True,
            "treeDigestBefore": args["expectedGeneratedTreeDigestBefore"],
            "contentDigestBefore": args["expectedGeneratedContentDigestBefore"],
            "entryCountBefore": args["expectedGeneratedEntryCountBefore"],
            "byteCountBefore": args["expectedGeneratedByteCountBefore"],
            "treeDigestAfter": "1" * 64,
            "contentDigestAfter": args["expectedGeneratedContentDigestBefore"],
            "entryCountAfter": args["expectedGeneratedEntryCountBefore"],
            "byteCountAfter": args["expectedGeneratedByteCountBefore"],
            "addedEntryCount": 0,
            "modifiedEntryCount": args["expectedGeneratedEntryCountBefore"],
            "removedEntryCount": 0,
            "targetResidue": False,
            "deltaDigest": "a" * 64,
            "cacheRestored": True,
            "backupBounded": True,
            "backupMaxEntries": bitpack.CACHE_BACKUP_MAX_ENTRIES,
            "backupMaxBytes": bitpack.CACHE_BACKUP_MAX_BYTES,
            "journalSchema": bitpack.CACHE_JOURNAL_SCHEMA,
            "journalId": "parameter-bit-packing-" + "a" * 32,
            "journalClosed": True,
        },
        "managedOutput": {
            "root": bitpack.OUTPUT_ROOT,
            "kindRoot": bitpack.OUTPUT_KIND_ROOT,
            "targetRoot": f"{bitpack.OUTPUT_KIND_ROOT}/Packed Clone",
            "rootExistsBefore": args["expectedOutputRootExistsBefore"],
            "rootExistsAfter": True,
            "treeDigestBefore": args["expectedOutputTreeDigestBefore"],
            "entryCountBefore": args["expectedOutputEntryCountBefore"],
            "treeDigestAfter": "4" * 64,
            "entryCountAfter": args["expectedOutputEntryCountBefore"] + 7,
            "addedEntryCount": 7,
            "targetSubtreeCount": 1,
            "modifiedEntryCount": 0,
            "removedEntryCount": 0,
            "addedEntriesDigest": "a" * 64,
            "leaseBound": True,
            "stageSavedBeforeMove": True,
            "guidPreservingWholeTreeMove": True,
            "temporaryTreeRemoved": True,
            "prefabGuidPreserved": True,
            "stagedManifest": staged_manifest,
            "finalManifest": final_manifest,
            "manifestSchema": bitpack.OUTPUT_MANIFEST_SCHEMA,
            "manifestDigest": final_manifest["receiptDigest"],
            "manifestEntryCount": final_manifest["entryCount"],
            "manifestByteCount": final_manifest["byteCount"],
            "manifestContentDigest": final_manifest["contentDigest"],
            "manifestHandleEvidenceDigest": final_manifest["handleEvidenceDigest"],
            "guidMapDigest": final_manifest["guidMapDigest"],
            "dependencyGuidDigest": final_manifest["dependencyGuidDigest"],
            "referenceClosureDigest": final_manifest["referenceClosureDigest"],
            "noTemporaryReferences": True,
            "reparseFree": True,
            "singleLink": True,
            "handleHashed": True,
            "finalEnumerationVerified": True,
        },
        "protectedProjectTree": {
            "rootIdentityDigestBefore": args["expectedRootIdentityDigest"],
            "rootIdentityDigestAfter": args["expectedRootIdentityDigest"],
            "rootIdentityCountBefore": args["expectedRootIdentityCount"],
            "rootIdentityCountAfter": args["expectedRootIdentityCount"],
            "treeDigestBefore": args["expectedProtectedTreeDigestBefore"],
            "treeDigestAfter": args["expectedProtectedTreeDigestBefore"],
            "entryCountBefore": args["expectedProtectedEntryCountBefore"],
            "entryCountAfter": args["expectedProtectedEntryCountBefore"],
        },
    }
    result["applyReceiptDigest"] = compute_apply_receipt_digest(result)
    return result


def test_preview_arguments_allow_only_fixed_schema_and_force_zero_write() -> None:
    raw = request_arguments()
    raw.update(
        {
            "preview": False,
            "runBuildCallbacks": True,
            "saveScene": True,
            "expectedCapabilityDigest": "0" * 64,
            "internalService": "must-not-cross-boundary",
            "privateKey": "must-not-cross-boundary",
        }
    )

    result = build_preview_arguments(raw)

    assert result == {
        **request_arguments(),
        "preview": True,
        "runBuildCallbacks": False,
        "saveScene": False,
    }


def test_flat_request_is_wrapped_without_caller_preconditions() -> None:
    result = build_wrapper_arguments({"projectPath": PROJECT_PATH, **request_arguments()})

    assert result == wrapper()


def test_wrapper_and_authoritative_binding_strip_caller_private_and_forged_fields() -> None:
    raw = {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "privateKey": "must-not-cross-boundary",
        "transportToken": "must-not-cross-boundary",
        "expectedPreviewDigest": "f" * 64,
        "arguments": {
            **request_arguments(),
            "preview": False,
            "runBuildCallbacks": True,
            "saveScene": True,
            "expectedSourceStateDigest": "1" * 64,
            "privatePayload": "must-not-cross-boundary",
        },
    }

    wrapped = build_wrapper_arguments(raw)
    canonical, _approval = bind_authoritative_preview(raw, preview_payload())

    assert set(wrapped) == {"projectPath", "toolName", "arguments"}
    assert wrapped == wrapper()
    assert set(canonical) == {"projectPath", "toolName", "arguments"}
    assert canonical["projectPath"] == PROJECT_PATH
    assert canonical["toolName"] == TOOL_NAME
    assert canonical["arguments"]["preview"] is False
    assert canonical["arguments"]["runBuildCallbacks"] is True
    assert canonical["arguments"]["saveScene"] is False
    assert canonical["arguments"]["expectedPreviewDigest"] == preview_payload()["previewDigest"]
    assert "privateKey" not in canonical
    assert "transportToken" not in canonical
    assert "privatePayload" not in canonical["arguments"]
    assert canonical["arguments"]["expectedSourceStateDigest"] != "1" * 64


def test_authoritative_preview_binds_source_capability_cache_preferences_and_empty_output() -> None:
    canonical, approval = bind_authoritative_preview(wrapper(), preview_payload())
    args = canonical["arguments"]

    assert approval["schema"] == APPROVAL_SCHEMA
    assert approval["rollbackRequired"] is True
    assert approval["sourceMustRemainUnchanged"] is True
    assert approval["cost"] == {
        "beforeBits": 260,
        "maximumBits": 256,
        "safeCandidateCount": 20,
        "excludedCount": 3,
    }
    assert args["preview"] is False
    assert args["runBuildCallbacks"] is True
    assert args["saveScene"] is False
    assert args["expectedPackageRootIdentityDigest"] == "7" * 64
    assert args["expectedGeneratedTreeDigestBefore"] == "0" * 64
    assert args["expectedGeneratedContentDigestBefore"] == "9" * 64
    assert args["expectedGeneratedEntryCountBefore"] == 4
    assert args["expectedGeneratedByteCountBefore"] == 1234
    assert args["expectedPreferenceDigest"] == preview_payload()["preferences"]["receiptDigest"]
    assert args["expectedProtectedTreeDigestBefore"] == "b" * 64
    assert args["expectedProtectedEntryCountBefore"] == 1400
    assert args["expectedRootIdentityDigest"] == "c" * 64
    assert args["expectedRootIdentityCount"] == 6
    assert args["expectedExcludedDigest"] == preview_payload()["source"]["excludedDigest"]
    assert set(args) == {
        *request_arguments().keys(),
        "preview",
        "runBuildCallbacks",
        "saveScene",
        "expectedProjectPath",
        "expectedSourceSceneGuid",
        "expectedSourceSceneFileDigest",
        "expectedSourceSceneMetaDigest",
        "expectedSourceGlobalObjectId",
        "expectedSourceHierarchyDigest",
        "expectedSourceStateDigest",
        "expectedSourceAssetSetDigest",
        "expectedSourceAssetCount",
        "expectedParameterStateDigest",
        "expectedControllerStateDigest",
        "expectedMenuStateDigest",
        "expectedSourceBehaviorEvidenceDigest",
        "expectedSourceCostBits",
        "expectedParameterCount",
        "expectedSafeCandidateDigest",
        "expectedSafeCandidateCount",
        "expectedExcludedDigest",
        "expectedExcludedCount",
        "expectedCapabilityDigest",
        "expectedPackageRootIdentityDigest",
        "expectedRootIdentityDigest",
        "expectedRootIdentityCount",
        "expectedGeneratedTreeDigestBefore",
        "expectedGeneratedEntryCountBefore",
        "expectedGeneratedContentDigestBefore",
        "expectedGeneratedByteCountBefore",
        "expectedPreferenceDigest",
        "expectedProtectedTreeDigestBefore",
        "expectedProtectedEntryCountBefore",
        "expectedOutputSceneName",
        "expectedOutputPrefabPath",
        "expectedOutputTreeDigestBefore",
        "expectedOutputEntryCountBefore",
        "expectedOutputRootExistsBefore",
        "expectedPreviewDigest",
    }


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.update({"schema": "unknown"}),
        lambda p: p.update({"preview": False}),
        lambda p: p.update({"changed": True}),
        lambda p: p.update({"callbacksInvoked": True}),
        lambda p: p.update({"mutationCount": 1}),
        lambda p: p.update({"projectPath": "D:/OtherProject"}),
        lambda p: p["source"].update({"scenePath": "Packages/Bad.unity"}),
        lambda p: p["source"].update({"objectPath": "../Avatar"}),
        lambda p: p["source"].update({"sourceDirty": True}),
        lambda p: p["source"].update({"referencedAssetsDirty": True}),
        lambda p: p["source"].update({"sourceCostBits": 256}),
        lambda p: p["source"].update({"safeCandidateNames": []}),
        lambda p: p["source"].update({"safeCandidateDigest": "0" * 64}),
        lambda p: p["source"].update({"excludedDigest": "0" * 64}),
        lambda p: p["capability"].update({"packageVersion": "1.1335.0"}),
        lambda p: p["capability"].update({"packageTreeSha256": "0" * 64}),
        lambda p: p["capability"].update({"callbackAssemblySha256": "0" * 64}),
        lambda p: p["capability"].update({"callbackAssemblyPublicKeyToken": "unexpected"}),
        lambda p: p["capability"].update({"callbackSignature": "public static void Run()"}),
        lambda p: p["capability"].update({"registeredHookCount": 2}),
        lambda p: p["capability"].update({"callbackRosterCount": 17}),
        lambda p: p["capability"].update({"callbackRosterDigest": "0" * 64}),
        lambda p: p["capability"].update({"capabilityDigest": "0" * 64}),
        lambda p: p["generated"].update({"entryCountBefore": 1}),
        lambda p: p["generated"].update({"treeDigestBefore": "f" * 64}),
        lambda p: p["generated"].update({"reparseFree": False}),
        lambda p: p["output"].update({"cloneExists": True}),
        lambda p: p.update({"previewDigest": "0" * 64}),
    ],
)
def test_preview_fails_closed_on_forged_or_unknown_state(mutator) -> None:
    payload = preview_payload()
    mutator(payload)

    with pytest.raises(ParameterBitPackingError):
        bind_authoritative_preview(wrapper(), payload)


@pytest.mark.parametrize("reframe", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "top",
        "source",
        "source_evidence",
        "generated",
        "preferences",
        "platform",
        "output",
        "capability",
        "excluded",
    ],
)
def test_preview_rejects_unvalidated_field_injection(case: str, reframe: bool) -> None:
    payload = preview_payload()
    targets = {
        "top": payload,
        "source": payload["source"],
        "source_evidence": payload["source"]["behaviorEvidence"],
        "generated": payload["generated"],
        "preferences": payload["preferences"],
        "platform": payload["platformProof"],
        "output": payload["output"],
        "capability": payload["capability"],
        "excluded": payload["source"]["excludedParameters"][0],
    }
    targets[case]["maliciousExtra"] = "must-not-cross-boundary"
    if reframe:
        payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="fields are invalid"):
        bind_authoritative_preview(wrapper(), payload)


def test_preview_rejects_insufficient_safe_candidate_budget() -> None:
    payload = preview_payload()
    payload["source"]["sourceCostBits"] = 300
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="required overhead"):
        bind_authoritative_preview(wrapper(), payload)


def test_preview_rejects_canonically_reframed_nonautomatic_preference() -> None:
    payload = preview_payload()
    payload["preferences"].update(
        {
            "compressorPresent": True,
            "compressorValue": 1,
            "compressorMode": "explicit-automatic",
        }
    )
    refresh_preference_receipt(payload["preferences"])
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="compressorValue"):
        bind_authoritative_preview(wrapper(), payload)


def test_preview_rejects_canonically_reframed_mobile_target_claim() -> None:
    payload = preview_payload()
    payload["preferences"]["buildTarget"] = "Android"
    payload["platformProof"]["buildTarget"] = "Android"
    refresh_preference_receipt(payload["preferences"])
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="desktop build target"):
        bind_authoritative_preview(wrapper(), payload)


def test_apply_accepts_only_verified_reduction_with_durable_managed_output() -> None:
    args, _ = approved()

    result = validate_apply_result(args, apply_payload())

    assert result["costBeforeBits"] == 260
    assert result["costAfterBits"] == 248
    assert result["output"]["scenePersistent"] is False
    assert result["output"]["prefabPersistent"] is True
    assert result["output"]["prefabExistsAfter"] is True
    assert result["output"]["prefabPath"].endswith("/Packed Clone/Packed Clone.prefab")
    assert result["sceneLoadedAfter"] is False
    assert result["temporaryObjectResidue"] is False
    assert result["generated"]["addedEntryCount"] == 0
    assert result["generated"]["contentDigestAfter"] == result["generated"]["contentDigestBefore"]
    assert result["generated"]["cacheRestored"] is True
    assert result["generated"]["journalClosed"] is True
    assert result["managedOutput"]["addedEntryCount"] == 7
    assert result["managedOutput"]["leaseBound"] is True
    assert result["managedOutput"]["guidPreservingWholeTreeMove"] is True
    assert result["managedOutput"]["finalManifest"]["noTemporaryReferences"] is True
    assert result["behaviorProof"]["platformScope"] == bitpack.PLATFORM_SCOPE
    assert result["platformProof"]["crossPlatformEquivalent"] is False


@pytest.mark.parametrize(
    "case",
    [
        "top",
        "source",
        "source_evidence",
        "output",
        "generated",
        "managed",
        "staged_manifest",
        "final_manifest",
        "protected",
        "capability",
        "preferences",
        "platform",
        "behavior",
        "excluded",
    ],
)
def test_apply_rejects_unvalidated_field_injection_even_with_reframed_receipt(case: str) -> None:
    args, _ = approved()
    payload = apply_payload(args)
    targets = {
        "top": payload,
        "source": payload["source"],
        "source_evidence": payload["source"]["behaviorEvidence"],
        "output": payload["output"],
        "generated": payload["generated"],
        "managed": payload["managedOutput"],
        "staged_manifest": payload["managedOutput"]["stagedManifest"],
        "final_manifest": payload["managedOutput"]["finalManifest"],
        "protected": payload["protectedProjectTree"],
        "capability": payload["capability"],
        "preferences": payload["preferences"],
        "platform": payload["platformProof"],
        "behavior": payload["behaviorProof"],
        "excluded": payload["excludedParameters"][0],
    }
    targets[case]["privatePayload"] = "must-not-cross-boundary"
    payload["applyReceiptDigest"] = compute_apply_receipt_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="fields are invalid"):
        validate_apply_result(args, payload)


@pytest.mark.parametrize("case", ["cache", "manifest", "codec", "preference", "platform", "journal", "closure", "source"])
def test_apply_rejects_reframed_nested_proof_drift(case: str) -> None:
    args, _ = approved()
    payload = apply_payload(args)
    if case == "cache":
        payload["generated"]["contentDigestAfter"] = "f" * 64
    elif case == "manifest":
        final = payload["managedOutput"]["finalManifest"]
        final["contentDigest"] = "f" * 64
        refresh_manifest_receipt(final)
        payload["managedOutput"]["manifestDigest"] = final["receiptDigest"]
        payload["managedOutput"]["manifestContentDigest"] = final["contentDigest"]
    elif case == "codec":
        payload["behaviorProof"]["codecMappingCount"] -= 1
        refresh_behavior_proof_receipt(payload["behaviorProof"])
        payload["output"]["prefabBehaviorProofDigest"] = payload["behaviorProof"]["receiptDigest"]
    elif case == "preference":
        payload["preferences"]["alignMobileValue"] = False
        refresh_preference_receipt(payload["preferences"])
    elif case == "platform":
        payload["platformProof"]["localAppDataAccessed"] = True
    elif case == "journal":
        payload["generated"]["journalClosed"] = False
    elif case == "closure":
        final = payload["managedOutput"]["finalManifest"]
        final["noTemporaryReferences"] = False
        refresh_manifest_receipt(final)
        payload["managedOutput"]["manifestDigest"] = final["receiptDigest"]
        payload["managedOutput"]["noTemporaryReferences"] = False
    elif case == "source":
        source_evidence = payload["source"]["behaviorEvidence"]
        source_evidence["portableAvatarDigest"] = "f" * 64
        refresh_behavior_evidence_receipt(source_evidence)
    payload["applyReceiptDigest"] = compute_apply_receipt_digest(payload)

    with pytest.raises(ParameterBitPackingError):
        validate_apply_result(args, payload)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload["compressedParameterNames"].reverse(),
        lambda payload: payload["approvedSafeCandidateNames"].reverse(),
        lambda payload: payload["excludedParameters"].reverse(),
        lambda payload: payload["excludedParameters"][0]["reasons"].reverse(),
    ],
)
def test_apply_rejects_noncanonical_lists_even_with_recomputed_receipt(mutator) -> None:
    args, _ = approved()
    payload = apply_payload(args)
    mutator(payload)
    payload["applyReceiptDigest"] = compute_apply_receipt_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="canonical"):
        validate_apply_result(args, payload)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.update({"verified": False}),
        lambda p: p.update({"callbacksInvoked": False}),
        lambda p: p.update({"costAfterBits": 260}),
        lambda p: p.update({"costAfterBits": 257}),
        lambda p: p["compressedParameterNames"].append("UnsafeFloat"),
        lambda p: p.update({"excludedParameters": p["excludedParameters"][:-1]}),
        lambda p: p["excludedParameters"][0].update({"stateDigest": "0" * 64}),
        lambda p: p.update({"sourceUnchanged": False}),
        lambda p: p.update({"sourceSceneDirtyAfter": True}),
        lambda p: p.update({"sourceStateDigestAfter": "0" * 64}),
        lambda p: p.update({"sourceAssetSetDigestAfter": "0" * 64}),
        lambda p: p["output"].update({"scenePersistent": True}),
        lambda p: p["output"].update({"scenePath": "Assets/Generated.unity"}),
        lambda p: p["output"].update({"prefabPath": f"{bitpack.GENERATED_ROOT}/Other/Other.prefab"}),
        lambda p: p["output"].update({"prefabPersistent": False}),
        lambda p: p["output"].update({"prefabExistsAfter": False}),
        lambda p: p["output"].update({"prefabGuid": "0" * 32}),
        lambda p: p["output"].update({"prefabPortableAvatarDigest": "0" * 64}),
        lambda p: p["output"].update({"sceneLoadedAfter": True}),
        lambda p: p.update({"temporaryObjectResidue": True}),
        lambda p: p["generated"].update({"addedEntryCount": 1}),
        lambda p: p["generated"].update({"targetResidue": True}),
        lambda p: p["generated"].update({"stagingRoot": bitpack.GENERATED_ROOT + "/Other"}),
        lambda p: p["generated"].update({"stagingRemoved": False}),
        lambda p: p["managedOutput"].update({"root": "Assets/Other"}),
        lambda p: p["managedOutput"].update({"targetRoot": bitpack.OUTPUT_KIND_ROOT + "/Other"}),
        lambda p: p["managedOutput"].update({"rootExistsBefore": False}),
        lambda p: p["managedOutput"].update({"rootExistsAfter": False}),
        lambda p: p["managedOutput"].update({"treeDigestBefore": "0" * 64}),
        lambda p: p["managedOutput"].update({"entryCountAfter": 14}),
        lambda p: p["managedOutput"].update({"targetSubtreeCount": 2}),
        lambda p: p["managedOutput"].update({"modifiedEntryCount": 1}),
        lambda p: p["managedOutput"].update({"removedEntryCount": 1}),
        lambda p: p["managedOutput"].update({"leaseBound": False}),
        lambda p: p["protectedProjectTree"].update({"treeDigestAfter": "0" * 64}),
        lambda p: p["protectedProjectTree"].update({"rootIdentityDigestAfter": "0" * 64}),
        lambda p: p["protectedProjectTree"].update({"entryCountAfter": p["protectedProjectTree"]["entryCountAfter"] + 1}),
        lambda p: p.update({"cleanupRequired": True}),
        lambda p: p["capability"].update({"packageTreeSha256": "0" * 64}),
        lambda p: p.update({"projectPath": "D:/OtherProject"}),
        lambda p: p.update({"applyReceiptDigest": "f" * 64}),
    ],
)
def test_apply_rejects_cost_exclusion_source_output_or_capability_drift(mutator) -> None:
    args, _ = approved()
    payload = apply_payload()
    mutator(payload)

    with pytest.raises(ParameterBitPackingError):
        validate_apply_result(args, payload)


def test_authoritative_backend_registers_preview_and_supervised_write() -> None:
    assert TOOL_NAME in authoritative_writes.AUTHORITATIVE_UNITY_WRITE_TOOLS
    assert TOOL_NAME in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
    assert TOOL_NAME in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    assert "vrcforge_preview_parameter_bit_packing" in dashboard_server.AGENT_GATEWAY._tools


def test_dashboard_preview_preparer_and_apply_share_one_verified_receipt(
    tmp_path: Path,
) -> None:
    project = tmp_path / "UnityProject"
    for root in ("Assets", "Packages", "ProjectSettings"):
        (project / root).mkdir(parents=True, exist_ok=True)
    project_path = str(project.resolve())
    params = {"projectPath": project_path, **request_arguments()}
    preview_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": preview_payload(project_path)},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=preview_result) as invoke,
    ):
        plan = dashboard_server.preview_parameter_bit_packing_sync(deepcopy(params))
        prepared, approval = dashboard_server.prepare_unity_mcp_write_request(
            build_wrapper_arguments(deepcopy(params)),
            {"spoofed": True},
        )

    assert plan == {"ok": True, "preview": approval}
    assert prepared["toolName"] == TOOL_NAME
    assert prepared["projectPath"] == project_path
    assert prepared["arguments"]["expectedPreviewDigest"] == approval["previewDigest"]
    assert prepared["arguments"]["runBuildCallbacks"] is True
    assert "spoofed" not in approval
    assert [call.args[1] for call in invoke.call_args_list] == [TOOL_NAME, TOOL_NAME]
    for call in invoke.call_args_list:
        assert call.args[2]["preview"] is True
        assert call.args[2]["runBuildCallbacks"] is False
        assert not any(
            key.startswith("expected") and key != "expectedProjectPath"
            for key in call.args[2]
        )

    apply_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": apply_payload(prepared["arguments"])},
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=apply_result),
    ):
        executed = dashboard_server.unity_mcp_write_sync(deepcopy(prepared))
    assert executed["ok"] is True

    forged_payload = apply_payload(prepared["arguments"])
    forged_payload["sourceStateDigestAfter"] = "0" * 64
    forged_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="private transport detail",
        stderr="private transport detail",
        payload={"data": forged_payload},
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=forged_result),
    ):
        rejected = dashboard_server.unity_mcp_write_sync(deepcopy(prepared))
    assert rejected == {
        "ok": False,
        "toolName": TOOL_NAME,
        "error": "Parameter bit-packing apply returned an invalid verification receipt.",
    }


def test_fastapi_preview_request_and_approved_execution_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "UnityProject"
    for root in ("Assets", "Packages", "ProjectSettings"):
        (project / root).mkdir(parents=True, exist_ok=True)
    project_path = str(project.resolve())
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
        payload={"data": preview_payload(project_path)},
    )
    canonical_arguments, _approval = approved(project_path)
    apply_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": apply_payload(canonical_arguments)},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
            patch(
                "dashboard_server.invoke_unity_mcp",
                side_effect=[preview_result, preview_result, preview_result, apply_result],
        ) as invoke,
    ):
        client = TestClient(dashboard_server.app)
        try:
            preview_response = client.post(
                "/api/agent/tool/vrcforge_preview_parameter_bit_packing",
                headers=headers,
                json={
                    "agent_name": "backend-contract",
                    "params": {"projectPath": project_path, **request_arguments()},
                },
            )
            assert preview_response.status_code == 200
            assert preview_response.json()["result"]["preview"]["schema"] == APPROVAL_SCHEMA

            request_response = client.post(
                "/api/agent/tool/vrcforge_request_apply",
                headers=headers,
                json={
                    "agent_name": "backend-contract",
                    "params": {
                        "target_tool": "vrcforge_unity_mcp_write",
                        "arguments": wrapper(project_path),
                        "preview": {"spoofed": True},
                        "reason": "Verify the supervised parameter build lane.",
                    },
                },
            )
            assert request_response.status_code == 200
            pending = request_response.json()["result"]
            assert pending["status"] == "pending"
            assert "spoofed" not in pending["approval"]["preview"]

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

    assert [call.args[1] for call in invoke.call_args_list] == [
        TOOL_NAME,
        TOOL_NAME,
        TOOL_NAME,
        TOOL_NAME,
    ]
    assert invoke.call_args_list[-1].args[2]["preview"] is False
    assert invoke.call_args_list[-1].args[2]["runBuildCallbacks"] is True


def test_public_csharp_tool_uses_only_public_build_dispatch_and_rejects_deprecated_path() -> None:
    source = Path("Assets/VRCForge/Editor/ParameterBitPackingTool.cs").read_text(encoding="utf-8")
    evidence = Path("Assets/VRCForge/Editor/ParameterBitPackingEvidence.cs").read_text(encoding="utf-8")

    assert "VRCBuildPipelineCallbacks.OnPreprocessAvatar" in source
    assert "PrefabUtility.SaveAsPrefabAsset" in source
    assert source.index("PrefabUtility.SaveAsPrefabAsset") < source.index("AssetDatabase.MoveAsset")
    assert "CacheTransaction.Create" in source
    assert "cacheTransaction.Restore()" in source
    assert "File.Replace(nextPath, journalPath" in source
    assert source.index('WriteJournal("closing", true)') < source.index("completed = true")
    assert source.index("Directory.Delete(transactionRoot, true)") < source.index("completed = true")
    assert "A cache copy exceeds the bounded entry limit." in source
    assert "A cache copy exceeds the bounded byte limit." in source
    assert "CaptureTreeAbsolute(cacheRoot, CacheContentSchema + \".restore_target\")" in source
    assert "CaptureAssetTreeManifest" in source
    assert "guidPreservingWholeTreeMove = true" in source
    assert '"current-target-only"' in source
    assert "BuildTarget.StandaloneWindows64" in source
    assert "CaptureOutputPrefab" in source
    assert "ParameterCompressorService" not in source
    assert "VRCFuryInjectorBuilder" not in source
    assert "UnlimitedParameters" not in source
    assert "EditorPrefs.Set" not in source
    assert "EditorPrefs.Delete" not in source
    assert "LocalApplicationData" not in source
    assert "Environment.GetFolderPath" not in source
    assert "SearchOption.AllDirectories" not in source
    assert "System.Reflection.MethodInfo.Invoke" not in source
    assert "ordered_parameters" in evidence
    assert "menu_graph" in evidence
    assert "animator_behavior" in evidence
    assert "parameter_codec_mapping" in evidence
    assert "EditorJsonUtility.ToJson" not in evidence


def test_protected_tree_uses_only_fixed_persistent_project_roots() -> None:
    source = Path("Assets/VRCForge/Editor/ParameterBitPackingTool.cs").read_text(encoding="utf-8")
    roots_start = source.index("private static readonly string[] ProtectedProjectRoots")
    roots_end = source.index("private static readonly HashSet<string> PreviewKeys")
    roots_block = source[roots_start:roots_end]
    capture_start = source.index("private static TreeSnapshot CaptureProtectedTree()")
    capture_end = source.index("private static void CaptureProtectedRoot")
    capture_block = source[capture_start:capture_end]

    for root in ("Assets", "Packages", "ProjectSettings"):
        assert f'"{root}"' in roots_block
    for excluded in ("Library", "Temp", "Logs", "UserSettings"):
        assert f'"{excluded}"' not in roots_block
    assert "foreach (var relativeRoot in ProtectedProjectRoots)" in capture_block
    assert "Path.Combine(project, relativeRoot)" in capture_block


def test_csharp_tool_rejects_dirty_registered_assets_and_open_project_scenes_before_mutation() -> None:
    source = Path("Assets/VRCForge/Editor/ParameterBitPackingTool.cs").read_text(encoding="utf-8")
    fixture = Path(
        "tests/fixtures/primitive_basis/parameter_bit_packing/ParameterBitPackingFixtureProbe.cs"
    ).read_text(encoding="utf-8")
    guard_start = source.index(
        "private static void RequireNoDirtyProjectAssets(params string[] allowedDirtyRoots)"
    )
    guard_end = source.index("private static bool IsProjectOwnedAssetPath", guard_start)
    guard = source[guard_start:guard_end]

    assert "RegisteredAssetPathScanLimit" in guard
    assert "RegisteredAssetObjectScanLimit" in guard
    assert "OpenProjectSceneScanLimit" in guard
    assert "AssetDatabase.GetAllAssetPaths()" in guard
    assert "AssetDatabase.LoadAllAssetsAtPath(path)" in guard
    assert "AssetImporter.GetAtPath(path)" in guard
    assert "EditorUtility.IsPersistent" in guard
    assert "EditorUtility.IsDirty" in guard
    assert "SceneManager.sceneCount" in guard
    assert "scene.isDirty" in guard
    assert "LoadAssetAtPath<SceneAsset>" in guard
    assert "AssetDatabase.SaveAssets" not in guard

    calls = [match.start() for match in re.finditer(r"RequireNoDirtyProjectAssets\(\);", source)]
    assert len(calls) == 2
    assert calls[0] < source.index("if (preview)")
    assert source.index("ValidateApplyPreconditions") < calls[1]
    assert calls[1] < source.index("cacheTransaction = CacheTransaction.Create")
    assert calls[1] < source.index("EditorSceneManager.NewScene")
    assert calls[1] < source.index("AssetDatabase.SaveAssets")
    for save in re.finditer(r"AssetDatabase\.SaveAssets\(\);", source):
        assert "RequireNoDirtyProjectAssets(" in source[max(0, save.start() - 180) : save.start()]

    assert "dirty registered asset preview unexpectedly succeeded" in fixture
    assert "post-preview dirty registered asset apply unexpectedly succeeded" in fixture
    assert "dirty guard disk bytes changed" in fixture
    assert "cache journal residue" in fixture


def test_disposable_fixture_keeps_dangerous_parameters_unsynced_and_checks_cleanup() -> None:
    source = Path(
        "tests/fixtures/primitive_basis/parameter_bit_packing/ParameterBitPackingFixtureProbe.cs"
    ).read_text(encoding="utf-8")

    assert "FT/JawOpen" in source
    assert "Puppet/X" in source
    assert "OSC/Raw" in source
    assert "networkSynced: false" in source
    assert "VerifySourceUnchanged" in source
    assert "VerifyNoResidueAfterFailure" in source
    assert "SeedGeneratedCache" in source
    assert "CaptureCacheReceipt" in source
    assert "RequireNoActiveCacheTransaction" in source
    assert "VerifyDurableOutputAfterApprovedApply" in source
    assert "guidPreservingWholeTreeMove" in source
    assert "VRCAvatarParameterDriver" in source
    assert "Assets/VRCForge/Generated/ParameterBitPacking/" in source
    assert "VRCFORGE_PARAMETER_BIT_PACKING_PROBE_OK" in source
