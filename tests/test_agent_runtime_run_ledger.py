from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_gateway import AgentGateway
from agent_runtime_run_ledger import AgentRuntimeRunLedger, AgentRuntimeRunLedgerPorts


class LedgerError(RuntimeError):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.status_code = status_code


def make_ledger(tmp_path: Path) -> tuple[AgentRuntimeRunLedger, threading.RLock]:
    lock = threading.RLock()
    path = tmp_path / "audit" / "runtime-runs.jsonl"

    def ensure_boundary(candidate: Path) -> None:
        if not candidate.exists() or candidate.stat().st_size == 0:
            return
        with candidate.open("r+b") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) not in {b"\n", b"\r"}:
                handle.seek(0, os.SEEK_END)
                handle.write(b"\n")

    ledger = AgentRuntimeRunLedger(
        AgentRuntimeRunLedgerPorts(
            log_path=lambda: path,
            shared_state_lock=lock,
            now=lambda: "2026-08-09T00:00:00+00:00",
            normalize_path=lambda value: str(value).replace("\\", "/").lower(),
            normalize_visual_accent=lambda value: str(value or "blue").lower(),
            summarize_text=lambda value, limit: " ".join(str(value or "").split())[:limit],
            redact=lambda value: value,
            ensure_append_boundary=ensure_boundary,
            flush_and_fsync=lambda handle: handle.flush(),
            error_factory=lambda detail, status: LedgerError(detail, status),
        )
    )
    return ledger, lock


def test_gateway_wires_one_dynamic_ledger_with_the_gateway_lock(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit-a")

    assert gateway.runtime_runs is gateway.runtime_runs
    assert gateway.runtime_runs.shared_state_lock is gateway._lock
    assert gateway.runtime_runs.log_path == tmp_path / "audit-a" / "runtime-runs.jsonl"
    assert not hasattr(gateway, "runtime_run_log_path")
    assert not hasattr(gateway, "_append_runtime_run")
    assert not hasattr(gateway, "_read_runtime_run_events")

    gateway.configure_paths(tmp_path / "next-config.json", tmp_path / "audit-b")
    assert gateway.runtime_runs.log_path == tmp_path / "audit-b" / "runtime-runs.jsonl"


def test_append_repairs_truncated_tail_and_read_skips_invalid_json(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.log_path.parent.mkdir(parents=True)
    ledger.log_path.write_bytes(b'{"schema":"broken"')

    ledger.append({"event": "runtime_started"})

    lines = ledger.log_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"schema":"broken"'
    events = ledger.read_events()
    assert len(events) == 1
    assert events[0]["schema"] == "vrcforge.runtime_run.v1"
    assert events[0]["event"] == "runtime_started"


def test_queue_event_and_grouped_run_projection_preserve_filters(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    queued = ledger.record_queue_event(
        {
            "clientTurnId": "client-1",
            "sessionId": "session-1",
            "message": "  hello   run  ",
            "attachments": [{"kind": "image"}],
            "providerLabel": "Provider",
            "projectRoot": "D:\\Avatar",
        }
    )
    assert queued == {
        "ok": True,
        "status": "queued",
        "event": {
            "event": "runtime_turn_queued",
            "status": "queued",
            "sessionId": "session-1",
            "clientTurnId": "client-1",
            "messageSummary": "hello run",
            "attachmentCount": 1,
            "provider": "",
            "providerLabel": "Provider",
            "model": "",
            "projectRoot": "D:\\Avatar",
        },
    }
    ledger.append(
        {
            "event": "runtime_turn_completed",
            "status": "completed",
            "sessionId": "session-1",
            "clientTurnId": "client-1",
            "projectRoot": "d:/avatar",
            "approvalIds": ["approval-1"],
        }
    )
    ledger.append(
        {
            "event": "approval_applied",
            "status": "completed",
            "approvalId": "approval-1",
            "projectRoot": "",
        }
    )
    ledger.append(
        {
            "event": "unrelated",
            "status": "failed",
            "sessionId": "session-2",
            "projectRoot": "D:/Other",
        }
    )

    projected = ledger.list_runs(session_id="session-1", project_root="D:/AVATAR", limit=10)
    assert projected["schema"] == "vrcforge.runtime_runs.v1"
    assert projected["count"] == 2
    assert {event["event"] for event in projected["events"]} == {
        "runtime_turn_queued",
        "runtime_turn_completed",
        "approval_applied",
    }
    client_run = next(run for run in projected["runs"] if run.get("clientTurnId") == "client-1")
    assert client_run["eventCount"] == 2
    assert client_run["lastEvent"] == "runtime_turn_completed"

    with pytest.raises(LedgerError) as raised:
        ledger.record_queue_event({})
    assert raised.value.status_code == 400
    assert str(raised.value) == "clientTurnId is required."


def test_run_builder_and_terminal_status_contract_are_preserved(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    record = ledger.build_run_from_turn(
        event="runtime_turn_completed",
        status="blocked",
        agent_name="desktop-agent",
        session_id="session",
        turn_id="turn",
        client_turn_id="client",
        message="  do   work ",
        attachments=[{"kind": "image"}],
        params={
            "goalDeliveryId": "goal",
            "provider": "provider",
            "providerLabel": "Provider",
            "model": "model",
            "projectRoot": "Project",
            "_computerUseRequested": True,
            "_computerUseVisualTheme": "dark",
            "_computerUseVisualAccent": "GREEN",
        },
        top_plan={"summary": "  waiting   approval ", "planner": "planner", "nextStep": "approval_required"},
        steps=[{"tool": "read"}],
        shell_payload=None,
        skill_payload={"status": "pending_approval", "approvalId": "approval-1"},
        write_payload={"status": "pending", "approval": {"id": "approval-2"}},
        approval_id="approval-0",
        context_usage={"totalTokens": 10},
        context_compaction={"applied": True},
    )

    assert record["messageSummary"] == "do work"
    assert record["planSummary"] == "waiting approval"
    assert record["computerUseVisualAccent"] == "green"
    assert record["approvalIds"] == ["approval-0", "approval-1", "approval-2"]
    assert record["contextUsage"] == {"totalTokens": 10}
    assert record["contextCompaction"] == {"applied": True}

    assert ledger.turn_run_status(
        top_plan={"nextStep": "context_compaction_required"},
        shell_payload=None,
        skill_payload=None,
        write_payload=None,
        approval_id="",
    ) == "blocked"
    assert ledger.turn_run_status(
        top_plan={"nextStep": "planner_failed"},
        shell_payload=None,
        skill_payload={"ok": True, "status": "executed"},
        write_payload=None,
        approval_id="",
    ) == "failed"
    assert ledger.turn_run_status(
        top_plan={"nextStep": "done"},
        shell_payload=None,
        skill_payload={"ok": False, "status": "failed"},
        write_payload=None,
        approval_id="",
    ) == "failed"
    assert ledger.turn_run_status(
        top_plan={"nextStep": "done"},
        shell_payload=None,
        skill_payload=None,
        write_payload=None,
        approval_id="",
    ) == "completed"
