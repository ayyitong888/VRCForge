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


def test_native_result_recovery_decodes_only_candidate_audit_rows(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    active, rotated, empty = iter_jsonl_generations(gateway.audit_log_path)
    active.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "sessionId": "session-日本語",
        "binding": "binding",
        "turnId": "turn",
        "callId": "pending",
        "content": {"observations": ["active"]},
    }
    active.write_text("\n".join(json.dumps(row) for row in [
        {"event": "unrelated", "padding": "x" * (1024 * 1024)},
        {"event": "approval_applied", "nativeResult": {**result, "sessionId": "another"}},
        {"event": "approval_applied", "nativeResult": result},
    ]) + "\n", encoding="utf-8")
    older = {"observations": ["rotated"]}
    rotated.write_text(json.dumps(
        {"event": "approval_applied", "nativeResult": {**result, "content": older}},
        ensure_ascii=False,
    ), encoding="utf-8")
    empty.touch()

    with patch("agent_approval_transactions.json.loads", wraps=json.loads) as decode:
        recovered = gateway.approval_transactions.recover_native_results(
            result["sessionId"], {"binding": result["binding"], "turnId": result["turnId"]}, {"pending"},
        )
    assert decode.call_count == 2
    assert recovered == {"pending": json.dumps(older, ensure_ascii=False)}

    # A damaged row in a rotated ledger cannot hide other applied receipts.
    damaged = json.dumps({"event": "approval_applied", "nativeResult": result}).encode("utf-8")
    with rotated.open("ab") as handle:
        handle.write(b"\n" + damaged.replace(b'"active"', b'"\xff"'))
    assert gateway.approval_transactions.recover_native_results(
        result["sessionId"], {"binding": result["binding"], "turnId": result["turnId"]}, {"pending"},
    ) == recovered
