"""Restore preparation receives the stored disk footprint, never caller scope."""
from pathlib import Path

import pytest

from agent_gateway import AgentGateway


@pytest.mark.parametrize("incident", [False, True])
@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("prepare_ok", [False, True])
def test_restore_prepare_receives_checkpoint_scope(tmp_path, incident, exact, prepare_ok):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    owner = gateway.checkpoint_recovery
    project = tmp_path / "project"
    controller_path = "Assets/Generated/Recovery_Work.controller"
    controller = project / controller_path
    controller.parent.mkdir(parents=True)
    controller.write_bytes(b"checkpoint")
    Path(str(controller) + ".meta").write_bytes(b"metadata")
    other = project / "Assets/Unrelated.shader"
    other.write_bytes(b"unrelated")
    approval = {"id": "scope-approval", "targetTool": "vrcforge_test_write", "riskLevel": "high"}
    record = {
        "schema": "vrcforge.checkpoint.v1", "id": "scope-checkpoint",
        "createdAt": "2026-10-17T00:00:00+00:00", "projectRoot": str(project),
        "targetTool": approval["targetTool"],
        **({"archiveFiles": [controller_path]} if exact else {}),
    }
    checkpoint = owner._create_archive_checkpoint(project, record)
    assert checkpoint["ok"]
    expected = [controller_path, controller_path + ".meta"] if exact else None
    if incident:
        gateway.approval_transactions._start_apply_recovery(
            approval, {"projectRoot": str(project)}, checkpoint
        )
    controller.write_bytes(b"current")
    calls = []

    def prepare(root, *, checkpoint_files):
        calls.append((root, checkpoint_files))
        return {"ok": prepare_ok, "scenes": [], "activeScenePath": "", "error": "dirty scene" if not prepare_ok else ""}

    owner.checkpoint_restore_prepare_handler = prepare
    owner.checkpoint_restore_handler = lambda _root, _context: {"ok": True}
    result = owner.restore_checkpoint({
        "checkpointId": checkpoint["id"], "confirmRestore": True,
        # Public JSON must not narrow the trusted footprint or fake preparation.
        "checkpoint_files": [], "archiveFiles": ["Assets/Unrelated.shader"],
        "_restorePrepareForRecovery": {"ok": True},
    })
    assert calls == [(project, expected)]
    assert result["ok"] is prepare_ok, result
    assert controller.read_bytes() == (b"checkpoint" if prepare_ok else b"current")
    assert other.read_bytes() == b"unrelated"
    if not prepare_ok:
        assert result["status"] == "restore_prepare_failed"
        assert not list(owner._ports.checkpoint_store_dir().rglob("restore_safety_*.zip"))
        if incident:
            recovery = owner.list_interrupted_apply_recoveries()["recoveries"][0]
            assert recovery.get("restoreAttemptCount", 0) == 0
