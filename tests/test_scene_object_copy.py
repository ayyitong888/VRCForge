from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scene_object_copy import (
    APPROVAL_SCHEMA,
    DUPLICATE_TOOL_NAME,
    PREFAB_TOOL_NAME,
    RESULT_SCHEMA,
    SceneObjectCopyError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_preview_digest,
)


def _source() -> dict:
    return {
        "scenePath": "Assets/Scenes/AccessoryCopy.unity",
        "sceneGuid": "a" * 32,
        "sceneHandle": 11,
        "objectPath": "AvatarA/Accessory",
        "objectId": "b" * 64,
        "hierarchyDigest": "c" * 64,
        "sceneFileDigest": "d" * 64,
        "sceneFileIdentity": "9" * 64,
        "sceneMetaDigest": "8" * 64,
        "sceneMetaIdentity": "7" * 64,
        "pathUnique": True,
    }


def _duplicate_payload() -> dict:
    payload = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "operation": "duplicate_scene_object",
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "source": _source(),
        "target": {
            "scenePath": "Assets/Scenes/AccessoryCopy.unity",
            "sceneGuid": "a" * 32,
            "sceneHandle": 11,
            "parentPath": "AvatarB",
            "parentObjectId": "e" * 64,
            "parentHierarchyDigest": "f" * 64,
            "sceneFileDigest": "d" * 64,
            "sceneFileIdentity": "9" * 64,
            "sceneMetaDigest": "8" * 64,
            "sceneMetaIdentity": "7" * 64,
            "objectPath": "AvatarB/AccessoryCopy",
            "name": "AccessoryCopy",
            "parentPathUnique": True,
            "nameCollision": False,
            "sameDestination": False,
            "targetWithinSource": False,
        },
        "preserveWorldTransform": False,
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def _prefab_payload() -> dict:
    payload = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "operation": "save_scene_object_as_prefab",
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "source": _source(),
        "target": {
            "assetPath": "Assets/VRCForge/Generated/Accessories/AccessoryCopy.prefab",
            "parentFolderPath": "Assets/VRCForge/Generated/Accessories",
            "parentFolderGuid": "1" * 32,
            "parentFolderIdentity": "2" * 64,
            "stagingRootPath": "Assets/VRCForge/Generated",
            "stagingRootGuid": "3" * 32,
            "stagingRootIdentity": "4" * 64,
            "stagingPolicy": "random_create_new_folder_v1",
            "assetExists": False,
            "metaExists": False,
            "createNew": True,
        },
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def _duplicate_wrapper() -> dict:
    return build_wrapper_arguments(
        {
            "projectPath": "D:/DisposableUnityProject",
            "sourceScenePath": "Assets/Scenes/AccessoryCopy.unity",
            "sourceObjectPath": "AvatarA/Accessory",
            "targetParentScenePath": "Assets/Scenes/AccessoryCopy.unity",
            "targetParentPath": "AvatarB",
            "targetName": "AccessoryCopy",
            "preserveWorldTransform": False,
        },
        DUPLICATE_TOOL_NAME,
    )


def _prefab_wrapper() -> dict:
    return build_wrapper_arguments(
        {
            "projectPath": "D:/DisposableUnityProject",
            "sourceScenePath": "Assets/Scenes/AccessoryCopy.unity",
            "sourceObjectPath": "AvatarA/Accessory",
            "prefabAssetPath": "Assets/VRCForge/Generated/Accessories/AccessoryCopy.prefab",
        },
        PREFAB_TOOL_NAME,
    )


@pytest.mark.parametrize("tool_name", [DUPLICATE_TOOL_NAME, PREFAB_TOOL_NAME])
def test_preview_arguments_discard_caller_preconditions_and_force_zero_write(tool_name: str) -> None:
    raw = {
        "sourceObjectPath": "AvatarA/Accessory",
        "expectedSourceObjectId": "0" * 64,
        "expectedSourceHierarchyDigest": "1" * 64,
        "expectedPreviewDigest": "2" * 64,
        "preview": False,
        "saveScene": True,
        "saveAssets": True,
        "overwrite": True,
        "secretField": "must-not-cross-the-tool-boundary",
        "unknownNested": {"value": 1},
    }

    prepared = build_preview_arguments(tool_name, raw)

    assert prepared["preview"] is True
    assert prepared["saveScene"] is False
    assert prepared["saveAssets"] is False
    assert prepared["overwrite"] is False
    assert not any(key.startswith("expected") for key in prepared)
    assert "secretField" not in prepared
    assert "unknownNested" not in prepared
    assert raw["expectedSourceObjectId"] == "0" * 64


def test_unknown_nested_fields_do_not_enter_preview_or_canonical_apply() -> None:
    wrapper = _duplicate_wrapper()
    wrapper["arguments"]["secretField"] = "must-not-cross-the-tool-boundary"
    wrapper["arguments"]["unknownNested"] = {"value": 1}

    preview = build_preview_arguments(DUPLICATE_TOOL_NAME, wrapper["arguments"])
    canonical, _ = bind_authoritative_preview(wrapper, _duplicate_payload())

    assert "secretField" not in preview
    assert "unknownNested" not in preview
    assert "secretField" not in canonical["arguments"]
    assert "unknownNested" not in canonical["arguments"]


def test_flat_requests_are_normalized_into_exact_tool_wrappers() -> None:
    wrapper = _duplicate_wrapper()

    assert wrapper["toolName"] == DUPLICATE_TOOL_NAME
    assert wrapper["projectPath"] == "D:/DisposableUnityProject"
    assert wrapper["arguments"] == {
        "sourceScenePath": "Assets/Scenes/AccessoryCopy.unity",
        "sourceObjectPath": "AvatarA/Accessory",
        "targetParentScenePath": "Assets/Scenes/AccessoryCopy.unity",
        "targetParentPath": "AvatarB",
        "targetName": "AccessoryCopy",
        "preserveWorldTransform": False,
    }
    assert "sourceObjectPath" not in wrapper


def test_duplicate_preview_binds_every_expected_before_field() -> None:
    canonical, approval = bind_authoritative_preview(
        _duplicate_wrapper(),
        _duplicate_payload(),
    )
    arguments = canonical["arguments"]

    assert canonical["toolName"] == DUPLICATE_TOOL_NAME
    assert arguments["preview"] is False
    assert arguments["saveScene"] is True
    assert arguments["overwrite"] is False
    assert arguments["expectedProjectPath"] == "D:/DisposableUnityProject"
    assert arguments["expectedSourceSceneGuid"] == "a" * 32
    assert arguments["expectedSourceSceneHandle"] == 11
    assert arguments["expectedSourceObjectId"] == "b" * 64
    assert arguments["expectedSourceHierarchyDigest"] == "c" * 64
    assert arguments["expectedSourceSceneFileDigest"] == "d" * 64
    assert arguments["expectedSourceSceneFileIdentity"] == "9" * 64
    assert arguments["expectedSourceSceneMetaDigest"] == "8" * 64
    assert arguments["expectedSourceSceneMetaIdentity"] == "7" * 64
    assert arguments["expectedTargetSceneGuid"] == "a" * 32
    assert arguments["expectedTargetSceneHandle"] == 11
    assert arguments["expectedTargetParentObjectId"] == "e" * 64
    assert arguments["expectedTargetParentHierarchyDigest"] == "f" * 64
    assert arguments["expectedTargetSceneFileDigest"] == "d" * 64
    assert arguments["expectedTargetSceneFileIdentity"] == "9" * 64
    assert arguments["expectedTargetSceneMetaDigest"] == "8" * 64
    assert arguments["expectedTargetSceneMetaIdentity"] == "7" * 64
    assert arguments["expectedDestinationPath"] == "AvatarB/AccessoryCopy"
    assert arguments["expectedPreviewDigest"] == _duplicate_payload()["previewDigest"]
    assert approval == {
        "schema": APPROVAL_SCHEMA,
        "toolName": DUPLICATE_TOOL_NAME,
        "operation": "duplicate_scene_object",
        "source": _duplicate_payload()["source"],
        "target": _duplicate_payload()["target"],
        "preserveWorldTransform": False,
        "mutationCount": 1,
        "createNew": True,
        "rollbackRequired": True,
        "previewDigest": _duplicate_payload()["previewDigest"],
    }


def test_prefab_preview_binds_create_new_destination_and_source() -> None:
    canonical, approval = bind_authoritative_preview(
        _prefab_wrapper(),
        _prefab_payload(),
    )
    arguments = canonical["arguments"]

    assert canonical["toolName"] == PREFAB_TOOL_NAME
    assert arguments["preview"] is False
    assert arguments["saveAssets"] is True
    assert arguments["overwrite"] is False
    assert arguments["expectedProjectPath"] == "D:/DisposableUnityProject"
    assert arguments["expectedSourceObjectId"] == "b" * 64
    assert arguments["expectedSourceHierarchyDigest"] == "c" * 64
    assert arguments["expectedSourceSceneFileDigest"] == "d" * 64
    assert arguments["expectedSourceSceneFileIdentity"] == "9" * 64
    assert arguments["expectedSourceSceneMetaDigest"] == "8" * 64
    assert arguments["expectedSourceSceneMetaIdentity"] == "7" * 64
    assert arguments["expectedPrefabParentFolderGuid"] == "1" * 32
    assert arguments["expectedPrefabParentFolderIdentity"] == "2" * 64
    assert arguments["expectedStagingRootGuid"] == "3" * 32
    assert arguments["expectedStagingRootIdentity"] == "4" * 64
    assert arguments["expectedStagingPolicy"] == "random_create_new_folder_v1"
    assert arguments["expectedPreviewDigest"] == _prefab_payload()["previewDigest"]
    assert approval["schema"] == APPROVAL_SCHEMA
    assert approval["operation"] == "save_scene_object_as_prefab"
    assert approval["target"]["createNew"] is True
    assert approval["mutationCount"] == 1
    assert approval["rollbackRequired"] is True


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"changed": True}),
        lambda payload: payload.update({"saved": True}),
        lambda payload: payload.update({"mutationCount": 1}),
        lambda payload: payload.update({"verified": False}),
        lambda payload: payload["source"].update({"pathUnique": False}),
        lambda payload: payload["target"].update({"parentPathUnique": False}),
        lambda payload: payload["target"].update({"nameCollision": True}),
        lambda payload: payload["target"].update({"sameDestination": True}),
        lambda payload: payload["target"].update({"targetWithinSource": True}),
    ],
)
def test_duplicate_preview_fails_closed_on_write_or_ambiguous_relationship(mutator) -> None:
    payload = _duplicate_payload()
    mutator(payload)
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(SceneObjectCopyError):
        bind_authoritative_preview(_duplicate_wrapper(), payload)


@pytest.mark.parametrize(
    "change",
    [
        {"sourceObjectPath": "AvatarA/Other"},
        {"sourceScenePath": "Assets/Scenes/Other.unity"},
        {"targetParentPath": "AvatarC"},
        {"targetParentScenePath": "Assets/Scenes/Other.unity"},
        {"targetName": "OtherName"},
        {"preserveWorldTransform": True},
    ],
)
def test_duplicate_preview_cannot_substitute_requested_selector(change: dict) -> None:
    wrapper = _duplicate_wrapper()
    wrapper["arguments"].update(change)

    with pytest.raises(SceneObjectCopyError, match="requested"):
        bind_authoritative_preview(wrapper, _duplicate_payload())


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "Assets/Accessory.prefab",
        "Assets/VRCForge/Other/Accessory.prefab",
        "Assets/VRCForge/Generated.prefab",
        "Assets/VRCForge/Generated/../Accessory.prefab",
        "Assets\\VRCForge\\Generated\\Accessory.prefab",
        "/Assets/VRCForge/Generated/Accessory.prefab",
        "Packages/VRCForge/Generated/Accessory.prefab",
        "Assets/VRCForge/Generated/Accessory.asset",
    ],
)
def test_prefab_destination_is_restricted_to_generated_create_new_path(unsafe_path: str) -> None:
    wrapper = _prefab_wrapper()
    wrapper["arguments"]["prefabAssetPath"] = unsafe_path

    with pytest.raises(SceneObjectCopyError):
        bind_authoritative_preview(wrapper, _prefab_payload())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload["target"].update({"assetExists": True}),
        lambda payload: payload["target"].update({"metaExists": True}),
        lambda payload: payload["target"].update({"createNew": False}),
        lambda payload: payload["target"].update({"parentFolderGuid": "0" * 32}),
        lambda payload: payload["target"].update({"parentFolderIdentity": "0" * 64}),
        lambda payload: payload["target"].update({"stagingRootGuid": "0" * 32}),
        lambda payload: payload["target"].update({"stagingRootIdentity": "0" * 64}),
        lambda payload: payload["target"].update({"stagingPolicy": "deterministic_path"}),
    ],
)
def test_prefab_preview_rejects_existing_or_unverified_destination(mutator) -> None:
    payload = _prefab_payload()
    mutator(payload)
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(SceneObjectCopyError):
        bind_authoritative_preview(_prefab_wrapper(), payload)


def test_preview_digest_commits_all_security_relevant_fields() -> None:
    payload = _duplicate_payload()
    stale = payload["previewDigest"]
    payload["source"]["hierarchyDigest"] = "9" * 64

    assert compute_preview_digest(payload) != stale
    with pytest.raises(SceneObjectCopyError, match="digest"):
        bind_authoritative_preview(_duplicate_wrapper(), payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objectPath", "AvatarA/Accessory/.."),
        ("objectPath", "AvatarA//Accessory"),
        ("scenePath", "Assets/Scenes/../AccessoryCopy.unity"),
        ("scenePath", "Packages/AccessoryCopy.unity"),
        ("objectId", "f" * 63),
        ("hierarchyDigest", "not-a-digest"),
        ("sceneMetaIdentity", "0" * 64),
    ],
)
def test_source_identity_and_paths_are_strict(field: str, value: object) -> None:
    payload = _duplicate_payload()
    payload["source"][field] = value
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(SceneObjectCopyError):
        bind_authoritative_preview(_duplicate_wrapper(), payload)


def test_csharp_domain_declares_both_static_tools_and_hard_fail_closed_guards() -> None:
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "Assets/VRCForge/Editor/SceneObjectCopyCore.cs",
            "Assets/VRCForge/Editor/DuplicateSceneObjectTool.cs",
            "Assets/VRCForge/Editor/SaveSceneObjectAsPrefabTool.cs",
        )
    )

    required_fragments = (
        'name: "vrc_duplicate_scene_object"',
        'name: "vrc_save_scene_object_as_prefab"',
        "GlobalObjectId.GetGlobalObjectIdSlow",
        "ComputeHierarchyDigest",
        "expectedPreviewDigest",
        "targetParent.transform.IsChildOf(source.transform)",
        'Assets/VRCForge/Generated/',
        "PrefabUtility.SaveAsPrefabAsset",
        "AssetDatabase.MoveAsset",
            "AssetDatabase.DeleteAsset",
            "AssetDatabase.CreateFolder",
            "RandomNumberGenerator.Create",
            "EditorSceneManager.SaveScene",
            "FileAttributes.ReparsePoint",
            "GetFileInformationByHandle",
            "NumberOfLinks",
            "ReadStableAssetEvidence",
            "VerifyMovedAssetEvidence",
            "AssetPathToGUIDOptions.OnlyExistingAssets",
            "BuildMutationFailure",
            "cleanupVerified",
            "checkpointRestoreRequired",
            "checkpoint_restore_required",
            "whileHandlesHeldProbe",
        "preview = true",
        "mutationCount = 0",
        "overwrite",
    )
    for fragment in required_fragments:
        assert fragment in source

    assert "GameObject.Find(" not in source
    assert "GenerateUniqueAssetPath" not in source
    assert "ReserveStagingPath" not in source
    assert "FileMode.CreateNew" not in source


def test_disposable_fixture_covers_atomic_snapshot_and_structured_failures() -> None:
    source = Path(
        "tests/fixtures/primitive_basis/scene_object_copy/SceneObjectCopyFixtureProbe.cs"
    ).read_text(encoding="utf-8")

    for fragment in (
        "VerifyStructuredMutationFailureSignals",
        "cleanupVerified",
        "checkpointRestoreRequired",
        "checkpoint_restore_required",
        "pre-mutation failure claimed a mutation",
        "VerifyAtomicSnapshotWriteDenied",
        "snapshot allowed a prefab write handle",
        "snapshot allowed a metadata write handle",
        "FileAccess.Write",
        "FileShare.ReadWrite",
        "CreateHardLinkW",
        "staging metadata hardlink was accepted",
        "final metadata hardlink was accepted",
        "ReplaceAssetFilesWithExactBytes",
        "VRCFORGE_SCENE_OBJECT_COPY_PROBE_OK",
    ):
        assert fragment in source


def test_csharp_preview_branches_precede_mutation_calls() -> None:
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "Assets/VRCForge/Editor/DuplicateSceneObjectTool.cs",
            "Assets/VRCForge/Editor/SaveSceneObjectAsPrefabTool.cs",
        )
    )

    duplicate_preview = source.index("return SceneObjectCopyCore.Success(\n                    snapshot.ToPayload())")
    duplicate_mutation = source.index("UnityEngine.Object.Instantiate")
    prefab_preview = source.index(
        "return SceneObjectCopyCore.Success(\n                    snapshot.ToPayload())",
        duplicate_preview + 1,
    )
    prefab_mutation = source.index("PrefabUtility.SaveAsPrefabAsset")

    assert duplicate_preview < duplicate_mutation
    assert prefab_preview < prefab_mutation
