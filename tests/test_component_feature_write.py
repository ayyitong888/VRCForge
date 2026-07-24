from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from component_feature_write import (
    APPROVAL_PREVIEW_SCHEMA,
    COMPATIBILITY_DIGEST_SCHEMA,
    EXPECTED_COMPATIBILITY,
    FEATURE_DIGEST_SCHEMA,
    RESULT_SCHEMA,
    TOOL_NAME,
    ComponentFeatureWriteError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_compatibility_digest,
    compute_feature_digest,
    compute_preview_digest,
    normalize_request,
)


PROJECT_PATH = "D:/DisposableUnityProject"
SCENE_PATH = "Assets/VRCForge/Generated/ComponentFeatureProbe.unity"


def toggle_request() -> dict:
    return {
        "scenePath": SCENE_PATH,
        "gameObjectPath": "Avatar/FeatureHost",
        "featureKind": "toggle",
        "menuPath": "Wardrobe/Hat",
        "targetObjectPaths": ["Avatar/Hat", "Avatar/Hat/Charm"],
        "slider": False,
        "defaultOn": True,
        "saved": True,
        "globalParameter": "Wardrobe_Hat",
    }


def armature_request() -> dict:
    return {
        "scenePath": SCENE_PATH,
        "gameObjectPath": "Avatar/ArmatureFeatureHost",
        "featureKind": "armature_link",
        "linkFromPath": "Avatar/PropRoot",
        "linkTargets": [
            {"targetKind": "humanoid_bone", "target": "Chest", "offset": "SpineOffset"},
            {"targetKind": "game_object", "target": "Avatar/ChestTarget", "offset": "Socket"},
            {"targetKind": "relative_path", "target": "Armature/Hips/Spine", "offset": ""},
        ],
        "recursive": True,
        "align": False,
    }


def toggle_target() -> dict:
    return {
        "present": True,
        "featureKind": "toggle",
        "menuPath": "Wardrobe/Hat",
        "slider": False,
        "defaultOn": True,
        "saved": True,
        "globalParameter": "Wardrobe_Hat",
        "targets": [
            {"objectPath": "Avatar/Hat", "objectId": "GlobalObjectId_V1-2-100-0-0"},
            {"objectPath": "Avatar/Hat/Charm", "objectId": "GlobalObjectId_V1-2-101-0-0"},
        ],
    }


def armature_target() -> dict:
    return {
        "present": True,
        "featureKind": "armature_link",
        "linkFrom": {
            "objectPath": "Avatar/PropRoot",
            "objectId": "GlobalObjectId_V1-2-200-0-0",
        },
        "links": [
            {
                "targetKind": "humanoid_bone",
                "target": "Chest",
                "objectId": "",
                "offset": "SpineOffset",
            },
            {
                "targetKind": "game_object",
                "target": "Avatar/ChestTarget",
                "objectId": "GlobalObjectId_V1-2-201-0-0",
                "offset": "Socket",
            },
            {
                "targetKind": "relative_path",
                "target": "Armature/Hips/Spine",
                "objectId": "",
                "offset": "",
            },
        ],
        "recursive": True,
        "align": False,
    }


def wrapper(request: dict) -> dict:
    return {
        "projectPath": PROJECT_PATH,
        "toolName": TOOL_NAME,
        "arguments": deepcopy(request),
    }


def compatibility_evidence() -> dict:
    compatibility = deepcopy(EXPECTED_COMPATIBILITY)
    compatibility.update(
        {
            "apiAssemblyDigest": "2a495c2aa4bc90ddbf30318fec175486326cb11ce8722076817689991e74243f",
            "runtimeAssemblyDigest": "e9bd68b6f84770f44687d45e172ad2884cf6e432a66c48e99f855f67812e6d3a",
        }
    )
    return compatibility


def preview_payload(request: dict) -> dict:
    feature_kind = request["featureKind"]
    before = {"present": False, "featureKind": feature_kind}
    target = toggle_target() if feature_kind == "toggle" else armature_target()
    compatibility = compatibility_evidence()
    payload = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "projectPath": PROJECT_PATH,
        "compatibility": compatibility,
        "compatibilityDigestSchema": COMPATIBILITY_DIGEST_SCHEMA,
        "compatibilityDigest": compute_compatibility_digest(compatibility),
        "scene": {
            "path": SCENE_PATH,
            "guid": "1" * 32,
            "handle": -1322,
            "fileDigestBefore": "2" * 64,
            "fileDigestAfter": "2" * 64,
            "fileIdentity": "3" * 64,
            "metaDigestBefore": "4" * 64,
            "metaDigestAfter": "4" * 64,
            "metaIdentity": "5" * 64,
            "dirtyBefore": False,
            "dirtyAfter": False,
        },
        "host": {
            "objectPath": request["gameObjectPath"],
            "objectId": "GlobalObjectId_V1-2-300-0-0",
            "componentType": "VF.Model.VRCFury",
            "componentIndex": 0,
            "componentIdentitySeed": "6" * 64,
            "existingFeatureCount": 0,
        },
        "before": before,
        "target": target,
        "featureDigestSchema": FEATURE_DIGEST_SCHEMA,
        "beforeFeatureDigest": compute_feature_digest(before),
        "targetFeatureDigest": compute_feature_digest(target),
        "wouldChange": True,
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


@pytest.mark.parametrize("arguments", [toggle_request(), armature_request()])
def test_normalize_request_accepts_only_fixed_product_schema(arguments: dict) -> None:
    assert normalize_request(arguments) == arguments


def test_text_bounds_match_editor_utf16_length() -> None:
    accepted = toggle_request()
    accepted["menuPath"] = "\U0001f600" * 1024
    assert normalize_request(accepted)["menuPath"] == accepted["menuPath"]

    rejected = toggle_request()
    rejected["menuPath"] = "\U0001f600" * 1025
    with pytest.raises(ComponentFeatureWriteError):
        normalize_request(rejected)


@pytest.mark.parametrize("arguments", [toggle_request(), armature_request()])
def test_preview_forces_zero_write_and_discards_caller_preconditions(arguments: dict) -> None:
    arguments = deepcopy(arguments)
    arguments.update(
        {
            "preview": False,
            "saveScene": True,
            "expectedBeforeFeatureDigest": "0" * 64,
            "expectedComponentType": "arbitrary.type",
        }
    )

    preview = build_preview_arguments(arguments)

    assert preview["preview"] is True
    assert preview["saveScene"] is False
    assert not any(key.startswith("expected") for key in preview)


@pytest.mark.parametrize(
    ("request_factory", "field", "value"),
    [
        (toggle_request, "componentType", "arbitrary.type"),
        (toggle_request, "methodName", "CreateAnything"),
        (toggle_request, "propertyName", "content"),
        (armature_request, "menuPath", "Wrong/Feature"),
        (armature_request, "assemblyName", "unknown"),
    ],
)
def test_unknown_or_cross_kind_fields_fail_closed(request_factory, field: str, value: object) -> None:
    request = request_factory()
    request[field] = value

    with pytest.raises(ComponentFeatureWriteError, match="unsupported fields"):
        normalize_request(request)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"targetObjectPaths": []}),
        lambda value: value.update({"targetObjectPaths": ["Avatar/Hat", "Avatar/Hat"]}),
        lambda value: value.update({"slider": 1}),
        lambda value: value.update({"menuPath": "Wardrobe/../Hat"}),
        lambda value: value.update({"globalParameter": "unsafe parameter"}),
    ],
)
def test_toggle_schema_rejects_ambiguous_or_unbounded_values(mutator) -> None:
    request = toggle_request()
    mutator(request)

    with pytest.raises(ComponentFeatureWriteError):
        normalize_request(request)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["linkTargets"][0].update({"targetKind": "type_name"}),
        lambda value: value["linkTargets"][0].update({"target": "LastBone"}),
        lambda value: value["linkTargets"][2].update({"offset": "Second/Path"}),
        lambda value: value["linkTargets"][0].update({"extra": "reflection"}),
        lambda value: value.update({"linkTargets": value["linkTargets"] * 3}),
    ],
)
def test_armature_schema_rejects_unknown_modes_layouts_and_bounds(mutator) -> None:
    request = armature_request()
    mutator(request)

    with pytest.raises(ComponentFeatureWriteError):
        normalize_request(request)


@pytest.mark.parametrize("arguments", [toggle_request(), armature_request()])
def test_authoritative_preview_binds_scene_component_and_compatibility(arguments: dict) -> None:
    payload = preview_payload(arguments)

    canonical, approval = bind_authoritative_preview(wrapper(arguments), payload)

    arguments = canonical["arguments"]
    assert arguments["preview"] is False
    assert arguments["saveScene"] is True
    assert arguments["expectedSceneGuid"] == "1" * 32
    assert arguments["expectedSceneHandle"] == -1322
    assert arguments["expectedSceneFileDigest"] == "2" * 64
    assert arguments["expectedSceneMetaDigest"] == "4" * 64
    assert arguments["expectedHostObjectId"] == "GlobalObjectId_V1-2-300-0-0"
    assert arguments["expectedComponentType"] == "VF.Model.VRCFury"
    assert arguments["expectedComponentIndex"] == 0
    assert arguments["expectedBeforeFeatureDigest"] == payload["beforeFeatureDigest"]
    assert arguments["expectedTargetFeatureDigest"] == payload["targetFeatureDigest"]
    assert arguments["expectedCompatibilityDigest"] == payload["compatibilityDigest"]
    assert arguments["expectedPreviewDigest"] == payload["previewDigest"]
    assert approval["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert approval["change"]["createNew"] is True
    assert approval["mutationCount"] == 1
    assert approval["rollbackRequired"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("packageVersion", "1.1335.0"),
        ("packageTreeDigest", "0" * 64),
        ("apiAssemblySignatureState", "signed"),
        ("apiSignatureDigest", "0" * 64),
    ],
)
def test_package_assembly_or_method_signature_drift_fails_closed(key: str, value: object) -> None:
    request = toggle_request()
    payload = preview_payload(request)
    payload["compatibility"][key] = value
    payload["compatibilityDigest"] = "0" * 64
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ComponentFeatureWriteError, match="unsupported"):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize("key", ["apiAssemblyDigest", "runtimeAssemblyDigest"])
def test_assembly_digest_is_exact_and_committed_by_compatibility_digest(key: str) -> None:
    request = toggle_request()
    payload = preview_payload(request)
    payload["compatibility"][key] = "f" * 64
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ComponentFeatureWriteError, match="compatibility digest"):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize("value", ["", "not-a-digest", "g" * 64])
def test_assembly_digest_rejects_missing_or_malformed_values(value: str) -> None:
    request = toggle_request()
    payload = preview_payload(request)
    payload["compatibility"]["apiAssemblyDigest"] = value
    payload["compatibilityDigest"] = "0" * 64
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ComponentFeatureWriteError):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"changed": True}),
        lambda payload: payload.update({"saved": True}),
        lambda payload: payload.update({"mutationCount": 1}),
        lambda payload: payload["scene"].update({"fileDigestAfter": "9" * 64}),
        lambda payload: payload["scene"].update({"dirtyAfter": True}),
        lambda payload: payload["host"].update({"existingFeatureCount": 1}),
        lambda payload: payload["host"].update({"componentType": "arbitrary.type"}),
        lambda payload: payload.update({"wouldChange": False}),
    ],
)
def test_preview_write_duplicate_or_substitution_fails_closed(mutator) -> None:
    request = toggle_request()
    payload = preview_payload(request)
    mutator(payload)
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ComponentFeatureWriteError):
        bind_authoritative_preview(wrapper(request), payload)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["scene"].update({"handle": 0}),
        lambda payload: payload["host"].update({"objectId": "not-a-global-object-id"}),
        lambda payload: payload["target"]["targets"][0].update(
            {"objectId": "not-a-global-object-id"}
        ),
    ],
)
def test_preview_shape_and_stable_object_id_contract_fail_closed(mutator) -> None:
    request = toggle_request()
    payload = preview_payload(request)
    mutator(payload)
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ComponentFeatureWriteError):
        bind_authoritative_preview(wrapper(request), payload)


def test_preview_digest_commits_resolved_object_order_and_expected_before() -> None:
    request = toggle_request()
    payload = preview_payload(request)
    original = payload["previewDigest"]
    payload["target"]["targets"].reverse()

    assert compute_preview_digest(payload) != original
    with pytest.raises(ComponentFeatureWriteError):
        bind_authoritative_preview(wrapper(request), payload)


def test_wrapper_does_not_expose_reflection_controls() -> None:
    raw = toggle_request()
    raw["projectPath"] = PROJECT_PATH

    built = build_wrapper_arguments(raw)

    assert built["toolName"] == TOOL_NAME
    assert built["projectPath"] == PROJECT_PATH
    assert built["arguments"] == toggle_request()
    assert not any(
        key in built["arguments"]
        for key in ("typeName", "methodName", "propertyName", "assemblyName")
    )


def test_csharp_domain_is_fixed_schema_public_api_only() -> None:
    paths = (
        "Assets/VRCForge/Editor/ComponentFeatureWriteCore.cs",
        "Assets/VRCForge/Editor/ComponentFeatureWriterTool.cs",
    )
    if not all(Path(path).exists() for path in paths):
        pytest.skip("C# component feature domain is not written yet")
    source = "\n".join(Path(path).read_text(encoding="utf-8") for path in paths)
    for fragment in (
        'name: "vrc_create_component_feature"',
        '"CreateToggle"',
        '"CreateArmatureLink"',
        "BindingFlags.Public",
        "TypedStructuredListCore.ReadManagedReference",
        "StructuredManagedReferenceSchema",
        "expectedBeforeFeatureDigest",
        "expectedTargetFeatureDigest",
        "expectedCompatibilityDigest",
        "expectedPreviewDigest",
        'allowed.Add("expectedProjectPath")',
        "Undo.RegisterCreatedObjectUndo",
        "SceneObjectCopyCore.ReadStableAssetEvidence",
        "SceneEvidenceMatches(snapshot.Scene, immediate)",
        "EditorSceneManager.SaveScene",
        "CreateNew",
        "checkpointRestoreRequired",
    ):
        assert fragment in source
    for forbidden in (
        "GetMethod(methodName",
        "GetType(typeName",
        "SetValue(component",
        "ArmatureLinkService",
        "methodName = args",
        "propertyName = args",
    ):
        assert forbidden not in source


def test_disposable_fixture_declares_all_negative_and_restore_controls() -> None:
    path = Path(
        "tests/fixtures/primitive_basis/component_feature_write/ComponentFeatureFixtureProbe.cs"
    )
    if not path.exists():
        pytest.skip("Disposable component feature fixture is not written yet")
    source = path.read_text(encoding="utf-8")
    for fragment in (
        "VerifyMissingPackageFailsClosed",
        "VerifyTamperedPackageFailsClosed",
        "VerifyMethodSignatureDriftFailsClosed",
        "VerifyDuplicateFeatureFailsClosed",
        "VerifyStaleExpectedBeforeFailsClosed",
        "VerifyUnknownFieldFailsClosed",
        "VerifyPreviewZeroWrite",
        "VerifyPartialFailureRestore",
        "RunToggleLifecycle",
        "RunArmatureLinkLifecycle",
        "Undo.PerformUndo",
        "VRCFORGE_COMPONENT_FEATURE_PROBE_OK",
    ):
        assert fragment in source
