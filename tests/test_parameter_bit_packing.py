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
from approved_unity_execution import current_approved_unity_execution
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

CAPABILITY_PROFILE_FIXTURES = {
    "embedded-minimal-v1": {
        "callbackAssemblySha256": "e568293abe29428b7fb35d805cb3053cc8437621a19ae714d5fc76931d9fe10f",
        "callbackRosterCount": 16,
        "callbackRosterDigest": "305bc43e713cc76fe13f16d99e6e1d7137d87c066d6a46a6917196b909de10ba",
        "callbackAssemblySetCount": 3,
        "callbackAssemblySetDigest": "1884970046bc7b2f7194cef03c3c085dffb02df8cc6eddc9173e90fd231794d1",
    },
    "embedded-extended-v1": {
        "callbackAssemblySha256": "c220c73e91f69aa88425c8cd81cf271a6b484eb5b34cca15a33f6edcde89c8f4",
        "callbackRosterCount": 23,
        "callbackRosterDigest": "a345576b0aad61991a4518413a5685d3b9df85e9ad33af50ff6b04a71d0f920e",
        "callbackAssemblySetCount": 7,
        "callbackAssemblySetDigest": "2eebf5d668c881ac7b208191e488c6a69c896549473fb44281d12c07404dc221",
    },
}


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


def capability(profile_id: str = "embedded-minimal-v1") -> dict:
    profile = CAPABILITY_PROFILE_FIXTURES[profile_id]
    value = {
        "packageId": bitpack.PACKAGE_ID,
        "packageVersion": bitpack.PACKAGE_VERSION,
        "packageAuthor": bitpack.PACKAGE_AUTHOR,
        "packageArchiveSha256": bitpack.PACKAGE_ARCHIVE_SHA256,
        "packageTreeSha256": bitpack.PACKAGE_TREE_SHA256,
        "packageFileCount": bitpack.PACKAGE_FILE_COUNT,
        "packageRootIdentityDigest": "7" * 64,
        "profileId": profile_id,
        "callbackAssemblyName": bitpack.CALLBACK_ASSEMBLY_NAME,
        "callbackAssemblyVersion": bitpack.CALLBACK_ASSEMBLY_VERSION,
        "callbackAssemblyPublicKeyToken": bitpack.CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN,
        "callbackAssemblySha256": profile["callbackAssemblySha256"],
        "sdkCallbackAssemblyName": bitpack.SDK_CALLBACK_ASSEMBLY_NAME,
        "sdkCallbackAssemblyVersion": bitpack.SDK_CALLBACK_ASSEMBLY_VERSION,
        "sdkCallbackAssemblyPublicKeyToken": bitpack.SDK_CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN,
        "sdkCallbackAssemblySha256": bitpack.SDK_CALLBACK_ASSEMBLY_SHA256,
        "callbackType": bitpack.CALLBACK_TYPE,
        "callbackSignature": bitpack.CALLBACK_SIGNATURE,
        "registeredHookType": bitpack.REGISTERED_HOOK_TYPE,
        "registeredHookCount": 1,
        "callbackRosterCount": profile["callbackRosterCount"],
        "callbackRosterDigest": profile["callbackRosterDigest"],
        "callbackAssemblySetCount": profile["callbackAssemblySetCount"],
        "callbackAssemblySetDigest": profile["callbackAssemblySetDigest"],
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


def preview_payload(
    project_path: str = PROJECT_PATH,
    *,
    profile_id: str = "embedded-minimal-v1",
) -> dict:
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
        "capability": capability(profile_id),
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
            "rootIdentityCountBefore": 7,
            "exists": True,
            "reparseFree": True,
        },
        "auxiliaryGenerated": {
            "root": bitpack.AUXILIARY_GENERATED_ROOT,
            "packageRoot": bitpack.AUXILIARY_PACKAGE_ROOT,
            "packageRootIdentityDigestBefore": "e" * 64,
            "packageManifestDigestBefore": "f" * 64,
            "packageManifestIdentityDigestBefore": "1" * 64,
            "rootExistsBefore": False,
            "treeDigestBefore": "2" * 64,
            "contentDigestBefore": "3" * 64,
            "entryCountBefore": 0,
            "byteCountBefore": 0,
            "backupMaxEntries": bitpack.CACHE_BACKUP_MAX_ENTRIES,
            "backupMaxBytes": bitpack.CACHE_BACKUP_MAX_BYTES,
            "journalSchema": bitpack.AUXILIARY_JOURNAL_SCHEMA,
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


def approved(
    project_path: str = PROJECT_PATH,
    *,
    profile_id: str = "embedded-minimal-v1",
) -> tuple[dict, dict]:
    canonical, approval = bind_authoritative_preview(
        wrapper(project_path),
        preview_payload(project_path, profile_id=profile_id),
    )
    return canonical["arguments"], approval


def apply_payload(
    approved_arguments: dict | None = None,
    *,
    profile_id: str = "embedded-minimal-v1",
) -> dict:
    args = (
        deepcopy(approved_arguments)
        if approved_arguments is not None
        else approved(profile_id=profile_id)[0]
    )
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
        "capability": capability(profile_id),
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
        "auxiliaryGenerated": {
            "root": bitpack.AUXILIARY_GENERATED_ROOT,
            "packageRoot": bitpack.AUXILIARY_PACKAGE_ROOT,
            "packageRootIdentityDigestBefore": args[
                "expectedAuxiliaryPackageRootIdentityDigest"
            ],
            "packageRootIdentityDigestAfter": args[
                "expectedAuxiliaryPackageRootIdentityDigest"
            ],
            "packageManifestDigestBefore": args[
                "expectedAuxiliaryPackageManifestDigest"
            ],
            "packageManifestDigestAfter": args[
                "expectedAuxiliaryPackageManifestDigest"
            ],
            "packageManifestIdentityDigestBefore": args[
                "expectedAuxiliaryPackageManifestIdentityDigest"
            ],
            "packageManifestIdentityDigestAfter": args[
                "expectedAuxiliaryPackageManifestIdentityDigest"
            ],
            "rootExistsBefore": args["expectedAuxiliaryRootExistsBefore"],
            "rootExistsAfter": args["expectedAuxiliaryRootExistsBefore"],
            "treeDigestBefore": args["expectedAuxiliaryTreeDigestBefore"],
            "treeDigestAfter": args["expectedAuxiliaryTreeDigestBefore"],
            "contentDigestBefore": args["expectedAuxiliaryContentDigestBefore"],
            "contentDigestAfter": args["expectedAuxiliaryContentDigestBefore"],
            "entryCountBefore": args["expectedAuxiliaryEntryCountBefore"],
            "entryCountAfter": args["expectedAuxiliaryEntryCountBefore"],
            "byteCountBefore": args["expectedAuxiliaryByteCountBefore"],
            "byteCountAfter": args["expectedAuxiliaryByteCountBefore"],
            "observedRootExists": True,
            "observedTreeDigest": "4" * 64,
            "observedContentDigest": "5" * 64,
            "observedEntryCount": 8,
            "observedByteCount": 2048,
            "ownedRootIdentityDigest": "6" * 64,
            "createdByOperation": True,
            "restorationMode": "removed_created_root",
            "restoreVerified": True,
            "backupBounded": True,
            "backupMaxEntries": bitpack.CACHE_BACKUP_MAX_ENTRIES,
            "backupMaxBytes": bitpack.CACHE_BACKUP_MAX_BYTES,
            "journalSchema": bitpack.AUXILIARY_JOURNAL_SCHEMA,
            "journalId": "parameter-auxiliary-generated-" + "b" * 32,
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
    assert args["expectedAuxiliaryPackageRootIdentityDigest"] == "e" * 64
    assert args["expectedAuxiliaryPackageManifestDigest"] == "f" * 64
    assert args["expectedAuxiliaryPackageManifestIdentityDigest"] == "1" * 64
    assert args["expectedAuxiliaryRootExistsBefore"] is False
    assert args["expectedAuxiliaryTreeDigestBefore"] == "2" * 64
    assert args["expectedAuxiliaryContentDigestBefore"] == "3" * 64
    assert args["expectedAuxiliaryEntryCountBefore"] == 0
    assert args["expectedAuxiliaryByteCountBefore"] == 0
    assert args["expectedPreferenceDigest"] == preview_payload()["preferences"]["receiptDigest"]
    assert args["expectedProtectedTreeDigestBefore"] == "b" * 64
    assert args["expectedProtectedEntryCountBefore"] == 1400
    assert args["expectedRootIdentityDigest"] == "c" * 64
    assert args["expectedRootIdentityCount"] == 7
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
        "expectedAuxiliaryPackageRootIdentityDigest",
        "expectedAuxiliaryPackageManifestDigest",
        "expectedAuxiliaryPackageManifestIdentityDigest",
        "expectedAuxiliaryRootExistsBefore",
        "expectedAuxiliaryTreeDigestBefore",
        "expectedAuxiliaryContentDigestBefore",
        "expectedAuxiliaryEntryCountBefore",
        "expectedAuxiliaryByteCountBefore",
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
    ("profile_id", "expected_digest"),
    [
        ("embedded-minimal-v1", "9f42739a6b8d94158c50525e474ade1d294788fbb29dc5733deacdbba7c55c8e"),
        ("embedded-extended-v1", "f6c6bd34af4fca5c3ce734feabcaf227a1f6a9d287e6c3d8f04f933e8390414f"),
    ],
)
def test_preview_and_apply_accept_each_exact_capability_profile(
    profile_id: str,
    expected_digest: str,
) -> None:
    payload = preview_payload(profile_id=profile_id)

    canonical, approval = bind_authoritative_preview(wrapper(), payload)
    applied = validate_apply_result(
        canonical["arguments"],
        apply_payload(canonical["arguments"], profile_id=profile_id),
    )

    assert payload["capability"]["capabilityDigest"] == expected_digest
    assert approval["capability"] == payload["capability"]
    assert applied["capability"] == payload["capability"]


def test_compatibility_capability_constants_alias_minimal_profile() -> None:
    minimal = CAPABILITY_PROFILE_FIXTURES["embedded-minimal-v1"]

    assert bitpack.CAPABILITY_PROFILE_ID == "embedded-minimal-v1"
    assert bitpack.CALLBACK_ASSEMBLY_SHA256 == minimal["callbackAssemblySha256"]
    assert bitpack.CALLBACK_ROSTER_COUNT == minimal["callbackRosterCount"]
    assert bitpack.CALLBACK_ROSTER_DIGEST == minimal["callbackRosterDigest"]
    assert bitpack.CALLBACK_ASSEMBLY_SET_COUNT == minimal["callbackAssemblySetCount"]
    assert bitpack.CALLBACK_ASSEMBLY_SET_DIGEST == minimal["callbackAssemblySetDigest"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profileId", "unknown-profile-v1"),
        (
            "callbackAssemblySha256",
            CAPABILITY_PROFILE_FIXTURES["embedded-extended-v1"]["callbackAssemblySha256"],
        ),
        ("callbackRosterCount", CAPABILITY_PROFILE_FIXTURES["embedded-extended-v1"]["callbackRosterCount"]),
        ("callbackRosterDigest", CAPABILITY_PROFILE_FIXTURES["embedded-extended-v1"]["callbackRosterDigest"]),
        (
            "callbackAssemblySetCount",
            CAPABILITY_PROFILE_FIXTURES["embedded-extended-v1"]["callbackAssemblySetCount"],
        ),
        (
            "callbackAssemblySetDigest",
            CAPABILITY_PROFILE_FIXTURES["embedded-extended-v1"]["callbackAssemblySetDigest"],
        ),
    ],
)
def test_preview_rejects_unknown_or_mixed_capability_profile(field: str, value: object) -> None:
    payload = preview_payload()
    payload["capability"][field] = value
    payload["capability"]["capabilityDigest"] = compute_capability_digest(payload["capability"])
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ParameterBitPackingError, match="capability"):
        bind_authoritative_preview(wrapper(), payload)


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
        lambda p: p["auxiliaryGenerated"].update({"root": "Packages/other/__Generated"}),
        lambda p: p["auxiliaryGenerated"].update({"packageRootIdentityDigestBefore": "0" * 64}),
        lambda p: p["auxiliaryGenerated"].update({"rootExistsBefore": True}),
        lambda p: p["auxiliaryGenerated"].update({"entryCountBefore": 1}),
        lambda p: p["auxiliaryGenerated"].update({"reparseFree": False}),
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
        "auxiliary",
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
        "auxiliary": payload["auxiliaryGenerated"],
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
    assert result["auxiliaryGenerated"]["rootExistsAfter"] is False
    assert result["auxiliaryGenerated"]["createdByOperation"] is True
    assert result["auxiliaryGenerated"]["restorationMode"] == "removed_created_root"
    assert result["auxiliaryGenerated"]["restoreVerified"] is True
    assert result["auxiliaryGenerated"]["journalClosed"] is True
    assert result["managedOutput"]["addedEntryCount"] == 7
    assert result["managedOutput"]["leaseBound"] is True
    assert result["managedOutput"]["guidPreservingWholeTreeMove"] is True
    assert result["managedOutput"]["finalManifest"]["noTemporaryReferences"] is True
    assert result["behaviorProof"]["platformScope"] == bitpack.PLATFORM_SCOPE
    assert result["platformProof"]["crossPlatformEquivalent"] is False


def test_apply_accepts_exact_byte_restore_for_a_present_auxiliary_baseline() -> None:
    preview = preview_payload()
    preview["auxiliaryGenerated"].update(
        {
            "rootExistsBefore": True,
            "treeDigestBefore": "7" * 64,
            "contentDigestBefore": "8" * 64,
            "entryCountBefore": 9,
            "byteCountBefore": 4096,
        }
    )
    preview["previewDigest"] = compute_preview_digest(preview)
    canonical, _approval = bind_authoritative_preview(wrapper(), preview)
    result = apply_payload(canonical["arguments"])
    result["auxiliaryGenerated"].update(
        {
            "rootExistsBefore": True,
            "rootExistsAfter": True,
            "treeDigestBefore": "7" * 64,
            "treeDigestAfter": "9" * 64,
            "contentDigestBefore": "8" * 64,
            "contentDigestAfter": "8" * 64,
            "entryCountBefore": 9,
            "entryCountAfter": 9,
            "byteCountBefore": 4096,
            "byteCountAfter": 4096,
            "createdByOperation": False,
            "restorationMode": "restored_baseline",
        }
    )
    result["applyReceiptDigest"] = compute_apply_receipt_digest(result)

    validated = validate_apply_result(canonical["arguments"], result)

    assert validated["auxiliaryGenerated"]["treeDigestAfter"] != validated[
        "auxiliaryGenerated"
    ]["treeDigestBefore"]
    assert validated["auxiliaryGenerated"]["contentDigestAfter"] == validated[
        "auxiliaryGenerated"
    ]["contentDigestBefore"]


@pytest.mark.parametrize(
    "case",
    [
        "top",
        "source",
        "source_evidence",
        "output",
        "generated",
        "auxiliary",
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
        "auxiliary": payload["auxiliaryGenerated"],
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


@pytest.mark.parametrize("case", ["cache", "auxiliary", "manifest", "codec", "preference", "platform", "journal", "closure", "source"])
def test_apply_rejects_reframed_nested_proof_drift(case: str) -> None:
    args, _ = approved()
    payload = apply_payload(args)
    if case == "cache":
        payload["generated"]["contentDigestAfter"] = "f" * 64
    elif case == "auxiliary":
        payload["auxiliaryGenerated"]["contentDigestAfter"] = "f" * 64
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
        lambda p: p["auxiliaryGenerated"].update({"root": "Packages/other/__Generated"}),
        lambda p: p["auxiliaryGenerated"].update({"packageRootIdentityDigestAfter": "0" * 64}),
        lambda p: p["auxiliaryGenerated"].update({"packageManifestDigestAfter": "0" * 64}),
        lambda p: p["auxiliaryGenerated"].update({"rootExistsAfter": True}),
        lambda p: p["auxiliaryGenerated"].update({"contentDigestAfter": "0" * 64}),
        lambda p: p["auxiliaryGenerated"].update({"createdByOperation": False}),
        lambda p: p["auxiliaryGenerated"].update({"restorationMode": "restored_baseline"}),
        lambda p: p["auxiliaryGenerated"].update({"restoreVerified": False}),
        lambda p: p["auxiliaryGenerated"].update({"journalClosed": False}),
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
    assert "immediatePrefab == importedPrefab" not in source
    assert "AssetDatabase.GetAssetPath(immediatePrefab) == temporaryPrefabPath" in source
    assert source.index("PrefabUtility.SaveAsPrefabAsset") < source.index("AssetDatabase.MoveAsset")
    assert "CacheTransaction.Plan" in source
    assert "cacheTransaction.Prepare();" in source
    assert "cacheTransaction.AbortPreparation()" in source
    assert "RequireSafeOwnedTreeForDeletion(transactionRoot);" in source
    assert "unfinished.Length == 0" in source
    assert '"parameter-bit-packing.lock"' in source
    assert "FileMode.CreateNew" in source
    assert "FileOptions.DeleteOnClose | FileOptions.WriteThrough" in source
    cache_transaction = source.index("private sealed class CacheTransaction")
    prepare_body = source[
        source.index("internal void Prepare()", cache_transaction) : source.index(
            "internal bool Restore(", cache_transaction
        )
    ]
    assert prepare_body.index("transactionLock = new FileStream") < prepare_body.index(
        "Directory.EnumerateFileSystemEntries"
    )
    assert "transactionLock.Flush(true);" in prepare_body
    assert "ReleaseLock();" in source
    assert "cacheTransaction.Restore(\n                            allowAuxiliaryRootDirty:" in source
    assert "File.Replace(nextPath, journalPath" in source
    complete = source[
        source.index("internal void Complete()", cache_transaction) : source.index(
            "internal static void RequireSafeOwnedTreeForDeletion", cache_transaction
        )
    ]
    assert complete.index('WriteJournal("closing", true)') < complete.index("completed = true")
    assert complete.index("DeleteOwnedTransactionTreeWithRetry(transactionRoot)") < complete.index(
        "completed = true"
    )
    assert "A cache copy exceeds the bounded entry limit." in source
    assert "A cache copy exceeds the bounded byte limit." in source
    assert "CaptureTreeAbsolute(cacheRoot, CacheContentSchema + \".restore_target\")" in source
    assert "CaptureAssetTreeManifest" in source
    assert "guidPreservingWholeTreeMove = true" in source
    assert '"current-target-only"' in source
    assert "BuildTarget.StandaloneWindows64" in source
    assert "CaptureOutputPrefab" in source
    assert 'CapabilitySchema = "vrcforge.parameter_capability.v2"' in source
    assert 'Id = "embedded-minimal-v1"' in source
    assert 'Id = "embedded-extended-v1"' in source
    assert "CallbackAssemblySetSchema" in source
    assert ".Concat(new[] { runtimeAssembly })" in source
    assert "capability.CallbackAssemblyPaths.Count == capability.CallbackAssemblySetCount" in source
    assert "EnsureNonInteractiveBuildPolicy(clone);" in source
    assert 'NonInteractiveFeatureModeName = "Disabled"' in source
    assert source.index("EnsureNonInteractiveBuildPolicy(clone);") < source.index(
        "VRCBuildPipelineCallbacks.OnPreprocessAvatar"
    )
    assert "existingPolicies.Length <= 1" in source
    assert "contentField.SetValue(policyComponent, policyFeature)" in source
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
    assert "Animator state names must be unique within a state machine." not in evidence
    assert "Animator child state-machine names must be unique." not in evidence
    assert '"/state:" + stateIndex.ToString(CultureInfo.InvariantCulture)' in evidence
    assert '"/machine:" + machineIndex.ToString(CultureInfo.InvariantCulture)' in evidence
    assert "var defaultStateToken = StateWithinMachineToken(machine.defaultState, states);" in evidence
    assert "ControllerObjectToken(machine.defaultState, controller)" not in evidence
    assert 'return "state:" + index.ToString(CultureInfo.InvariantCulture)' in evidence
    assert "var animatorPathIndex = BuildAnimatorPathIndex(layer.stateMachine, layerPath);" in evidence
    assert "AnimatorStatePathToken(transition.destinationState, animatorPathIndex)" in evidence
    assert "AnimatorStateMachinePathToken(transition.destinationStateMachine, animatorPathIndex)" in evidence
    assert "ControllerObjectToken(transition.destinationState, controller)" not in evidence
    assert "ControllerObjectToken(transition.destinationStateMachine, controller)" not in evidence
    assert "An animator state has more than one structural path." in evidence
    assert 'setStage?.Invoke("animator_subset_transition_identity");' in evidence
    assert '"destination", "exit", "mute", "solo", "conditions", "duration", "exit_time"' in evidence
    assert "transition.name" not in evidence
    assert "Scope = path" in evidence
    assert "SemanticName = index.ToString(CultureInfo.InvariantCulture)" in evidence
    assert "Body = Frame(path) + Frame(index) + string.Concat(semanticFields.Select(Frame))" in evidence
    excluded_start = evidence.index("var excludedBefore = source.Parameters")
    excluded_end = evidence.index('setStage?.Invoke("codec_graph")', excluded_start)
    excluded_block = evidence[excluded_start:excluded_end]
    assert ".Select(row => row.Canonical)" not in excluded_block
    assert "Frame(row.Name) + Frame(row.Type) + Frame(row.DefaultValue)" in excluded_block
    assert "+ Frame(row.Saved) + Frame(row.NetworkSynced)" in excluded_block
    assert 'operationStage = "temporary_output_prefab_save_flag";' in source
    assert 'operationStage = "temporary_output_prefab_import_readback";' in source
    assert 'operationStage = "temporary_output_prefab_identity_reconciliation";' in source
    assert 'operationStage = "temporary_output_prefab_receipt_capture";' in source
    assert 'stage => operationStage = "temporary_output_prefab_receipt_" + stage' in source
    assert 'setStage?.Invoke("animator_behavior");' in source
    assert "PortableObjectDigest" in evidence
    assert "PortableComponentDigest" in evidence
    assert "PortablePropertyDigest" in evidence
    assert "PortableTransformEditorPropertyDigest" in evidence
    assert "PortableTransformRuntimePropertyDigest" in evidence
    assert "PortableTransformSpatialPropertyDigest" in evidence
    assert "PortableTransformHierarchyPropertyDigest" in evidence
    assert "PortableTransformOtherPropertyDigest" in evidence
    assert "PortableDescriptorPropertyDigest" in evidence
    assert "PortableDescriptorPropertyGroupDigests" in evidence
    assert "DescriptorPropertyGroup(iterator.propertyPath)" in evidence
    assert "FirstMismatchedDescriptorPropertyGroup(" in source
    assert '"portable_avatar_properties_descriptor_"' in source
    assert "PortableOtherPropertyDigest" in evidence
    assert "if (component is Transform) continue;" in evidence
    assert '|| iterator.propertyPath == "serializedVersion")' in evidence
    assert '|| iterator.propertyPath == "m_GameObject"' in evidence
    assert 'iterator.propertyPath.StartsWith("m_GameObject.", StringComparison.Ordinal)' in evidence
    assert 'iterator.propertyPath.StartsWith("m_GameObject[", StringComparison.Ordinal)' in evidence
    assert 'iterator.propertyPath.StartsWith("m_CorrespondingSourceObject.", StringComparison.Ordinal)' in evidence
    assert 'iterator.propertyPath.StartsWith("m_PrefabInstance.", StringComparison.Ordinal)' in evidence
    assert 'iterator.propertyPath.StartsWith("m_PrefabAsset.", StringComparison.Ordinal)' in evidence
    assert "Frame(VectorText(transform.localPosition))" in evidence
    assert "Frame(QuaternionText(transform.localRotation))" in evidence
    assert "Frame(VectorText(transform.localScale))" in evidence
    assert 'propertyPath.StartsWith("m_LocalEulerAnglesHint.", StringComparison.Ordinal)' in evidence
    assert 'propertyPath == "serializedVersion"' in evidence
    assert '"portable_avatar_properties_transform_editor"' in source
    assert '"portable_avatar_properties_transform_hierarchy"' in source
    assert "ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate" in source
    controller_parameter_start = evidence.index(
        "var semanticDefault = ControllerParameterDefault(parameter);"
    )
    controller_parameters = evidence[
        controller_parameter_start : evidence.index(
            "var controllerLayers", controller_parameter_start
        )
    ]
    assert "Frame(parameterIndex)" not in controller_parameters
    assert "Frame(role) + Frame(parameter.name) + Frame(parameter.type)" in controller_parameters
    assert "Frame(semanticDefault)" in controller_parameters
    assert "Frame(FloatText(parameter.defaultFloat)) + Frame(parameter.defaultInt)" not in controller_parameters
    assert 'var role = group + ":" + value.type;' in evidence
    assert 'value.type + ":" + layerSlot.ToString' not in evidence
    assert 'var layerPath = role + "/layer:" + layer.name;' in evidence
    assert '"/layer:" + index.ToString(CultureInfo.InvariantCulture)' not in evidence
    assert "synchronizedLayer = controllerLayers[layer.syncedLayerIndex].name" in evidence
    assert "+ Frame(layer.iKPass) + Frame(layer.syncedLayerIndex)" not in evidence
    assert "var avatarMaskToken = AvatarMaskToken(layer.avatarMask, out var avatarMaskSummary);" in evidence
    assert "ObjectToken(layer.avatarMask, null)" not in evidence
    assert "FirstLayerMaskMismatchCategory(sourceLayers, outputLayers)" in evidence
    assert 'return "null";' in evidence
    assert 'StartsWith("mask:", StringComparison.Ordinal)' in evidence
    assert "Frame(row.Scope) + Frame(row.SemanticName)" in evidence
    assert 'summary = "mask_body_" + activeBodyPartCount.ToString' in evidence
    assert '"_transform_" + activeTransformCount.ToString' in evidence
    assert "SemanticMaskSummary = avatarMaskSummary" in evidence
    assert 'operationStage = "output_null_layer_mask_restore";' in source
    assert "RestoreSourceNullLayerMasks(" in source
    assert 'stage => operationStage = "output_null_layer_mask_restore_" + stage' in source
    assert '"persistent_other"' in source
    assert 'operationStage = "output_null_layer_mask_restore_dirty_scope";' in source
    assert 'operationStage = "output_null_layer_mask_restore_source_readback";' in source
    assert "normalizedSource.SourceStateDigest == beforeSource.SourceStateDigest" in source
    assert "verifyAuxiliaryTree: false" in source
    assert "if (verifyAuxiliaryTree)" in source
    assert 'row.SemanticFields[2] == "null"' in source
    assert "if (!expectedNullMasks.Contains(identity)) continue;" in source
    assert "layer.avatarMask = null;" in source
    assert "!EditorUtility.IsPersistent(controller)" in source
    assert "IsGeneratedMutationPath(controllerPath)" in source
    assert 'assetPath.StartsWith(GeneratedRoot + "/", StringComparison.Ordinal)' in source
    assert 'assetPath.StartsWith(AuxiliaryGeneratedRoot + "/", StringComparison.Ordinal)' in source


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


def test_csharp_tool_owns_only_the_fixed_auxiliary_generated_root_transactionally() -> None:
    source = Path("Assets/VRCForge/Editor/ParameterBitPackingTool.cs").read_text(encoding="utf-8")
    fixture = Path(
        "tests/fixtures/primitive_basis/parameter_bit_packing/ParameterBitPackingFixtureProbe.cs"
    ).read_text(encoding="utf-8")

    assert 'AuxiliaryPackageRoot = "Packages/nadena.dev.ndmf"' in source
    assert 'AuxiliaryGeneratedRoot = AuxiliaryPackageRoot + "/__Generated"' in source
    assert 'AuxiliaryPackageManifest = AuxiliaryPackageRoot + "/package.json"' in source
    assert 'AuxiliaryJournalSchema = "vrcforge.parameter_auxiliary_journal.v1"' in source
    assert "CaptureAuxiliaryGenerated()" in source
    assert "CaptureManagedTree(AuxiliaryGeneratedRoot" in source
    assert "packageManifestIdentityDigest" in source
    assert 'relative.Equals(AuxiliaryGeneratedRoot + ".meta"' in source
    assert 'relative.Equals(AuxiliaryGeneratedRoot,' in source

    guard_start = source.index(
        "private static void RequireNoDirtyProjectAssets(params string[] allowedDirtyRoots)"
    )
    guard_end = source.index("private static bool IsProjectOwnedAssetPath", guard_start)
    guard = source[guard_start:guard_end]
    assert "root == AuxiliaryGeneratedRoot" in guard
    assert "root.StartsWith(AuxiliaryGeneratedRoot" not in guard

    transaction_start = source.index("private sealed class AuxiliaryGeneratedTransaction")
    transaction = source[transaction_start:]
    assert '"parameter-auxiliary-generated-" + Guid.NewGuid().ToString("N")' in transaction
    assert '"parameter-auxiliary-generated.lock"' in transaction
    assert 'WriteJournal("prepared"' in transaction
    assert "CopyTree(AbsoluteProjectPath(AuxiliaryGeneratedRoot), backupRoot)" in transaction
    assert "File.Copy(auxiliaryMeta, backupMetaPath" in transaction
    assert "ObserveMutation()" in transaction
    assert "current.Tree.Digest == observed.Tree.Digest" in transaction
    assert "current.Tree.ContentDigest == observed.Tree.ContentDigest" in transaction
    assert "DeleteCreatedRoot" in transaction
    assert "RestorePresentBaseline" in transaction
    assert "final.Tree.ContentDigest == baseline.Tree.ContentDigest" in transaction
    assert "final.Tree.EntryCount == baseline.Tree.EntryCount" in transaction
    assert "final.Tree.TotalBytes == baseline.Tree.TotalBytes" in transaction
    assert "PackageRootIdentityDigest == baseline.PackageRootIdentityDigest" in transaction
    assert "PackageManifestIdentityDigest == baseline.PackageManifestIdentityDigest" in transaction
    assert "PackageManifestDigest == baseline.PackageManifestDigest" in transaction

    callback = source.index("VRCBuildPipelineCallbacks.OnPreprocessAvatar(clone)")
    observed = source.index("auxiliaryTransaction.ObserveMutation();", callback)
    allowed = source.index(
        "RequireNoDirtyProjectAssets(outputScene, GeneratedRoot, AuxiliaryGeneratedRoot)",
        callback,
    )
    assert callback < observed < allowed
    assert "auxiliaryTransaction.Restore(\n                            allowGeneratedRootDirty:" in source
    assert "auxiliaryTransaction.AbortPreparation()" in source
    assert "checkpoint_restore_required" in source

    assert 'AuxiliaryGeneratedRoot = "Packages/nadena.dev.ndmf/__Generated"' in fixture
    assert "auxiliaryBaseline" in fixture
    assert "CaptureAuxiliaryReceipt()" in fixture
    assert "fixture final auxiliary tree readback" in fixture


def test_failure_cleanup_breaks_owned_dirty_scope_cycles_and_fails_closed_on_unknown_output() -> None:
    source = Path("Assets/VRCForge/Editor/ParameterBitPackingTool.cs").read_text(encoding="utf-8")
    fixture = Path(
        "tests/fixtures/primitive_basis/parameter_bit_packing/ParameterBitPackingFixtureProbe.cs"
    ).read_text(encoding="utf-8")

    cleanup_start = source.index("private static bool TryCleanupFailure(")
    cleanup_end = source.index("private static bool EditorSceneManagerClose", cleanup_start)
    cleanup = source[cleanup_start:cleanup_end]
    output_cleanup = cleanup.index("RestoreManagedOutputAfterFailure(")
    auxiliary_restore = cleanup.index("auxiliaryTransaction.Restore(")
    cache_restore = cleanup.index("cacheTransaction.Restore(")
    global_clean = cleanup.index("RequireNoDirtyProjectAssets();", cache_restore)
    assert output_cleanup < auxiliary_restore < cache_restore < global_clean
    assert "allowGeneratedRootDirty: cacheTransaction != null" in cleanup
    assert "&& cacheTransaction.Prepared && !cacheTransaction.Restored" in cleanup
    assert "allowAuxiliaryRootDirty: auxiliaryTransaction != null" in cleanup
    assert "&& auxiliaryTransaction.Prepared && !auxiliaryTransaction.Restored" in cleanup

    helper_start = source.index("private static void RestoreManagedOutputAfterFailure(")
    helper = source[helper_start:cleanup_end]
    assert "stagedOutputManifest" in helper
    assert "outputManifest" in helper
    assert '"An unverified durable output target requires checkpoint restore."' in helper
    assert "CaptureAssetTreeManifest(" in helper
    assert "requireNoTemporaryReferences: false" in helper
    assert "VerifyGuidPreservingMove(" in helper
    assert "VerifyCreatedAssetFolder(folder);" in helper
    assert "SearchOption.TopDirectoryOnly).Any()" in helper
    assert "RequireNoDirtyProjectAssets(OutputRoot)" not in helper
    assert "allowedDirtyRoots.Add(GeneratedRoot)" in helper
    assert "allowedDirtyRoots.Add(AuxiliaryGeneratedRoot)" in helper
    assert 'allowedDirtyRoots.Add("Packages' not in helper

    auxiliary_start = source.index("private sealed class AuxiliaryGeneratedTransaction")
    cache_start = source.index("private sealed class CacheTransaction", auxiliary_start)
    auxiliary = source[auxiliary_start:cache_start]
    cache = source[cache_start:]
    assert "internal bool Restore(bool allowGeneratedRootDirty)" in auxiliary
    assert "RequireNoDirtyProjectAssets(AuxiliaryGeneratedRoot, GeneratedRoot)" in auxiliary
    assert "internal bool Restore(bool allowAuxiliaryRootDirty)" in cache
    assert "RequireNoDirtyProjectAssets(GeneratedRoot, AuxiliaryGeneratedRoot)" in cache
    assert "RequireNoDirtyProjectAssets(Packages" not in auxiliary
    assert "RequireNoDirtyProjectAssets(Packages" not in cache

    assert '"unsupported asset staging failure stage"' in fixture
    assert '"failure cache restore"' in fixture
    assert '"failure auxiliary tree restore"' in fixture


def test_transaction_close_is_idempotent_and_receipts_follow_verified_terminal_state() -> None:
    source = Path("Assets/VRCForge/Editor/ParameterBitPackingTool.cs").read_text(encoding="utf-8")

    success_start = source.index("var afterRoots = CaptureRootIdentities();")
    success_end = source.index("return new SuccessResponse(", success_start)
    success = source[success_start:success_end]
    auxiliary_complete = success.index("auxiliaryTransaction.Complete();")
    cache_complete = success.index("cacheTransaction.Complete();")
    terminal_verification = success.index("auxiliaryTransaction.VerifyClosedTerminal()")
    digest = success.index("var applyReceiptDigest = ComputeApplyReceiptDigest(")
    assert auxiliary_complete < cache_complete < terminal_verification < digest

    cleanup_start = source.index("private static bool TryCleanupFailure(")
    cleanup_end = source.index("private static void RestoreManagedOutputAfterFailure(", cleanup_start)
    cleanup = source[cleanup_start:cleanup_end]
    auxiliary_completed = cleanup.index("auxiliaryTransaction.Completed")
    auxiliary_restore = cleanup.index("auxiliaryTransaction.Restore(")
    cache_completed = cleanup.index("cacheTransaction.Completed", auxiliary_restore)
    cache_restore = cleanup.index("cacheTransaction.Restore(", cache_completed)
    assert auxiliary_completed < auxiliary_restore
    assert cache_completed < cache_restore
    assert "auxiliaryTransaction.VerifyClosedTerminal()" in cleanup
    assert "cacheTransaction.VerifyClosedTerminal()" in cleanup
    assert "auxiliaryTransaction.Restored" in cleanup
    assert "cacheTransaction.Restored" in cleanup

    auxiliary_start = source.index("private sealed class AuxiliaryGeneratedTransaction")
    cache_start = source.index("private sealed class CacheTransaction", auxiliary_start)
    auxiliary = source[auxiliary_start:cache_start]
    cache = source[cache_start:]
    for transaction in (auxiliary, cache):
        complete_start = transaction.index("internal void Complete()")
        verify_start = transaction.index("internal bool VerifyRestoredBaseline()", complete_start)
        complete = transaction[complete_start:verify_start]
        assert "if (completed)" in complete
        assert "if (!closingStarted)" in complete
        assert "&& transactionLock != null" in complete
        assert "&& File.Exists(lockPath)" in complete
        assert "cannot begin closing from an incomplete state" in complete
        assert "if (Directory.Exists(transactionRoot))" in complete
        assert "RequireStableRegularFile(journalPath);" in complete
        assert complete.index("RequireStableRegularFile(journalPath);") < complete.index(
            'WriteJournal("closing", true);'
        )
        assert 'if (File.Exists(journalPath)) WriteJournal("closing", true);' not in complete
        assert complete.index('WriteJournal("closing", true);') < complete.index(
            "closingStarted = true;"
        )
        assert "if (transactionLock != null) ReleaseLock();" in complete
        assert "else Require(!File.Exists(lockPath)" in complete
        assert complete.index("DeleteOwnedTransactionTreeWithRetry(transactionRoot);") < complete.index(
            "completed = true;"
        )
        assert "VerifyClosedTerminal()" in transaction
        terminal = transaction[transaction.index("internal bool VerifyClosedTerminal()") :]
        assert "transactionLock == null" in terminal
        assert "(!prepared || closingStarted)" in terminal
        assert "!Directory.Exists(transactionRoot)" in terminal
        assert "!File.Exists(lockPath)" in terminal
        assert "VerifyRestoredBaseline()" in terminal

    stable_file_start = source.index("private static void RequireStableRegularFile(string path)")
    stable_file_end = source.index("private static FileIdentity CaptureIdentity", stable_file_start)
    stable_file = source[stable_file_start:stable_file_end]
    assert "Require(File.Exists(path)" in stable_file
    assert "!identity.IsReparsePoint && identity.NumberOfLinks == 1" in stable_file

    publish_start = source.index("private static void PublishTransactionJournal(")
    delete_start = source.index("private static void DeleteOwnedTransactionTreeWithRetry(")
    publish = source[publish_start:delete_start]
    delete_end = source.index("private static FileIdentity CaptureIdentity", delete_start)
    delete_tree = source[delete_start:delete_end]
    assert "attempt < TransactionIoRetryAttempts" in publish
    assert "File.Replace(nextPath, journalPath, null, true);" in publish
    assert "File.Move(nextPath, journalPath);" in publish
    assert "File.ReadAllBytes(journalPath).SequenceEqual(bytes)" in publish
    assert "exception is IOException || exception is UnauthorizedAccessException" in publish
    assert "The prior transaction journal staging file could not be removed." in publish
    assert source.count("PublishTransactionJournal(journalPath, bytes);") == 2
    assert "attempt < TransactionIoRetryAttempts" in delete_tree
    assert delete_tree.index("RequireSafeOwnedTreeForDeletion(transactionRoot);") < delete_tree.index(
        "Directory.Delete(transactionRoot, true);"
    )
    assert "exception is IOException || exception is UnauthorizedAccessException" in delete_tree
    assert source.count("DeleteOwnedTransactionTreeWithRetry(transactionRoot);") == 4

    assert 'journalClosed = cacheTransaction.Completed' in source
    assert 'journalClosed = transaction.Completed' in source


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
    assert "Resources.FindObjectsOfTypeAll<Object>()" in guard
    assert "AssetDatabase.LoadAllAssetsAtPath(path)" not in guard
    assert "AssetImporter.GetAtPath(path)" in guard
    assert "AssetDatabase.Contains(asset)" in guard
    assert "AssetDatabase.IsNativeAsset(asset)" in guard
    assert "EditorUtility.IsPersistent" in guard
    assert "EditorUtility.IsDirty" in guard
    assert '"An unrelated project asset importer is dirty: " + path' in guard
    assert '"An unrelated project asset is dirty: " + path' in guard
    assert guard.index("AssetDatabase.IsNativeAsset(asset)") < guard.index(
        '"An unrelated project asset is dirty: " + path'
    )
    assert "SceneManager.sceneCount" in guard
    assert "scene.isDirty" in guard
    assert "scene.handle == allowedTransientScene.handle" in guard
    assert "allowedTransientSceneMatches == 1" in guard
    assert 'string.IsNullOrWhiteSpace(scene.path) && !scene.isSubScene' in guard
    assert "!scene.isSubScene" in guard
    assert "scene.isSubScene" in guard
    assert 'scenePath.StartsWith("Assets/", StringComparison.Ordinal)' in guard
    assert 'scenePath.StartsWith("Packages/", StringComparison.Ordinal)' in guard
    assert "isProjectScene || isReadOnlyPackageSubScene" in guard
    assert "LoadAssetAtPath<SceneAsset>" in guard
    assert "AssetDatabase.SaveAssets" not in guard

    calls = [match.start() for match in re.finditer(r"RequireNoDirtyProjectAssets\(\);", source)]
    assert len(calls) >= 4
    assert calls[0] < source.index("if (preview)")
    assert source.index("ValidateApplyPreconditions") < calls[1]
    planned = source.index("cacheTransaction = CacheTransaction.Plan")
    mutation_started = source.index("mutationStarted = true;", planned)
    prepared = source.index("cacheTransaction.Prepare();", mutation_started)
    assert calls[1] < planned < mutation_started < prepared
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
    assert 'details["failureStage"] == "clone_asset_staging"' in source
    assert "SeedGeneratedCache" in source
    assert "CaptureCacheReceipt" in source
    assert "RequireNoActiveCacheTransaction" in source
    assert '"parameter-bit-packing.lock"' in source
    assert '"parameter-auxiliary-generated.lock"' in source
    assert "VerifyDurableOutputAfterApprovedApply" in source
    assert "guidPreservingWholeTreeMove" in source
    assert "VRCAvatarParameterDriver" in source
    assert "Assets/VRCForge/Generated/ParameterBitPacking/" in source
    assert "VRCFORGE_PARAMETER_BIT_PACKING_PROBE_OK" in source
