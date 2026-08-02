from __future__ import annotations

from pathlib import Path

import pytest

import dashboard_server as dashboard
import prepared_file_imports
from chat_attachment_vault import ChatAttachmentVault
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _prepared(monkeypatch, tmp_path: Path):
    vault = ChatAttachmentVault(tmp_path / "vault")
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
    stored = vault.ingest(data=image_bytes, name="Ref Image.png", declared_type="image/png", chat_id="chat")
    project = _project(tmp_path)
    monkeypatch.setattr(dashboard, "chat_attachment_vault_store", lambda: vault)
    prepared, _ = dashboard.prepare_import_chat_image_request({
        "payloadHash": stored["payloadHash"],
        "projectPath": str(project),
        "targetFolder": "Assets/VRCForge/Imports",
    }, {})
    return vault, stored, project, prepared


def test_chat_image_preparer_seals_exact_refresh_call(monkeypatch, tmp_path: Path) -> None:
    _vault, _stored, project, prepared = _prepared(monkeypatch, tmp_path)
    assert build_prepared_execution_plan(prepared) == [(
        "vrc_refresh_asset_database",
        {"projectPath": str(project), "resolvePackages": False, "packageResolveTimeoutSeconds": 120},
    )]


def test_chat_image_target_race_does_not_overwrite(monkeypatch, tmp_path: Path) -> None:
    _vault, _stored, project, prepared = _prepared(monkeypatch, tmp_path)
    target = project / "Assets" / "VRCForge" / "Imports" / "Ref_Image.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"must-survive")
    monkeypatch.setattr(dashboard, "refresh_asset_database_sync", lambda _params: pytest.fail("refresh must not run"))

    with pytest.raises(ValueError, match="appeared after approval"):
        dashboard.import_chat_image_sync(prepared)
    assert target.read_bytes() == b"must-survive"


def test_chat_image_absent_parent_appearing_after_approval_blocks_write(monkeypatch, tmp_path: Path) -> None:
    _vault, _stored, project, prepared = _prepared(monkeypatch, tmp_path)
    (project / "Assets" / "VRCForge" / "Imports").mkdir(parents=True)
    monkeypatch.setattr(dashboard, "refresh_asset_database_sync", lambda _params: pytest.fail("refresh must not run"))

    with pytest.raises(ValueError, match="absent import parent appeared"):
        dashboard.import_chat_image_sync(prepared)
    assert not (project / "Assets" / "VRCForge" / "Imports" / "Ref_Image.png").exists()


def test_chat_image_create_new_collision_never_deletes_foreign_file(monkeypatch, tmp_path: Path) -> None:
    _vault, _stored, project, prepared = _prepared(monkeypatch, tmp_path)
    original = dashboard.copy_approved_file_create_new

    def race(**kwargs):
        target = Path(str(kwargs["project_identity"]["path"])) / str(kwargs["target_relative_path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(b"foreign")
        return original(
            **{
                **kwargs,
                "parent_identities": [prepared_file_imports.capture_directory(target.parent, label="Import target parent")],
                "absent_parent_relative_paths": [],
            }
        )

    monkeypatch.setattr(dashboard, "copy_approved_file_create_new", race)
    monkeypatch.setattr(dashboard, "refresh_asset_database_sync", lambda _params: pytest.fail("refresh must not run"))

    with pytest.raises(ValueError, match="approval-bound import target appeared after approval"):
        dashboard.import_chat_image_sync(prepared)
    target = project / "Assets" / "VRCForge" / "Imports" / "Ref_Image.png"
    assert target.read_bytes() == b"foreign"


def test_chat_image_source_drift_makes_zero_project_writes(monkeypatch, tmp_path: Path) -> None:
    vault, stored, project, prepared = _prepared(monkeypatch, tmp_path)
    source = vault.root / "files" / f"{stored['payloadHash']}.png"
    source.write_bytes(b"changed")
    monkeypatch.setattr(dashboard, "refresh_asset_database_sync", lambda _params: pytest.fail("refresh must not run"))

    with pytest.raises(ValueError, match="drifted"):
        dashboard.import_chat_image_sync(prepared)
    assert not (project / "Assets" / "VRCForge").exists()


def test_chat_image_refresh_failure_cleans_owned_file(monkeypatch, tmp_path: Path) -> None:
    _vault, _stored, project, prepared = _prepared(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "refresh_asset_database_sync", lambda _params: {"ok": False, "error": "refresh failed"})

    with pytest.raises(RuntimeError, match="refresh failed"):
        dashboard.import_chat_image_sync(prepared)
    assert not (project / "Assets" / "VRCForge" / "Imports" / "Ref_Image.png").exists()


def test_chat_image_success_reports_hash_and_asset_path(monkeypatch, tmp_path: Path) -> None:
    _vault, stored, project, prepared = _prepared(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "refresh_asset_database_sync", lambda _params: {"ok": True, "refreshed": True})

    result = dashboard.import_chat_image_sync(prepared)
    assert result["ok"] is True
    assert result["copiedSha256"] == stored["payloadHash"]
    assert (project / result["assetPath"]).read_bytes().endswith(b"image-bytes")


def test_chat_image_preparer_rejects_reserved_injection(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard.prepare_import_chat_image_request({
            "payloadHash": "0" * 64,
            "projectPath": str(project),
            PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {},
        }, {})
