import json
from types import MethodType, SimpleNamespace

from agent_checkpoint_recovery import AgentCheckpointRecoveryService


def test_project_checkpoint_filter_precedes_global_window_and_cache(tmp_path):
    ledger = tmp_path / "checkpoints.jsonl"
    def row(name, project):
        return {"id": name, "projectRoot": project, "createdAt": "2026-09-15", "targetTool": "test", "status": "ready"}
    rows = [row("target-old", "D:/Target"), row("target-new", "D:/Target")]
    rows += [row(f"other-{i}", "D:/Other") for i in range(1100)]
    ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    service = SimpleNamespace(_ports=SimpleNamespace(checkpoint_log_path=lambda: ledger), _checkpoint_entry_cache={},
                              _checkpoint_archive_metadata_available=lambda entry: {"ok": True})
    service._read_checkpoint_entries = MethodType(AgentCheckpointRecoveryService._read_checkpoint_entries, service)
    call = lambda args: AgentCheckpointRecoveryService._list_checkpoints_locked(service, args)
    assert call({"limit": 1})["checkpoints"][0]["id"] == "other-1099"
    found = call({"projectRoot": "D:/Target", "limit": 1})
    assert [r["id"] for r in found["checkpoints"]] == ["target-new"]
    both = call({"projectRoot": "D:/Target", "limit": 2})
    assert [r["id"] for r in both["checkpoints"]] == ["target-new", "target-old"]
    assert call({"limit": 1})["checkpoints"][0]["id"] == "other-1099"
