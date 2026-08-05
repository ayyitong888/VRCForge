from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from constraint_source_write import (
    APPROVAL_PREVIEW_SCHEMA,
    RESULT_SCHEMA,
    SOURCES_DIGEST_SCHEMA,
    TOOL_NAME,
    ConstraintSourceWriteError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_component_id,
    compute_sources_digest,
    normalize_request,
)


PROJECT_PATH = "D:/DisposableUnityProject"
SCENE_PATH = "Assets/VRCForge/Generated/ConstraintProbe.unity"
SCENE_GUID = "1" * 32
SCENE_DIGEST = "2" * 64
SCENE_IDENTITY = "3" * 64
META_DIGEST = "4" * 64
META_IDENTITY = "5" * 64
COMPONENT_GLOBAL_ID = "GlobalObjectId_V1-2-123-456-0"
COMPONENT_TYPE = "VRC.SDK3.Dynamics.Constraint.Components.VRCPositionConstraint"
COMPONENT_ID = compute_component_id(
    scene_guid=SCENE_GUID,
    component_global_id=COMPONENT_GLOBAL_ID,
    game_object_path="Avatar/ConstraintHost",
    component_type=COMPONENT_TYPE,
    component_index=0,
)


def request_arguments() -> dict:
    return {
        "scenePath": SCENE_PATH,
        "gameObjectPath": "Avatar/ConstraintHost",
        "constraintKind": "position",
        "componentIndex": 0,
        "sources": [
            {"sourcePath": "Avatar/SourceA", "weight": 0.25},
            {"sourcePath": "Avatar/SourceB", "weight": 0.75},
        ],
    }


def wrapper_arguments() -> dict:
    return {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "arguments": request_arguments(),
    }


def source(path: str, object_id: str, weight: float, bits: str) -> dict:
    return {
        "sourcePath": path,
        "sourceObjectId": object_id,
        "weight": weight,
        "weightBits": bits,
    }


def preview_payload(*, would_change: bool = True) -> dict:
    before = [] if would_change else [
        source("Avatar/SourceA", "GlobalObjectId_V1-2-201-1-0", 0.25, "3e800000"),
        source("Avatar/SourceB", "GlobalObjectId_V1-2-202-1-0", 0.75, "3f400000"),
    ]
    target = [
        source("Avatar/SourceA", "GlobalObjectId_V1-2-201-1-0", 0.25, "3e800000"),
        source("Avatar/SourceB", "GlobalObjectId_V1-2-202-1-0", 0.75, "3f400000"),
    ]
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "wouldChange": would_change,
        "projectPath": PROJECT_PATH,
        "scenePath": SCENE_PATH,
        "sceneGuid": SCENE_GUID,
        "sceneHandle": -17,
        "sceneFileDigestBefore": SCENE_DIGEST,
        "sceneFileDigestAfter": SCENE_DIGEST,
        "sceneFileIdentity": SCENE_IDENTITY,
        "sceneFileLinkCount": 1,
        "sceneMetaDigestBefore": META_DIGEST,
        "sceneMetaDigestAfter": META_DIGEST,
        "sceneMetaIdentity": META_IDENTITY,
        "sceneMetaLinkCount": 1,
        "sceneDirtyBefore": False,
        "sceneDirtyAfter": False,
        "gameObjectPath": "Avatar/ConstraintHost",
        "constraintKind": "position",
        "componentType": COMPONENT_TYPE,
        "componentIndex": 0,
        "componentId": COMPONENT_ID,
        "componentGlobalId": COMPONENT_GLOBAL_ID,
        "beforeSources": before,
        "targetSources": target,
        "beforeSourcesDigest": compute_sources_digest(before),
        "targetSourcesDigest": compute_sources_digest(target),
        "sourcesDigestSchema": SOURCES_DIGEST_SCHEMA,
    }


def test_wrapper_and_preview_are_canonical_and_strip_all_caller_expected_fields() -> None:
    flat = {"projectPath": PROJECT_PATH, **request_arguments(), "expectedInjected": "bad"}
    wrapped = build_wrapper_arguments(flat)
    wrapped["arguments"]["expectedInjected"] = "bad"

    preview = build_preview_arguments(wrapped["arguments"])

    assert wrapped["toolName"] == TOOL_NAME
    assert wrapped["arguments"]["scenePath"] == SCENE_PATH
    assert preview["preview"] is True
    assert preview["saveScene"] is False
    assert all(not key.startswith("expected") for key in preview)


@pytest.mark.parametrize("kind", ["position", "rotation", "scale", "parent", "aim", "look_at"])
def test_normalize_accepts_only_registered_constraint_kinds(kind: str) -> None:
    arguments = request_arguments()
    arguments["constraintKind"] = kind

    normalized = normalize_request(arguments)

    assert normalized["constraintKind"] == kind
    assert normalized["sources"][0]["weightBits"] == "3e800000"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(constraintKind="unknown"),
        lambda value: value.update(scenePath="Assets/not-a-scene.prefab"),
        lambda value: value.update(scenePath="../outside.unity"),
        lambda value: value.update(gameObjectPath="Avatar//Host"),
        lambda value: value.update(componentIndex=-1),
        lambda value: value.update(componentIndex=True),
        lambda value: value.update(sources=None),
        lambda value: value.update(sources=[{"sourcePath": None, "weight": 0.5}]),
        lambda value: value.update(sources=[{"sourcePath": "Avatar/A", "weight": -0.01}]),
        lambda value: value.update(sources=[{"sourcePath": "Avatar/A", "weight": 1.01}]),
        lambda value: value.update(sources=[{"sourcePath": "Avatar/A", "weight": True}]),
        lambda value: value.update(sources=[{"sourcePath": "Avatar/A", "weight": float("nan")}]),
        lambda value: value.update(sources=[{"sourcePath": "Avatar/A", "weight": 0.5, "field": "x"}]),
        lambda value: value.update(
            sources=[
                {"sourcePath": "Avatar/A", "weight": 0.5},
                {"sourcePath": "Avatar/A", "weight": 0.25},
            ]
        ),
    ],
)
def test_normalize_rejects_unsafe_or_untyped_requests(mutator) -> None:
    arguments = request_arguments()
    mutator(arguments)

    with pytest.raises(ConstraintSourceWriteError):
        normalize_request(arguments)


def test_authoritative_preview_binds_every_identity_and_preserves_list_order() -> None:
    canonical, approval = bind_authoritative_preview(wrapper_arguments(), preview_payload())

    arguments = canonical["arguments"]
    assert arguments["expectedProjectPath"] == PROJECT_PATH.replace("/", "\\")
    assert arguments["expectedSceneGuid"] == SCENE_GUID
    assert arguments["expectedSceneFileIdentity"] == SCENE_IDENTITY
    assert arguments["expectedSceneMetaIdentity"] == META_IDENTITY
    assert arguments["expectedComponentId"] == COMPONENT_ID
    assert arguments["expectedComponentGlobalId"] == COMPONENT_GLOBAL_ID
    assert arguments["expectedBeforeSourcesDigest"] == compute_sources_digest([])
    assert arguments["expectedTargetSourcesDigest"] == compute_sources_digest(preview_payload()["targetSources"])
    assert [item["sourcePath"] for item in arguments["sources"]] == ["Avatar/SourceA", "Avatar/SourceB"]
    assert arguments["preview"] is False
    assert arguments["saveScene"] is True
    assert approval["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert approval["rollbackRequired"] is True
    assert approval["change"]["wouldChange"] is True


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(schema="wrong"),
        lambda value: value.update(ok=False),
        lambda value: value.update(preview=False),
        lambda value: value.update(verified=False),
        lambda value: value.update(changed=True),
        lambda value: value.update(saved=True),
        lambda value: value.update(sceneDirtyAfter=True),
        lambda value: value.update(sceneFileDigestAfter="a" * 64),
        lambda value: value.update(sceneMetaDigestAfter="a" * 64),
        lambda value: value.update(sceneFileLinkCount=2),
        lambda value: value.update(sceneMetaLinkCount=2),
        lambda value: value.update(projectPath="D:/OtherProject"),
        lambda value: value.update(scenePath="Assets/Other.unity"),
        lambda value: value.update(gameObjectPath="Avatar/Other"),
        lambda value: value.update(constraintKind="rotation"),
        lambda value: value.update(componentType="UnityEngine.Transform"),
        lambda value: value.update(componentIndex=1),
        lambda value: value.update(componentId="a" * 64),
        lambda value: value.update(sourcesDigestSchema="wrong"),
        lambda value: value.update(beforeSourcesDigest="a" * 64),
        lambda value: value.update(targetSourcesDigest="a" * 64),
        lambda value: value["targetSources"].reverse(),
        lambda value: value["targetSources"][0].update(weightBits="3f000000"),
        lambda value: value["targetSources"][1].update(
            sourceObjectId=value["targetSources"][0]["sourceObjectId"]
        ),
    ],
)
def test_authoritative_preview_rejects_tamper_or_ambiguous_identity(mutator) -> None:
    payload = preview_payload()
    mutator(payload)

    with pytest.raises(ConstraintSourceWriteError):
        bind_authoritative_preview(wrapper_arguments(), payload)


def test_noop_preview_is_valid_but_still_uses_the_supervised_apply_contract() -> None:
    canonical, approval = bind_authoritative_preview(
        wrapper_arguments(),
        preview_payload(would_change=False),
    )

    assert canonical["arguments"]["preview"] is False
    assert canonical["arguments"]["saveScene"] is True
    assert approval["change"]["wouldChange"] is False


def test_csharp_core_exposes_only_registered_typed_schemas() -> None:
    core = Path("Assets/VRCForge/Editor/TypedStructuredListCore.cs").read_text(encoding="utf-8-sig")
    tool = Path("Assets/VRCForge/Editor/ConstraintSourceTool.cs").read_text(encoding="utf-8-sig")

    assert "internal static class TypedStructuredListCore" in core
    assert "StructuredListSchema" in core
    assert "StructuredValueKind.ObjectReference" in tool
    assert "StructuredValueKind.BoundedSingle" in tool
    assert "ManagedElementFactory" in core
    assert "CollectionFactory" in core
    assert "ValidateSerializedShape" in core
    assert 'toolId: "vrc_set_constraint_sources"' in tool
    assert '@params["propertyPath"]' not in tool
    assert '@params["memberName"]' not in tool
    assert "expectedSceneFileIdentity" in tool
    assert "expectedSceneMetaIdentity" in tool
    assert "restored.SceneFileLinkCount == snapshot.SceneFileLinkCount" in tool
    assert "restored.SceneMetaLinkCount == snapshot.SceneMetaLinkCount" in tool
    assert "checkpointRestoreRequired" in tool
    assert "TryRestoreBeforeSources" in tool


def test_managed_reference_reader_is_registered_object_only_and_nested_collection_aware() -> None:
    core = Path("Assets/VRCForge/Editor/TypedStructuredListCore.cs").read_text(
        encoding="utf-8-sig"
    )

    assert "StructuredManagedReferenceSchema" in core
    assert "RegisterManagedReferenceSchema" in core
    assert "BuildManagedReferenceReadPlan(\n            Component component,\n            StructuredManagedReferenceSchema schema)" in core
    assert "ReadManagedReference(\n            Component component,\n            StructuredManagedReferenceSchema schema)" in core
    assert "ReferenceEquals(schema, registered)" in core
    assert "ManagedSchemaFingerprints" in core
    assert "ComputeManagedSchemaFingerprint(schema) != registeredFingerprint" in core
    assert "SerializedPropertyType.ManagedReference" in core
    assert "managedReferenceFullTypename" in core
    assert "StructuredManagedCollectionKind.TypedList" in core
    assert "StructuredManagedCollectionKind.ManagedReferenceList" in core
    assert "CanonicalDigest" in core
    assert "ToCanonicalJObject" in core
    assert "BuildManagedReferenceReadPlan(Component component, string" not in core
    assert "ReadManagedReference(Component component, string" not in core


def test_disposable_unity_fixture_covers_the_full_constraint_lifecycle() -> None:
    fixture = Path(
        "tests/fixtures/primitive_basis/constraint_source_write/ConstraintSourceFixtureProbe.cs"
    )
    assert fixture.is_file()
    source_text = fixture.read_text(encoding="utf-8-sig")
    for stage in (
        "preview zero write",
        "ambiguous source accepted",
        "unsupported kind accepted",
        "unknown source field accepted",
        "null source accepted",
        "weight bounds accepted",
        "stale precondition accepted",
        "apply list order",
        "no-op saved",
        "restore baseline",
        "checkpointRestoreRequired",
        "VRCFORGE_CONSTRAINT_SOURCE_PROBE_OK",
        "EditorApplication.Exit(0)",
        "EditorApplication.Exit(1)",
    ):
        assert stage in source_text
