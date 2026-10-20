"""Real exact-footprint ZIP rollback, including create-only and Windows locks."""
from pathlib import Path
import os
import zipfile

import pytest

from agent_gateway import AgentGateway
from test_checkpoint_archive_atomic_restore import deny_replace


def case(tmp_path, existing=()):
    project = tmp_path / "project"
    (project / "Assets").mkdir(parents=True)
    (project / "Assets/Unrelated.txt").write_bytes(b"keep")
    for name in existing:
        (project / name).write_bytes(b"before")
    service = AgentGateway(tmp_path / "config.json", tmp_path / "audit").checkpoint_recovery
    record = {"id": "ckpt_footprint", "createdAt": "2026-10-18T00:00:00+00:00",
              "targetTool": "vrcforge_import_outfit_package", "projectRoot": str(project), "archiveFiles": list(existing),
              "archiveAbsentFiles": ["Assets/New/A.txt", "Assets/New/A.txt.meta"],
              "archiveAbsentDirectories": ["Assets/New"],
              "assetFootprint": {"schema": "vrcforge.asset_checkpoint_footprint.v1"}}
    return service, project, record


def create(service, project, record):
    checkpoint = service._create_archive_checkpoint(project, record)
    assert checkpoint["ok"], checkpoint
    return checkpoint


def capture(project, checkpoint):
    from checkpoint_asset_footprint import capture_created_asset_post_state
    return capture_created_asset_post_state(project, checkpoint)


def write_new(project):
    (project / "Assets/New").mkdir()
    (project / "Assets/New/A.txt").write_bytes(b"created")
    (project / "Assets/New/A.txt.meta").write_bytes(b"created-meta")


def test_create_only_empty_zip_and_exact_restore_preserve_unrelated(tmp_path):
    service, project, record = case(tmp_path)
    checkpoint = create(service, project, record)
    assert checkpoint["checkpointScope"]["kind"] == "unity_project_asset_footprint"
    assert checkpoint["archiveFiles"] == []
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert archive.namelist() == []
    write_new(project)
    checkpoint = capture(project, checkpoint)
    assert service._checkpoint_available(checkpoint)["ok"]
    result = service._restore_archive_checkpoint(checkpoint)
    assert result["ok"], result
    assert not (project / "Assets/New").exists()
    assert (project / "Assets/Unrelated.txt").read_bytes() == b"keep"


def test_mixed_exact_files_no_automatic_meta_and_unknown_directory_content_kept(tmp_path):
    service, project, record = case(tmp_path, ["Assets/Existing.mat", "Assets/Existing.mat.meta"])
    checkpoint = create(service, project, record)
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert set(archive.namelist()) == set(record["archiveFiles"])
    (project / "Assets/Existing.mat").write_bytes(b"changed")
    write_new(project)
    checkpoint = capture(project, checkpoint)
    (project / "Assets/New/User.txt").write_bytes(b"foreign")
    result = service._restore_archive_checkpoint(checkpoint)
    assert result["ok"], result
    assert (project / "Assets/Existing.mat").read_bytes() == b"before"
    assert not (project / "Assets/New/A.txt").exists()
    assert (project / "Assets/New/User.txt").read_bytes() == b"foreign"


@pytest.mark.parametrize("mutation", ["changed", "missing_witness", "missing_key", "was_absent"])
def test_changed_created_file_or_missing_witness_rejects_whole_restore(tmp_path, mutation):
    service, project, record = case(tmp_path, ["Assets/Existing.mat"])
    checkpoint = create(service, project, record)
    (project / "Assets/Existing.mat").write_bytes(b"changed-existing")
    write_new(project)
    if mutation == "was_absent":
        (project / "Assets/New/A.txt").unlink()
    if mutation != "missing_witness":
        checkpoint = capture(project, checkpoint)
    if mutation == "changed":
        (project / "Assets/New/A.txt").write_bytes(b"user-content")
    elif mutation == "missing_key":
        checkpoint["createdAssetPostState"].pop("Assets/New/A.txt.meta")
    elif mutation == "was_absent":
        (project / "Assets/New/A.txt").write_bytes(b"user-created-later")
    result = service._restore_archive_checkpoint(checkpoint)
    assert not result["ok"], result
    assert (project / "Assets/Existing.mat").read_bytes() == b"changed-existing"
    assert (project / "Assets/New/A.txt.meta").read_bytes() == b"created-meta"


@pytest.mark.parametrize("bad", ["missing_existing_manifest", "missing_absent_manifest", "escape", "backslash", "duplicate", "overlap", "present_absent", "present_directory", "missing_existing", "directory_as_file"])
def test_invalid_manifest_fails_without_full_project_fallback(tmp_path, bad):
    service, project, record = case(tmp_path)
    if bad == "missing_existing_manifest": record.pop("archiveFiles")
    elif bad == "missing_absent_manifest": record.pop("archiveAbsentFiles")
    elif bad == "escape": record["archiveAbsentFiles"] = ["Assets/../outside"]
    elif bad == "backslash": record["archiveAbsentFiles"] = ["Assets\\New\\A"]
    elif bad == "duplicate": record["archiveAbsentFiles"] *= 2
    elif bad == "overlap": record["archiveFiles"] = ["Assets/Unrelated.txt"]; record["archiveAbsentFiles"] = ["Assets/Unrelated.txt"]
    elif bad == "present_absent": record["archiveAbsentFiles"] = ["Assets/Unrelated.txt"]
    elif bad == "present_directory": record["archiveAbsentDirectories"] = ["Assets"]
    elif bad == "missing_existing": record["archiveFiles"] = ["Assets/Missing.mat"]
    else: record["archiveFiles"] = ["Assets"]
    checkpoint = service._create_archive_checkpoint(project, record)
    assert checkpoint["ok"] is False, checkpoint
    assert not Path(checkpoint["archivePath"]).exists()
    assert (project / "Assets/Unrelated.txt").read_bytes() == b"keep"


def test_linked_parent_is_rejected_without_touching_link_target(tmp_path):
    service, project, record = case(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (project / "Assets/New").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable for this Windows account")
    result = service._create_archive_checkpoint(project, record)
    assert not result["ok"]
    assert list(outside.iterdir()) == []


def test_locked_created_file_rolls_back_all_existing_and_new_files(tmp_path):
    if os.name != "nt": pytest.skip("Requires real Windows delete-sharing denial")
    service, project, record = case(tmp_path, ["Assets/0-Existing.mat"])
    checkpoint = create(service, project, record)
    (project / "Assets/0-Existing.mat").write_bytes(b"current-existing")
    write_new(project)
    checkpoint = capture(project, checkpoint)
    with deny_replace(project / "Assets/New/A.txt.meta"):
        result = service._restore_archive_checkpoint(checkpoint)
    assert not result["ok"], result
    assert (project / "Assets/0-Existing.mat").read_bytes() == b"current-existing"
    assert (project / "Assets/New/A.txt").read_bytes() == b"created"
    assert (project / "Assets/New/A.txt.meta").read_bytes() == b"created-meta"
    assert not list((project / "Assets").rglob("*.tmp"))


def test_overwrite_only_scope_and_empty_manifest_do_not_fallback(tmp_path):
    service, project, record = case(tmp_path, ["Assets/Existing.mat"])
    record.update(archiveAbsentFiles=[], archiveAbsentDirectories=[])
    checkpoint = create(service, project, record)
    assert checkpoint["archiveFiles"] == ["Assets/Existing.mat"]
    (project / "Assets/Existing.mat").write_bytes(b"changed")
    checkpoint = capture(project, checkpoint)
    assert checkpoint["createdAssetPostState"] == {}
    assert service._restore_archive_checkpoint(checkpoint)["ok"]
    assert (project / "Assets/Existing.mat").read_bytes() == b"before"
    empty = {**record, "id": "ckpt_empty", "archiveFiles": [], "checkpointScope": {}}
    assert service._create_archive_checkpoint(project, empty)["ok"] is False


def test_preview_and_safety_backup_remain_exact_and_user_can_restore_safety_point(tmp_path):
    service, project, record = case(tmp_path, ["Assets/Existing.mat"])
    (project / "Assets/Unrelated.unity").write_bytes(b"unrelated scene")
    checkpoint = create(service, project, record)
    (project / "Assets/Existing.mat").write_bytes(b"after")
    write_new(project)
    checkpoint = capture(project, checkpoint)
    service._append_checkpoint(checkpoint)
    preview = service._preview_archive_checkpoint(checkpoint)
    assert preview["ok"], preview
    assert not any("Unrelated" in name for name in preview["changedFiles"])
    assert service._archive_reload_scene_error(checkpoint, ["Assets/Unrelated.unity"]) is None
    service._append_apply_recovery_entry({"id": "recovery_footprint", "status": "needs_recovery",
        "checkpointId": checkpoint["id"], "projectRoot": str(project), "checkpoint": checkpoint})
    prepared_scopes = []
    def prepare(_project, **options):
        prepared_scopes.append(options["checkpoint_files"])
        return {"ok": True, "saved": False, "mutationStarted": False}
    service.checkpoint_restore_prepare_handler = prepare
    service.checkpoint_restore_handler = lambda _project, _context: {"ok": True}
    result = service.restore_checkpoint({"checkpointId": checkpoint["id"], "recoveryId": "recovery_footprint", "confirmRestore": True})
    assert result["ok"], result
    assert prepared_scopes and all(set(paths) == {"Assets/Existing.mat", "Assets/New/A.txt", "Assets/New/A.txt.meta"} for paths in prepared_scopes)
    recovery = service.list_interrupted_apply_recoveries({"includeResolved": True})["recoveries"][0]
    safety = service._load_checkpoint(recovery["restoreSafetyCheckpointId"])
    assert safety["checkpointScope"]["kind"] == "unity_project_asset_footprint"
    assert set(safety["archiveFiles"]) == {"Assets/Existing.mat", "Assets/New/A.txt", "Assets/New/A.txt.meta"}
    assert "createdAssetPostState" in safety
    assert service._restore_archive_checkpoint(safety)["ok"]
    assert (project / "Assets/Existing.mat").read_bytes() == b"after"
    assert (project / "Assets/New/A.txt").read_bytes() == b"created"


@pytest.mark.parametrize("bad", ["no_schema", "empty_existing_only", "reserved", "extra_zip_member", "extra_witness"])
def test_explicit_scope_and_manifest_tampering_rejected(tmp_path, bad):
    service, project, record = case(tmp_path)
    if bad == "no_schema":
        record.pop("assetFootprint")
    elif bad == "empty_existing_only":
        record = {"id": record["id"], "projectRoot": str(project), "archiveFiles": []}
    elif bad == "reserved":
        record["archiveAbsentFiles"] = ["Assets/CON"]
    if bad in {"no_schema", "empty_existing_only", "reserved"}:
        assert service._create_archive_checkpoint(project, record)["ok"] is False
        return
    checkpoint = create(service, project, record)
    write_new(project)
    checkpoint = capture(project, checkpoint)
    if bad == "extra_zip_member":
        with zipfile.ZipFile(checkpoint["archivePath"], "a") as archive:
            archive.writestr("Assets/Unrelated.txt", b"malicious overwrite")
    else:
        checkpoint["createdAssetPostState"]["Assets/Unrelated.txt"] = None
    assert service._restore_archive_checkpoint(checkpoint)["ok"] is False
    assert (project / "Assets/Unrelated.txt").read_bytes() == b"keep"
    assert (project / "Assets/New/A.txt").read_bytes() == b"created"
