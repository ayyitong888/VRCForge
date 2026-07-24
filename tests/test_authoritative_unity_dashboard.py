from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_server
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
    for tool_name in (
        DUPLICATE_TOOL_NAME,
        PREFAB_TOOL_NAME,
        TEXTURE_TOOL_NAME,
        CONSTRAINT_TOOL_NAME,
        COMPONENT_FEATURE_TOOL_NAME,
    ):
        assert tool_name in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
        assert tool_name in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    for plan_tool in (
        "vrcforge_preview_scene_object_duplicate",
        "vrcforge_preview_scene_object_prefab",
        "vrcforge_preview_texture_import_settings",
        "vrcforge_preview_constraint_sources",
        "vrcforge_preview_component_feature",
    ):
        assert plan_tool in dashboard_server.AGENT_GATEWAY._tools
