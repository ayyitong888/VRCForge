from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, install_prepared_calls


def _prepared(project: Path, *, with_scan: bool = True) -> dict:
    calls = []
    if with_scan:
        calls.append(("vrc_scan_avatar_materials", {"avatarPath": "Scene/A", "materialIds": ["m"]}))
    calls.append(("vrc_apply_material_tuning", {
        "avatarPath": "Scene/A",
        "changes": [{"material_id": "m", "semantic_property": "smoothness", "after": 0.8}],
        "saveAssets": True,
    }))
    return install_prepared_calls(
        {"projectPath": str(project), "avatar_path": "Scene/A"}, calls, {"avatarPath": "Scene/A"}
    )


def test_semantic_prepared_checkpoint_revalidates_inner_material_call(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    arguments = _prepared(project)
    with patch("dashboard_server.prepare_unity_checkpoint_sync") as save_prepare:
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(project, arguments)
    assert result["ok"] is True
    assert result["toolName"] == "vrc_apply_material_tuning"
    assert result["mode"] == "sealed_plan_checkpoint"
    assert result["preparedPlanValidated"] is True
    save_prepare.assert_not_called()


def test_invalid_semantic_seal_is_blocked_without_global_checkpoint(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    arguments = _prepared(project, with_scan=False)
    arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]["calls"][0]["toolName"] = "unknown"
    with patch("dashboard_server.prepare_unity_checkpoint_sync") as save_prepare:
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(project, arguments)
    assert result == {"ok": False, "error": "The prepared Unity execution plan is invalid."}
    save_prepare.assert_not_called()


def test_semantic_restore_prepared_checkpoint_uses_the_same_scoped_revalidation(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    arguments = _prepared(project, with_scan=False)
    with patch("dashboard_server.prepare_unity_checkpoint_sync") as save_prepare:
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(project, arguments)
    assert result["ok"] is True
    assert result["toolName"] == "vrc_apply_material_tuning"
    assert result["mode"] == "sealed_plan_checkpoint"
    save_prepare.assert_not_called()


def test_non_material_prepared_checkpoint_keeps_existing_behavior(tmp_path: Path) -> None:
    arguments = install_prepared_calls(
        {"projectPath": str(tmp_path)}, [("vrc_apply_blendshapes", {"changes": []})], {}
    )
    with patch("dashboard_server.prepare_unity_checkpoint_sync", return_value={"ok": True}) as prepare:
        assert dashboard_server.prepare_authoritative_unity_checkpoint_sync(tmp_path, arguments) == {"ok": True}
    prepare.assert_called_once_with(tmp_path)


def test_material_seal_cannot_checkpoint_another_project(tmp_path: Path) -> None:
    with patch("dashboard_server.prepare_unity_checkpoint_sync") as prepare:
        result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(tmp_path / "other", _prepared(tmp_path))
    assert result["ok"] is False
    prepare.assert_not_called()
