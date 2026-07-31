from __future__ import annotations

import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


RESULT_SCHEMA = "vrcforge.parameter_bit_packing.v2"
APPROVAL_SCHEMA = "vrcforge.parameter_bit_packing_approval.v1"
APPLY_RECEIPT_SCHEMA = "vrcforge.parameter_bit_packing_apply_receipt.v2"
CAPABILITY_SCHEMA = "vrcforge.parameter_capability.v2"
TOOL_NAME = "vrc_build_parameter_bit_packed_clone"

BEHAVIOR_EVIDENCE_SCHEMA = "vrcforge.parameter_behavior_evidence.v1"
BEHAVIOR_PROOF_SCHEMA = "vrcforge.parameter_behavior_proof.v1"
CACHE_JOURNAL_SCHEMA = "vrcforge.parameter_cache_journal.v1"
AUXILIARY_JOURNAL_SCHEMA = "vrcforge.parameter_auxiliary_journal.v1"
OUTPUT_MANIFEST_SCHEMA = "vrcforge.parameter_output_manifest.v1"
PREFERENCE_SCHEMA = "vrcforge.parameter_preferences.v1"
CACHE_BACKUP_MAX_ENTRIES = 100000
CACHE_BACKUP_MAX_BYTES = 536870912
DESKTOP_BUILD_TARGET = "StandaloneWindows64"
PLATFORM_SCOPE = "current-target-only"

PACKAGE_ID = "com.vrcfury.vrcfury"
PACKAGE_VERSION = "1.1334.0"
PACKAGE_AUTHOR = "VRCFury"
PACKAGE_ARCHIVE_SHA256 = "01c750a3f87d3003ac31e23345e0e3afb43a790c2aeb2c43ed933cd46efafcfe"
PACKAGE_TREE_SHA256 = "230340bd6eef1e633b18cc9587c91b71ff30143e85cb9732b5208fffcdd076d2"
PACKAGE_FILE_COUNT = 1255

# Filled from clean Unity 2022.3.22f1 compiles of the exact package tree above.
# The package assembly is intentionally unsigned; each allowlisted profile pins
# its complete bytes and the complete callback assembly set.
CALLBACK_ASSEMBLY_NAME = "VRCFury-Editor-Avatars"
CALLBACK_ASSEMBLY_VERSION = "0.0.0.0"
CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN = ""

SDK_CALLBACK_ASSEMBLY_NAME = "VRCSDKBase-Editor"
SDK_CALLBACK_ASSEMBLY_VERSION = "1.0.0.0"
SDK_CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN = ""
SDK_CALLBACK_ASSEMBLY_SHA256 = "952abdd2e9f696acba1fa773402d824fac4f0c6dd0b1b3488df8e4a3d870eba9"
CALLBACK_TYPE = "VRC.SDKBase.Editor.BuildPipeline.VRCBuildPipelineCallbacks"
CALLBACK_SIGNATURE = "public static System.Boolean OnPreprocessAvatar(UnityEngine.GameObject)"
REGISTERED_HOOK_TYPE = "VF.Hooks.ParameterCompressorHook"

MINIMAL_CAPABILITY_PROFILE_ID = "embedded-minimal-v1"
MINIMAL_CALLBACK_ASSEMBLY_SHA256 = "e568293abe29428b7fb35d805cb3053cc8437621a19ae714d5fc76931d9fe10f"
MINIMAL_CALLBACK_ROSTER_COUNT = 16
MINIMAL_CALLBACK_ROSTER_DIGEST = "305bc43e713cc76fe13f16d99e6e1d7137d87c066d6a46a6917196b909de10ba"
MINIMAL_CALLBACK_ASSEMBLY_SET_COUNT = 3
MINIMAL_CALLBACK_ASSEMBLY_SET_DIGEST = "1884970046bc7b2f7194cef03c3c085dffb02df8cc6eddc9173e90fd231794d1"

EXTENDED_CAPABILITY_PROFILE_ID = "embedded-extended-v1"
EXTENDED_CALLBACK_ASSEMBLY_SHA256 = "c220c73e91f69aa88425c8cd81cf271a6b484eb5b34cca15a33f6edcde89c8f4"
EXTENDED_CALLBACK_ROSTER_COUNT = 23
EXTENDED_CALLBACK_ROSTER_DIGEST = "a345576b0aad61991a4518413a5685d3b9df85e9ad33af50ff6b04a71d0f920e"
EXTENDED_CALLBACK_ASSEMBLY_SET_COUNT = 7
EXTENDED_CALLBACK_ASSEMBLY_SET_DIGEST = "2eebf5d668c881ac7b208191e488c6a69c896549473fb44281d12c07404dc221"

_CAPABILITY_PROFILES = {
    MINIMAL_CAPABILITY_PROFILE_ID: {
        "callbackAssemblySha256": MINIMAL_CALLBACK_ASSEMBLY_SHA256,
        "callbackRosterCount": MINIMAL_CALLBACK_ROSTER_COUNT,
        "callbackRosterDigest": MINIMAL_CALLBACK_ROSTER_DIGEST,
        "callbackAssemblySetCount": MINIMAL_CALLBACK_ASSEMBLY_SET_COUNT,
        "callbackAssemblySetDigest": MINIMAL_CALLBACK_ASSEMBLY_SET_DIGEST,
    },
    EXTENDED_CAPABILITY_PROFILE_ID: {
        "callbackAssemblySha256": EXTENDED_CALLBACK_ASSEMBLY_SHA256,
        "callbackRosterCount": EXTENDED_CALLBACK_ROSTER_COUNT,
        "callbackRosterDigest": EXTENDED_CALLBACK_ROSTER_DIGEST,
        "callbackAssemblySetCount": EXTENDED_CALLBACK_ASSEMBLY_SET_COUNT,
        "callbackAssemblySetDigest": EXTENDED_CALLBACK_ASSEMBLY_SET_DIGEST,
    },
}

# Compatibility aliases preserve the prior single-profile constants while new
# payloads identify the profile explicitly.
CAPABILITY_PROFILE_ID = MINIMAL_CAPABILITY_PROFILE_ID
CALLBACK_ASSEMBLY_SHA256 = MINIMAL_CALLBACK_ASSEMBLY_SHA256
CALLBACK_ROSTER_COUNT = MINIMAL_CALLBACK_ROSTER_COUNT
CALLBACK_ROSTER_DIGEST = MINIMAL_CALLBACK_ROSTER_DIGEST
CALLBACK_ASSEMBLY_SET_COUNT = MINIMAL_CALLBACK_ASSEMBLY_SET_COUNT
CALLBACK_ASSEMBLY_SET_DIGEST = MINIMAL_CALLBACK_ASSEMBLY_SET_DIGEST

GENERATED_ROOT = "Packages/com.vrcfury.temp/Builds"
STAGING_ROOT = GENERATED_ROOT + "/VRCForge Input"
AUXILIARY_PACKAGE_ROOT = "Packages/nadena.dev.ndmf"
AUXILIARY_GENERATED_ROOT = AUXILIARY_PACKAGE_ROOT + "/__Generated"
OUTPUT_ROOT = "Assets/VRCForge/Generated"
OUTPUT_KIND_ROOT = OUTPUT_ROOT + "/ParameterBitPacking"
EMPTY_GENERATED_TREE_DIGEST = hashlib.sha256(
    b"vrcforge.generated_tree.v1\n"
).hexdigest()

REQUEST_ARGUMENT_KEYS = (
    "sourceScenePath",
    "sourceAvatarPath",
    "outputCloneName",
)

_PREVIEW_RESULT_KEYS = frozenset(
    {
        "schema", "ok", "preview", "verified", "changed", "saved",
        "callbacksInvoked", "mutationStarted", "mutationCount", "projectPath",
        "source", "capability", "generated", "auxiliaryGenerated", "preferences", "platformProof",
        "output", "previewDigest",
    }
)
_PREVIEW_SOURCE_KEYS = frozenset(
    {
        "scenePath", "sceneGuid", "sceneFileDigest", "sceneMetaDigest", "objectPath",
        "globalObjectId", "hierarchyDigest", "sourceStateDigest", "sourceAssetSetDigest",
        "sourceAssetCount", "parameterStateDigest", "controllerStateDigest", "menuStateDigest",
        "behaviorEvidence", "sourceCostBits", "parameterCount", "safeCandidateNames",
        "safeCandidateDigest", "excludedParameters", "excludedDigest", "sourceDirty",
        "referencedAssetsDirty",
    }
)
_PREVIEW_GENERATED_KEYS = frozenset(
    {
        "root", "treeDigestBefore", "contentDigestBefore", "entryCountBefore",
        "byteCountBefore", "backupMaxEntries", "backupMaxBytes", "journalSchema",
        "protectedTreeDigestBefore", "protectedEntryCountBefore",
        "rootIdentityDigestBefore", "rootIdentityCountBefore", "exists", "reparseFree",
    }
)
_PREVIEW_AUXILIARY_KEYS = frozenset(
    {
        "root", "packageRoot", "packageRootIdentityDigestBefore",
        "packageManifestDigestBefore", "packageManifestIdentityDigestBefore",
        "rootExistsBefore", "treeDigestBefore", "contentDigestBefore",
        "entryCountBefore", "byteCountBefore", "backupMaxEntries",
        "backupMaxBytes", "journalSchema", "reparseFree",
    }
)
_PREVIEW_OUTPUT_KEYS = frozenset(
    {
        "root", "kindRoot", "cloneName", "sceneName", "temporaryPrefabPath",
        "prefabPath", "treeDigestBefore", "entryCountBefore", "rootExistsBefore",
        "targetExistsBefore", "cloneExists", "sceneCreated", "prefabExists",
    }
)

_APPLY_RESULT_KEYS = frozenset(
    {
        "schema", "ok", "preview", "verified", "changed", "saved",
        "callbacksInvoked", "mutationStarted", "restored", "cleanupRequired",
        "checkpointRestoreRequired", "operationState", "cleanupVerified",
        "sceneLoadedAfter", "temporaryObjectResidue", "projectPath", "previewDigest",
        "capability", "preferences", "platformProof", "behaviorProof", "costBeforeBits",
        "costAfterBits", "compressedParameterNames", "approvedSafeCandidateNames",
        "excludedParameters", "sourceUnchanged", "sourceSceneDirtyAfter",
        "sourceStateDigestAfter", "sourceAssetSetDigestAfter", "source", "output",
        "generated", "auxiliaryGenerated", "managedOutput", "protectedProjectTree",
        "applyReceiptDigest",
    }
)
_APPLY_SOURCE_KEYS = frozenset(
    {
        "scenePath", "sceneGuid", "sceneFileDigest", "sceneMetaDigest", "objectPath",
        "globalObjectId", "hierarchyDigest", "sourceStateDigestBefore",
        "sourceStateDigestAfter", "sourceAssetSetDigestBefore", "sourceAssetSetDigestAfter",
        "sourceAssetCount", "parameterStateDigest", "parameterCount", "controllerStateDigest", "menuStateDigest",
        "behaviorEvidence", "sourceUnchanged", "sceneDirtyAfter",
    }
)
_APPLY_OUTPUT_KEYS = frozenset(
    {
        "cloneName", "sceneName", "scenePath", "scenePersistent",
        "clonePortableAvatarDigest", "cloneEvidenceDigest", "cloneParameterStateDigest",
        "prefabPath", "prefabGuid", "prefabFileDigest", "prefabMetaDigest",
        "prefabRootGlobalObjectId", "prefabPortableAvatarDigest",
        "prefabOrderedParameterDigest", "prefabMenuGraphDigest",
        "prefabAnimatorBehaviorDigest", "prefabEvidenceDigest",
        "prefabBehaviorProofDigest", "prefabParameterStateDigest", "prefabPersistent",
        "prefabExistsAfter", "sceneLoadedAfter", "temporaryObjectResidue",
    }
)
_APPLY_GENERATED_KEYS = frozenset(
    {
        "root", "stagingRoot", "stagingRemoved", "treeDigestBefore",
        "contentDigestBefore", "entryCountBefore", "byteCountBefore", "treeDigestAfter",
        "contentDigestAfter", "entryCountAfter", "byteCountAfter", "addedEntryCount",
        "modifiedEntryCount", "removedEntryCount", "targetResidue", "deltaDigest",
        "cacheRestored", "backupBounded", "backupMaxEntries", "backupMaxBytes",
        "journalSchema", "journalId", "journalClosed",
    }
)
_APPLY_AUXILIARY_KEYS = frozenset(
    {
        "root", "packageRoot", "packageRootIdentityDigestBefore",
        "packageRootIdentityDigestAfter", "packageManifestDigestBefore",
        "packageManifestDigestAfter", "packageManifestIdentityDigestBefore",
        "packageManifestIdentityDigestAfter", "rootExistsBefore", "rootExistsAfter",
        "treeDigestBefore", "treeDigestAfter", "contentDigestBefore",
        "contentDigestAfter", "entryCountBefore", "entryCountAfter",
        "byteCountBefore", "byteCountAfter", "observedRootExists",
        "observedTreeDigest", "observedContentDigest", "observedEntryCount",
        "observedByteCount", "ownedRootIdentityDigest", "createdByOperation",
        "restorationMode", "restoreVerified", "backupBounded", "backupMaxEntries",
        "backupMaxBytes", "journalSchema", "journalId", "journalClosed",
    }
)
_APPLY_MANAGED_OUTPUT_KEYS = frozenset(
    {
        "root", "kindRoot", "targetRoot", "rootExistsBefore", "rootExistsAfter",
        "treeDigestBefore", "entryCountBefore", "treeDigestAfter", "entryCountAfter",
        "addedEntryCount", "targetSubtreeCount", "modifiedEntryCount",
        "removedEntryCount", "addedEntriesDigest", "leaseBound", "stageSavedBeforeMove",
        "guidPreservingWholeTreeMove", "temporaryTreeRemoved", "prefabGuidPreserved",
        "stagedManifest", "finalManifest", "manifestSchema", "manifestDigest",
        "manifestEntryCount", "manifestByteCount", "manifestContentDigest",
        "manifestHandleEvidenceDigest", "guidMapDigest", "dependencyGuidDigest",
        "referenceClosureDigest", "noTemporaryReferences", "reparseFree", "singleLink",
        "handleHashed", "finalEnumerationVerified",
    }
)
_APPLY_PROTECTED_KEYS = frozenset(
    {
        "rootIdentityDigestBefore", "rootIdentityDigestAfter", "rootIdentityCountBefore",
        "rootIdentityCountAfter", "treeDigestBefore", "treeDigestAfter",
        "entryCountBefore", "entryCountAfter",
    }
)
_CAPABILITY_KEYS = frozenset(
    {
        "packageId", "packageVersion", "packageAuthor", "packageArchiveSha256",
        "packageTreeSha256", "packageFileCount", "packageRootIdentityDigest",
        "profileId", "callbackAssemblyName", "callbackAssemblyVersion",
        "callbackAssemblyPublicKeyToken", "callbackAssemblySha256",
        "sdkCallbackAssemblyName", "sdkCallbackAssemblyVersion",
        "sdkCallbackAssemblyPublicKeyToken", "sdkCallbackAssemblySha256",
        "callbackType", "callbackSignature", "registeredHookType",
        "registeredHookCount", "callbackRosterCount", "callbackRosterDigest",
        "callbackAssemblySetCount", "callbackAssemblySetDigest", "capabilityDigest",
    }
)
_BEHAVIOR_EVIDENCE_KEYS = frozenset(
    {
        "schema", "portableAvatarDigest", "orderedParameterDigest", "parameterCount",
        "menuGraphDigest", "menuRowCount", "animatorBehaviorDigest", "animatorRowCount",
        "receiptDigest",
    }
)
_BEHAVIOR_PROOF_KEYS = frozenset(
    {
        "schema", "status", "platformScope", "crossPlatformEquivalent",
        "sourceOrderedParameterDigest", "outputOrderedParameterDigest",
        "sourceParameterCount", "outputParameterCount", "sourceMenuGraphDigest",
        "outputMenuGraphDigest", "sourceMenuRowCount", "outputMenuRowCount",
        "sourceAnimatorBehaviorDigest", "outputAnimatorBehaviorDigest",
        "sourceAnimatorRowCount", "outputAnimatorRowCount", "preservedBehaviorDigest",
        "codecGraphDigest", "codecMappingDigest", "codecMappingCount",
        "excludedBeforeDigest", "excludedAfterDigest", "receiptDigest",
    }
)
_PREFERENCE_KEYS = frozenset(
    {
        "schema", "compressorPresent", "compressorValue", "compressorMode",
        "alignMobilePresent", "alignMobileValue", "readOnly", "buildTarget",
        "platformScope", "crossPlatformEquivalent", "localAppDataAccessed", "receiptDigest",
    }
)
_PLATFORM_PROOF_KEYS = frozenset(
    {"buildTarget", "scope", "crossPlatformEquivalent", "localAppDataAccessed"}
)
_ASSET_MANIFEST_KEYS = frozenset(
    {
        "schema", "rootPath", "prefabPath", "entryCount", "byteCount", "contentDigest",
        "handleEvidenceDigest", "guidMapDigest", "dependencyGuidDigest",
        "referenceClosureDigest", "noTemporaryReferences", "reparseFree", "singleLink",
        "handleHashed", "finalEnumerationVerified", "receiptDigest",
    }
)
_EXCLUDED_PARAMETER_KEYS = frozenset(
    {"name", "type", "networkSynced", "reasons", "stateDigest"}
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GUID = re.compile(r"^[0-9a-f]{32}$")
_OBJECT_NAME = re.compile(r'^[^<>:"/\\|?*\x00-\x1f]{1,80}$')
_PARAMETER_NAME = re.compile(r"^[^\x00-\x1f]{1,128}$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[/\\]")
_WINDOWS_RESERVED_STEMS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_ALLOWED_EXCLUSION_REASONS = {
    "float_or_int",
    "puppet",
    "osc_or_unmapped",
    "face_tracking",
    "not_toggle_only",
    "not_network_synced",
}
_PARAMETER_TYPES = {"Bool", "Int", "Float"}
_CACHE_JOURNAL_ID = re.compile(r"^parameter-bit-packing-[0-9a-f]{32}$")
_AUXILIARY_JOURNAL_ID = re.compile(r"^parameter-auxiliary-generated-[0-9a-f]{32}$")


class ParameterBitPackingError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ParameterBitPackingError("Parameter bit-packing parameters are required.")
    wrapper = deepcopy(params)
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in REQUEST_ARGUMENT_KEYS if key in wrapper}
    project_path = wrapper.get("projectPath")
    if project_path is None:
        project_path = nested.get("projectPath")
    return {
        "projectPath": deepcopy(project_path),
        "toolName": TOOL_NAME,
        "arguments": {
            key: deepcopy(nested[key])
            for key in REQUEST_ARGUMENT_KEYS
            if key in nested
        },
    }


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    request = arguments if isinstance(arguments, dict) else {}
    preview = {
        key: deepcopy(request[key])
        for key in REQUEST_ARGUMENT_KEYS
        if key in request
    }
    preview["preview"] = True
    preview["runBuildCallbacks"] = False
    preview["saveScene"] = False
    return preview


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict):
        raise ParameterBitPackingError("Parameter bit-packing wrapper is required.")
    if wrapper_arguments.get("toolName", TOOL_NAME) != TOOL_NAME:
        raise ParameterBitPackingError("Parameter bit-packing tool name is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    if not isinstance(nested, dict):
        raise ParameterBitPackingError("Parameter bit-packing arguments are required.")

    project_path = _project_path(wrapper_arguments.get("projectPath"))
    requested_scene = _asset_path(nested.get("sourceScenePath"), suffix=".unity")
    requested_avatar = _scene_object_path(nested.get("sourceAvatarPath"))
    requested_clone = _object_name(nested.get("outputCloneName"))

    result = _exact_dict(payload, "preview result", _PREVIEW_RESULT_KEYS)
    for key, expected in (
        ("schema", RESULT_SCHEMA),
        ("ok", True),
        ("preview", True),
        ("verified", True),
        ("changed", False),
        ("saved", False),
        ("callbacksInvoked", False),
        ("mutationStarted", False),
    ):
        if result.get(key) != expected:
            raise ParameterBitPackingError(f"Parameter bit-packing preview {key} is invalid.")
    if _strict_int(result.get("mutationCount"), "mutationCount", 0, 0) != 0:
        raise ParameterBitPackingError("Parameter bit-packing preview reported a mutation.")
    actual_project = _project_path(result.get("projectPath"))
    if os.path.normcase(actual_project) != os.path.normcase(project_path):
        raise ParameterBitPackingError("Parameter bit-packing preview changed the selected project.")

    source = _source(result.get("source"))
    if source["scenePath"] != requested_scene or source["objectPath"] != requested_avatar:
        raise ParameterBitPackingError("Parameter bit-packing preview changed the source selector.")

    capability = _capability(result.get("capability"))
    generated = _generated_before(result.get("generated"))
    auxiliary = _auxiliary_before(result.get("auxiliaryGenerated"))
    preferences = _preferences(result.get("preferences"))
    platform = _platform_proof(result.get("platformProof"))
    output = _output_preview(result.get("output"), requested_clone)
    if preferences["buildTarget"] != platform["buildTarget"]:
        raise ParameterBitPackingError("Parameter bit-packing preference target is inconsistent.")

    preview_digest = _hex(result.get("previewDigest"), "previewDigest")
    if compute_preview_digest(result) != preview_digest:
        raise ParameterBitPackingError("Parameter bit-packing preview digest is invalid.")

    canonical = build_wrapper_arguments(wrapper_arguments)
    canonical["projectPath"] = project_path
    canonical_nested = canonical["arguments"]
    canonical_nested.clear()
    canonical_nested.update(
        {
            "sourceScenePath": requested_scene,
            "sourceAvatarPath": requested_avatar,
            "outputCloneName": requested_clone,
            "preview": False,
            "runBuildCallbacks": True,
            "saveScene": False,
            "expectedProjectPath": project_path,
            "expectedSourceSceneGuid": source["sceneGuid"],
            "expectedSourceSceneFileDigest": source["sceneFileDigest"],
            "expectedSourceSceneMetaDigest": source["sceneMetaDigest"],
            "expectedSourceGlobalObjectId": source["globalObjectId"],
            "expectedSourceHierarchyDigest": source["hierarchyDigest"],
            "expectedSourceStateDigest": source["sourceStateDigest"],
            "expectedSourceAssetSetDigest": source["sourceAssetSetDigest"],
            "expectedSourceAssetCount": source["sourceAssetCount"],
            "expectedParameterStateDigest": source["parameterStateDigest"],
            "expectedControllerStateDigest": source["controllerStateDigest"],
            "expectedMenuStateDigest": source["menuStateDigest"],
            "expectedSourceBehaviorEvidenceDigest": source["behaviorEvidence"]["receiptDigest"],
            "expectedSourceCostBits": source["sourceCostBits"],
            "expectedParameterCount": source["parameterCount"],
            "expectedSafeCandidateDigest": source["safeCandidateDigest"],
            "expectedSafeCandidateCount": len(source["safeCandidateNames"]),
            "expectedExcludedDigest": source["excludedDigest"],
            "expectedExcludedCount": len(source["excludedParameters"]),
            "expectedCapabilityDigest": capability["capabilityDigest"],
            "expectedPackageRootIdentityDigest": capability["packageRootIdentityDigest"],
            "expectedRootIdentityDigest": generated["rootIdentityDigestBefore"],
            "expectedRootIdentityCount": generated["rootIdentityCountBefore"],
            "expectedGeneratedTreeDigestBefore": generated["treeDigestBefore"],
            "expectedGeneratedEntryCountBefore": generated["entryCountBefore"],
            "expectedGeneratedContentDigestBefore": generated["contentDigestBefore"],
            "expectedGeneratedByteCountBefore": generated["byteCountBefore"],
            "expectedAuxiliaryPackageRootIdentityDigest": auxiliary[
                "packageRootIdentityDigestBefore"
            ],
            "expectedAuxiliaryPackageManifestDigest": auxiliary[
                "packageManifestDigestBefore"
            ],
            "expectedAuxiliaryPackageManifestIdentityDigest": auxiliary[
                "packageManifestIdentityDigestBefore"
            ],
            "expectedAuxiliaryRootExistsBefore": auxiliary["rootExistsBefore"],
            "expectedAuxiliaryTreeDigestBefore": auxiliary["treeDigestBefore"],
            "expectedAuxiliaryContentDigestBefore": auxiliary["contentDigestBefore"],
            "expectedAuxiliaryEntryCountBefore": auxiliary["entryCountBefore"],
            "expectedAuxiliaryByteCountBefore": auxiliary["byteCountBefore"],
            "expectedPreferenceDigest": preferences["receiptDigest"],
            "expectedProtectedTreeDigestBefore": generated["protectedTreeDigestBefore"],
            "expectedProtectedEntryCountBefore": generated["protectedEntryCountBefore"],
            "expectedOutputSceneName": output["sceneName"],
            "expectedOutputPrefabPath": output["prefabPath"],
            "expectedOutputTreeDigestBefore": output["treeDigestBefore"],
            "expectedOutputEntryCountBefore": output["entryCountBefore"],
            "expectedOutputRootExistsBefore": output["rootExistsBefore"],
            "expectedPreviewDigest": preview_digest,
        }
    )

    approval = {
        "schema": APPROVAL_SCHEMA,
        "projectPath": project_path,
        "source": source,
        "capability": capability,
        "generated": generated,
        "auxiliaryGenerated": auxiliary,
        "preferences": preferences,
        "platformProof": platform,
        "output": output,
        "cost": {
            "beforeBits": source["sourceCostBits"],
            "maximumBits": 256,
            "safeCandidateCount": len(source["safeCandidateNames"]),
            "excludedCount": len(source["excludedParameters"]),
        },
        "previewDigest": preview_digest,
        "rollbackRequired": True,
        "sourceMustRemainUnchanged": True,
    }
    return canonical, approval


def validate_apply_result(
    approved_arguments: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    args = _dict(approved_arguments, "approved arguments")
    result = _exact_dict(payload, "apply result", _APPLY_RESULT_KEYS)
    for key, expected in (
        ("schema", RESULT_SCHEMA),
        ("ok", True),
        ("preview", False),
        ("verified", True),
        ("changed", True),
        ("saved", True),
        ("callbacksInvoked", True),
        ("mutationStarted", True),
        ("restored", False),
        ("cleanupRequired", False),
        ("checkpointRestoreRequired", False),
        ("operationState", "verified"),
        ("cleanupVerified", True),
        ("sceneLoadedAfter", False),
        ("temporaryObjectResidue", False),
    ):
        if result.get(key) != expected:
            raise ParameterBitPackingError(f"Parameter bit-packing apply {key} is invalid.")

    actual_project = _project_path(result.get("projectPath"))
    expected_project = _project_path(args.get("expectedProjectPath"))
    if os.path.normcase(actual_project) != os.path.normcase(expected_project):
        raise ParameterBitPackingError("Parameter bit-packing apply changed the selected project.")

    if _hex(result.get("previewDigest"), "previewDigest") != _hex(
        args.get("expectedPreviewDigest"), "expectedPreviewDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing apply changed the approved preview.")
    capability = _capability(result.get("capability"))
    if capability["capabilityDigest"] != _hex(
        args.get("expectedCapabilityDigest"), "expectedCapabilityDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing capability changed after approval.")
    if capability["packageRootIdentityDigest"] != _hex(
        args.get("expectedPackageRootIdentityDigest"), "expectedPackageRootIdentityDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing package root changed after approval.")
    preferences = _preferences(result.get("preferences"))
    platform = _platform_proof(result.get("platformProof"))
    if preferences["receiptDigest"] != _hex(args.get("expectedPreferenceDigest"), "expectedPreferenceDigest"):
        raise ParameterBitPackingError("Parameter bit-packing preferences changed after approval.")
    if preferences["buildTarget"] != platform["buildTarget"]:
        raise ParameterBitPackingError("Parameter bit-packing platform target is inconsistent.")

    before_bits = _strict_int(result.get("costBeforeBits"), "costBeforeBits", 257, 4096)
    after_bits = _strict_int(result.get("costAfterBits"), "costAfterBits", 0, 256)
    if before_bits != _strict_int(args.get("expectedSourceCostBits"), "expectedSourceCostBits", 257, 4096):
        raise ParameterBitPackingError("Parameter bit-packing source cost changed after approval.")
    if after_bits >= before_bits:
        raise ParameterBitPackingError("Parameter bit-packing did not reduce synchronized parameter cost.")

    compressed = _sorted_names(
        result.get("compressedParameterNames"),
        "compressedParameterNames",
        require_canonical=True,
    )
    safe = _sorted_names(
        result.get("approvedSafeCandidateNames"),
        "approvedSafeCandidateNames",
        require_canonical=True,
    )
    if not compressed or not set(compressed).issubset(set(safe)):
        raise ParameterBitPackingError("Parameter bit-packing compressed a non-approved parameter.")
    if len(safe) != _strict_int(args.get("expectedSafeCandidateCount"), "expectedSafeCandidateCount", 1, 2048):
        raise ParameterBitPackingError("Parameter bit-packing safe candidate set changed after approval.")
    if _name_digest(safe, "vrcforge.safe_parameter_names.v1") != _hex(
        args.get("expectedSafeCandidateDigest"), "expectedSafeCandidateDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing safe candidate digest changed after approval.")

    excluded = _excluded(result.get("excludedParameters"), require_canonical=True)
    if len(excluded) != _strict_int(args.get("expectedExcludedCount"), "expectedExcludedCount", 0, 2048):
        raise ParameterBitPackingError("Parameter bit-packing exclusion count changed after approval.")
    if _excluded_digest(excluded) != _hex(args.get("expectedExcludedDigest"), "expectedExcludedDigest"):
        raise ParameterBitPackingError("Parameter bit-packing exclusions drifted.")

    source = _exact_dict(result.get("source"), "source receipt", _APPLY_SOURCE_KEYS)
    source_behavior = _behavior_evidence(source.get("behaviorEvidence"), "source behavior evidence")
    source_expected = {
        "scenePath": _asset_path(args.get("sourceScenePath"), suffix=".unity"),
        "sceneGuid": _hex(args.get("expectedSourceSceneGuid"), "expectedSourceSceneGuid", _GUID),
        "sceneFileDigest": _hex(args.get("expectedSourceSceneFileDigest"), "expectedSourceSceneFileDigest"),
        "sceneMetaDigest": _hex(args.get("expectedSourceSceneMetaDigest"), "expectedSourceSceneMetaDigest"),
        "objectPath": _scene_object_path(args.get("sourceAvatarPath")),
        "globalObjectId": _bounded_string(args.get("expectedSourceGlobalObjectId"), "expectedSourceGlobalObjectId", 8, 256),
        "hierarchyDigest": _hex(args.get("expectedSourceHierarchyDigest"), "expectedSourceHierarchyDigest"),
        "sourceStateDigestBefore": _hex(args.get("expectedSourceStateDigest"), "expectedSourceStateDigest"),
        "sourceAssetSetDigestBefore": _hex(args.get("expectedSourceAssetSetDigest"), "expectedSourceAssetSetDigest"),
        "sourceAssetCount": _strict_int(
            args.get("expectedSourceAssetCount"), "expectedSourceAssetCount", 3, 4096
        ),
        "parameterStateDigest": _hex(args.get("expectedParameterStateDigest"), "expectedParameterStateDigest"),
        "parameterCount": _strict_int(args.get("expectedParameterCount"), "expectedParameterCount", 1, 2048),
        "controllerStateDigest": _hex(args.get("expectedControllerStateDigest"), "expectedControllerStateDigest"),
        "menuStateDigest": _hex(args.get("expectedMenuStateDigest"), "expectedMenuStateDigest"),
    }
    for key, expected in source_expected.items():
        if source.get(key) != expected:
            raise ParameterBitPackingError(f"Parameter bit-packing source receipt {key} changed.")
    if source.get("sourceUnchanged") is not True or source.get("sceneDirtyAfter") is not False:
        raise ParameterBitPackingError("Parameter bit-packing source receipt is not clean.")
    if source_behavior["receiptDigest"] != _hex(
        args.get("expectedSourceBehaviorEvidenceDigest"), "expectedSourceBehaviorEvidenceDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing source behavior evidence changed.")
    if source_behavior["parameterCount"] != _strict_int(
        args.get("expectedParameterCount"), "expectedParameterCount", 1, 2048
    ):
        raise ParameterBitPackingError("Parameter bit-packing source behavior parameter count changed.")
    if result.get("sourceUnchanged") is not True or result.get("sourceSceneDirtyAfter") is not False:
        raise ParameterBitPackingError("Parameter bit-packing changed the source avatar.")
    if _hex(result.get("sourceStateDigestAfter"), "sourceStateDigestAfter") != _hex(
        args.get("expectedSourceStateDigest"), "expectedSourceStateDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing source state digest changed.")
    if _hex(result.get("sourceAssetSetDigestAfter"), "sourceAssetSetDigestAfter") != _hex(
        args.get("expectedSourceAssetSetDigest"), "expectedSourceAssetSetDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing source asset set changed.")
    if _hex(source.get("sourceStateDigestAfter"), "sourceStateDigestAfter") != _hex(
        args.get("expectedSourceStateDigest"), "expectedSourceStateDigest"
    ) or _hex(source.get("sourceAssetSetDigestAfter"), "sourceAssetSetDigestAfter") != _hex(
        args.get("expectedSourceAssetSetDigest"), "expectedSourceAssetSetDigest"
    ):
        raise ParameterBitPackingError("Parameter bit-packing source receipt changed after apply.")

    behavior_proof = _behavior_proof(result.get("behaviorProof"))
    if behavior_proof["codecMappingCount"] != len(compressed):
        raise ParameterBitPackingError("Parameter bit-packing codec mapping count is invalid.")
    if (
        behavior_proof["sourceOrderedParameterDigest"] != source_behavior["orderedParameterDigest"]
        or behavior_proof["sourceParameterCount"] != source_behavior["parameterCount"]
        or behavior_proof["sourceMenuGraphDigest"] != source_behavior["menuGraphDigest"]
        or behavior_proof["sourceMenuRowCount"] != source_behavior["menuRowCount"]
        or behavior_proof["sourceAnimatorBehaviorDigest"] != source_behavior["animatorBehaviorDigest"]
        or behavior_proof["sourceAnimatorRowCount"] != source_behavior["animatorRowCount"]
    ):
        raise ParameterBitPackingError("Parameter bit-packing behavior proof changed its source evidence.")

    output = _exact_dict(result.get("output"), "output", _APPLY_OUTPUT_KEYS)
    if output.get("cloneName") != _object_name(args.get("outputCloneName")):
        raise ParameterBitPackingError("Parameter bit-packing output clone changed.")
    if output.get("sceneName") != _bounded_string(args.get("expectedOutputSceneName"), "expectedOutputSceneName", 1, 128):
        raise ParameterBitPackingError("Parameter bit-packing output scene changed.")
    expected_prefab_path = _asset_path(
        args.get("expectedOutputPrefabPath"),
        suffix=".prefab",
        required_prefix=OUTPUT_KIND_ROOT + "/",
    )
    if output.get("scenePath") != "" or output.get("scenePersistent") is not False:
        raise ParameterBitPackingError("Parameter bit-packing output scene became persistent.")
    if output.get("prefabPath") != expected_prefab_path or output.get("prefabPersistent") is not True:
        raise ParameterBitPackingError("Parameter bit-packing did not persist the approved output prefab.")
    if output.get("prefabExistsAfter") is not True:
        raise ParameterBitPackingError("Parameter bit-packing output prefab is missing.")
    if output.get("sceneLoadedAfter") is not False or output.get("temporaryObjectResidue") is not False:
        raise ParameterBitPackingError("Parameter bit-packing left temporary output state loaded.")
    for key in (
        "clonePortableAvatarDigest",
        "cloneEvidenceDigest",
        "cloneParameterStateDigest",
        "prefabFileDigest",
        "prefabMetaDigest",
        "prefabPortableAvatarDigest",
        "prefabOrderedParameterDigest",
        "prefabMenuGraphDigest",
        "prefabAnimatorBehaviorDigest",
        "prefabEvidenceDigest",
        "prefabBehaviorProofDigest",
        "prefabParameterStateDigest",
    ):
        _hex(output.get(key), key)
    _hex(output.get("prefabGuid"), "prefabGuid", _GUID)
    _bounded_string(output.get("prefabRootGlobalObjectId"), "prefabRootGlobalObjectId", 8, 256)
    expected_output_evidence = _sha256_framed(
        BEHAVIOR_EVIDENCE_SCHEMA,
        output.get("prefabPortableAvatarDigest"),
        output.get("prefabOrderedParameterDigest"),
        behavior_proof["outputParameterCount"],
        output.get("prefabMenuGraphDigest"),
        behavior_proof["outputMenuRowCount"],
        output.get("prefabAnimatorBehaviorDigest"),
        behavior_proof["outputAnimatorRowCount"],
    )
    if (
        output.get("prefabPortableAvatarDigest") != output.get("clonePortableAvatarDigest")
        or output.get("prefabEvidenceDigest") != output.get("cloneEvidenceDigest")
        or output.get("prefabEvidenceDigest") != expected_output_evidence
        or output.get("prefabParameterStateDigest") != output.get("cloneParameterStateDigest")
        or output.get("prefabBehaviorProofDigest") != behavior_proof["receiptDigest"]
        or output.get("prefabOrderedParameterDigest") != behavior_proof["outputOrderedParameterDigest"]
        or output.get("prefabMenuGraphDigest") != behavior_proof["outputMenuGraphDigest"]
        or output.get("prefabAnimatorBehaviorDigest") != behavior_proof["outputAnimatorBehaviorDigest"]
    ):
        raise ParameterBitPackingError("Parameter bit-packing persisted prefab state changed before readback.")

    generated = _exact_dict(result.get("generated"), "generated", _APPLY_GENERATED_KEYS)
    if generated.get("root") != GENERATED_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing generated root is invalid.")
    if generated.get("stagingRoot") != STAGING_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing staging root is invalid.")
    if generated.get("stagingRemoved") is not True:
        raise ParameterBitPackingError("Parameter bit-packing staging root was not consumed.")
    if _hex(generated.get("treeDigestBefore"), "treeDigestBefore") != _hex(
        args.get("expectedGeneratedTreeDigestBefore"), "expectedGeneratedTreeDigestBefore"
    ):
        raise ParameterBitPackingError("Parameter bit-packing generated tree changed before apply.")
    generated_content_before = _hex(generated.get("contentDigestBefore"), "contentDigestBefore")
    if generated_content_before != _hex(
        args.get("expectedGeneratedContentDigestBefore"), "expectedGeneratedContentDigestBefore"
    ):
        raise ParameterBitPackingError("Parameter bit-packing generated content changed before apply.")
    generated_count_before = _strict_int(
        generated.get("entryCountBefore"), "entryCountBefore", 0, 100000
    )
    if generated_count_before != _strict_int(
        args.get("expectedGeneratedEntryCountBefore"),
        "expectedGeneratedEntryCountBefore",
        0,
        100000,
    ):
        raise ParameterBitPackingError("Parameter bit-packing generated tree count changed before apply.")
    generated_bytes_before = _strict_int(
        generated.get("byteCountBefore"), "byteCountBefore", 0, CACHE_BACKUP_MAX_BYTES
    )
    if generated_bytes_before != _strict_int(
        args.get("expectedGeneratedByteCountBefore"),
        "expectedGeneratedByteCountBefore",
        0,
        CACHE_BACKUP_MAX_BYTES,
    ):
        raise ParameterBitPackingError("Parameter bit-packing generated byte count changed before apply.")
    generated_count_after = _strict_int(generated.get("entryCountAfter"), "entryCountAfter", 0, 100000)
    generated_bytes_after = _strict_int(
        generated.get("byteCountAfter"), "byteCountAfter", 0, CACHE_BACKUP_MAX_BYTES
    )
    generated_content_after = _hex(generated.get("contentDigestAfter"), "contentDigestAfter")
    if (
        generated_count_after != generated_count_before
        or generated_bytes_after != generated_bytes_before
        or generated_content_after != generated_content_before
    ):
        raise ParameterBitPackingError("Parameter bit-packing did not restore the dependency cache exactly.")
    if _strict_int(generated.get("addedEntryCount"), "addedEntryCount", 0, 0) != 0:
        raise ParameterBitPackingError("Parameter bit-packing left new assets in the temporary build root.")
    _strict_int(generated.get("modifiedEntryCount"), "modifiedEntryCount", 0, generated_count_before)
    if _strict_int(generated.get("removedEntryCount"), "removedEntryCount", 0, 0) != 0:
        raise ParameterBitPackingError("Parameter bit-packing removed dependency cache entries.")
    if generated.get("targetResidue") is not False:
        raise ParameterBitPackingError("Parameter bit-packing left its temporary target behind.")
    _hex(generated.get("treeDigestAfter"), "treeDigestAfter")
    _hex(generated.get("deltaDigest"), "deltaDigest")
    if generated.get("cacheRestored") is not True or generated.get("backupBounded") is not True:
        raise ParameterBitPackingError("Parameter bit-packing cache restoration proof is incomplete.")
    if generated.get("backupMaxEntries") != CACHE_BACKUP_MAX_ENTRIES:
        raise ParameterBitPackingError("Parameter bit-packing cache entry limit changed.")
    if generated.get("backupMaxBytes") != CACHE_BACKUP_MAX_BYTES:
        raise ParameterBitPackingError("Parameter bit-packing cache byte limit changed.")
    if generated.get("journalSchema") != CACHE_JOURNAL_SCHEMA:
        raise ParameterBitPackingError("Parameter bit-packing cache journal schema changed.")
    journal_id = _bounded_string(generated.get("journalId"), "journalId", 54, 54)
    if not _CACHE_JOURNAL_ID.fullmatch(journal_id) or generated.get("journalClosed") is not True:
        raise ParameterBitPackingError("Parameter bit-packing cache journal is not durably closed.")

    _validate_auxiliary_apply(args, result.get("auxiliaryGenerated"))

    managed = _exact_dict(
        result.get("managedOutput"),
        "managedOutput",
        _APPLY_MANAGED_OUTPUT_KEYS,
    )
    expected_target_root = OUTPUT_KIND_ROOT + "/" + _object_name(args.get("outputCloneName"))
    if (
        managed.get("root") != OUTPUT_ROOT
        or managed.get("kindRoot") != OUTPUT_KIND_ROOT
        or managed.get("targetRoot") != expected_target_root
    ):
        raise ParameterBitPackingError("Parameter bit-packing managed output scope is invalid.")
    expected_output_exists = args.get("expectedOutputRootExistsBefore")
    if not isinstance(expected_output_exists, bool):
        raise ParameterBitPackingError("Parameter bit-packing approved output root state is invalid.")
    if managed.get("rootExistsBefore") is not expected_output_exists:
        raise ParameterBitPackingError("Parameter bit-packing managed output root changed before apply.")
    if managed.get("rootExistsAfter") is not True:
        raise ParameterBitPackingError("Parameter bit-packing managed output root is missing after apply.")
    if _hex(managed.get("treeDigestBefore"), "managed treeDigestBefore") != _hex(
        args.get("expectedOutputTreeDigestBefore"), "expectedOutputTreeDigestBefore"
    ):
        raise ParameterBitPackingError("Parameter bit-packing managed output tree changed before apply.")
    output_count_before = _strict_int(
        managed.get("entryCountBefore"), "managed entryCountBefore", 0, 100000
    )
    if output_count_before != _strict_int(
        args.get("expectedOutputEntryCountBefore"),
        "expectedOutputEntryCountBefore",
        0,
        100000,
    ):
        raise ParameterBitPackingError("Parameter bit-packing managed output count changed before apply.")
    output_added_count = _strict_int(
        managed.get("addedEntryCount"), "managed addedEntryCount", 1, 100000
    )
    output_count_after = _strict_int(
        managed.get("entryCountAfter"), "managed entryCountAfter", 1, 100000
    )
    if output_count_after != output_count_before + output_added_count:
        raise ParameterBitPackingError("Parameter bit-packing managed output count is invalid.")
    if _strict_int(managed.get("targetSubtreeCount"), "targetSubtreeCount", 1, 1) != 1:
        raise ParameterBitPackingError("Parameter bit-packing managed output target is ambiguous.")
    if _strict_int(managed.get("modifiedEntryCount"), "managed modifiedEntryCount", 0, 0) != 0:
        raise ParameterBitPackingError("Parameter bit-packing modified an existing managed output.")
    if _strict_int(managed.get("removedEntryCount"), "managed removedEntryCount", 0, 0) != 0:
        raise ParameterBitPackingError("Parameter bit-packing removed an existing managed output.")
    _hex(managed.get("treeDigestAfter"), "managed treeDigestAfter")
    _hex(managed.get("addedEntriesDigest"), "managed addedEntriesDigest")
    if managed.get("leaseBound") is not True:
        raise ParameterBitPackingError("Parameter bit-packing managed output was not lease-bound.")
    for key in (
        "stageSavedBeforeMove",
        "guidPreservingWholeTreeMove",
        "temporaryTreeRemoved",
        "prefabGuidPreserved",
    ):
        if managed.get(key) is not True:
            raise ParameterBitPackingError(f"Parameter bit-packing migration proof {key} is invalid.")
    clone_name = _object_name(args.get("outputCloneName"))
    staged_root = f"{GENERATED_ROOT}/{clone_name}"
    staged_prefab = f"{staged_root}/{clone_name}.prefab"
    staged_manifest = _asset_manifest(
        managed.get("stagedManifest"),
        label="staged output manifest",
        expected_root=staged_root,
        expected_prefab=staged_prefab,
        expect_no_temporary_references=False,
    )
    final_manifest = _asset_manifest(
        managed.get("finalManifest"),
        label="final output manifest",
        expected_root=expected_target_root,
        expected_prefab=expected_prefab_path,
        expect_no_temporary_references=True,
    )
    for key in (
        "entryCount",
        "byteCount",
        "contentDigest",
        "handleEvidenceDigest",
        "guidMapDigest",
        "dependencyGuidDigest",
    ):
        if staged_manifest[key] != final_manifest[key]:
            raise ParameterBitPackingError(f"Parameter bit-packing migration changed manifest {key}.")
    if output_added_count < final_manifest["entryCount"]:
        raise ParameterBitPackingError("Parameter bit-packing output delta omits manifest entries.")
    flat_manifest = {
        "manifestSchema": OUTPUT_MANIFEST_SCHEMA,
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
    }
    for key, expected in flat_manifest.items():
        if managed.get(key) != expected:
            raise ParameterBitPackingError(f"Parameter bit-packing flat manifest {key} is inconsistent.")

    protected = _exact_dict(
        result.get("protectedProjectTree"),
        "protectedProjectTree",
        _APPLY_PROTECTED_KEYS,
    )
    expected_root_identity = _hex(args.get("expectedRootIdentityDigest"), "expectedRootIdentityDigest")
    root_identity_before = _hex(protected.get("rootIdentityDigestBefore"), "rootIdentityDigestBefore")
    root_identity_after = _hex(protected.get("rootIdentityDigestAfter"), "rootIdentityDigestAfter")
    expected_root_count = _strict_int(args.get("expectedRootIdentityCount"), "expectedRootIdentityCount", 5, 32)
    root_count_before = _strict_int(protected.get("rootIdentityCountBefore"), "rootIdentityCountBefore", 5, 32)
    root_count_after = _strict_int(protected.get("rootIdentityCountAfter"), "rootIdentityCountAfter", 5, 32)
    if (
        root_identity_before != expected_root_identity
        or root_identity_after != expected_root_identity
        or root_count_before != expected_root_count
        or root_count_after != expected_root_count
    ):
        raise ParameterBitPackingError("Parameter bit-packing project root identity changed.")
    protected_before = _hex(protected.get("treeDigestBefore"), "protectedTreeDigestBefore")
    protected_after = _hex(protected.get("treeDigestAfter"), "protectedTreeDigestAfter")
    expected_protected = _hex(args.get("expectedProtectedTreeDigestBefore"), "expectedProtectedTreeDigestBefore")
    if protected_before != expected_protected or protected_after != expected_protected:
        raise ParameterBitPackingError("Parameter bit-packing changed the protected project tree.")
    protected_count_before = _strict_int(
        protected.get("entryCountBefore"), "protectedEntryCountBefore", 1, 100000
    )
    protected_count_after = _strict_int(
        protected.get("entryCountAfter"), "protectedEntryCountAfter", 1, 100000
    )
    expected_protected_count = _strict_int(
        args.get("expectedProtectedEntryCountBefore"), "expectedProtectedEntryCountBefore", 1, 100000
    )
    if protected_count_before != expected_protected_count or protected_count_after != expected_protected_count:
        raise ParameterBitPackingError("Parameter bit-packing changed the protected project tree count.")

    receipt_digest = _hex(result.get("applyReceiptDigest"), "applyReceiptDigest")
    if compute_apply_receipt_digest(result) != receipt_digest:
        raise ParameterBitPackingError("Parameter bit-packing apply receipt digest is invalid.")

    return deepcopy(result)


def _validate_auxiliary_apply(args: dict[str, Any], value: Any) -> None:
    auxiliary = _exact_dict(value, "auxiliaryGenerated", _APPLY_AUXILIARY_KEYS)
    if auxiliary.get("root") != AUXILIARY_GENERATED_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary generated root is invalid.")
    if auxiliary.get("packageRoot") != AUXILIARY_PACKAGE_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary package root is invalid.")

    expected_parent = _hex(
        args.get("expectedAuxiliaryPackageRootIdentityDigest"),
        "expectedAuxiliaryPackageRootIdentityDigest",
    )
    expected_manifest = _hex(
        args.get("expectedAuxiliaryPackageManifestDigest"),
        "expectedAuxiliaryPackageManifestDigest",
    )
    expected_manifest_identity = _hex(
        args.get("expectedAuxiliaryPackageManifestIdentityDigest"),
        "expectedAuxiliaryPackageManifestIdentityDigest",
    )
    for key, expected in (
        ("packageRootIdentityDigestBefore", expected_parent),
        ("packageRootIdentityDigestAfter", expected_parent),
        ("packageManifestDigestBefore", expected_manifest),
        ("packageManifestDigestAfter", expected_manifest),
        ("packageManifestIdentityDigestBefore", expected_manifest_identity),
        ("packageManifestIdentityDigestAfter", expected_manifest_identity),
    ):
        if _hex(auxiliary.get(key), f"auxiliary {key}") != expected:
            raise ParameterBitPackingError(
                "Parameter bit-packing auxiliary package identity changed."
            )

    expected_exists = args.get("expectedAuxiliaryRootExistsBefore")
    if type(expected_exists) is not bool:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary approval state is invalid.")
    if (
        auxiliary.get("rootExistsBefore") is not expected_exists
        or auxiliary.get("rootExistsAfter") is not expected_exists
    ):
        raise ParameterBitPackingError("Parameter bit-packing auxiliary root was not restored.")

    expected_tree = _hex(
        args.get("expectedAuxiliaryTreeDigestBefore"), "expectedAuxiliaryTreeDigestBefore"
    )
    expected_content = _hex(
        args.get("expectedAuxiliaryContentDigestBefore"),
        "expectedAuxiliaryContentDigestBefore",
    )
    expected_count = _strict_int(
        args.get("expectedAuxiliaryEntryCountBefore"),
        "expectedAuxiliaryEntryCountBefore",
        0,
        CACHE_BACKUP_MAX_ENTRIES,
    )
    expected_bytes = _strict_int(
        args.get("expectedAuxiliaryByteCountBefore"),
        "expectedAuxiliaryByteCountBefore",
        0,
        CACHE_BACKUP_MAX_BYTES,
    )
    if _hex(auxiliary.get("treeDigestBefore"), "auxiliary treeDigestBefore") != expected_tree:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary tree changed before apply.")
    tree_after = _hex(auxiliary.get("treeDigestAfter"), "auxiliary treeDigestAfter")
    content_before = _hex(auxiliary.get("contentDigestBefore"), "auxiliary contentDigestBefore")
    content_after = _hex(auxiliary.get("contentDigestAfter"), "auxiliary contentDigestAfter")
    count_before = _strict_int(
        auxiliary.get("entryCountBefore"), "auxiliary entryCountBefore", 0, CACHE_BACKUP_MAX_ENTRIES
    )
    count_after = _strict_int(
        auxiliary.get("entryCountAfter"), "auxiliary entryCountAfter", 0, CACHE_BACKUP_MAX_ENTRIES
    )
    bytes_before = _strict_int(
        auxiliary.get("byteCountBefore"), "auxiliary byteCountBefore", 0, CACHE_BACKUP_MAX_BYTES
    )
    bytes_after = _strict_int(
        auxiliary.get("byteCountAfter"), "auxiliary byteCountAfter", 0, CACHE_BACKUP_MAX_BYTES
    )
    if (
        content_before != expected_content
        or content_after != expected_content
        or count_before != expected_count
        or count_after != expected_count
        or bytes_before != expected_bytes
        or bytes_after != expected_bytes
        or (not expected_exists and tree_after != expected_tree)
    ):
        raise ParameterBitPackingError(
            "Parameter bit-packing auxiliary root bytes were not restored exactly."
        )

    observed_exists = auxiliary.get("observedRootExists")
    if type(observed_exists) is not bool:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary observation is invalid.")
    _hex(auxiliary.get("observedTreeDigest"), "auxiliary observedTreeDigest")
    _hex(auxiliary.get("observedContentDigest"), "auxiliary observedContentDigest")
    observed_count = _strict_int(
        auxiliary.get("observedEntryCount"),
        "auxiliary observedEntryCount",
        0,
        CACHE_BACKUP_MAX_ENTRIES,
    )
    observed_bytes = _strict_int(
        auxiliary.get("observedByteCount"),
        "auxiliary observedByteCount",
        0,
        CACHE_BACKUP_MAX_BYTES,
    )
    owned_identity = auxiliary.get("ownedRootIdentityDigest")
    if observed_exists:
        _hex(owned_identity, "auxiliary ownedRootIdentityDigest")
        if observed_count < 2 or observed_bytes < 1:
            raise ParameterBitPackingError("Parameter bit-packing auxiliary observation is incomplete.")
    elif observed_count != 0 or observed_bytes != 0 or owned_identity != "":
        raise ParameterBitPackingError("Parameter bit-packing absent auxiliary observation has residue.")

    created = auxiliary.get("createdByOperation")
    if type(created) is not bool or created is not (not expected_exists and observed_exists):
        raise ParameterBitPackingError("Parameter bit-packing auxiliary ownership proof is invalid.")
    expected_mode = (
        "restored_baseline"
        if expected_exists
        else "removed_created_root"
        if observed_exists
        else "no_auxiliary_root"
    )
    if expected_exists and not observed_exists:
        raise ParameterBitPackingError("Parameter bit-packing present auxiliary root disappeared.")
    if auxiliary.get("restorationMode") != expected_mode:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary restoration mode is invalid.")
    if auxiliary.get("restoreVerified") is not True or auxiliary.get("backupBounded") is not True:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary restoration proof is incomplete.")
    if auxiliary.get("backupMaxEntries") != CACHE_BACKUP_MAX_ENTRIES:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary entry limit changed.")
    if auxiliary.get("backupMaxBytes") != CACHE_BACKUP_MAX_BYTES:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary byte limit changed.")
    if auxiliary.get("journalSchema") != AUXILIARY_JOURNAL_SCHEMA:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary journal schema changed.")
    journal_id = _bounded_string(auxiliary.get("journalId"), "auxiliary journalId", 62, 62)
    if not _AUXILIARY_JOURNAL_ID.fullmatch(journal_id) or auxiliary.get("journalClosed") is not True:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary journal is not durably closed.")


def compute_preview_digest(payload: dict[str, Any]) -> str:
    value = payload if isinstance(payload, dict) else {}
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    capability = value.get("capability") if isinstance(value.get("capability"), dict) else {}
    generated = value.get("generated") if isinstance(value.get("generated"), dict) else {}
    auxiliary = (
        value.get("auxiliaryGenerated")
        if isinstance(value.get("auxiliaryGenerated"), dict)
        else {}
    )
    preferences = value.get("preferences") if isinstance(value.get("preferences"), dict) else {}
    platform = value.get("platformProof") if isinstance(value.get("platformProof"), dict) else {}
    output = value.get("output") if isinstance(value.get("output"), dict) else {}
    fields: list[Any] = [
        value.get("schema"),
        value.get("ok"),
        value.get("preview"),
        value.get("verified"),
        value.get("changed"),
        value.get("saved"),
        value.get("callbacksInvoked"),
        value.get("mutationStarted"),
        value.get("mutationCount"),
        value.get("projectPath"),
        source.get("scenePath"),
        source.get("sceneGuid"),
        source.get("sceneFileDigest"),
        source.get("sceneMetaDigest"),
        source.get("objectPath"),
        source.get("globalObjectId"),
        source.get("hierarchyDigest"),
        source.get("sourceStateDigest"),
        source.get("sourceAssetSetDigest"),
        source.get("sourceAssetCount"),
        source.get("parameterStateDigest"),
        source.get("controllerStateDigest"),
        source.get("menuStateDigest"),
        (source.get("behaviorEvidence") or {}).get("receiptDigest")
        if isinstance(source.get("behaviorEvidence"), dict)
        else None,
        source.get("sourceCostBits"),
        source.get("parameterCount"),
        source.get("safeCandidateDigest"),
        source.get("excludedDigest"),
        capability.get("capabilityDigest"),
        generated.get("root"),
        generated.get("treeDigestBefore"),
        generated.get("contentDigestBefore"),
        generated.get("entryCountBefore"),
        generated.get("byteCountBefore"),
        generated.get("backupMaxEntries"),
        generated.get("backupMaxBytes"),
        generated.get("journalSchema"),
        generated.get("protectedTreeDigestBefore"),
        generated.get("protectedEntryCountBefore"),
        generated.get("rootIdentityDigestBefore"),
        generated.get("rootIdentityCountBefore"),
        auxiliary.get("root"),
        auxiliary.get("packageRoot"),
        auxiliary.get("packageRootIdentityDigestBefore"),
        auxiliary.get("packageManifestDigestBefore"),
        auxiliary.get("packageManifestIdentityDigestBefore"),
        auxiliary.get("rootExistsBefore"),
        auxiliary.get("treeDigestBefore"),
        auxiliary.get("contentDigestBefore"),
        auxiliary.get("entryCountBefore"),
        auxiliary.get("byteCountBefore"),
        auxiliary.get("backupMaxEntries"),
        auxiliary.get("backupMaxBytes"),
        auxiliary.get("journalSchema"),
        auxiliary.get("reparseFree"),
        preferences.get("receiptDigest"),
        platform.get("buildTarget"),
        platform.get("scope"),
        platform.get("crossPlatformEquivalent"),
        platform.get("localAppDataAccessed"),
        output.get("cloneName"),
        output.get("sceneName"),
        output.get("temporaryPrefabPath"),
        output.get("prefabPath"),
        output.get("root"),
        output.get("kindRoot"),
        output.get("treeDigestBefore"),
        output.get("entryCountBefore"),
        output.get("rootExistsBefore"),
        output.get("targetExistsBefore"),
        output.get("cloneExists"),
        output.get("sceneCreated"),
        output.get("prefabExists"),
    ]
    framed = "".join(_frame(item) for item in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def compute_apply_receipt_digest(payload: dict[str, Any]) -> str:
    value = payload if isinstance(payload, dict) else {}
    capability = value.get("capability") if isinstance(value.get("capability"), dict) else {}
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    source_behavior = source.get("behaviorEvidence") if isinstance(source.get("behaviorEvidence"), dict) else {}
    output = value.get("output") if isinstance(value.get("output"), dict) else {}
    preferences = value.get("preferences") if isinstance(value.get("preferences"), dict) else {}
    platform = value.get("platformProof") if isinstance(value.get("platformProof"), dict) else {}
    behavior = value.get("behaviorProof") if isinstance(value.get("behaviorProof"), dict) else {}
    generated = value.get("generated") if isinstance(value.get("generated"), dict) else {}
    auxiliary = (
        value.get("auxiliaryGenerated")
        if isinstance(value.get("auxiliaryGenerated"), dict)
        else {}
    )
    managed = value.get("managedOutput") if isinstance(value.get("managedOutput"), dict) else {}
    staged_manifest = managed.get("stagedManifest") if isinstance(managed.get("stagedManifest"), dict) else {}
    final_manifest = managed.get("finalManifest") if isinstance(managed.get("finalManifest"), dict) else {}
    protected = value.get("protectedProjectTree") if isinstance(value.get("protectedProjectTree"), dict) else {}
    compressed = value.get("compressedParameterNames") if isinstance(value.get("compressedParameterNames"), list) else []
    safe = value.get("approvedSafeCandidateNames") if isinstance(value.get("approvedSafeCandidateNames"), list) else []
    excluded = value.get("excludedParameters") if isinstance(value.get("excludedParameters"), list) else []
    compressed_digest = _name_digest([str(item) for item in compressed], "vrcforge.compressed_parameter_names.v1")
    safe_digest = _name_digest([str(item) for item in safe], "vrcforge.safe_parameter_names.v1")
    try:
        excluded_digest = _excluded_digest(_excluded(excluded))
    except ParameterBitPackingError:
        excluded_digest = "invalid"
    fields: list[Any] = [
        value.get("projectPath"),
        value.get("previewDigest"),
        capability.get("capabilityDigest"),
        value.get("costBeforeBits"),
        value.get("costAfterBits"),
        compressed_digest,
        len(compressed),
        safe_digest,
        len(safe),
        excluded_digest,
        len(excluded),
        source.get("scenePath"),
        source.get("sceneGuid"),
        source.get("sceneFileDigest"),
        source.get("sceneMetaDigest"),
        source.get("objectPath"),
        source.get("globalObjectId"),
        source.get("hierarchyDigest"),
        source.get("sourceStateDigestBefore"),
        source.get("sourceStateDigestAfter"),
        source.get("sourceAssetSetDigestBefore"),
        source.get("sourceAssetSetDigestAfter"),
        source.get("sourceAssetCount"),
        source.get("parameterStateDigest"),
        source.get("parameterCount"),
        source.get("controllerStateDigest"),
        source.get("menuStateDigest"),
        source_behavior.get("receiptDigest"),
        source.get("sourceUnchanged"),
        source.get("sceneDirtyAfter"),
        output.get("cloneName"),
        output.get("sceneName"),
        output.get("scenePath"),
        output.get("scenePersistent"),
        output.get("clonePortableAvatarDigest"),
        output.get("cloneEvidenceDigest"),
        output.get("cloneParameterStateDigest"),
        output.get("prefabPath"),
        output.get("prefabGuid"),
        output.get("prefabFileDigest"),
        output.get("prefabMetaDigest"),
        output.get("prefabRootGlobalObjectId"),
        output.get("prefabPortableAvatarDigest"),
        output.get("prefabOrderedParameterDigest"),
        output.get("prefabMenuGraphDigest"),
        output.get("prefabAnimatorBehaviorDigest"),
        output.get("prefabEvidenceDigest"),
        output.get("prefabBehaviorProofDigest"),
        output.get("prefabParameterStateDigest"),
        output.get("prefabPersistent"),
        output.get("prefabExistsAfter"),
        output.get("sceneLoadedAfter"),
        output.get("temporaryObjectResidue"),
        preferences.get("receiptDigest"),
        platform.get("buildTarget"),
        platform.get("scope"),
        platform.get("crossPlatformEquivalent"),
        platform.get("localAppDataAccessed"),
        behavior.get("receiptDigest"),
        generated.get("root"),
        generated.get("stagingRoot"),
        generated.get("stagingRemoved"),
        generated.get("treeDigestBefore"),
        generated.get("contentDigestBefore"),
        generated.get("entryCountBefore"),
        generated.get("byteCountBefore"),
        generated.get("treeDigestAfter"),
        generated.get("contentDigestAfter"),
        generated.get("entryCountAfter"),
        generated.get("byteCountAfter"),
        generated.get("addedEntryCount"),
        generated.get("modifiedEntryCount"),
        generated.get("removedEntryCount"),
        generated.get("targetResidue"),
        generated.get("deltaDigest"),
        generated.get("cacheRestored"),
        generated.get("backupBounded"),
        generated.get("backupMaxEntries"),
        generated.get("backupMaxBytes"),
        generated.get("journalSchema"),
        generated.get("journalId"),
        generated.get("journalClosed"),
        auxiliary.get("root"),
        auxiliary.get("packageRoot"),
        auxiliary.get("packageRootIdentityDigestBefore"),
        auxiliary.get("packageRootIdentityDigestAfter"),
        auxiliary.get("packageManifestDigestBefore"),
        auxiliary.get("packageManifestDigestAfter"),
        auxiliary.get("packageManifestIdentityDigestBefore"),
        auxiliary.get("packageManifestIdentityDigestAfter"),
        auxiliary.get("rootExistsBefore"),
        auxiliary.get("rootExistsAfter"),
        auxiliary.get("treeDigestBefore"),
        auxiliary.get("treeDigestAfter"),
        auxiliary.get("contentDigestBefore"),
        auxiliary.get("contentDigestAfter"),
        auxiliary.get("entryCountBefore"),
        auxiliary.get("entryCountAfter"),
        auxiliary.get("byteCountBefore"),
        auxiliary.get("byteCountAfter"),
        auxiliary.get("observedRootExists"),
        auxiliary.get("observedTreeDigest"),
        auxiliary.get("observedContentDigest"),
        auxiliary.get("observedEntryCount"),
        auxiliary.get("observedByteCount"),
        auxiliary.get("ownedRootIdentityDigest"),
        auxiliary.get("createdByOperation"),
        auxiliary.get("restorationMode"),
        auxiliary.get("restoreVerified"),
        auxiliary.get("backupBounded"),
        auxiliary.get("backupMaxEntries"),
        auxiliary.get("backupMaxBytes"),
        auxiliary.get("journalSchema"),
        auxiliary.get("journalId"),
        auxiliary.get("journalClosed"),
        managed.get("root"),
        managed.get("kindRoot"),
        managed.get("targetRoot"),
        managed.get("rootExistsBefore"),
        managed.get("rootExistsAfter"),
        managed.get("treeDigestBefore"),
        managed.get("entryCountBefore"),
        managed.get("treeDigestAfter"),
        managed.get("entryCountAfter"),
        managed.get("addedEntryCount"),
        managed.get("targetSubtreeCount"),
        managed.get("modifiedEntryCount"),
        managed.get("removedEntryCount"),
        managed.get("addedEntriesDigest"),
        managed.get("leaseBound"),
        managed.get("stageSavedBeforeMove"),
        managed.get("guidPreservingWholeTreeMove"),
        managed.get("temporaryTreeRemoved"),
        managed.get("prefabGuidPreserved"),
        staged_manifest.get("receiptDigest"),
        final_manifest.get("receiptDigest"),
        managed.get("manifestSchema"),
        managed.get("manifestDigest"),
        managed.get("manifestEntryCount"),
        managed.get("manifestByteCount"),
        managed.get("guidMapDigest"),
        managed.get("dependencyGuidDigest"),
        managed.get("referenceClosureDigest"),
        managed.get("noTemporaryReferences"),
        managed.get("reparseFree"),
        managed.get("singleLink"),
        managed.get("handleHashed"),
        managed.get("finalEnumerationVerified"),
        protected.get("rootIdentityDigestBefore"),
        protected.get("rootIdentityDigestAfter"),
        protected.get("rootIdentityCountBefore"),
        protected.get("rootIdentityCountAfter"),
        protected.get("treeDigestBefore"),
        protected.get("treeDigestAfter"),
        protected.get("entryCountBefore"),
        protected.get("entryCountAfter"),
        value.get("cleanupVerified"),
        value.get("sceneLoadedAfter"),
        value.get("temporaryObjectResidue"),
        value.get("restored"),
        value.get("cleanupRequired"),
        value.get("checkpointRestoreRequired"),
        value.get("operationState"),
    ]
    canonical = APPLY_RECEIPT_SCHEMA + "\n" + "".join(_frame(item) for item in fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_capability_digest(capability: dict[str, Any]) -> str:
    fields = [
        capability.get("packageId"),
        capability.get("packageVersion"),
        capability.get("packageAuthor"),
        capability.get("packageArchiveSha256"),
        capability.get("packageTreeSha256"),
        capability.get("packageFileCount"),
        capability.get("packageRootIdentityDigest"),
        capability.get("profileId"),
        capability.get("callbackAssemblyName"),
        capability.get("callbackAssemblyVersion"),
        capability.get("callbackAssemblyPublicKeyToken"),
        capability.get("callbackAssemblySha256"),
        capability.get("sdkCallbackAssemblyName"),
        capability.get("sdkCallbackAssemblyVersion"),
        capability.get("sdkCallbackAssemblyPublicKeyToken"),
        capability.get("sdkCallbackAssemblySha256"),
        capability.get("callbackType"),
        capability.get("callbackSignature"),
        capability.get("registeredHookType"),
        capability.get("registeredHookCount"),
        capability.get("callbackRosterCount"),
        capability.get("callbackRosterDigest"),
        capability.get("callbackAssemblySetCount"),
        capability.get("callbackAssemblySetDigest"),
    ]
    return hashlib.sha256(
        (CAPABILITY_SCHEMA + "\n" + "".join(_frame(item) for item in fields)).encode("utf-8")
    ).hexdigest()


def _source(value: Any) -> dict[str, Any]:
    source = _exact_dict(value, "source", _PREVIEW_SOURCE_KEYS)
    result = {
        "scenePath": _asset_path(source.get("scenePath"), suffix=".unity"),
        "sceneGuid": _hex(source.get("sceneGuid"), "sceneGuid", _GUID),
        "sceneFileDigest": _hex(source.get("sceneFileDigest"), "sceneFileDigest"),
        "sceneMetaDigest": _hex(source.get("sceneMetaDigest"), "sceneMetaDigest"),
        "objectPath": _scene_object_path(source.get("objectPath")),
        "globalObjectId": _bounded_string(source.get("globalObjectId"), "globalObjectId", 8, 256),
        "hierarchyDigest": _hex(source.get("hierarchyDigest"), "hierarchyDigest"),
        "sourceStateDigest": _hex(source.get("sourceStateDigest"), "sourceStateDigest"),
        "sourceAssetSetDigest": _hex(source.get("sourceAssetSetDigest"), "sourceAssetSetDigest"),
        "sourceAssetCount": _strict_int(source.get("sourceAssetCount"), "sourceAssetCount", 3, 4096),
        "parameterStateDigest": _hex(source.get("parameterStateDigest"), "parameterStateDigest"),
        "controllerStateDigest": _hex(source.get("controllerStateDigest"), "controllerStateDigest"),
        "menuStateDigest": _hex(source.get("menuStateDigest"), "menuStateDigest"),
        "behaviorEvidence": _behavior_evidence(source.get("behaviorEvidence"), "source behavior evidence"),
        "sourceCostBits": _strict_int(source.get("sourceCostBits"), "sourceCostBits", 257, 4096),
        "parameterCount": _strict_int(source.get("parameterCount"), "parameterCount", 1, 2048),
        "safeCandidateNames": _sorted_names(source.get("safeCandidateNames"), "safeCandidateNames"),
        "safeCandidateDigest": _hex(source.get("safeCandidateDigest"), "safeCandidateDigest"),
        "excludedParameters": _excluded(source.get("excludedParameters")),
        "excludedDigest": _hex(source.get("excludedDigest"), "excludedDigest"),
    }
    if source.get("sourceDirty") is not False or source.get("referencedAssetsDirty") is not False:
        raise ParameterBitPackingError("Parameter bit-packing source must be saved and clean.")
    if not result["safeCandidateNames"]:
        raise ParameterBitPackingError("Parameter bit-packing requires safe boolean toggle candidates.")
    if _name_digest(result["safeCandidateNames"], "vrcforge.safe_parameter_names.v1") != result["safeCandidateDigest"]:
        raise ParameterBitPackingError("Parameter bit-packing safe candidate digest is invalid.")
    if _excluded_digest(result["excludedParameters"]) != result["excludedDigest"]:
        raise ParameterBitPackingError("Parameter bit-packing exclusion digest is invalid.")
    if result["behaviorEvidence"]["parameterCount"] != result["parameterCount"]:
        raise ParameterBitPackingError("Parameter bit-packing behavior evidence parameter count is invalid.")
    if result["sourceCostBits"] - len(result["safeCandidateNames"]) > 248:
        raise ParameterBitPackingError("Safe candidates cannot reduce the parameter budget with required overhead.")
    return result


def _capability(value: Any) -> dict[str, Any]:
    capability = _exact_dict(value, "capability", _CAPABILITY_KEYS)
    profile_id = capability.get("profileId")
    if not isinstance(profile_id, str) or profile_id not in _CAPABILITY_PROFILES:
        raise ParameterBitPackingError("Parameter bit-packing capability profileId is not allowlisted.")
    profile = _CAPABILITY_PROFILES[profile_id]
    package_root_identity_digest = _hex(
        capability.get("packageRootIdentityDigest"), "packageRootIdentityDigest"
    )
    expected = {
        "packageId": PACKAGE_ID,
        "packageVersion": PACKAGE_VERSION,
        "packageAuthor": PACKAGE_AUTHOR,
        "packageArchiveSha256": PACKAGE_ARCHIVE_SHA256,
        "packageTreeSha256": PACKAGE_TREE_SHA256,
        "packageFileCount": PACKAGE_FILE_COUNT,
        "packageRootIdentityDigest": package_root_identity_digest,
        "profileId": profile_id,
        "callbackAssemblyName": CALLBACK_ASSEMBLY_NAME,
        "callbackAssemblyVersion": CALLBACK_ASSEMBLY_VERSION,
        "callbackAssemblyPublicKeyToken": CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN,
        "callbackAssemblySha256": profile["callbackAssemblySha256"],
        "sdkCallbackAssemblyName": SDK_CALLBACK_ASSEMBLY_NAME,
        "sdkCallbackAssemblyVersion": SDK_CALLBACK_ASSEMBLY_VERSION,
        "sdkCallbackAssemblyPublicKeyToken": SDK_CALLBACK_ASSEMBLY_PUBLIC_KEY_TOKEN,
        "sdkCallbackAssemblySha256": SDK_CALLBACK_ASSEMBLY_SHA256,
        "callbackType": CALLBACK_TYPE,
        "callbackSignature": CALLBACK_SIGNATURE,
        "registeredHookType": REGISTERED_HOOK_TYPE,
        "registeredHookCount": 1,
        "callbackRosterCount": profile["callbackRosterCount"],
        "callbackRosterDigest": profile["callbackRosterDigest"],
        "callbackAssemblySetCount": profile["callbackAssemblySetCount"],
        "callbackAssemblySetDigest": profile["callbackAssemblySetDigest"],
    }
    for key, expected_value in expected.items():
        actual_value = capability.get(key)
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            raise ParameterBitPackingError(f"Parameter bit-packing capability {key} is not allowlisted.")
    result = deepcopy(expected)
    result["capabilityDigest"] = _hex(capability.get("capabilityDigest"), "capabilityDigest")
    digest_input = deepcopy(result)
    digest_input.pop("capabilityDigest")
    if compute_capability_digest(digest_input) != result["capabilityDigest"]:
        raise ParameterBitPackingError("Parameter bit-packing capability digest is invalid.")
    return result


def _generated_before(value: Any) -> dict[str, Any]:
    generated = _exact_dict(value, "generated", _PREVIEW_GENERATED_KEYS)
    if generated.get("root") != GENERATED_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing generated root is invalid.")
    digest = _hex(generated.get("treeDigestBefore"), "treeDigestBefore")
    content_digest = _hex(generated.get("contentDigestBefore"), "contentDigestBefore")
    count = _strict_int(generated.get("entryCountBefore"), "entryCountBefore", 0, 100000)
    byte_count = _strict_int(generated.get("byteCountBefore"), "byteCountBefore", 0, CACHE_BACKUP_MAX_BYTES)
    protected_digest = _hex(generated.get("protectedTreeDigestBefore"), "protectedTreeDigestBefore")
    protected_count = _strict_int(
        generated.get("protectedEntryCountBefore"),
        "protectedEntryCountBefore",
        1,
        100000,
    )
    root_identity_digest = _hex(generated.get("rootIdentityDigestBefore"), "rootIdentityDigestBefore")
    root_identity_count = _strict_int(
        generated.get("rootIdentityCountBefore"), "rootIdentityCountBefore", 5, 32
    )
    if generated.get("exists") is not True or generated.get("reparseFree") is not True:
        raise ParameterBitPackingError("Parameter bit-packing generated root is not a safe directory.")
    if generated.get("backupMaxEntries") != CACHE_BACKUP_MAX_ENTRIES:
        raise ParameterBitPackingError("Parameter bit-packing cache entry limit is invalid.")
    if generated.get("backupMaxBytes") != CACHE_BACKUP_MAX_BYTES:
        raise ParameterBitPackingError("Parameter bit-packing cache byte limit is invalid.")
    if generated.get("journalSchema") != CACHE_JOURNAL_SCHEMA:
        raise ParameterBitPackingError("Parameter bit-packing cache journal schema is invalid.")
    return {
        "root": GENERATED_ROOT,
        "treeDigestBefore": digest,
        "contentDigestBefore": content_digest,
        "entryCountBefore": count,
        "byteCountBefore": byte_count,
        "backupMaxEntries": CACHE_BACKUP_MAX_ENTRIES,
        "backupMaxBytes": CACHE_BACKUP_MAX_BYTES,
        "journalSchema": CACHE_JOURNAL_SCHEMA,
        "protectedTreeDigestBefore": protected_digest,
        "protectedEntryCountBefore": protected_count,
        "rootIdentityDigestBefore": root_identity_digest,
        "rootIdentityCountBefore": root_identity_count,
    }


def _auxiliary_before(value: Any) -> dict[str, Any]:
    auxiliary = _exact_dict(value, "auxiliaryGenerated", _PREVIEW_AUXILIARY_KEYS)
    if auxiliary.get("root") != AUXILIARY_GENERATED_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary generated root is invalid.")
    if auxiliary.get("packageRoot") != AUXILIARY_PACKAGE_ROOT:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary package root is invalid.")
    package_root_identity = _hex(
        auxiliary.get("packageRootIdentityDigestBefore"),
        "auxiliary packageRootIdentityDigestBefore",
    )
    package_manifest_digest = _hex(
        auxiliary.get("packageManifestDigestBefore"),
        "auxiliary packageManifestDigestBefore",
    )
    package_manifest_identity = _hex(
        auxiliary.get("packageManifestIdentityDigestBefore"),
        "auxiliary packageManifestIdentityDigestBefore",
    )
    exists = auxiliary.get("rootExistsBefore")
    if type(exists) is not bool:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary baseline state is invalid.")
    tree_digest = _hex(auxiliary.get("treeDigestBefore"), "auxiliary treeDigestBefore")
    content_digest = _hex(auxiliary.get("contentDigestBefore"), "auxiliary contentDigestBefore")
    entry_count = _strict_int(
        auxiliary.get("entryCountBefore"), "auxiliary entryCountBefore", 0, CACHE_BACKUP_MAX_ENTRIES
    )
    byte_count = _strict_int(
        auxiliary.get("byteCountBefore"), "auxiliary byteCountBefore", 0, CACHE_BACKUP_MAX_BYTES
    )
    if exists:
        if entry_count < 2 or byte_count < 1:
            raise ParameterBitPackingError(
                "Parameter bit-packing present auxiliary baseline is incomplete."
            )
    elif entry_count != 0 or byte_count != 0:
        raise ParameterBitPackingError(
            "Parameter bit-packing absent auxiliary baseline has filesystem residue."
        )
    if auxiliary.get("backupMaxEntries") != CACHE_BACKUP_MAX_ENTRIES:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary entry limit is invalid.")
    if auxiliary.get("backupMaxBytes") != CACHE_BACKUP_MAX_BYTES:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary byte limit is invalid.")
    if auxiliary.get("journalSchema") != AUXILIARY_JOURNAL_SCHEMA:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary journal schema is invalid.")
    if auxiliary.get("reparseFree") is not True:
        raise ParameterBitPackingError("Parameter bit-packing auxiliary baseline is not reparse-free.")
    return {
        "root": AUXILIARY_GENERATED_ROOT,
        "packageRoot": AUXILIARY_PACKAGE_ROOT,
        "packageRootIdentityDigestBefore": package_root_identity,
        "packageManifestDigestBefore": package_manifest_digest,
        "packageManifestIdentityDigestBefore": package_manifest_identity,
        "rootExistsBefore": exists,
        "treeDigestBefore": tree_digest,
        "contentDigestBefore": content_digest,
        "entryCountBefore": entry_count,
        "byteCountBefore": byte_count,
        "backupMaxEntries": CACHE_BACKUP_MAX_ENTRIES,
        "backupMaxBytes": CACHE_BACKUP_MAX_BYTES,
        "journalSchema": AUXILIARY_JOURNAL_SCHEMA,
        "reparseFree": True,
    }


def _output_preview(value: Any, clone_name: str) -> dict[str, Any]:
    output = _exact_dict(value, "output", _PREVIEW_OUTPUT_KEYS)
    scene_name = _bounded_string(output.get("sceneName"), "sceneName", 1, 128)
    expected_scene = "VRCForge Parameter Build - " + clone_name
    expected_prefab = _output_prefab_path(clone_name)
    expected_temporary_prefab = f"{GENERATED_ROOT}/{clone_name}/{clone_name}.prefab"
    if (
        scene_name != expected_scene
        or output.get("cloneName") != clone_name
        or output.get("temporaryPrefabPath") != expected_temporary_prefab
        or output.get("prefabPath") != expected_prefab
        or output.get("root") != OUTPUT_ROOT
        or output.get("kindRoot") != OUTPUT_KIND_ROOT
    ):
        raise ParameterBitPackingError("Parameter bit-packing output target is invalid.")
    if (
        output.get("cloneExists") is not False
        or output.get("sceneCreated") is not False
        or output.get("prefabExists") is not False
        or output.get("targetExistsBefore") is not False
    ):
        raise ParameterBitPackingError("Parameter bit-packing preview created output state.")
    root_exists = output.get("rootExistsBefore")
    if not isinstance(root_exists, bool):
        raise ParameterBitPackingError("Parameter bit-packing output root state is invalid.")
    return {
        "root": OUTPUT_ROOT,
        "kindRoot": OUTPUT_KIND_ROOT,
        "cloneName": clone_name,
        "sceneName": scene_name,
        "temporaryPrefabPath": expected_temporary_prefab,
        "prefabPath": expected_prefab,
        "treeDigestBefore": _hex(output.get("treeDigestBefore"), "output treeDigestBefore"),
        "entryCountBefore": _strict_int(
            output.get("entryCountBefore"), "output entryCountBefore", 0, 100000
        ),
        "rootExistsBefore": root_exists,
        "targetExistsBefore": False,
        "cloneExists": False,
        "sceneCreated": False,
        "prefabExists": False,
        "scenePersistent": False,
        "generatedRoot": GENERATED_ROOT,
    }


def _behavior_evidence(value: Any, label: str) -> dict[str, Any]:
    evidence = _exact_dict(value, label, _BEHAVIOR_EVIDENCE_KEYS)
    if evidence.get("schema") != BEHAVIOR_EVIDENCE_SCHEMA:
        raise ParameterBitPackingError(f"{label} schema is invalid.")
    result = {
        "schema": BEHAVIOR_EVIDENCE_SCHEMA,
        "portableAvatarDigest": _hex(evidence.get("portableAvatarDigest"), f"{label} portableAvatarDigest"),
        "orderedParameterDigest": _hex(evidence.get("orderedParameterDigest"), f"{label} orderedParameterDigest"),
        "parameterCount": _strict_int(evidence.get("parameterCount"), f"{label} parameterCount", 1, 4096),
        "menuGraphDigest": _hex(evidence.get("menuGraphDigest"), f"{label} menuGraphDigest"),
        "menuRowCount": _strict_int(evidence.get("menuRowCount"), f"{label} menuRowCount", 0, 100000),
        "animatorBehaviorDigest": _hex(
            evidence.get("animatorBehaviorDigest"), f"{label} animatorBehaviorDigest"
        ),
        "animatorRowCount": _strict_int(
            evidence.get("animatorRowCount"), f"{label} animatorRowCount", 0, 100000
        ),
        "receiptDigest": _hex(evidence.get("receiptDigest"), f"{label} receiptDigest"),
    }
    expected = _sha256_framed(
        BEHAVIOR_EVIDENCE_SCHEMA,
        result["portableAvatarDigest"],
        result["orderedParameterDigest"],
        result["parameterCount"],
        result["menuGraphDigest"],
        result["menuRowCount"],
        result["animatorBehaviorDigest"],
        result["animatorRowCount"],
    )
    if result["receiptDigest"] != expected:
        raise ParameterBitPackingError(f"{label} receipt digest is invalid.")
    return result


def _behavior_proof(value: Any) -> dict[str, Any]:
    proof = _exact_dict(value, "behavior proof", _BEHAVIOR_PROOF_KEYS)
    if proof.get("schema") != BEHAVIOR_PROOF_SCHEMA:
        raise ParameterBitPackingError("Parameter bit-packing behavior proof schema is invalid.")
    if proof.get("status") != "verified" or proof.get("platformScope") != PLATFORM_SCOPE:
        raise ParameterBitPackingError("Parameter bit-packing behavior proof state is invalid.")
    if proof.get("crossPlatformEquivalent") is not False:
        raise ParameterBitPackingError("Parameter bit-packing behavior proof overclaims platform equivalence.")
    result = {
        "schema": BEHAVIOR_PROOF_SCHEMA,
        "status": "verified",
        "platformScope": PLATFORM_SCOPE,
        "crossPlatformEquivalent": False,
        "sourceOrderedParameterDigest": _hex(proof.get("sourceOrderedParameterDigest"), "sourceOrderedParameterDigest"),
        "outputOrderedParameterDigest": _hex(proof.get("outputOrderedParameterDigest"), "outputOrderedParameterDigest"),
        "sourceParameterCount": _strict_int(proof.get("sourceParameterCount"), "sourceParameterCount", 1, 4096),
        "outputParameterCount": _strict_int(proof.get("outputParameterCount"), "outputParameterCount", 1, 4096),
        "sourceMenuGraphDigest": _hex(proof.get("sourceMenuGraphDigest"), "sourceMenuGraphDigest"),
        "outputMenuGraphDigest": _hex(proof.get("outputMenuGraphDigest"), "outputMenuGraphDigest"),
        "sourceMenuRowCount": _strict_int(proof.get("sourceMenuRowCount"), "sourceMenuRowCount", 0, 100000),
        "outputMenuRowCount": _strict_int(proof.get("outputMenuRowCount"), "outputMenuRowCount", 0, 100000),
        "sourceAnimatorBehaviorDigest": _hex(
            proof.get("sourceAnimatorBehaviorDigest"), "sourceAnimatorBehaviorDigest"
        ),
        "outputAnimatorBehaviorDigest": _hex(
            proof.get("outputAnimatorBehaviorDigest"), "outputAnimatorBehaviorDigest"
        ),
        "sourceAnimatorRowCount": _strict_int(
            proof.get("sourceAnimatorRowCount"), "sourceAnimatorRowCount", 0, 100000
        ),
        "outputAnimatorRowCount": _strict_int(
            proof.get("outputAnimatorRowCount"), "outputAnimatorRowCount", 0, 100000
        ),
        "preservedBehaviorDigest": _hex(proof.get("preservedBehaviorDigest"), "preservedBehaviorDigest"),
        "codecGraphDigest": _hex(proof.get("codecGraphDigest"), "codecGraphDigest"),
        "codecMappingDigest": _hex(proof.get("codecMappingDigest"), "codecMappingDigest"),
        "codecMappingCount": _strict_int(proof.get("codecMappingCount"), "codecMappingCount", 1, 2048),
        "excludedBeforeDigest": _hex(proof.get("excludedBeforeDigest"), "excludedBeforeDigest"),
        "excludedAfterDigest": _hex(proof.get("excludedAfterDigest"), "excludedAfterDigest"),
        "receiptDigest": _hex(proof.get("receiptDigest"), "behavior proof receiptDigest"),
    }
    if result["excludedBeforeDigest"] != result["excludedAfterDigest"]:
        raise ParameterBitPackingError("Parameter bit-packing changed excluded behavior.")
    expected = _sha256_framed(
        BEHAVIOR_PROOF_SCHEMA,
        result["status"],
        result["platformScope"],
        result["crossPlatformEquivalent"],
        result["sourceOrderedParameterDigest"],
        result["outputOrderedParameterDigest"],
        result["sourceParameterCount"],
        result["outputParameterCount"],
        result["sourceMenuGraphDigest"],
        result["outputMenuGraphDigest"],
        result["sourceMenuRowCount"],
        result["outputMenuRowCount"],
        result["sourceAnimatorBehaviorDigest"],
        result["outputAnimatorBehaviorDigest"],
        result["sourceAnimatorRowCount"],
        result["outputAnimatorRowCount"],
        result["preservedBehaviorDigest"],
        result["codecGraphDigest"],
        result["codecMappingDigest"],
        result["codecMappingCount"],
        result["excludedBeforeDigest"],
        result["excludedAfterDigest"],
    )
    if result["receiptDigest"] != expected:
        raise ParameterBitPackingError("Parameter bit-packing behavior proof receipt is invalid.")
    return result


def _preferences(value: Any) -> dict[str, Any]:
    preferences = _exact_dict(value, "preferences", _PREFERENCE_KEYS)
    if preferences.get("schema") != PREFERENCE_SCHEMA:
        raise ParameterBitPackingError("Parameter bit-packing preference schema is invalid.")
    compressor_present = preferences.get("compressorPresent")
    align_mobile_present = preferences.get("alignMobilePresent")
    align_mobile_value = preferences.get("alignMobileValue")
    if not isinstance(compressor_present, bool) or not isinstance(align_mobile_present, bool):
        raise ParameterBitPackingError("Parameter bit-packing preference presence is invalid.")
    if not isinstance(align_mobile_value, bool):
        raise ParameterBitPackingError("Parameter bit-packing mobile alignment preference is invalid.")
    compressor_value = _strict_int(preferences.get("compressorValue"), "compressorValue", 0, 0)
    expected_mode = "explicit-automatic" if compressor_present else "missing-default-automatic"
    if preferences.get("compressorMode") != expected_mode or preferences.get("readOnly") is not True:
        raise ParameterBitPackingError("Parameter bit-packing preference receipt is not read-only automatic mode.")
    if preferences.get("buildTarget") != DESKTOP_BUILD_TARGET:
        raise ParameterBitPackingError("Parameter bit-packing requires the current desktop build target.")
    if (
        preferences.get("platformScope") != PLATFORM_SCOPE
        or preferences.get("crossPlatformEquivalent") is not False
        or preferences.get("localAppDataAccessed") is not False
    ):
        raise ParameterBitPackingError("Parameter bit-packing preference scope is invalid.")
    receipt = _hex(preferences.get("receiptDigest"), "preference receiptDigest")
    expected = _sha256_framed(
        PREFERENCE_SCHEMA,
        compressor_present,
        compressor_value,
        align_mobile_present,
        align_mobile_value,
        DESKTOP_BUILD_TARGET,
        PLATFORM_SCOPE,
        False,
        False,
    )
    if receipt != expected:
        raise ParameterBitPackingError("Parameter bit-packing preference receipt digest is invalid.")
    return {
        "schema": PREFERENCE_SCHEMA,
        "compressorPresent": compressor_present,
        "compressorValue": compressor_value,
        "compressorMode": expected_mode,
        "alignMobilePresent": align_mobile_present,
        "alignMobileValue": align_mobile_value,
        "readOnly": True,
        "buildTarget": DESKTOP_BUILD_TARGET,
        "platformScope": PLATFORM_SCOPE,
        "crossPlatformEquivalent": False,
        "localAppDataAccessed": False,
        "receiptDigest": receipt,
    }


def _platform_proof(value: Any) -> dict[str, Any]:
    proof = _exact_dict(value, "platform proof", _PLATFORM_PROOF_KEYS)
    if (
        proof.get("buildTarget") != DESKTOP_BUILD_TARGET
        or proof.get("scope") != PLATFORM_SCOPE
        or proof.get("crossPlatformEquivalent") is not False
        or proof.get("localAppDataAccessed") is not False
    ):
        raise ParameterBitPackingError("Parameter bit-packing platform proof is invalid.")
    return {
        "buildTarget": DESKTOP_BUILD_TARGET,
        "scope": PLATFORM_SCOPE,
        "crossPlatformEquivalent": False,
        "localAppDataAccessed": False,
    }


def _asset_manifest(
    value: Any,
    *,
    label: str,
    expected_root: str,
    expected_prefab: str,
    expect_no_temporary_references: bool,
) -> dict[str, Any]:
    manifest = _exact_dict(value, label, _ASSET_MANIFEST_KEYS)
    if manifest.get("schema") != OUTPUT_MANIFEST_SCHEMA:
        raise ParameterBitPackingError(f"{label} schema is invalid.")
    if manifest.get("rootPath") != expected_root or manifest.get("prefabPath") != expected_prefab:
        raise ParameterBitPackingError(f"{label} target is invalid.")
    result = {
        "schema": OUTPUT_MANIFEST_SCHEMA,
        "rootPath": expected_root,
        "prefabPath": expected_prefab,
        "entryCount": _strict_int(manifest.get("entryCount"), f"{label} entryCount", 4, 100000),
        "byteCount": _strict_int(manifest.get("byteCount"), f"{label} byteCount", 1, 2**63 - 1),
        "contentDigest": _hex(manifest.get("contentDigest"), f"{label} contentDigest"),
        "handleEvidenceDigest": _hex(
            manifest.get("handleEvidenceDigest"), f"{label} handleEvidenceDigest"
        ),
        "guidMapDigest": _hex(manifest.get("guidMapDigest"), f"{label} guidMapDigest"),
        "dependencyGuidDigest": _hex(
            manifest.get("dependencyGuidDigest"), f"{label} dependencyGuidDigest"
        ),
        "referenceClosureDigest": _hex(
            manifest.get("referenceClosureDigest"), f"{label} referenceClosureDigest"
        ),
        "noTemporaryReferences": expect_no_temporary_references,
        "reparseFree": True,
        "singleLink": True,
        "handleHashed": True,
        "finalEnumerationVerified": True,
        "receiptDigest": _hex(manifest.get("receiptDigest"), f"{label} receiptDigest"),
    }
    for key, expected in (
        ("noTemporaryReferences", expect_no_temporary_references),
        ("reparseFree", True),
        ("singleLink", True),
        ("handleHashed", True),
        ("finalEnumerationVerified", True),
    ):
        if manifest.get(key) is not expected:
            raise ParameterBitPackingError(f"{label} {key} is invalid.")
    expected_receipt = _sha256_framed(
        OUTPUT_MANIFEST_SCHEMA,
        result["rootPath"],
        result["prefabPath"],
        result["entryCount"],
        result["byteCount"],
        result["contentDigest"],
        result["handleEvidenceDigest"],
        result["guidMapDigest"],
        result["dependencyGuidDigest"],
        result["referenceClosureDigest"],
        result["noTemporaryReferences"],
        result["reparseFree"],
        result["singleLink"],
        result["handleHashed"],
        result["finalEnumerationVerified"],
    )
    if result["receiptDigest"] != expected_receipt:
        raise ParameterBitPackingError(f"{label} receipt digest is invalid.")
    return result


def _excluded(value: Any, *, require_canonical: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ParameterBitPackingError("excludedParameters must be a list.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        item = _exact_dict(raw, "excluded parameter", _EXCLUDED_PARAMETER_KEYS)
        name = _parameter_name(item.get("name"))
        if name in seen:
            raise ParameterBitPackingError("excludedParameters contains duplicate names.")
        seen.add(name)
        parameter_type = str(item.get("type") or "")
        if parameter_type not in _PARAMETER_TYPES:
            raise ParameterBitPackingError("Excluded parameter type is invalid.")
        synced = item.get("networkSynced")
        if not isinstance(synced, bool):
            raise ParameterBitPackingError("Excluded parameter networkSynced is invalid.")
        reasons_raw = item.get("reasons")
        if not isinstance(reasons_raw, list) or not reasons_raw:
            raise ParameterBitPackingError("Excluded parameter reasons are required.")
        raw_reasons = [str(reason) for reason in reasons_raw]
        reasons = sorted(set(raw_reasons))
        if len(reasons) != len(reasons_raw) or not set(reasons).issubset(_ALLOWED_EXCLUSION_REASONS):
            raise ParameterBitPackingError("Excluded parameter reasons are invalid.")
        if require_canonical and raw_reasons != reasons:
            raise ParameterBitPackingError("Excluded parameter reasons are not canonical.")
        result.append(
            {
                "name": name,
                "type": parameter_type,
                "networkSynced": synced,
                "reasons": reasons,
                "stateDigest": _hex(item.get("stateDigest"), "excluded stateDigest"),
            }
        )
    canonical = sorted(result, key=lambda item: item["name"])
    if require_canonical and result != canonical:
        raise ParameterBitPackingError("excludedParameters is not canonical.")
    result = canonical
    return result


def _excluded_digest(items: list[dict[str, Any]]) -> str:
    framed = "vrcforge.excluded_parameters.v1\n"
    for item in items:
        framed += "".join(
            _frame(value)
            for value in (
                item["name"],
                item["type"],
                item["networkSynced"],
                ",".join(item["reasons"]),
                item["stateDigest"],
            )
        )
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _name_digest(names: list[str], schema: str) -> str:
    return hashlib.sha256(
        (schema + "\n" + "".join(_frame(name) for name in names)).encode("utf-8")
    ).hexdigest()


def _sorted_names(
    value: Any,
    label: str,
    *,
    require_canonical: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ParameterBitPackingError(f"{label} must be a list.")
    raw_names = [_parameter_name(item) for item in value]
    names = sorted(raw_names)
    if len(names) != len(set(names)):
        raise ParameterBitPackingError(f"{label} contains duplicates.")
    if require_canonical and raw_names != names:
        raise ParameterBitPackingError(f"{label} is not canonical.")
    return names


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParameterBitPackingError(f"{label} must be an object.")
    return value


def _exact_dict(
    value: Any,
    label: str,
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    result = _dict(value, label)
    actual = set(result)
    if actual != expected_keys:
        raise ParameterBitPackingError(f"{label} fields are invalid.")
    return result


def _project_path(value: Any) -> str:
    raw = _bounded_string(value, "projectPath", 3, 1024)
    if not _WINDOWS_ABSOLUTE.match(raw):
        raise ParameterBitPackingError("projectPath must be an absolute Windows path.")
    return str(Path(raw).resolve())


def _asset_path(value: Any, *, suffix: str, required_prefix: str = "Assets/") -> str:
    raw = _bounded_string(value, "asset path", 8, 512).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or not raw.startswith(required_prefix) or not raw.endswith(suffix):
        raise ParameterBitPackingError("Asset path is outside the approved project root.")
    return path.as_posix()


def _scene_object_path(value: Any) -> str:
    raw = _bounded_string(value, "scene object path", 1, 512).replace("\\", "/")
    parts = raw.split("/")
    if any(not part or part in {".", ".."} or not _OBJECT_NAME.fullmatch(part) for part in parts):
        raise ParameterBitPackingError("Scene object path is invalid.")
    return "/".join(parts)


def _object_name(value: Any) -> str:
    raw = _bounded_string(value, "object name", 1, 80).strip()
    stem = raw.split(".", 1)[0].casefold()
    if (
        not _OBJECT_NAME.fullmatch(raw)
        or raw in {".", ".."}
        or raw.endswith((".", " "))
        or stem in _WINDOWS_RESERVED_STEMS
    ):
        raise ParameterBitPackingError("Object name is invalid.")
    return raw


def _output_prefab_path(clone_name: str) -> str:
    safe_name = _object_name(clone_name)
    return f"{OUTPUT_KIND_ROOT}/{safe_name}/{safe_name}.prefab"


def _parameter_name(value: Any) -> str:
    raw = _bounded_string(value, "parameter name", 1, 128)
    if not _PARAMETER_NAME.fullmatch(raw):
        raise ParameterBitPackingError("Parameter name is invalid.")
    return raw


def _bounded_string(value: Any, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ParameterBitPackingError(f"{label} must be text.")
    text = value.strip()
    if not minimum <= len(text) <= maximum:
        raise ParameterBitPackingError(f"{label} length is invalid.")
    return text


def _strict_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ParameterBitPackingError(f"{label} is invalid.")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str] = _DIGEST) -> str:
    if not isinstance(value, str):
        raise ParameterBitPackingError(f"{label} must be hexadecimal text.")
    lowered = value.strip().lower()
    if not pattern.fullmatch(lowered):
        raise ParameterBitPackingError(f"{label} is invalid.")
    return lowered


def _frame(value: Any) -> str:
    if value is True:
        text = "true"
    elif value is False:
        text = "false"
    elif value is None:
        text = "null"
    else:
        text = str(value)
    return f"{len(text.encode('utf-8'))}:{text}"


def _sha256_framed(schema: str, *values: Any) -> str:
    canonical = schema + "\n" + "".join(_frame(value) for value in values)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
