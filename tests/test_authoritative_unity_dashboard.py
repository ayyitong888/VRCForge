from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_server
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


def test_new_write_protocols_are_required_allowlisted_and_registered() -> None:
    for tool_name in (DUPLICATE_TOOL_NAME, PREFAB_TOOL_NAME, TEXTURE_TOOL_NAME):
        assert tool_name in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
        assert tool_name in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    for plan_tool in (
        "vrcforge_preview_scene_object_duplicate",
        "vrcforge_preview_scene_object_prefab",
        "vrcforge_preview_texture_import_settings",
    ):
        assert plan_tool in dashboard_server.AGENT_GATEWAY._tools
