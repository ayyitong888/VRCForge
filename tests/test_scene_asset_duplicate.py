from __future__ import annotations

from pathlib import Path

import pytest

import agent_gateway
import authoritative_unity_writes
import dashboard_server
from scene_asset_duplicate import (
    APPROVAL_SCHEMA,
    OPERATION,
    RESULT_SCHEMA,
    TOOL_NAME,
    SceneAssetDuplicateError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_preview_digest,
    validate_apply_result,
)
from unity_mcp_tool_contract import EXPECTED_TOOL_COUNT


def preview_payload(*, open_copy: bool = True) -> dict:
    payload = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "operation": OPERATION,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "mutationStarted": False,
        "commitState": "not_started",
        "checkpointRestoreRequired": False,
        "manualRecoveryRequired": False,
        "projectPath": "D:/DisposableUnityProject",
        "source": {
            "assetPath": "Assets/2.unity",
            "guid": "1" * 32,
            "fileDigest": "2" * 64,
            "fileIdentity": "3" * 64,
            "metaDigest": "4" * 64,
            "metaIdentity": "5" * 64,
            "mainAssetType": "UnityEditor.SceneAsset",
            "loaded": True,
            "unchanged": True,
        },
        "target": {
            "assetPath": "Assets/3.unity",
            "parentPath": "Assets",
            "assetExists": False,
            "metaExists": False,
            "createNew": True,
            "openAsOnlyActiveScene": open_copy,
            "willBecomeOnlyActiveScene": open_copy,
        },
        "sceneSetupDigest": "6" * 64,
        "openSceneStateDigest": "7" * 64,
        "cleanupRequired": False,
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def wrapper(*, open_copy: bool = True) -> dict:
    return build_wrapper_arguments(
        {
            "projectPath": "D:/DisposableUnityProject",
            "sourceScenePath": "Assets/2.unity",
            "destinationScenePath": "Assets/3.unity",
            "openAsOnlyActiveScene": open_copy,
        }
    )


def apply_payload(canonical: dict) -> dict:
    args = canonical["arguments"]
    open_copy = args["openAsOnlyActiveScene"]
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "operation": OPERATION,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "mutationCount": 2 if open_copy else 1,
        "mutationStarted": True,
        "commitState": "committed",
        "checkpointRestoreRequired": False,
        "manualRecoveryRequired": False,
        "source": {
            "assetPath": args["sourceScenePath"],
            "guid": args["expectedSourceGuid"],
            "fileDigest": args["expectedSourceFileDigest"],
            "fileIdentity": args["expectedSourceFileIdentity"],
            "metaDigest": args["expectedSourceMetaDigest"],
            "metaIdentity": args["expectedSourceMetaIdentity"],
            "mainAssetType": "UnityEditor.SceneAsset",
            "loaded": True,
            "unchanged": True,
        },
        "target": {
            "assetPath": args["destinationScenePath"],
            "guid": "8" * 32,
            "fileDigest": args["expectedSourceFileDigest"],
            "fileIdentity": "9" * 64,
            "metaDigest": "a" * 64,
            "metaIdentity": "b" * 64,
            "mainAssetType": "UnityEditor.SceneAsset",
            "bytesIdenticalToSource": True,
            "createNew": True,
            "readbackVerified": True,
            "openAsOnlyActiveScene": open_copy,
            "opened": open_copy,
            "active": open_copy,
        },
        "previewDigest": args["expectedPreviewDigest"],
        "cleanupRequired": False,
    }


def test_preview_forces_create_new_and_discards_caller_preconditions() -> None:
    assert build_preview_arguments(
        {
            "sourceScenePath": "Assets/2.unity",
            "destinationScenePath": "Assets/3.unity",
            "openAsOnlyActiveScene": True,
            "overwrite": True,
            "expectedSourceGuid": "f" * 32,
            "secret": "must-not-cross",
        }
    ) == {
        "sourceScenePath": "Assets/2.unity",
        "destinationScenePath": "Assets/3.unity",
        "openAsOnlyActiveScene": True,
        "preview": True,
        "overwrite": False,
    }


@pytest.mark.parametrize("open_copy", [False, True])
def test_preview_binds_source_destination_setup_and_open_behavior(open_copy: bool) -> None:
    canonical, approval = bind_authoritative_preview(
        wrapper(open_copy=open_copy),
        preview_payload(open_copy=open_copy),
    )
    args = canonical["arguments"]

    assert canonical["toolName"] == TOOL_NAME
    assert args["preview"] is False
    assert args["overwrite"] is False
    assert args["expectedDestinationAbsent"] is True
    assert args["expectedDestinationParentPath"] == "Assets"
    assert args["expectedSceneSetupDigest"] == "6" * 64
    assert args["expectedOpenSceneStateDigest"] == "7" * 64
    assert args["expectedOpenAsOnlyActiveScene"] is open_copy
    assert approval["schema"] == APPROVAL_SCHEMA
    assert approval["mutationCount"] == (2 if open_copy else 1)
    assert approval["createNew"] is True
    assert approval["overwrite"] is False


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        ("Assets/2.prefab", "Assets/3.unity"),
        ("Assets/2.unity", "Assets/3.prefab"),
        ("Assets/2.unity", "Assets/2.unity"),
        ("Packages/2.unity", "Assets/3.unity"),
        ("Assets/../2.unity", "Assets/3.unity"),
    ],
)
def test_unsafe_scene_paths_fail_closed(source: str, destination: str) -> None:
    request = wrapper()
    request["arguments"]["sourceScenePath"] = source
    request["arguments"]["destinationScenePath"] = destination

    with pytest.raises(SceneAssetDuplicateError):
        bind_authoritative_preview(request, preview_payload())


def test_tampered_preview_digest_fails_closed() -> None:
    payload = preview_payload()
    payload["source"]["fileDigest"] = "e" * 64

    with pytest.raises(SceneAssetDuplicateError):
        bind_authoritative_preview(wrapper(), payload)


def test_preview_from_another_project_fails_closed() -> None:
    payload = preview_payload()
    payload["projectPath"] = "D:/OtherUnityProject"
    payload["previewDigest"] = compute_preview_digest(payload)

    with pytest.raises(SceneAssetDuplicateError):
        bind_authoritative_preview(wrapper(), payload)


@pytest.mark.parametrize("open_copy", [False, True])
def test_apply_receipt_requires_independent_guid_bytes_and_open_readback(open_copy: bool) -> None:
    canonical, _approval = bind_authoritative_preview(
        wrapper(open_copy=open_copy),
        preview_payload(open_copy=open_copy),
    )
    result = apply_payload(canonical)

    assert validate_apply_result(canonical["arguments"], result) == result


@pytest.mark.parametrize("mutation", ["same_guid", "changed_bytes", "not_opened", "source_changed"])
def test_invalid_apply_receipts_fail_closed(mutation: str) -> None:
    canonical, _approval = bind_authoritative_preview(wrapper(), preview_payload())
    result = apply_payload(canonical)
    if mutation == "same_guid":
        result["target"]["guid"] = result["source"]["guid"]
    elif mutation == "changed_bytes":
        result["target"]["fileDigest"] = "e" * 64
    elif mutation == "not_opened":
        result["target"]["opened"] = False
    else:
        result["source"]["unchanged"] = False

    with pytest.raises(SceneAssetDuplicateError):
        validate_apply_result(canonical["arguments"], result)


def test_core_uses_asset_database_single_open_exact_readback_and_owned_rollback() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "Assets/VRCForge/Editor/SceneAssetDuplicateTool.cs").read_text(
        encoding="utf-8"
    )

    assert 'toolId: "vrc_duplicate_scene_asset"' in source
    assert "when-to-use:" in source and "when-NOT-to-use:" in source
    assert "AssetDatabase.CopyAsset" in source
    assert "EditorSceneManager.OpenScene" in source
    assert "OpenSceneMode.Single" in source
    assert "EditorSceneManager.RestoreSceneManagerSetup" in source
    assert "SceneObjectCopyCore.ReadStableAssetEvidence" in source
    assert "SceneObjectCopyCore.StableAssetEvidenceMatches" in source
    assert "SceneObjectCopyCore.DeleteOwnedAsset" in source
    assert source.index("EditorSceneManager.RestoreSceneManagerSetup") < source.index(
        "SceneObjectCopyCore.DeleteOwnedAsset"
    )
    assert "File.Copy" not in source
    assert "File.Move" not in source
    assert "AssetDatabase.MoveAsset" not in source
    assert "overwrite is unsupported" in source


def test_core_contract_gateway_and_package_manifest_register_scene_duplicate() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = (root / "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs").read_text(
        encoding="utf-8"
    )
    server = (root / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text(
        encoding="utf-8"
    )
    dashboard = (root / "dashboard_server.py").read_text(encoding="utf-8")
    gateway = (root / "agent_gateway.py").read_text(encoding="utf-8")
    manifest = (root / "packaging/unitypackage_guid_manifest.json").read_text(
        encoding="utf-8"
    )

    assert f"internal const int ToolCount = {EXPECTED_TOOL_COUNT};" in contract
    assert '{ "vrc_duplicate_scene_asset", "VRCForge.Editor.SceneAssetDuplicateTool" }' in contract
    assert '"vrc_duplicate_scene_asset",' in server
    assert '"vrcforge_preview_scene_asset_duplicate"' in dashboard
    assert '"vrcforge_duplicate_scene_asset"' in dashboard
    assert '"vrcforge_preview_scene_asset_duplicate"' in gateway
    assert '"vrcforge_duplicate_scene_asset"' in gateway
    assert '"Assets/VRCForge/Editor/SceneAssetDuplicateTool.cs"' in manifest


def test_external_handler_binds_exact_core_execution_plan() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[  # noqa: SLF001
        "vrcforge_duplicate_scene_asset"
    ]
    canonical, _approval = bind_authoritative_preview(wrapper(), preview_payload())

    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is not None
    assert handler.approved_execution_plan_builder(canonical) == [
        (TOOL_NAME, canonical["arguments"]),
    ]


def test_authoritative_write_registry_replaces_caller_binding_with_live_preview(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    request = wrapper()
    request["projectPath"] = str(project)
    observed: list[tuple[str, dict]] = []
    payload = preview_payload()
    payload["projectPath"] = str(project.resolve())
    payload["previewDigest"] = compute_preview_digest(payload)

    canonical, approval = authoritative_unity_writes.prepare_authoritative_unity_write(
        request,
        {"spoofed": True},
        lambda tool_name, arguments: (
            observed.append((tool_name, arguments.copy())) or payload
        ),
    )

    assert TOOL_NAME in authoritative_unity_writes.AUTHORITATIVE_UNITY_WRITE_TOOLS
    assert observed == [
        (
            TOOL_NAME,
            {
                "sourceScenePath": "Assets/2.unity",
                "destinationScenePath": "Assets/3.unity",
                "openAsOnlyActiveScene": True,
                "preview": True,
                "overwrite": False,
                "expectedProjectPath": str(project.resolve()),
            },
        )
    ]
    assert canonical["arguments"]["expectedProjectPath"] == str(project.resolve())
    assert canonical["arguments"]["expectedDestinationAbsent"] is True
    assert approval["schema"] == APPROVAL_SCHEMA


def test_preview_and_write_share_one_closed_public_input_schema() -> None:
    write_schema = agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS[
        "vrcforge_duplicate_scene_asset"
    ]

    assert write_schema["additionalProperties"] is False
    assert write_schema["required"] == [
        "projectPath",
        "sourceScenePath",
        "destinationScenePath",
    ]
    assert agent_gateway.canonical_unity_read_tool_input_schema(
        "vrcforge_preview_scene_asset_duplicate"
    ) == write_schema
    assert agent_gateway.canonical_unity_write_tool_input_schema(
        "vrcforge_duplicate_scene_asset"
    ) == write_schema
