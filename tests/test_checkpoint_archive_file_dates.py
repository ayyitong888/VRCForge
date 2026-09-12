"""Imported files may carry timestamps outside the DOS ZIP date range."""
import os
import zipfile
from pathlib import Path

import pytest

from agent_approval_transactions import _checkpoint_archive_files_for_write
from agent_gateway import AgentGateway


@pytest.mark.parametrize("scope", ["project", "local_state"])
@pytest.mark.parametrize("timestamp, zip_year", [(0, 1980), (7258118400, 2107)])
def test_checkpoint_keeps_file_bytes_with_out_of_range_dates(tmp_path, scope, timestamp, zip_year):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.checkpoint_recovery
    project = tmp_path / "Project"
    if scope == "project":
        source = project / "Assets" / "Imported.txt"
        member = "Assets/Imported.txt"
    else:
        source = service._local_state_checkpoint_roots()["skills"] / "fixture" / "SKILL.md"
        member = "skills/fixture/SKILL.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    content = b"Imported content must survive checkpointing.\x00\xff"
    source.write_bytes(content)
    os.utime(source, (timestamp, timestamp))
    original_mtime = source.stat().st_mtime_ns
    record = {"id": "ckpt_import_dates", "projectRoot": str(project), "status": "unavailable"}
    checkpoint = (
        service._create_archive_checkpoint(project, record)
        if scope == "project" else service._create_local_state_checkpoint(record)
    )
    assert checkpoint["ok"] is True, checkpoint.get("error")
    assert checkpoint["status"] == "ready"
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert archive.testzip() is None
        assert archive.read(member) == content
        assert archive.getinfo(member).date_time[0] == zip_year
    assert source.read_bytes() == content
    assert source.stat().st_mtime_ns == original_mtime


def test_archive_checkpoint_records_scope_bytes_and_independent_timing(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Assets" / "A.txt").write_text("A", encoding="utf-8")
    record = {"id": "ckpt_timing", "projectRoot": str(project), "status": "unavailable"}

    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(project, record)

    assert checkpoint["ok"] is True
    assert checkpoint["checkpointScope"] == {
        "kind": "unity_project_top_level",
        "pathspecs": ["Assets"],
    }
    assert checkpoint["fileCount"] == 1
    assert checkpoint["uncompressedBytes"] == 1
    assert checkpoint["archiveBytes"] == Path(checkpoint["archivePath"]).stat().st_size
    assert checkpoint["archiveElapsedMs"] >= 0
    assert checkpoint["archiveStartedAt"]
    assert checkpoint["archiveFinishedAt"]


def test_batch_checkpoint_can_archive_exact_assets_without_project_snapshot(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    target = project / "Assets" / "Generated" / "clip.anim"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"clip-before")
    (target.with_name(target.name + ".meta")).write_text("guid: clip-guid\n", encoding="utf-8")
    unrelated = project / "Assets" / "unrelated-large.bin"
    unrelated.write_bytes(b"unrelated" * 1000)
    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(
        project,
        {
            "id": "ckpt_exact_assets",
            "projectRoot": str(project),
            "status": "unavailable",
            "targetTool": "vrcforge_write_animation_curve",
            "archiveFiles": ["Assets/Generated/clip.anim"],
        },
    )
    assert checkpoint["ok"] is True
    assert checkpoint["checkpointScope"]["kind"] == "unity_project_files"
    assert checkpoint["fileCount"] == 2
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert sorted(archive.namelist()) == ["Assets/Generated/clip.anim", "Assets/Generated/clip.anim.meta"]
    target.write_bytes(b"clip-after")
    unrelated.write_bytes(b"unrelated-after")
    preview = gateway.checkpoint_recovery._preview_archive_checkpoint(checkpoint)
    assert preview["ok"] is True
    assert preview["changedFiles"] == ["M\tAssets/Generated/clip.anim"]
    restored = gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)
    assert restored["ok"] is True
    assert target.read_bytes() == b"clip-before"
    assert unrelated.read_bytes() == b"unrelated-after"


def test_batch_checkpoint_scope_requires_complete_valid_batch_shape():
    clips = [{"clipPath": f"Assets/Wardrobe/{index}.anim"} for index in range(32)]
    assert _checkpoint_archive_files_for_write(
        "vrcforge_write_animation_curve", {"clips": clips}
    ) == sorted(f"Assets/Wardrobe/{index}.anim" for index in range(32))
    assert _checkpoint_archive_files_for_write(
        "vrcforge_manage_fx_animator",
        {"controllerPath": "Assets/Wardrobe/FX.controller", "edits": [{"action": "update"}, {"motionClipPath": "Assets/Wardrobe/a.anim"}]},
    ) == ["Assets/Wardrobe/FX.controller", "Assets/Wardrobe/a.anim"]
    assert _checkpoint_archive_files_for_write(
        "vrcforge_write_animation_curve", {"clips": clips[:-1] + [{"clipPath": "../outside.anim"}]}
    ) == []


def test_missing_batch_target_falls_back_to_full_project_archive(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Assets" / "existing.txt").write_text("keep", encoding="utf-8")
    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(
        project,
        {
            "id": "ckpt_missing_target",
            "projectRoot": str(project),
            "status": "unavailable",
            "targetTool": "vrcforge_write_animation_curve",
            "archiveFiles": ["Assets/missing.anim"],
        },
    )
    assert checkpoint["checkpointScope"]["kind"] == "unity_project_top_level"
    assert checkpoint["fileCount"] == 1

    mixed = gateway.checkpoint_recovery._create_archive_checkpoint(
        project,
        {
            "id": "ckpt_mixed_target",
            "projectRoot": str(project),
            "status": "unavailable",
            "targetTool": "vrcforge_write_animation_curve",
            "archiveFiles": ["Assets/existing.txt", "Assets/missing.anim"],
        },
    )
    assert mixed["checkpointScope"]["kind"] == "unity_project_top_level"
    assert "archiveFiles" not in mixed
    assert "archiveFiles" not in checkpoint


def test_scoped_checkpoint_audit_does_not_claim_project_wide_coverage(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    target = project / "Assets" / "clip.anim"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"clip")
    target.with_name("clip.anim.meta").write_text("guid: clip-guid\n", encoding="utf-8")
    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(
        project,
        {"id": "ckpt_scoped_audit", "projectRoot": str(project), "status": "unavailable", "archiveFiles": ["Assets/clip.anim"]},
    )
    audit = checkpoint["rollbackCoverageAudit"]
    checks = {check["id"]: check for check in audit["checks"]}
    assert checks["scene_prefab_component_state"]["coverageScope"] == "exact_files"
    assert checks["generated_assets"]["coverageScope"] == "exact_files"
    assert checks["packages_manifest"]["status"] == "not_applicable"
    assert checks["project_settings"]["status"] == "not_applicable"

    assert "packages_manifest" not in audit["blockingGaps"]
    assert "project_settings" not in audit["blockingGaps"]
    assert not gateway.checkpoint_recovery._checkpoint_touches_top_level(checkpoint, "Packages")
    assert not gateway.checkpoint_recovery._checkpoint_touches_top_level(checkpoint, "ProjectSettings")
