from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_server
from agent_gateway import AgentGateway
from atomic_reference_rename import TOOL_NAME as ATOMIC_REFERENCE_RENAME_TOOL_NAME
from parameter_bit_packing import TOOL_NAME as PARAMETER_BIT_PACKING_TOOL_NAME
from component_feature_write import (
    COMPATIBILITY_DIGEST_SCHEMA,
    EXPECTED_COMPATIBILITY,
    FEATURE_DIGEST_SCHEMA,
    TOOL_NAME as COMPONENT_FEATURE_TOOL_NAME,
    build_wrapper_arguments as build_component_feature_wrapper,
    compute_compatibility_digest as compute_component_compatibility_digest,
    compute_feature_digest,
    compute_preview_digest as compute_component_preview_digest,
)
from constraint_source_write import (
    TOOL_NAME as CONSTRAINT_TOOL_NAME,
    build_wrapper_arguments as build_constraint_wrapper,
    compute_component_id,
    compute_sources_digest,
)
from scene_object_copy import (
    DUPLICATE_TOOL_NAME,
    PREFAB_TOOL_NAME,
    build_wrapper_arguments as build_scene_wrapper,
    compute_preview_digest,
)
from texture_import_settings import (
    TOOL_NAME as TEXTURE_TOOL_NAME,
    build_wrapper_arguments as build_texture_wrapper,
    compute_settings_digest,
)


def _scene_source() -> dict:
    return {
        "scenePath": "Assets/Scenes/Fixture.unity",
        "sceneGuid": "a" * 32,
        "sceneHandle": 7,
        "objectPath": "Avatar/Accessory",
        "objectId": "b" * 64,
        "hierarchyDigest": "c" * 64,
        "sceneFileDigest": "d" * 64,
        "sceneFileIdentity": "e" * 64,
        "sceneMetaDigest": "9" * 64,
        "sceneMetaIdentity": "8" * 64,
        "pathUnique": True,
    }


def _duplicate_payload() -> dict:
    payload = {
        "schema": "vrcforge.scene_object_copy.v1",
        "ok": True,
        "operation": "duplicate_scene_object",
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "source": _scene_source(),
        "target": {
            "scenePath": "Assets/Scenes/Fixture.unity",
            "sceneGuid": "a" * 32,
            "sceneHandle": 7,
            "parentPath": "Avatar",
            "parentObjectId": "f" * 64,
            "parentHierarchyDigest": "1" * 64,
            "sceneFileDigest": "d" * 64,
            "sceneFileIdentity": "e" * 64,
            "sceneMetaDigest": "9" * 64,
            "sceneMetaIdentity": "8" * 64,
            "objectPath": "Avatar/AccessoryCopy",
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
        "schema": "vrcforge.scene_object_copy.v1",
        "ok": True,
        "operation": "save_scene_object_as_prefab",
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "source": _scene_source(),
        "target": {
            "assetPath": "Assets/VRCForge/Generated/Accessory.prefab",
            "parentFolderPath": "Assets/VRCForge/Generated",
            "parentFolderGuid": "2" * 32,
            "parentFolderIdentity": "3" * 64,
            "stagingRootPath": "Assets/VRCForge/Generated",
            "stagingRootGuid": "2" * 32,
            "stagingRootIdentity": "3" * 64,
            "stagingPolicy": "random_create_new_folder_v1",
            "assetExists": False,
            "metaExists": False,
            "createNew": True,
        },
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def _texture_settings(*, target: bool) -> dict:
    return {
        "platform": "standalone",
        "platformName": "Standalone",
        "overridden": True,
        "maxTextureSize": 2048 if target else 4096,
        "format": "dxt5_crunched" if target else "automatic",
        "compression": "high" if target else "normal",
        "crunch": target,
        "quality": 82 if target else 50,
        "ignorePlatformSupport": False,
    }


def _texture_payload(project: Path) -> dict:
    before = _texture_settings(target=False)
    target = _texture_settings(target=True)
    return {
        "schema": "vrcforge.texture_import_settings.v1",
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "wouldChange": True,
        "saved": False,
        "reimported": False,
        "projectPath": str(project.resolve()),
        "textureAssetPath": "Assets/Textures/Body.png",
        "textureAssetGuid": "4" * 32,
        "sourceFileDigestBefore": "5" * 64,
        "sourceFileDigestAfter": "5" * 64,
        "sourceFileIdentityDigest": "6" * 64,
        "sourceFileLinkCount": 1,
        "metaFileDigestBefore": "7" * 64,
        "metaFileDigestAfter": "7" * 64,
        "metaFileIdentityDigest": "8" * 64,
        "metaFileLinkCount": 1,
        "importerType": "Default",
        "beforeSettings": before,
        "targetSettings": target,
        "importerSettingsDigestBefore": compute_settings_digest("Default", before),
        "importerSettingsDigestAfter": compute_settings_digest("Default", before),
        "targetSettingsDigest": compute_settings_digest("Default", target),
        "importerDirtyBefore": False,
        "importerDirtyAfter": False,
    }


def _constraint_payload(project: Path) -> dict:
    scene_guid = "a" * 32
    component_global_id = "GlobalObjectId_V1-2-123-456-0"
    component_type = "VRC.SDK3.Dynamics.Constraint.Components.VRCPositionConstraint"
    target_sources = [
        {
            "sourcePath": "Avatar/SourceA",
            "sourceObjectId": "GlobalObjectId_V1-2-201-1-0",
            "weight": 0.25,
            "weightBits": "3e800000",
        }
    ]
    return {
        "schema": "vrcforge.constraint_source_write.v1",
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "wouldChange": True,
        "projectPath": str(project.resolve()),
        "scenePath": "Assets/Scenes/Fixture.unity",
        "sceneGuid": scene_guid,
        "sceneHandle": 7,
        "sceneFileDigestBefore": "b" * 64,
        "sceneFileDigestAfter": "b" * 64,
        "sceneFileIdentity": "c" * 64,
        "sceneFileLinkCount": 1,
        "sceneMetaDigestBefore": "d" * 64,
        "sceneMetaDigestAfter": "d" * 64,
        "sceneMetaIdentity": "e" * 64,
        "sceneMetaLinkCount": 1,
        "sceneDirtyBefore": False,
        "sceneDirtyAfter": False,
        "gameObjectPath": "Avatar/ConstraintHost",
        "constraintKind": "position",
        "componentType": component_type,
        "componentIndex": 0,
        "componentId": compute_component_id(
            scene_guid=scene_guid,
            component_global_id=component_global_id,
            game_object_path="Avatar/ConstraintHost",
            component_type=component_type,
            component_index=0,
        ),
        "componentGlobalId": component_global_id,
        "beforeSources": [],
        "targetSources": target_sources,
        "beforeSourcesDigest": compute_sources_digest([]),
        "targetSourcesDigest": compute_sources_digest(target_sources),
        "sourcesDigestSchema": "vrcforge.constraint_sources_digest.v1",
    }


def _component_feature_payload(project: Path) -> dict:
    before = {"present": False, "featureKind": "toggle"}
    target = {
        "present": True,
        "featureKind": "toggle",
        "menuPath": "Wardrobe/Hat",
        "slider": False,
        "defaultOn": True,
        "saved": True,
        "globalParameter": "Wardrobe_Hat",
        "targets": [
            {
                "objectPath": "Avatar/Hat",
                "objectId": "GlobalObjectId_V1-2-100-0-0",
            }
        ],
    }
    compatibility = deepcopy(EXPECTED_COMPATIBILITY)
    compatibility.update(
        {
            "apiAssemblyDigest": "6" * 64,
            "runtimeAssemblyDigest": "7" * 64,
        }
    )
    payload = {
        "schema": "vrcforge.component_feature_write.v1",
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "projectPath": str(project.resolve()),
        "compatibility": compatibility,
        "compatibilityDigestSchema": COMPATIBILITY_DIGEST_SCHEMA,
        "compatibilityDigest": compute_component_compatibility_digest(compatibility),
        "scene": {
            "path": "Assets/Scenes/Fixture.unity",
            "guid": "a" * 32,
            "handle": -1322,
            "fileDigestBefore": "b" * 64,
            "fileDigestAfter": "b" * 64,
            "fileIdentity": "c" * 64,
            "metaDigestBefore": "d" * 64,
            "metaDigestAfter": "d" * 64,
            "metaIdentity": "e" * 64,
            "dirtyBefore": False,
            "dirtyAfter": False,
        },
        "host": {
            "objectPath": "Avatar/FeatureHost",
            "objectId": "GlobalObjectId_V1-2-300-0-0",
            "componentType": "VF.Model.VRCFury",
            "componentIndex": 0,
            "componentIdentitySeed": "f" * 64,
            "existingFeatureCount": 0,
        },
        "before": before,
        "target": target,
        "featureDigestSchema": FEATURE_DIGEST_SCHEMA,
        "beforeFeatureDigest": compute_feature_digest(before),
        "targetFeatureDigest": compute_feature_digest(target),
        "wouldChange": True,
    }
    payload["previewDigest"] = compute_component_preview_digest(payload)
    return payload


@pytest.mark.parametrize(
    ("tool_name", "params", "payload", "plan_call", "expected_precondition"),
    [
        (
            DUPLICATE_TOOL_NAME,
            {
                "sourceScenePath": "Assets/Scenes/Fixture.unity",
                "sourceObjectPath": "Avatar/Accessory",
                "targetParentScenePath": "Assets/Scenes/Fixture.unity",
                "targetParentPath": "Avatar",
                "targetName": "AccessoryCopy",
                "preserveWorldTransform": False,
            },
            _duplicate_payload,
            lambda values: dashboard_server.preview_scene_object_copy_sync(values, DUPLICATE_TOOL_NAME),
            "expectedDestinationPath",
        ),
        (
            PREFAB_TOOL_NAME,
            {
                "sourceScenePath": "Assets/Scenes/Fixture.unity",
                "sourceObjectPath": "Avatar/Accessory",
                "prefabAssetPath": "Assets/VRCForge/Generated/Accessory.prefab",
            },
            _prefab_payload,
            lambda values: dashboard_server.preview_scene_object_copy_sync(values, PREFAB_TOOL_NAME),
            "expectedStagingRootIdentity",
        ),
    ],
)
def test_scene_plan_and_approval_use_one_authoritative_mapping(
    tmp_path: Path,
    tool_name: str,
    params: dict,
    payload,
    plan_call,
    expected_precondition: str,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    request = {"projectPath": str(project), **params}
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": payload()},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        plan = plan_call(deepcopy(request))
        wrapper = build_scene_wrapper(deepcopy(request), tool_name)
        prepared, approval = dashboard_server.prepare_unity_mcp_write_request(
            wrapper,
            {"spoofed": True},
        )

    assert plan == {"ok": True, "preview": approval}
    assert prepared["toolName"] == tool_name
    assert prepared["projectPath"] == str(project.resolve())
    assert prepared["arguments"][expected_precondition]
    assert prepared["arguments"]["preview"] is False
    assert "spoofed" not in approval
    assert [call.args[1] for call in invoke.call_args_list] == [tool_name, tool_name]
    for call in invoke.call_args_list:
        assert call.args[2]["preview"] is True
        assert not any(key.startswith("expected") and key != "expectedProjectPath" for key in call.args[2])


def test_texture_plan_and_approval_use_one_authoritative_mapping(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    params = {
        "projectPath": str(project),
        "textureAssetPath": "Assets/Textures/Body.png",
        "platform": "standalone",
        "maxTextureSize": 2048,
        "format": "dxt5_crunched",
        "compression": "high",
        "crunch": True,
        "quality": 82,
    }
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": _texture_payload(project)},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        plan = dashboard_server.preview_texture_import_settings_sync(deepcopy(params))
        prepared, approval = dashboard_server.prepare_unity_mcp_write_request(
            build_texture_wrapper(deepcopy(params)),
            {"spoofed": True},
        )

    assert plan == {"ok": True, "preview": approval}
    assert prepared["toolName"] == TEXTURE_TOOL_NAME
    assert prepared["projectPath"] == str(project.resolve())
    assert prepared["arguments"]["expectedSourceFileIdentityDigest"] == "6" * 64
    assert prepared["arguments"]["expectedMetaFileIdentityDigest"] == "8" * 64
    assert prepared["arguments"]["saveAndReimport"] is True
    assert "spoofed" not in approval
    assert [call.args[1] for call in invoke.call_args_list] == [TEXTURE_TOOL_NAME, TEXTURE_TOOL_NAME]
    for call in invoke.call_args_list:
        assert call.args[2]["preview"] is True
        assert call.args[2]["saveAndReimport"] is False
        assert not any(key.startswith("expected") and key != "expectedProjectPath" for key in call.args[2])


def test_constraint_plan_and_approval_use_one_authoritative_mapping(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    params = {
        "projectPath": str(project),
        "scenePath": "Assets/Scenes/Fixture.unity",
        "gameObjectPath": "Avatar/ConstraintHost",
        "constraintKind": "position",
        "componentIndex": 0,
        "sources": [{"sourcePath": "Avatar/SourceA", "weight": 0.25}],
    }
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": _constraint_payload(project)},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        plan = dashboard_server.preview_constraint_sources_sync(deepcopy(params))
        prepared, approval = dashboard_server.prepare_unity_mcp_write_request(
            build_constraint_wrapper(deepcopy(params)),
            {"spoofed": True},
        )

    assert plan == {"ok": True, "preview": approval}
    assert prepared["toolName"] == CONSTRAINT_TOOL_NAME
    assert prepared["projectPath"] == str(project.resolve())
    assert prepared["arguments"]["expectedSceneFileIdentity"] == "c" * 64
    assert prepared["arguments"]["expectedSceneMetaIdentity"] == "e" * 64
    assert prepared["arguments"]["expectedTargetSourcesDigest"] == approval["change"]["afterSourcesDigest"]
    assert prepared["arguments"]["preview"] is False
    assert prepared["arguments"]["saveScene"] is True
    assert "spoofed" not in approval
    assert [call.args[1] for call in invoke.call_args_list] == [CONSTRAINT_TOOL_NAME, CONSTRAINT_TOOL_NAME]
    for call in invoke.call_args_list:
        assert call.args[2]["preview"] is True
        assert call.args[2]["saveScene"] is False
        assert not any(key.startswith("expected") and key != "expectedProjectPath" for key in call.args[2])


def test_component_feature_plan_and_approval_use_one_authoritative_mapping(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    params = {
        "projectPath": str(project),
        "scenePath": "Assets/Scenes/Fixture.unity",
        "gameObjectPath": "Avatar/FeatureHost",
        "featureKind": "toggle",
        "menuPath": "Wardrobe/Hat",
        "targetObjectPaths": ["Avatar/Hat"],
        "slider": False,
        "defaultOn": True,
        "saved": True,
        "globalParameter": "Wardrobe_Hat",
    }
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": _component_feature_payload(project)},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        plan = dashboard_server.preview_component_feature_sync(deepcopy(params))
        prepared, approval = dashboard_server.prepare_unity_mcp_write_request(
            build_component_feature_wrapper(deepcopy(params)),
            {"spoofed": True},
        )

    assert plan == {"ok": True, "preview": approval}
    assert prepared["toolName"] == COMPONENT_FEATURE_TOOL_NAME
    assert prepared["projectPath"] == str(project.resolve()).replace("\\", "/")
    assert prepared["arguments"]["expectedSceneHandle"] == -1322
    assert prepared["arguments"]["expectedSceneFileIdentity"] == "c" * 64
    assert prepared["arguments"]["expectedCompatibilityDigest"]
    assert prepared["arguments"]["expectedPreviewDigest"] == approval["previewDigest"]
    assert prepared["arguments"]["preview"] is False
    assert prepared["arguments"]["saveScene"] is True
    assert "spoofed" not in approval
    assert [call.args[1] for call in invoke.call_args_list] == [
        COMPONENT_FEATURE_TOOL_NAME,
        COMPONENT_FEATURE_TOOL_NAME,
    ]
    for call in invoke.call_args_list:
        assert call.args[2]["preview"] is True
        assert call.args[2]["saveScene"] is False
        assert not any(
            key.startswith("expected") and key != "expectedProjectPath"
            for key in call.args[2]
        )


def test_new_write_protocols_are_required_allowlisted_and_registered() -> None:
    assert dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST.issubset(
        dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
    )


def test_duplicate_scene_object_external_failure_reports_unknown_commit_state() -> None:
    params = {
        "toolName": "vrc_duplicate_scene_object",
        "arguments": {"sourceScenePath": "Assets/A.unity", "sourceObjectPath": "Avatar/Body"},
    }
    failed = dashboard_server.McpResult(exit_code=1, stdout="", stderr="Unity disconnected", payload=None)
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=failed),
    ):
        result = dashboard_server.duplicate_scene_object_sync(params)

    assert result["ok"] is False
    assert result["failureLayer"] == "unity_mcp_transport"
    assert result["mutationStarted"] is None
    assert result["committed"] is None
    assert result["commitState"] == "unknown"
    assert result["requestMayHaveCommitted"] is True
    assert result["checkpointRecoveryRequired"] is True


def test_duplicate_scene_object_plan_claim_rejection_is_confirmed_no_write() -> None:
    params = {
        "toolName": "vrc_duplicate_scene_object",
        "arguments": {"sourceScenePath": "Assets/A.unity", "sourceObjectPath": "Avatar/Body"},
    }
    failure = dashboard_server.UnityMcpError(
        "Approved Unity execution arguments do not match the frozen plan.",
        cause_code="approved_execution_claim_rejected",
        core_tool="vrc_duplicate_scene_object",
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", side_effect=failure),
    ):
        result = dashboard_server.duplicate_scene_object_sync(params)

    assert result["ok"] is False
    assert result["failureLayer"] == "gateway_execution_plan"
    assert result["errorCode"] == "approved_execution_claim_rejected"
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert result["requestMayHaveCommitted"] is False
    assert result["checkpointRecoveryRequired"] is False


def test_duplicate_preview_preserves_managed_peer_error_state(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    params = {
        "projectPath": str(project),
        "sourceScenePath": "Assets/A.unity",
        "sourceObjectPath": "Avatar/Body",
        "targetParentScenePath": "Assets/A.unity",
        "targetParentPath": "Avatar",
        "targetName": "BodyCopy",
    }
    with patch(
        "dashboard_server.invoke_unity_mcp",
        return_value=dashboard_server.McpResult(
            exit_code=1,
            stdout="",
            stderr="",
            payload={
                "isError": True,
                "structuredContent": {
                    "code": "managed_peer_ineligible",
                    "message": "The selected Unity peer is not eligible for managed writes.",
                    "data": {
                        "mutationStarted": False,
                        "committed": False,
                        "commitState": "not_started",
                    },
                },
            },
        ),
    ):
        result = dashboard_server.preview_scene_object_copy_sync(params, DUPLICATE_TOOL_NAME)

    assert result["ok"] is False
    assert result["failureLayer"] == "unity_core_preview"
    assert result["errorCode"] == "managed_peer_ineligible"
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert result["requestMayHaveCommitted"] is False
    assert result["checkpointRecoveryRequired"] is False
    assert "managed writes" in result["error"]
    for tool_name in (
        DUPLICATE_TOOL_NAME,
        PREFAB_TOOL_NAME,
        TEXTURE_TOOL_NAME,
        CONSTRAINT_TOOL_NAME,
        COMPONENT_FEATURE_TOOL_NAME,
        PARAMETER_BIT_PACKING_TOOL_NAME,
        ATOMIC_REFERENCE_RENAME_TOOL_NAME,
    ):
        assert tool_name in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
        assert tool_name in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    for plan_tool in (
        "vrcforge_preview_scene_object_duplicate",
        "vrcforge_preview_scene_object_prefab",
        "vrcforge_preview_texture_import_settings",
        "vrcforge_preview_constraint_sources",
        "vrcforge_preview_component_feature",
        "vrcforge_preview_parameter_bit_packing",
        "vrcforge_preview_atomic_reference_rename",
    ):
        assert plan_tool in dashboard_server.AGENT_GATEWAY._tools

    texture_handler = dashboard_server.AGENT_GATEWAY._write_handlers[
        "vrcforge_set_texture_import_settings"
    ]
    assert texture_handler.risk_level == "medium"
    assert (
        texture_handler.request_preparer
        is dashboard_server.prepare_texture_import_settings_request
    )
    assert texture_handler.handler is dashboard_server.unity_mcp_write_sync
    assert texture_handler.requires_approved_execution_context is True
    assert (
        texture_handler.approved_execution_plan_builder
        is dashboard_server.build_unity_mcp_write_execution_plan
    )

    component_handler = dashboard_server.AGENT_GATEWAY._write_handlers[
        "vrcforge_create_component_feature"
    ]
    assert component_handler.risk_level == "medium"
    assert (
        component_handler.request_preparer
        is dashboard_server.prepare_component_feature_request
    )
    assert component_handler.handler is dashboard_server.unity_mcp_write_sync
    assert component_handler.requires_approved_execution_context is True
    assert (
        component_handler.approved_execution_plan_builder
        is dashboard_server.build_unity_mcp_write_execution_plan
    )
    description = component_handler.description.lower()
    assert "vrcfury" in description
    assert "when to use" in description
    assert "when not to use" in description
    assert "negative example" in description

    write_handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_unity_mcp_write"]
    assert write_handler.manual_approval_resolver is dashboard_server.unity_mcp_manual_approval_reason
    assert (
        write_handler.checkpoint_prepare_handler
        is dashboard_server.prepare_authoritative_unity_checkpoint_sync
    )
    managed_artifact_targets = {
        "vrcforge_capture_screenshot",
        "vrcforge_capture_multi_screenshot",
    }
    for target_name in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS:
        target = dashboard_server.AGENT_GATEWAY._write_handlers[target_name]
        if target_name in managed_artifact_targets:
            assert target.checkpoint_prepare_handler is None
        else:
            assert (
                target.checkpoint_prepare_handler
                is dashboard_server.prepare_authoritative_unity_checkpoint_sync
            )
    for nested_tool in (PARAMETER_BIT_PACKING_TOOL_NAME, ATOMIC_REFERENCE_RENAME_TOOL_NAME):
        assert write_handler.manual_approval_resolver({"toolName": nested_tool}, {})
    assert write_handler.manual_approval_resolver({"toolName": DUPLICATE_TOOL_NAME}, {}) == ""


@pytest.mark.parametrize(
    ("target_name", "arguments", "expected_plan"),
    [
        (
            "vrcforge_toggle_scene_object",
            {"objectPath": "Avatar/Hat", "active": True},
            [
                (
                    "vrc_toggle_scene_object",
                    {"objectPath": "Avatar/Hat", "active": True, "saveAssets": True},
                )
            ],
        ),
        (
            "vrcforge_apply_clothing_fx",
            {"avatarPath": "Assets/Avatar.prefab", "items": [{"name": "Hat"}], "dryRun": False},
            [("vrc_apply_clothing_fx", {"avatarPath": "Assets/Avatar.prefab", "items": [{"name": "Hat"}]})],
        ),
        (
            "vrcforge_apply_parameter_optimization",
            {"avatarPath": "Assets/Avatar.prefab", "suggestions": [{"name": "Hat"}], "dryRun": False},
            [
                (
                    "vrc_apply_parameter_optimization",
                    {"avatarPath": "Assets/Avatar.prefab", "suggestions": [{"name": "Hat"}]},
                )
            ],
        ),
        (
            "vrcforge_restore_safe_backup",
            {"backupId": "backup-1", "assetPaths": ["Assets/Avatar.prefab"]},
            [
                (
                    "vrc_restore_safe_backup",
                    dashboard_server.build_safe_backup_restore_request(
                        {"backupId": "backup-1", "assetPaths": ["Assets/Avatar.prefab"]},
                        True,
                    ),
                )
            ],
        ),
    ],
)
def test_core_write_handlers_are_approval_gated_with_exact_frozen_plans(
    target_name: str,
    arguments: dict[str, object],
    expected_plan: list[tuple[str, dict[str, object]]],
) -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[target_name]
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is not None
    assert handler.approved_execution_plan_builder(arguments) == expected_plan


@pytest.mark.parametrize(
    ("target_name", "arguments"),
    [
        (
            "vrcforge_apply_clothing_fx",
            {"avatarPath": "Assets/Avatar.prefab", "items": [{"name": "Hat"}], "dryRun": True},
        ),
        (
            "vrcforge_apply_parameter_optimization",
            {"avatarPath": "Assets/Avatar.prefab", "suggestions": [{"name": "Hat"}], "dryRun": True},
        ),
    ],
)
def test_tuning_dry_runs_cannot_be_frozen_as_approved_core_plans(
    target_name: str,
    arguments: dict[str, object],
) -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[target_name]
    assert handler.approved_execution_plan_builder is not None
    with pytest.raises(ValueError, match="dry_run=false"):
        handler.approved_execution_plan_builder(arguments)


def test_strict_apply_exposes_only_the_canonical_validated_receipt() -> None:
    transport = dashboard_server.McpResult(
        exit_code=0,
        stdout="untrusted transport text",
        stderr="untrusted transport error",
        payload={
            "data": {"schema": "valid.inner.v1"},
            "spoofed": {"ok": False, "secret": "must-not-cross"},
        },
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport),
        patch(
            "dashboard_server.validate_authoritative_unity_write_result",
            return_value={"schema": "canonical.inner.v1"},
        ) as validate,
    ):
        result = dashboard_server.unity_mcp_write_sync(
            {
                "projectPath": "D:/Project",
                "toolName": PARAMETER_BIT_PACKING_TOOL_NAME,
                "arguments": {},
                "_vrcforge_approved_execution": {"lane": "approved_write"},
            }
        )

    assert result == {
        "ok": True,
        "toolName": PARAMETER_BIT_PACKING_TOOL_NAME,
        "result": {
            "exitCode": 0,
            "stdout": "",
            "stderr": "",
            "payload": {"data": {"schema": "canonical.inner.v1"}},
        },
    }
    assert validate.call_args.args[1] == {"schema": "valid.inner.v1"}


def test_strict_apply_transport_failure_does_not_expose_raw_output() -> None:
    transport = dashboard_server.McpResult(
        exit_code=7,
        stdout="secret stdout must not cross",
        stderr="secret stderr must not cross",
        payload={"secret": "secret payload must not cross"},
    )
    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=transport),
        patch("dashboard_server.validate_authoritative_unity_write_result") as validate,
    ):
        result = dashboard_server.unity_mcp_write_sync(
            {
                "projectPath": "D:/Project",
                "toolName": PARAMETER_BIT_PACKING_TOOL_NAME,
                "arguments": {},
                "_vrcforge_approved_execution": {"lane": "approved_write"},
            }
        )

    assert result["ok"] is False
    assert result["toolName"] == PARAMETER_BIT_PACKING_TOOL_NAME
    assert result["error"] == "The authoritative Unity write transport failed."
    assert result["failureCause"]["kind"] == "transport_failure"
    assert result["commitState"] == "unknown"
    assert "secret" not in repr(result)
    validate.assert_not_called()


@pytest.mark.parametrize(
    "nested_tool",
    [
        ATOMIC_REFERENCE_RENAME_TOOL_NAME,
        CONSTRAINT_TOOL_NAME,
        DUPLICATE_TOOL_NAME,
        PREFAB_TOOL_NAME,
    ],
)
def test_strict_checkpoint_revalidates_canonical_state_without_saving(
    tmp_path: Path,
    nested_tool: str,
) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    approved = {
        "projectPath": str(project.resolve()),
        "toolName": nested_tool,
        "arguments": {
            "operationKind": "parameter",
            "expectedPlanDigest": "a" * 64,
        },
    }
    apply_arguments = {
        **deepcopy(approved),
        "_vrcforge_user_constraints": {
            "status": "ok",
            "path": "<configured>",
            "enabled": True,
        },
    }
    with (
        patch(
            "dashboard_server.prepare_unity_mcp_write_request",
            return_value=(deepcopy(approved), {"schema": "refreshed.v1"}),
        ) as reprepare,
        patch("dashboard_server.prepare_unity_checkpoint_sync") as save_prepare,
    ):
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
            project.resolve(),
            apply_arguments,
        )

    assert result["ok"] is True
    assert result["mode"] == "read_only_authoritative_revalidation"
    assert result["canonicalRevalidated"] is True
    assert reprepare.call_args.args == (approved, None)
    save_prepare.assert_not_called()


def test_strict_checkpoint_blocks_canonical_drift_before_saving(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    approved = {
        "projectPath": str(project.resolve()),
        "toolName": PARAMETER_BIT_PACKING_TOOL_NAME,
        "arguments": {"expectedPreviewDigest": "a" * 64},
    }
    changed = deepcopy(approved)
    changed["arguments"]["expectedPreviewDigest"] = "b" * 64
    with (
        patch(
            "dashboard_server.prepare_unity_mcp_write_request",
            return_value=(changed, {"schema": "refreshed.v1"}),
        ),
        patch("dashboard_server.prepare_unity_checkpoint_sync") as save_prepare,
    ):
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
            project.resolve(),
            deepcopy(approved),
        )

    assert result == {
        "ok": False,
        "error": "The approved Unity state changed before checkpointing.",
    }
    save_prepare.assert_not_called()


def test_existing_unity_writes_keep_the_saving_checkpoint_prepare(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    expected = {"ok": True, "mode": "save_existing_state"}
    with patch(
        "dashboard_server.prepare_unity_checkpoint_sync",
        return_value=expected,
    ) as save_prepare:
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
            project.resolve(),
            {
                "projectPath": str(project.resolve()),
                "toolName": "vrc_set_gameobject_active",
                "arguments": {},
            },
        )

    assert result == expected
    save_prepare.assert_called_once_with(project.resolve())


@pytest.mark.parametrize(
    ("execution_mode", "expected_status"),
    [("auto", "pending"), ("roslyn_full_auto", "executed")],
)
def test_generic_parameter_request_respects_selected_permission_mode(
    tmp_path: Path,
    execution_mode: str,
    expected_status: str,
) -> None:
    gateway = AgentGateway(
        tmp_path / "gateway" / "config.json",
        tmp_path / "gateway" / "audit",
    )
    executed: list[dict] = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_unity_mcp_write",
        "Test Unity wrapper.",
        "high",
        lambda arguments: executed.append(arguments) or {"ok": True},
        request_preparer=lambda arguments, preview: (arguments, preview),
        manual_approval_resolver=dashboard_server.unity_mcp_manual_approval_reason,
    )
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = execution_mode
    config.roslyn_risk_acknowledged = execution_mode == "roslyn_full_auto"
    config.allow_roslyn_advanced = execution_mode == "roslyn_full_auto"
    gateway.save_config(config)
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)

    result = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_unity_mcp_write",
            "arguments": {
                "projectPath": str(project),
                "toolName": PARAMETER_BIT_PACKING_TOOL_NAME,
                "arguments": {},
            },
            "never_auto_approve": False,
            "requires_explicit_approval": False,
        }
    )

    assert result["status"] == expected_status
    if execution_mode == "auto":
        assert result["approval"]["requiresExplicitApproval"] is True
        assert result["approval"]["autoApprovalBlocked"] is True
        assert executed == []
    else:
        assert result["autoApproved"] is True
        assert result["approval"].get("requiresExplicitApproval") is not True
        assert len(executed) == 1
