from __future__ import annotations

from pathlib import Path

import pytest

import dashboard_server
from scene_asset_save import (
    APPROVAL_SCHEMA,
    OPERATION,
    RESULT_SCHEMA,
    TOOL_NAME,
    SceneAssetSaveError,
    bind_authoritative_preview,
    build_wrapper_arguments,
    compute_preview_digest,
    validate_apply_result,
)
from authoritative_unity_writes import (
    AuthoritativeUnityWriteError,
    prepare_authoritative_unity_write,
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
        "projectPath": project.as_posix(),
        "scenePath": "Assets/AvatarAssembly.unity",
        "sceneHandle": 7,
        "sceneName": "Untitled",
        "sceneWasDirty": True,
        "rootObjectCount": 2,
        "sceneHierarchyDigest": "a" * 64,
        "targetExists": False,
        "targetMetaExists": False,
    }
    payload["previewDigest"] = compute_preview_digest(payload)
    return payload


def prepared_request(project: Path) -> tuple[dict, dict]:
    wrapper = build_wrapper_arguments(
        {
            "projectPath": str(project),
            "scenePath": "Assets/AvatarAssembly.unity",
        }
    )
    return bind_authoritative_preview(wrapper, preview_payload(project))


def apply_payload(prepared: dict) -> dict[str, object]:
    arguments = prepared["arguments"]
    return {
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
        "scenePath": arguments["scenePath"],
        "sceneGuid": "b" * 32,
        "sceneHandle": arguments["expectedSceneHandle"],
        "sceneHierarchyDigest": arguments["expectedSceneHierarchyDigest"],
        "sceneFileDigest": "c" * 64,
        "sceneFileIdentity": "d" * 64,
        "sceneMetaDigest": "e" * 64,
        "sceneMetaIdentity": "f" * 64,
        "previewDigest": arguments["expectedPreviewDigest"],
    }


def test_preview_binds_exact_unsaved_scene_and_create_new_destination(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    prepared, approval = prepared_request(tmp_path)

    assert prepared["toolName"] == TOOL_NAME
    assert prepared["arguments"] == {
        "scenePath": "Assets/AvatarAssembly.unity",
        "preview": False,
        "expectedProjectPath": str(tmp_path.resolve()),
        "expectedSceneHandle": 7,
        "expectedSceneName": "Untitled",
        "expectedSceneWasDirty": True,
        "expectedRootObjectCount": 2,
        "expectedSceneHierarchyDigest": "a" * 64,
        "expectedPreviewDigest": preview_payload(tmp_path)["previewDigest"],
    }
    assert approval["schema"] == APPROVAL_SCHEMA
    assert approval["createNew"] is True
    assert approval["overwrite"] is False


def test_preview_accepts_unity_owned_signed_scene_handle(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    wrapper = build_wrapper_arguments(
        {"projectPath": str(tmp_path), "scenePath": "Assets/AvatarAssembly.unity"}
    )
    payload = preview_payload(tmp_path)
    payload["sceneHandle"] = -1
    payload["previewDigest"] = compute_preview_digest(payload)

    prepared, approval = bind_authoritative_preview(wrapper, payload)

    assert prepared["arguments"]["expectedSceneHandle"] == -1
    assert approval["sceneHandle"] == -1


def test_authoritative_preview_failure_reports_exact_contract_reason(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    wrapper = build_wrapper_arguments(
        {"projectPath": str(tmp_path), "scenePath": "Assets/AvatarAssembly.unity"}
    )
    payload = preview_payload(tmp_path)
    payload["targetExists"] = True

    with pytest.raises(
        AuthoritativeUnityWriteError,
        match="Reason: New scene preview did not prove CreateNew destination absence",
    ):
        prepare_authoritative_unity_write(wrapper, None, lambda _tool, _args: payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("targetExists", True),
        ("targetMetaExists", True),
        ("scenePath", "Assets/Changed.unity"),
        ("sceneHierarchyDigest", "9" * 64),
    ],
)
def test_preview_rejects_destination_or_scene_drift(
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
    with pytest.raises(SceneAssetSaveError):
        bind_authoritative_preview(wrapper, payload)


def test_apply_receipt_requires_exact_committed_readback(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    prepared, _approval = prepared_request(tmp_path)
    payload = apply_payload(prepared)
    assert validate_apply_result(prepared["arguments"], payload) == payload

    changed = dict(payload)
    changed["commitState"] = "unknown"
    with pytest.raises(SceneAssetSaveError):
        validate_apply_result(prepared["arguments"], changed)


def test_external_facade_is_medium_risk_checkpointed_and_uses_one_core_write() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_save_new_scene"]
    assert handler.risk_level == "medium"
    assert handler.request_preparer is dashboard_server.prepare_save_new_scene_request
    assert handler.checkpoint_prepare_handler is dashboard_server.prepare_authoritative_unity_checkpoint_sync
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is dashboard_server.build_unity_mcp_write_execution_plan
    assert "when-to-use:" in handler.description
    assert "when-NOT-to-use:" in handler.description
    assert "Negative example:" in handler.description
    assert "vrcforge_save_new_scene" in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
    assert TOOL_NAME in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST


def test_unity_source_saves_existing_unsaved_scene_without_replacing_it() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "SaveNewSceneTool.cs"
    ).read_text(encoding="utf-8-sig")
    assert 'toolId: "vrc_save_new_scene"' in source
    assert "EditorSceneManager.SaveScene(snapshot.Scene, snapshot.ScenePath, false)" in source
    assert "NewScene(" not in source
    assert "AssetDatabase.DeleteAsset" not in source
    assert "AssetOrMetaExists" in source
    assert "Exactly one active project scene" in source
    assert "checkpointRestoreRequired" in source


def test_checkpoint_restore_refuses_to_discard_dirty_scenes() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "CheckpointRecoveryTool.cs"
    ).read_text(encoding="utf-8-sig")
    start = source.index('if (string.Equals(phase, "prepare_restore"')
    end = source.index('if (!string.Equals(phase, "reload"', start)
    prepare_restore = source[start:end]
    assert ".Where(scene => scene.isDirty)" in prepare_restore
    assert "Checkpoint restore cannot discard dirty scenes." in prepare_restore
