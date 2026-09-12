from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import agent_gateway
import dashboard_server
from unity_mcp_tool_contract import EXPECTED_TOOL_COUNT

from project_asset_copy import (
    ANCHOR_ROOT,
    APPROVAL_SCHEMA,
    GENERATED_ROOT,
    OPERATION,
    RESULT_SCHEMA,
    TOOL_NAME,
    ProjectAssetCopyError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_preview_digest,
    validate_apply_result,
)


def preview_payload(*, generated_root_exists: bool = True) -> dict:
    payload = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "operation": OPERATION,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "source": {
            "assetPath": "Assets/VRCFaceTracking/SapphySetup/Sapphy_FT.controller",
            "guid": "1" * 32,
            "fileDigest": "2" * 64,
            "fileIdentity": "3" * 64,
            "metaDigest": "4" * 64,
            "metaIdentity": "5" * 64,
            "mainAssetType": "UnityEditor.Animations.AnimatorController",
            "objectLayoutDigest": "0" * 64,
            "unchanged": True,
        },
        "target": {
            "assetPath": f"{GENERATED_ROOT}/FinalAvatar_FT.controller",
            "generatedRootPath": GENERATED_ROOT,
            "generatedRootExists": generated_root_exists,
            "generatedRootGuid": "6" * 32 if generated_root_exists else "",
            "generatedRootIdentity": "7" * 64 if generated_root_exists else "",
            "anchorFolderPath": ANCHOR_ROOT,
            "anchorFolderGuid": "8" * 32,
            "anchorFolderIdentity": "9" * 64,
            "parentFolderPath": GENERATED_ROOT,
            "parentFolderGuid": "6" * 32 if generated_root_exists else "",
            "parentFolderIdentity": "7" * 64 if generated_root_exists else "",
            "assetExists": False,
            "metaExists": False,
            "createNew": True,
        },
        "cleanupRequired": False,
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def wrapper() -> dict:
    return build_wrapper_arguments(
        {
            "projectPath": "D:/DisposableUnityProject",
            "sourceAssetPath": "Assets/VRCFaceTracking/SapphySetup/Sapphy_FT.controller",
            "destinationAssetPath": f"{GENERATED_ROOT}/FinalAvatar_FT.controller",
        }
    )


def apply_payload(canonical: dict) -> dict:
    args = canonical["arguments"]
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "operation": OPERATION,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "mutationCount": 1,
        "source": {
            "assetPath": args["sourceAssetPath"],
            "guid": args["expectedSourceGuid"],
            "fileDigest": args["expectedSourceFileDigest"],
            "fileIdentity": args["expectedSourceFileIdentity"],
            "metaDigest": args["expectedSourceMetaDigest"],
            "metaIdentity": args["expectedSourceMetaIdentity"],
            "mainAssetType": args["expectedSourceMainAssetType"],
            "objectLayoutDigest": args["expectedSourceObjectLayoutDigest"],
            "unchanged": True,
        },
        "target": {
            "assetPath": args["destinationAssetPath"],
            "guid": "a" * 32,
            "fileDigest": args["expectedSourceFileDigest"],
            "fileIdentity": "b" * 64,
            "metaDigest": "c" * 64,
            "metaIdentity": "d" * 64,
            "mainAssetType": args["expectedSourceMainAssetType"],
            "objectLayoutDigest": args["expectedSourceObjectLayoutDigest"],
            "bytesIdenticalToSource": False,
            "generatedRootPath": GENERATED_ROOT,
            "generatedRootCreated": False,
            "createNew": True,
            "readbackVerified": True,
        },
        "previewDigest": args["expectedPreviewDigest"],
        "cleanupRequired": False,
    }


def test_preview_discards_caller_preconditions_and_forces_create_new() -> None:
    preview = build_preview_arguments(
        {
            "sourceAssetPath": "Assets/A.controller",
            "destinationAssetPath": f"{GENERATED_ROOT}/B.controller",
            "overwrite": True,
            "expectedSourceGuid": "f" * 32,
            "secret": "must-not-cross",
        }
    )

    assert preview == {
        "sourceAssetPath": "Assets/A.controller",
        "destinationAssetPath": f"{GENERATED_ROOT}/B.controller",
        "preview": True,
        "overwrite": False,
    }


def test_preview_binds_exact_source_destination_and_absence_evidence() -> None:
    payload = preview_payload()
    canonical, approval = bind_authoritative_preview(wrapper(), payload)
    args = canonical["arguments"]

    assert canonical["toolName"] == TOOL_NAME
    assert args["preview"] is False
    assert args["overwrite"] is False
    assert args["expectedDestinationAbsent"] is True
    assert args["expectedSourceGuid"] == "1" * 32
    assert args["expectedSourceFileDigest"] == "2" * 64
    assert args["expectedSourceObjectLayoutDigest"] == "0" * 64
    assert args["expectedGeneratedRootExists"] is True
    assert args["expectedGeneratedRootGuid"] == "6" * 32
    assert approval["schema"] == APPROVAL_SCHEMA
    assert approval["mutationCount"] == 1
    assert approval["createNew"] is True
    assert approval["overwrite"] is False


@pytest.mark.parametrize(
    "source_path",
    [
        "Assets/Avatar/Face.mat",
        f"{GENERATED_ROOT}/FinalAvatar_Face_SkinQuality_M3.mat",
        f"{GENERATED_ROOT}/FinalAvatar/Materials/FinalAvatar_Face_SkinQuality_M3.mat",
        "Assets/VRCForge/Generated/FinalAvatar_Face_SkinQuality_M3.mat",
    ],
)
def test_material_copy_preserves_create_new_identity_and_rollback(source_path: str) -> None:
    destination = f"{GENERATED_ROOT}/FinalAvatar_Face_SkinQuality_M4.mat"
    request = build_wrapper_arguments(
        {
            "projectPath": "D:/DisposableUnityProject",
            "sourceAssetPath": source_path,
            "destinationAssetPath": destination,
        }
    )
    payload = preview_payload()
    payload["source"]["assetPath"] = source_path
    payload["source"]["mainAssetType"] = "UnityEngine.Material"
    payload["target"]["assetPath"] = destination
    payload["previewDigest"] = compute_preview_digest(payload)

    canonical, approval = bind_authoritative_preview(request, payload)
    result = apply_payload(canonical)

    assert canonical["arguments"]["sourceAssetPath"] == source_path
    assert canonical["arguments"]["destinationAssetPath"] == destination
    assert canonical["arguments"]["expectedSourceMainAssetType"] == "UnityEngine.Material"
    assert canonical["arguments"]["expectedDestinationAbsent"] is True
    assert approval["rollbackRequired"] is True
    assert approval["createNew"] is True
    assert approval["overwrite"] is False
    assert validate_apply_result(canonical["arguments"], result) == result
    assert result["target"]["guid"] != result["source"]["guid"]


def test_absent_generated_root_is_bound_as_second_create_new_mutation() -> None:
    canonical, approval = bind_authoritative_preview(wrapper(), preview_payload(generated_root_exists=False))

    assert canonical["arguments"]["expectedGeneratedRootExists"] is False
    assert canonical["arguments"]["expectedGeneratedRootGuid"] == ""
    assert approval["mutationCount"] == 2


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        ("Assets/A.cs", f"{GENERATED_ROOT}/A.cs"),
        ("Assets/A.controller", "Assets/Elsewhere/A.controller"),
        ("Assets/A.asset", f"{GENERATED_ROOT}/A.controller"),
        (f"{GENERATED_ROOT}/A.asset", f"{GENERATED_ROOT}/B.asset"),
        (f"{GENERATED_ROOT}/Nested/A.mat", f"{GENERATED_ROOT}/B.mat"),
        (f"{GENERATED_ROOT}/A.mat", f"{GENERATED_ROOT}/Nested/B.mat"),
    ],
)
def test_unsupported_or_unsafe_copy_requests_fail_closed(source: str, destination: str) -> None:
    request = wrapper()
    request["arguments"]["sourceAssetPath"] = source
    request["arguments"]["destinationAssetPath"] = destination

    with pytest.raises(ProjectAssetCopyError):
        bind_authoritative_preview(request, preview_payload())


def test_tampered_preview_digest_fails_closed() -> None:
    payload = preview_payload()
    payload["source"]["fileDigest"] = "e" * 64

    with pytest.raises(ProjectAssetCopyError):
        bind_authoritative_preview(wrapper(), payload)


def test_apply_receipt_requires_new_guid_verified_layout_and_source_unchanged() -> None:
    canonical, _approval = bind_authoritative_preview(wrapper(), preview_payload())
    result = apply_payload(canonical)

    validated = validate_apply_result(canonical["arguments"], result)

    assert validated == result


@pytest.mark.parametrize("mutation", ["same_guid", "changed_layout", "source_changed"])
def test_invalid_apply_receipts_fail_closed(mutation: str) -> None:
    canonical, _approval = bind_authoritative_preview(wrapper(), preview_payload())
    result = apply_payload(canonical)
    if mutation == "same_guid":
        result["target"]["guid"] = result["source"]["guid"]
    elif mutation == "changed_layout":
        result["target"]["objectLayoutDigest"] = "e" * 64
    else:
        result["source"]["unchanged"] = False

    with pytest.raises(ProjectAssetCopyError):
        validate_apply_result(canonical["arguments"], result)


def test_core_and_external_registry_include_only_the_atomic_copy_surface() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = (root / "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs").read_text(encoding="utf-8")
    server = (root / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
    dashboard = (root / "dashboard_server.py").read_text(encoding="utf-8")
    # Verify actual block membership after its definitions move to a leaf owner.
    gateway = json.dumps({
        block: sorted(names)
        for block, names in agent_gateway.EXTERNAL_MCP_READ_TOOL_BLOCKS.items()
    })
    tool = (root / "Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs").read_text(encoding="utf-8")

    assert f'internal const int ToolCount = {EXPECTED_TOOL_COUNT};' in contract
    assert f'{{ "{TOOL_NAME}", "VRCForge.Editor.DuplicateProjectAssetTool" }}' in contract
    assert f'"{TOOL_NAME}",' in server
    assert '"vrcforge_duplicate_project_asset"' in dashboard
    assert '"vrcforge_preview_project_asset_duplicate"' in gateway
    assert "AssetDatabase.CopyAsset" in tool
    assert "AssetDatabase.MoveAsset" not in tool
    assert "overwrite is not supported" in tool
    assert 'internal const string GeneratedRoot = "Assets/VRCForgeGenerated";' in tool
    assert '".mat",' in tool
    assert "ValidateGeneratedSourceType(sourcePath, sourceType.FullName ?? sourceType.Name)" in tool


def test_external_copy_handler_binds_the_exact_core_execution_plan() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001
        "vrcforge_duplicate_project_asset"
    ]
    canonical, _approval = bind_authoritative_preview(wrapper(), preview_payload())

    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is not None
    assert handler.approved_execution_plan_builder(canonical) == [
        (TOOL_NAME, canonical["arguments"]),
    ]


def test_validated_copy_receipt_reaches_external_transaction_verification() -> None:
    from agent_approval_transactions import _domain_write_receipt

    canonical, _approval = bind_authoritative_preview(wrapper(), preview_payload())
    payload = apply_payload(canonical)
    payload["after"] = dict(payload["target"])
    transport = dashboard_server.McpResult(
        exit_code=0, stdout="", stderr="", payload={"data": payload}
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport),
    ):
        result = dashboard_server.unity_mcp_write_sync(canonical)

    assert result["ok"] is True
    receipt = _domain_write_receipt(result)
    assert receipt.get("verified") is True
    assert receipt.get("commitState") == "committed"
    assert receipt.get("mutationApplied") is True
    assert receipt.get("readback", {}).get("persisted") is True
    assert receipt["readback"]["data"] == payload
    assert result["result"]["payload"]["data"] == payload


def test_strict_project_asset_copy_preserves_structured_core_failure() -> None:
    failure = {
        "isError": True,
        "structuredContent": {
            "success": False,
            "message": "The created project asset copy failed exact readback verification.",
            "data": {
                "schema": RESULT_SCHEMA,
                "ok": False,
                "operation": OPERATION,
                "failureLayer": "unity_mutation",
                "failurePhase": "created_asset_readback",
                "mutationStarted": True,
                "writeOccurred": True,
                "committed": False,
                "commitState": "not_committed",
                "requestMayHaveCommitted": False,
                "cleanupRequired": False,
                "checkpointRecoveryRequired": False,
            },
        },
    }
    transport = dashboard_server.McpResult(
        exit_code=1,
        stdout="untrusted transport text",
        stderr="",
        payload=failure,
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport) as invoke,
    ):
        result = dashboard_server.unity_mcp_write_sync(
            {
                "projectPath": "D:/Project",
                "toolName": TOOL_NAME,
                "arguments": {},
                "_vrcforge_approved_execution": {"lane": "approved_write"},
            }
        )

    assert invoke.call_args.kwargs["preserve_tool_error"] is True
    assert result == {
        "ok": False,
        "toolName": TOOL_NAME,
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "failureLayer": "unity_mutation",
        "failurePhase": "created_asset_readback",
        "errorCode": "unity_tool_failed",
        "error": "The created project asset copy failed exact readback verification.",
        "mutationStarted": True,
        "writeOccurred": True,
        "committed": False,
        "commitState": "not_committed",
        "requestMayHaveCommitted": False,
        "cleanupRequired": False,
        "checkpointRecoveryRequired": False,
        "failureCause": {
            "kind": "unity_tool_rejection",
            "code": "unity_tool_failed",
            "message": "The created project asset copy failed exact readback verification.",
            "failureLayer": "unity_mutation",
            "failurePhase": "created_asset_readback",
        },
    }


def test_core_copy_retries_only_the_stable_readback_and_reports_failure_phase() -> None:
    root = Path(__file__).resolve().parents[1]
    tool = (root / "Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs").read_text(
        encoding="utf-8"
    )

    assert "ReadCreatedEvidenceWithRetry" in tool
    assert "Thread.Sleep(StableReadRetryDelayMilliseconds)" in tool
    assert 'failurePhase = "created_asset_readback"' in tool
    assert "failurePhase," in tool


def test_generated_copy_root_is_independent_and_anchor_is_assets() -> None:
    assert GENERATED_ROOT == "Assets/VRCForgeGenerated"
    assert ANCHOR_ROOT == "Assets"


def test_classified_destination_binds_parent_identity_without_renaming() -> None:
    payload = preview_payload()
    destination = "Assets/VRCForgeGenerated/FinalAvatar/Controllers/FinalAvatar_FT.controller"
    parent = destination.rsplit("/", 1)[0]
    payload["target"].update(
        assetPath=destination,
        generatedRootPath="Assets/VRCForgeGenerated",
        anchorFolderPath="Assets",
        parentFolderPath=parent,
        parentFolderGuid="a" * 32,
        parentFolderIdentity="b" * 64,
    )
    payload["previewDigest"] = compute_preview_digest(payload)
    request = wrapper()
    request["arguments"]["destinationAssetPath"] = destination

    canonical, approval = bind_authoritative_preview(request, payload)

    assert canonical["arguments"]["destinationAssetPath"] == destination
    assert canonical["arguments"]["expectedDestinationParentFolderGuid"] == "a" * 32
    assert canonical["arguments"]["expectedDestinationParentFolderIdentity"] == "b" * 64
    assert approval["target"]["parentFolderPath"] == parent
    assert approval["mutationCount"] == 1
    assert approval["rollbackRequired"] is True
    assert validate_apply_result(canonical["arguments"], apply_payload(canonical))["target"]["assetPath"] == destination


@pytest.mark.parametrize("field,value", [
    ("parentFolderPath", "Assets/VRCForgeGenerated/Other"),
    ("parentFolderGuid", ""),
    ("parentFolderIdentity", ""),
])
def test_generated_copy_preview_rejects_unbound_destination_parent(field: str, value: str) -> None:
    payload = preview_payload()
    payload["target"][field] = value
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(ProjectAssetCopyError):
        bind_authoritative_preview(wrapper(), payload)


def test_absent_root_cannot_claim_an_existing_classified_parent() -> None:
    payload = preview_payload(generated_root_exists=False)
    destination = f"{GENERATED_ROOT}/Avatar/Controllers/Copy.controller"
    payload["target"].update(
        assetPath=destination,
        parentFolderPath=destination.rsplit("/", 1)[0],
        parentFolderGuid="a" * 32,
        parentFolderIdentity="b" * 64,
    )
    payload["previewDigest"] = compute_preview_digest(payload)
    request = wrapper()
    request["arguments"]["destinationAssetPath"] = destination

    with pytest.raises(ProjectAssetCopyError, match="classified destination parent"):
        bind_authoritative_preview(request, payload)


def test_disposable_copy_fixture_exercises_root_lifecycle_and_parent_replacement() -> None:
    fixture = Path("tests/fixtures/primitive_basis/project_asset_copy/ProjectAssetCopyFixtureProbe.cs").read_text(encoding="utf-8")
    for fragment in (
        'ReadAssetGuid("Assets", "anchor")',
        'ReadDirectoryIdentity("Assets", "anchor")',
        "first apply did not create the root",
        "root or meta remained after cleanup",
        "cleanup removed a nonempty generated root",
        "replaced classified parent was accepted",
        "cleanup removed a replacement root",
        "source bytes, metadata, GUID or file identity changed",
        "VRCFORGE_PROJECT_ASSET_COPY_PROBE_OK",
    ):
        assert fragment in fixture


@pytest.mark.parametrize("extension,type_name", [
    (".anim", "UnityEngine.AnimationClip"),
    (".controller", "UnityEditor.Animations.AnimatorController"),
    (".overridecontroller", "UnityEngine.AnimatorOverrideController"),
])
def test_generated_animation_copy_binds_and_verifies_exact_identity(extension, type_name):
    source = f"{GENERATED_ROOT}/FinalAvatar/Shared/Controllers/Original{extension}"
    destination = f"{GENERATED_ROOT}/Copy{extension}"
    request = wrapper()
    request["arguments"].update(sourceAssetPath=source, destinationAssetPath=destination)
    payload = preview_payload()
    payload["source"].update(assetPath=source, mainAssetType=type_name)
    payload["target"]["assetPath"] = destination
    payload["previewDigest"] = compute_preview_digest(payload)
    canonical, approval = bind_authoritative_preview(request, payload)
    assert canonical["arguments"]["expectedSourceMainAssetType"] == type_name
    assert canonical["arguments"]["expectedSourceFileDigest"] == payload["source"]["fileDigest"]
    assert canonical["arguments"]["expectedSourceObjectLayoutDigest"] == payload["source"]["objectLayoutDigest"]
    assert canonical["arguments"]["expectedDestinationAbsent"] is True
    assert canonical["arguments"]["overwrite"] is False
    assert approval["rollbackRequired"] is True
    result = apply_payload(canonical)
    assert validate_apply_result(canonical["arguments"], result) == result
    result["source"]["fileDigest"] = "f" * 64
    with pytest.raises(ProjectAssetCopyError):
        validate_apply_result(canonical["arguments"], result)


@pytest.mark.parametrize("path", [
    f"{GENERATED_ROOT}/Unknown.asset", f"{GENERATED_ROOT}/Script.cs",
    "Assets/VRCForge/Generated/Legacy.anim", f"{GENERATED_ROOT}/../A.anim",
    f"{GENERATED_ROOT}/Folder/", "Packages/Other/A.anim",
])
def test_source_policy_rejects_unsupported_generated_assets(path):
    from project_asset_copy import _source_path
    with pytest.raises(ProjectAssetCopyError):
        _source_path(path)


def test_generated_source_type_cannot_be_spoofed_by_extension():
    payload = preview_payload()
    payload["source"].update(assetPath=f"{GENERATED_ROOT}/Avatar/Fake.anim", mainAssetType="Untrusted.ScriptableAsset")
    payload["previewDigest"] = compute_preview_digest(payload)
    request = wrapper()
    request["arguments"]["sourceAssetPath"] = payload["source"]["assetPath"]
    with pytest.raises(ProjectAssetCopyError, match="type does not match"):
        bind_authoritative_preview(request, payload)


@pytest.mark.parametrize("path", [
    "Assets/VRCForge/Editor/Existing.controller", "Assets/Plugins/Other/Existing.anim",
])
def test_existing_non_generated_authoring_source_policy_is_preserved(path):
    from project_asset_copy import _source_path
    assert _source_path(path) == path


def missing_folder_preview():
    payload = preview_payload()
    target = payload["target"]
    target.update(assetPath=GENERATED_ROOT + "/Avatar/Materials/Copy.controller", parentFolderPath=GENERATED_ROOT + "/Avatar/Materials", parentFolderGuid="", parentFolderIdentity="", folderCreation={"ancestorPath": GENERATED_ROOT, "ancestorGuid": target["generatedRootGuid"], "ancestorIdentity": target["generatedRootIdentity"], "paths": [GENERATED_ROOT + "/Avatar", GENERATED_ROOT + "/Avatar/Materials"]})
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def test_missing_classified_parents_are_bound_and_visible_before_copy():
    payload = missing_folder_preview()
    request = wrapper()
    request["arguments"]["destinationAssetPath"] = payload["target"]["assetPath"]
    canonical, approval = bind_authoritative_preview(request, payload)
    assert canonical["arguments"]["expectedCreatedFolders"] == payload["target"]["folderCreation"]["paths"]
    assert approval["target"]["folderCreation"] == payload["target"]["folderCreation"]
    assert approval["mutationCount"] == 3


def test_missing_parent_plan_participates_in_preview_digest():
    payload = missing_folder_preview()
    digest = compute_preview_digest(payload)
    payload["target"]["folderCreation"]["ancestorIdentity"] = "a" * 64
    assert compute_preview_digest(payload) != digest


@pytest.mark.parametrize("mutate", [
    lambda t: t["folderCreation"].update(paths=[GENERATED_ROOT + "/Avatar/Materials"]),
    lambda t: t["folderCreation"].update(paths=[GENERATED_ROOT + "/Elsewhere", GENERATED_ROOT + "/Avatar/Materials"]),
    lambda t: t["folderCreation"].update(ancestorPath="Assets/Other"),
    lambda t: t["folderCreation"].update(ancestorGuid="a" * 32),
    lambda t: t.update(parentFolderGuid="a" * 32),
    lambda t: t.update(assetExists=True),
    lambda t: t.update(metaExists=True),
])
def test_classified_folder_plan_rejects_unbound_or_occupied_targets(mutate):
    payload = missing_folder_preview()
    request = wrapper()
    request["arguments"]["destinationAssetPath"] = payload["target"]["assetPath"]
    mutate(payload["target"])
    payload["previewDigest"] = compute_preview_digest(payload)
    with pytest.raises(ProjectAssetCopyError): bind_authoritative_preview(request, payload)


def test_classified_folder_apply_verifies_exact_created_chain():
    payload = missing_folder_preview()
    request = wrapper()
    request["arguments"]["destinationAssetPath"] = payload["target"]["assetPath"]
    canonical, _ = bind_authoritative_preview(request, payload)
    result = apply_payload(canonical)
    result["mutationCount"] = 3
    result["target"]["createdFolders"] = payload["target"]["folderCreation"]["paths"]
    assert validate_apply_result(canonical["arguments"], result)["verified"] is True
    result["target"]["createdFolders"] = []
    with pytest.raises(ProjectAssetCopyError): validate_apply_result(canonical["arguments"], result)
