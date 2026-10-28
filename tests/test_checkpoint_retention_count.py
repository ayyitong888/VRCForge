"""Count and byte retention share the existing archive cleanup and protections."""
import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from agent_gateway import AgentGateway, CHECKPOINT_ARCHIVE_DEFAULT_MAX_COUNT
from test_external_checkpoint_choice import fixture_gateway, arguments, call


def configure(gateway, count, size=0):
    config = gateway.ensure_config()
    config.checkpoint_archive_max_count = count
    config.checkpoint_archive_max_size_mb = size
    gateway.save_config(config)


def archives(gateway, count, *, old=True):
    folder = gateway.checkpoint_store_dir / "fixture"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        p = folder / f"ckpt_retained_{i}.zip"
        with zipfile.ZipFile(p, "w") as archive:
            archive.writestr("Assets/value.txt", str(i))
        timestamp = time.time() - 1800 + i if old else time.time() + i
        os.utime(p, (timestamp, timestamp))
        paths.append(p)
    return paths


def test_default_count_is_four_and_configuration_survives_reload(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    assert gateway.ensure_config().checkpoint_archive_max_count == CHECKPOINT_ARCHIVE_DEFAULT_MAX_COUNT == 4
    configure(gateway, 7)
    reopened = AgentGateway(gateway.config_path, tmp_path / "audit")
    assert reopened.ensure_config().checkpoint_archive_max_count == 7
    configure(reopened, 0)
    assert AgentGateway(gateway.config_path, tmp_path / "audit").ensure_config().checkpoint_archive_max_count == 0
    doc = json.loads(gateway.config_path.read_text())
    doc.pop("checkpoint_archive_max_count")
    gateway.config_path.write_text(json.dumps(doc))
    assert AgentGateway(gateway.config_path, tmp_path / "audit").ensure_config().checkpoint_archive_max_count == 4


@pytest.mark.parametrize("cap", [1, 2, 4])
def test_count_only_prunes_oldest_real_archives(tmp_path, cap):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    configure(gateway, cap)
    paths = archives(gateway, 5)
    result = gateway.checkpoint_recovery.prune_checkpoint_archives()
    assert result["deletedCount"] == 5 - cap
    assert result["archiveCount"] == cap
    assert result["maxCount"] == cap
    assert [p.exists() for p in paths] == [False] * (5 - cap) + [True] * cap
    for p in paths[-cap:]:
        with zipfile.ZipFile(p) as archive:
            assert archive.namelist() == ["Assets/value.txt"]
    assert gateway.checkpoint_recovery.checkpoint_archive_usage()["overCountLimit"] is False


def test_zero_disables_count_limit_and_size_policy_remains_available(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    configure(gateway, 0)
    paths = archives(gateway, 6)
    assert gateway.checkpoint_recovery.prune_checkpoint_archives()["deletedCount"] == 0
    assert all(p.exists() for p in paths)
    # Existing byte-limit path still enforces its own quota when count is unlimited.
    for p in paths:
        with p.open("ab") as f: f.write(b"x" * 600_000)
        timestamp = time.time() - 1800 + paths.index(p)
        os.utime(p, (timestamp, timestamp))
    result = gateway.checkpoint_recovery.prune_checkpoint_archives(max_size_mb=1)
    assert result["deletedCount"] == 4
    assert result["archiveCount"] == 2  # Existing latest-two byte-quota protection.


def test_active_recovery_and_recent_operations_report_count_overage(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    configure(gateway, 1)
    paths = archives(gateway, 3)
    gateway.checkpoint_recovery._append_apply_recovery_entry({
        "id": "active", "checkpointId": paths[0].stem, "status": "needs_recovery"})
    result = gateway.checkpoint_recovery.prune_checkpoint_archives()
    assert result["archiveCount"] == 2 and result["overCountLimit"] is True
    assert paths[0].exists() and paths[2].exists() and not paths[1].exists()
    assert gateway.checkpoint_recovery.checkpoint_archive_usage()["overCountLimit"] is True


@pytest.mark.parametrize("lane", ["internal", "external"])
def test_automatic_prewrite_checkpoint_uses_count_policy_in_both_lanes(tmp_path, lane):
    gateway, project, _, calls = fixture_gateway(tmp_path)
    configure(gateway, 1)
    old = archives(gateway, 4)
    if lane == "external":
        result = call(gateway, arguments(project))
    else:
        service = gateway.approval_transactions
        request = service.create_apply_request({"target_tool": "vrcforge_checkpoint_choice_fixture", "arguments": arguments(project)})
        approval_id = request["approval"]["id"]
        service.approve(approval_id)
        result = service.apply_approved({"approval_id": approval_id})
    assert result["ok"] is True, result
    assert len(calls) == 1
    assert not any(p.exists() for p in old)
    usage = gateway.checkpoint_recovery.checkpoint_archive_usage()
    assert usage["archiveCount"] == 1 and usage["maxCount"] == 1
    current = usage["archives"][0]
    assert current["checkpointId"] == result["checkpoint"]["id"]
    assert Path(current["path"]).exists()


def test_gateway_update_saves_and_applies_count_policy(tmp_path, monkeypatch):
    import dashboard_server as server
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    paths = archives(gateway, 5)
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(server, "safe_agent_health", lambda: {})
    monkeypatch.setattr(server, "safe_agent_manifest", lambda: {})
    monkeypatch.setattr(server, "connector_client_statuses", lambda **_: {})
    monkeypatch.setattr(server, "connector_bundle_sync", lambda _: {"ok": True})
    monkeypatch.setattr(server, "summarize_external_agent_audit", lambda: [])
    monkeypatch.setattr(server, "DASHBOARD_STATE", None)
    result = server.update_external_agent_gateway_sync({"checkpointArchiveMaxCount": 2, "checkpointArchiveMaxSizeMb": 0})
    assert result["gateway"]["checkpointArchiveMaxCount"] == 2
    assert result["gateway"]["checkpointArchivePrune"]["deletedCount"] == 3
    assert result["gateway"]["checkpointArchiveUsage"]["maxCount"] == 2
    assert sum(p.exists() for p in paths) == 2
    assert AgentGateway(gateway.config_path, tmp_path / "audit").ensure_config().checkpoint_archive_max_count == 2


def test_new_inactive_backups_immediately_converge_to_default_four(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    paths = archives(gateway, 6, old=False)
    result = gateway.checkpoint_recovery.prune_checkpoint_archives()
    assert result["deletedCount"] == 2 and result["archiveCount"] == 4
    assert not any(p.exists() for p in paths[:2]) and all(p.exists() for p in paths[2:])


def test_new_backups_keep_explicit_inflight_checkpoint(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    configure(gateway, 1)
    paths = archives(gateway, 3, old=False)
    result = gateway.checkpoint_recovery.prune_checkpoint_archives(protected_checkpoint_ids={paths[0].stem})
    assert result["deletedCount"] == 1 and result["overCountLimit"] is True
    assert paths[0].exists() and paths[-1].exists() and not paths[1].exists()
