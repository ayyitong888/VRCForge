from __future__ import annotations

from pathlib import Path

import dashboard_server
import pytest
from authoritative_unity_writes import AuthoritativeUnityWriteError, prepare_authoritative_unity_write
from scene_asset_save_current import (
    APPROVAL_SCHEMA,
    OPERATION,
    RESULT_SCHEMA,
    TOOL_NAME,
    CurrentSceneSaveError,
    bind_authoritative_preview,
    build_wrapper_arguments,
    compute_preview_digest,
    validate_apply_result,
)


def preview_payload(project: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "saved": False,
        "mutationCount": 0,
        "mutationStarted": False,
        "commitState": "not_started",
        "checkpointRestoreRequired": False,
        "manualRecoveryRequired": False,
        "projectPath": project.as_posix(),
        "scenePath": "Assets/AvatarAssembly.unity",
        "sceneGuid": "1" * 32,
        "sceneHandle": 7,
        "sceneName": "AvatarAssembly",
        "sceneWasDirty": True,
        "openSceneCount": 1,
        "rootObjectCount": 3,
        "sceneHierarchyDigest": "a" * 64,
        "sceneFileDigestBefore": "b" * 64,
        "sceneFileIdentityBefore": "c" * 64,
        "sceneMetaDigestBefore": "d" * 64,
        "sceneMetaIdentityBefore": "e" * 64,
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def prepared_request(project: Path) -> tuple[dict, dict]:
    wrapper = build_wrapper_arguments(
        {"projectPath": str(project), "scenePath": "Assets/AvatarAssembly.unity"}
    )
    return bind_authoritative_preview(wrapper, preview_payload(project))


def test_preview_binds_dirty_saved_scene_and_warns_checkpoint_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    prepared, approval = prepared_request(tmp_path)

    assert prepared["toolName"] == TOOL_NAME
    assert prepared["arguments"]["expectedSceneWasDirty"] is True
    assert prepared["arguments"]["expectedOpenSceneCount"] == 1
    assert prepared["arguments"]["expectedSceneFileDigestBefore"] == "b" * 64
    assert approval["schema"] == APPROVAL_SCHEMA
    assert approval["persistsExistingMemoryState"] is True
    assert approval["preSaveCheckpointAvailable"] is False
    assert approval["requiresExplicitUserApproval"] is True


@pytest.mark.parametrize(("field", "value"), [("sceneWasDirty", False), ("openSceneCount", 2), ("scenePath", "Assets/Other.unity")])
def test_preview_rejects_clean_multiple_or_wrong_scene(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    (tmp_path / "Assets").mkdir()
    wrapper = build_wrapper_arguments(
        {"projectPath": str(tmp_path), "scenePath": "Assets/AvatarAssembly.unity"}
    )
    payload = preview_payload(tmp_path)
    payload[field] = value
    payload["previewDigest"] = compute_preview_digest(payload)
    with pytest.raises(CurrentSceneSaveError):
        bind_authoritative_preview(wrapper, payload)


def test_authoritative_preview_failure_preserves_non_mutating_failure_details(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    wrapper = build_wrapper_arguments(
        {"projectPath": str(tmp_path), "scenePath": "Assets/AvatarAssembly.unity"}
    )
    payload = {"ok": False, "code": "scene_clean", "message": "The active scene is clean."}
    with pytest.raises(AuthoritativeUnityWriteError) as captured:
        prepare_authoritative_unity_write(wrapper, None, lambda _tool, _args: payload)
    assert captured.value.details["failureLayer"] == "unity_core_preview"
    assert captured.value.details["mutationStarted"] is False
    assert captured.value.details["commitState"] == "not_started"


def test_external_preview_rejection_preserves_core_reason_at_gateway_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Assets").mkdir()
    core_rejection = {
        "ok": False,
        "code": "scene_clean",
        "message": "The active scene is clean.",
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
    }
    monkeypatch.setattr(
        dashboard_server,
        "_invoke_authoritative_unity_preview",
        lambda _request, _tool_name, _preview_arguments: core_rejection,
    )

    with pytest.raises(dashboard_server.AgentGatewayError) as captured:
        dashboard_server.prepare_save_current_scene_request(
            {"projectPath": str(tmp_path)},
            None,
        )

    rejected = dashboard_server.AGENT_GATEWAY._external_mcp_no_write_error(
        "vrcforge_save_current_scene",
        "write_preparation",
        captured.value,
    )
    details = rejected["errorDetails"]
    assert details["failureLayer"] == "unity_core_preview"
    assert details["failurePhase"] == "preview_rejected"
    assert details["errorCode"] == "scene_clean"
    assert details["mutationStarted"] is False
    assert details["committed"] is False
    assert details["commitState"] == "not_started"
    assert details["rawResult"]["message"] == "The active scene is clean."


def test_apply_receipt_requires_committed_clean_scene_and_unchanged_metadata(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    prepared, _approval = prepared_request(tmp_path)
    arguments = prepared["arguments"]
    payload = {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "mutationCount": 1,
        "mutationStarted": True,
        "commitState": "committed",
        "checkpointRestoreRequired": False,
        "manualRecoveryRequired": False,
        "sceneIsDirty": False,
        "scenePath": arguments["scenePath"],
        "sceneGuid": arguments["expectedSceneGuid"],
        "sceneHierarchyDigest": arguments["expectedSceneHierarchyDigest"],
        "sceneFileDigestAfter": "f" * 64,
        "sceneFileIdentityAfter": "0" * 64,
        "sceneMetaDigestAfter": arguments["expectedSceneMetaDigestBefore"],
        "sceneMetaIdentityAfter": arguments["expectedSceneMetaIdentityBefore"],
        "previewDigest": arguments["expectedPreviewDigest"],
    }
    assert validate_apply_result(arguments, payload) == payload

    changed = dict(payload)
    changed["sceneIsDirty"] = True
    with pytest.raises(CurrentSceneSaveError):
        validate_apply_result(arguments, changed)


def test_external_facade_exposes_high_risk_current_scene_save() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_save_current_scene"]
    assert TOOL_NAME == "vrc_save_current_scene"
    assert handler.risk_level == "high"
    assert handler.request_preparer is dashboard_server.prepare_save_current_scene_request
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is dashboard_server.build_unity_mcp_write_execution_plan
    assert "when-to-use:" in handler.description
    assert "when-NOT-to-use:" in handler.description
    assert "Negative example:" in handler.description
    assert "vrcforge_save_current_scene" in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
    assert TOOL_NAME in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    tools = dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
        exposure_layer="execution",
        tool_blocks=["avatar"],
    )
    current_scene_save = next(
        tool
        for tool in tools
        if tool["name"] == "vrcforge_save_current_scene"
    )
    assert current_scene_save["inputSchema"]["required"] == ["projectPath"]
    assert current_scene_save["inputSchema"]["additionalProperties"] is False


def test_unity_source_has_distinct_current_scene_save_without_loosening_create_new() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "SaveNewSceneTool.cs"
    ).read_text(encoding="utf-8-sig")
    assert 'toolId: "vrc_save_current_scene"' in source
    assert "public static class SaveCurrentSceneTool" in source
    assert "EditorSceneManager.SaveScene(snapshot.Scene, snapshot.ScenePath, false)" in source
    create_new_start = source.index("public static class SaveNewSceneTool")
    current_start = source.index("public static class SaveCurrentSceneTool")
    create_new = source[create_new_start:current_start]
    assert "The active scene is already saved" in create_new
    assert "AssetDatabase.DeleteAsset" not in source
    assert "manualRecoveryRequired" in source[current_start:]
