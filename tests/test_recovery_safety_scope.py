from pathlib import Path
import zipfile

from agent_gateway import AgentGateway


def scoped_incident(tmp_path):
    project = tmp_path / "project"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}")
    controller = assets / "Recovery.controller"
    controller.write_text("original controller")
    controller.with_suffix(".controller.meta").write_text("guid: stable")
    (assets / "unrelated.txt").write_text("keep unrelated")
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(project, {
        "id": "scoped-original", "projectRoot": str(project),
        "archiveFiles": ["Assets/Recovery.controller"],
    })
    assert checkpoint["ok"]
    recovery = gateway.approval_transactions._start_apply_recovery(
        {"id": "approved-write", "targetTool": "vrcforge_manage_fx_animator"},
        {"projectRoot": str(project)}, checkpoint,
    )
    return gateway, project, controller, checkpoint, recovery


def test_safety_backup_retains_exact_scope_and_current_file_bytes(tmp_path):
    gateway, project, controller, checkpoint, recovery = scoped_incident(tmp_path)
    controller.write_text("partially changed controller")
    result = gateway.checkpoint_recovery._prepare_restore_attempt(checkpoint, recovery)
    assert result["ok"]
    backup = result["safetyBackup"]
    assert backup.get("archiveFiles") == checkpoint["archiveFiles"]
    with zipfile.ZipFile(backup["archivePath"]) as archive:
        assert set(archive.namelist()) == set(checkpoint["archiveFiles"])
        assert archive.read("Assets/Recovery.controller") == b"partially changed controller"
        assert archive.read("Assets/Recovery.controller.meta") == b"guid: stable"
    assert controller.read_text() == "partially changed controller"
    assert (project / "Assets/unrelated.txt").read_text() == "keep unrelated"
    assert result["recovery"]["restoreAttemptCount"] == 1


def test_missing_scoped_file_blocks_safety_backup_without_expanding_or_consuming_attempt(tmp_path):
    gateway, _, controller, checkpoint, recovery = scoped_incident(tmp_path)
    controller.with_suffix(".controller.meta").unlink()
    result = gateway.checkpoint_recovery._prepare_restore_attempt(checkpoint, recovery)
    assert not result["ok"]
    assert result["errorCode"] == "recovery_safety_backup_failed"
    assert not Path(result["safetyBackup"]["archivePath"]).exists()
    current = gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["recoveries"][0]
    assert current.get("restoreAttemptCount", 0) == 0
    assert controller.read_text() == "original controller"
