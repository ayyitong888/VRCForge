from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

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
    gateway = (root / "agent_gateway.py").read_text(encoding="utf-8")
    tool = (root / "Assets/VRCForge/Editor/Generic/DuplicateProjectAssetTool.cs").read_text(encoding="utf-8")

    assert f'internal const int ToolCount = {EXPECTED_TOOL_COUNT};' in contract
    assert f'{{ "{TOOL_NAME}", "VRCForge.Editor.DuplicateProjectAssetTool" }}' in contract
    assert f'"{TOOL_NAME}",' in server
    assert '"vrcforge_duplicate_project_asset"' in dashboard
    assert '"vrcforge_preview_project_asset_duplicate"' in gateway
    assert "AssetDatabase.CopyAsset" in tool
    assert "AssetDatabase.MoveAsset" not in tool
    assert "overwrite is not supported" in tool
    assert "Assets/VRCForge/Generated" in tool
    assert '".mat",' in tool
    assert 'string.Equals(Path.GetExtension(path), ".mat", StringComparison.OrdinalIgnoreCase)' in tool


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
