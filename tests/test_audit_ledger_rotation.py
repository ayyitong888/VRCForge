from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_gateway import AgentGateway
from atomic_file_replace import iter_jsonl_generations, rotate_jsonl_if_large


def test_rotation_keeps_two_generations_and_drops_the_oldest() -> None:
    with TemporaryDirectory() as temp_dir:
        ledger = Path(temp_dir) / "approvals.jsonl"
        for round_index in range(3):
            ledger.write_text(json.dumps({"round": round_index}) + "\n", encoding="utf-8")
            assert rotate_jsonl_if_large(ledger, max_bytes=8) is True
        first = json.loads(ledger.with_name(f"{ledger.name}.1").read_text(encoding="utf-8").splitlines()[0])
        second = json.loads(ledger.with_name(f"{ledger.name}.2").read_text(encoding="utf-8").splitlines()[0])
        assert first["round"] == 2
        assert second["round"] == 1
        assert not ledger.with_name(f"{ledger.name}.3").exists()
        assert [path.name for path in iter_jsonl_generations(ledger)] == [
            "approvals.jsonl",
            "approvals.jsonl.1",
            "approvals.jsonl.2",
        ]


def test_append_audit_rotates_and_recent_logs_span_generations() -> None:
    with TemporaryDirectory() as temp_dir:
        gateway = AgentGateway(Path(temp_dir) / "config.json", Path(temp_dir) / "audit")
        with patch("atomic_file_replace.JSONL_ROTATE_MAX_BYTES", 400):
            for index in range(12):
                gateway.append_audit({"event": "rotation_probe", "index": index, "padding": "x" * 60})
        active = gateway.audit_log_path
        assert active.stat().st_size <= 400 + 512, "append path must rotate oversized ledgers"
        assert active.with_name(f"{active.name}.1").exists(), "rotation must retain history generation"

        recent = gateway.approval_transactions.recent_audit_logs(limit=5)
        assert [entry["index"] for entry in recent] == [7, 8, 9, 10, 11], (
            "tail read must return the newest entries across generations in order"
        )
