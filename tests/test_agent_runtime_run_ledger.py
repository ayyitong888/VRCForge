from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

import agent_runtime_run_ledger as ledger_module
from agent_gateway import AgentGateway
from agent_runtime_run_ledger import AgentRuntimeRunLedger, AgentRuntimeRunLedgerPorts
from agent_task_loop import AgentTaskLoop
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTurnMetadata, RuntimePlannerService


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


def test_timeline_event_is_durable_before_projection_and_recovers_after_rebuild(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.append({
        "event": "runtime_turn_started", "status": "running",
        "sessionId": "s", "turnId": "t", "clientTurnId": "c",
        "provider": "p", "providerLabel": "Provider", "model": "m",
        "projectRoot": "P",
    })
    event = {"id": "timeline-t-0", "sequence": 0, "kind": "assistant",
             "payload": {"summary": "partial reply", "status": "streaming"}}
    ledger.append_timeline_event(
        session_id="s", turn_id="t", client_turn_id="c", event=event,
        provider="p", provider_label="Provider", model="m", project_root="P",
    )

    restarted, _ = make_ledger(tmp_path)
    assert restarted.recover_interrupted_runs() == 1
    continuations = restarted.list_runtime_continuations()
    assert len(continuations) == 1
    assert continuations[0]["continuationSource"] == "foreground_interrupted"
    assert continuations[0]["timeline"] == [event]
    assert restarted.list_runtime_continuations() == continuations


def test_live_ledger_is_not_interrupted_and_terminal_supersedes_recovery(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    started = {"event": "runtime_turn_started", "status": "running",
               "sessionId": "s", "turnId": "t", "clientTurnId": "c"}
    ledger.append(started)
    assert ledger.list_runtime_continuations() == []
    ledger.append({
        "event": "runtime_turn_completed", "status": "completed",
        "sessionId": "s", "turnId": "t", "clientTurnId": "c",
        "continuationEvent": {
            "schema": "vrcforge.runtime_turn_event.v1",
            "continuationSource": "foreground_completed", "sessionId": "s",
            "turnId": "t", "clientTurnId": "c", "plan": {"reply": "done"},
        },
    })
    assert ledger.list_runtime_continuations()[0]["continuationSource"] == "foreground_completed"


def test_continuation_projection_avoids_copying_large_cached_events(tmp_path: Path, monkeypatch) -> None:
    ledger, _lock = make_ledger(tmp_path)
    original_text = "detail" * 20_000
    native_snapshot = {"messages": [{"role": "assistant", "content": "private"}]}
    ledger.append({
        "event": "runtime_turn_completed", "status": "completed",
        "sessionId": "s", "turnId": "t", "clientTurnId": "c",
        "continuationEvent": {
            "schema": "vrcforge.runtime_turn_event.v1",
            "continuationSource": "foreground_completed", "sessionId": "s",
            "turnId": "t", "clientTurnId": "c", "plan": {"reply": "done"},
            "timeline": [{"id": "large-result", "sequence": 0, "kind": "tool_result",
                          "payload": {"result": {"text": original_text,
                                                  "_nativeConversation": native_snapshot}}}],
        },
    })
    restarted, _ = make_ledger(tmp_path)
    clone_calls: list[None] = []
    original_deepcopy = ledger_module.copy.deepcopy
    monkeypatch.setattr(
        ledger_module,
        "copy",
        SimpleNamespace(deepcopy=lambda value: (clone_calls.append(None), original_deepcopy(value))[1]),
    )

    first = restarted.list_runtime_continuations()
    assert len(first) == 1
    assert first[0]["timeline"][0]["payload"]["result"]["text"] == original_text
    assert "_nativeConversation" not in first[0]["timeline"][0]["payload"]["result"]
    assert clone_calls == []

    first[0]["timeline"][0]["payload"]["result"]["text"] = "caller mutation"
    second = restarted.list_runtime_continuations()
    assert second[0]["timeline"][0]["payload"]["result"]["text"] == original_text
    assert clone_calls == []

    persisted = ledger.read_events(limit=0)[-1]["continuationEvent"]["timeline"][0]["payload"]["result"]
    assert persisted["text"] == original_text
    assert persisted["_nativeConversation"] == native_snapshot


def test_list_runs_cold_cache_owns_one_detached_copy(tmp_path: Path, monkeypatch) -> None:
    ledger, _lock = make_ledger(tmp_path)
    original_text = "detail" * 20_000
    ledger.append({
        "event": "runtime_turn_completed", "status": "completed",
        "sessionId": "s", "turnId": "t", "clientTurnId": "c", "projectRoot": "P",
        "continuationEvent": {
            "schema": "vrcforge.runtime_turn_event.v1",
            "continuationSource": "foreground_completed", "sessionId": "s",
            "turnId": "t", "clientTurnId": "c", "projectRoot": "P",
            "plan": {"reply": "done"},
            "timeline": [{"id": "large-result", "sequence": 0, "kind": "tool_result",
                          "payload": {"result": {"text": original_text}}}],
        },
    })
    restarted, _ = make_ledger(tmp_path)
    clone_calls: list[None] = []
    original_deepcopy = ledger_module.copy.deepcopy
    monkeypatch.setattr(
        ledger_module,
        "copy",
        SimpleNamespace(deepcopy=lambda value: (clone_calls.append(None), original_deepcopy(value))[1]),
    )

    first = restarted.list_runs(limit=40, session_id="s", project_root="P")
    assert len(clone_calls) == 1
    assert first["runs"][0]["continuationEvent"]["timeline"][0]["payload"]["result"]["text"] == original_text
    first["events"][0]["continuationEvent"]["timeline"][0]["payload"]["result"]["text"] = "caller mutation"

    second = restarted.list_runs(limit=40, session_id="s", project_root="P")
    assert len(clone_calls) == 2
    assert second["events"][0]["continuationEvent"]["timeline"][0]["payload"]["result"]["text"] == original_text


def test_list_runtime_continuations_is_read_only_until_explicit_recovery(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.append({
        "event": "runtime_turn_started", "status": "running",
        "sessionId": "s", "turnId": "t", "clientTurnId": "c",
    })

    # Dashboard polling must not turn an ordinary read into a lifecycle write.
    restarted, _ = make_ledger(tmp_path)
    assert restarted.list_runtime_continuations() == []
    assert not any(row.get("event") == "runtime_turn_interrupted" for row in ledger.read_events(limit=0))

    assert restarted.recover_interrupted_runs() == 1
    assert restarted.list_runtime_continuations()[0]["continuationSource"] == "foreground_interrupted"


def test_recovery_seeds_continuation_reader_and_incrementally_adds_recovered_rows(tmp_path: Path, monkeypatch) -> None:
    ledger, _lock = make_ledger(tmp_path)
    legacy = {
        "event": "runtime_turn_completed",
        "sessionId": "legacy-session",
        "turnId": "legacy-turn",
        "clientTurnId": "legacy-client",
        "continuationEvent": {
            "schema": "vrcforge.runtime_turn_event.v1",
            "continuationSource": "foreground_completed",
            "sessionId": "legacy-session",
            "turnId": "legacy-turn",
            "clientTurnId": "legacy-client",
            "plan": {"reply": "legacy"},
        },
    }
    ledger.log_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.log_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    old_owner = AgentRuntimeRunLedger(ledger._ports)
    old_owner.append({"event": "runtime_turn_started", "status": "running",
                      "sessionId": "recovered-session", "turnId": "recovered-turn",
                      "clientTurnId": "recovered-client"})
    restarted = AgentRuntimeRunLedger(ledger._ports)

    assert restarted.recover_interrupted_runs() == 1
    cache_key = ("continuations", "", "", "", 512)
    seeded = restarted._list_runs_cache[cache_key]
    assert seeded["offset"] < restarted.log_path.stat().st_size

    read_offsets: list[int] = []
    original_open = Path.open

    class ReadSpy:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def seek(self, *args):
            return self.handle.seek(*args)
        def tell(self):
            return self.handle.tell()
        def read(self, size=-1):
            read_offsets.append(self.handle.tell())
            return self.handle.read(size)

    def spy_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        return ReadSpy(handle) if path == restarted.log_path and "b" in mode else handle

    monkeypatch.setattr(Path, "open", spy_open)
    small = restarted.list_runtime_continuations(limit=10)
    assert {row["continuationSource"] for row in small} == {
        "foreground_completed", "foreground_interrupted"
    }
    assert read_offsets and min(read_offsets) == seeded["offset"]
    reads_after_small = len(read_offsets)
    # A second limit within the seeded default window must reuse the same cursor.
    large = restarted.list_runtime_continuations(limit=64)
    assert len(large) == 2
    assert len(read_offsets) == reads_after_small


def test_recovery_seed_leaves_partial_final_row_for_incremental_reader(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.log_path.parent.mkdir(parents=True, exist_ok=True)
    complete = {"event": "runtime_turn_completed", "continuationEvent": {
        "sessionId": "complete", "turnId": "turn", "clientTurnId": "client",
        "continuationSource": "foreground_completed",
    }}
    partial = json.dumps({"event": "runtime_turn_completed", "continuationEvent": {
        "sessionId": "partial", "turnId": "turn", "clientTurnId": "client",
        "continuationSource": "foreground_completed",
    }})
    prefix = (json.dumps(complete) + "\n").encode()
    ledger.log_path.write_bytes(prefix + partial.encode())
    ledger.recover_interrupted_runs()
    seeded = ledger._list_runs_cache[("continuations", "", "", "", 512)]
    assert seeded["offset"] == len(prefix)
    assert [row.get("sessionId") for row in ledger.list_runtime_continuations()] == ["complete"]

    with ledger.log_path.open("ab") as handle:
        handle.write(b"\n")
    rows = ledger.list_runtime_continuations()
    assert {row.get("sessionId") for row in rows} == {"complete", "partial"}


def test_continuation_projection_reuses_incremental_reader_without_read_events_scan(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.append({
        "event": "runtime_turn_completed", "status": "completed",
        "sessionId": "s", "turnId": "t1", "clientTurnId": "c1",
        "continuationEvent": {
            "schema": "vrcforge.runtime_turn_event.v1", "continuationSource": "foreground_completed",
            "sessionId": "s", "turnId": "t1", "clientTurnId": "c1", "plan": {"reply": "one"},
        },
    })
    assert ledger.list_runtime_continuations()[0]["turnId"] == "t1"
    ledger.append({
        "event": "runtime_turn_completed", "status": "completed",
        "sessionId": "s", "turnId": "t2", "clientTurnId": "c2",
        "continuationEvent": {
            "schema": "vrcforge.runtime_turn_event.v1", "continuationSource": "foreground_completed",
            "sessionId": "s", "turnId": "t2", "clientTurnId": "c2", "plan": {"reply": "two"},
        },
    })
    with patch.object(AgentRuntimeRunLedger, "read_events", side_effect=AssertionError("continuation poll rescanned JSONL")):
        projected = ledger.list_runtime_continuations()
    assert [item["turnId"] for item in projected] == ["t1", "t2"]


def test_long_incomplete_turn_keeps_earlier_visible_messages(tmp_path: Path) -> None:
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "sessionId": "s", "turnId": "t", "clientTurnId": "c"})
    for index in range(300):
        ledger.append_timeline_event(session_id="s", turn_id="t", client_turn_id="c", event={
            "id": f"visible-{index}", "sequence": index, "kind": "assistant",
            "payload": {"summary": f"Message {index}"},
        })
    restored, _ = make_ledger(tmp_path)
    assert restored.recover_interrupted_runs() == 1
    timeline = restored.list_runtime_continuations()[0]["timeline"]
    assert [item["id"] for item in timeline] == [f"visible-{index}" for index in range(300)]


def test_visible_progress_survives_forced_process_termination(tmp_path: Path) -> None:
    marker = tmp_path / "visible.json"
    script = """
import json, sys, time
from pathlib import Path
from agent_gateway import AgentGateway
root = Path(sys.argv[1])
gateway = AgentGateway(root / 'config.json', root / 'audit')
gateway.runtime_runs.append({'event': 'runtime_turn_started', 'status': 'running',
    'sessionId': 'crash-session', 'turnId': 'crash-turn', 'clientTurnId': 'crash-client'})
event = {'id': 'visible-before-kill', 'sequence': 0, 'kind': 'assistant',
    'timestamp': '2026-01-01T00:00:00Z', 'payload': {'summary': 'Already visible before exit'}}
gateway.runtime_runs.append_timeline_event(session_id='crash-session',
    turn_id='crash-turn', client_turn_id='crash-client', event=event)
(root / 'visible.json').write_text(json.dumps(event), encoding='utf-8')
time.sleep(30)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "writer did not reach the visible-event boundary"
        visible = json.loads(marker.read_text(encoding="utf-8"))
        process.kill()
        process.wait(timeout=5)
        restored, _ = make_ledger(tmp_path)
        assert restored.recover_interrupted_runs() == 1
        events = restored.list_runtime_continuations()
        assert len(events) == 1
        assert events[0]["timeline"] == [visible]
        assert events[0]["plan"]["taskCompletion"]["status"] == "interrupted"
        assert restored.list_runtime_continuations() == events
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_usage_reconciliation_is_private_but_durable(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    reconciliation = {
        "schema": "vrcforge.request_usage_reconciliation.v1",
        "status": "verified",
        "chainId": "task-1",
        "parentTurnId": "turn-1",
        "turnId": "turn-2",
        "parentSnapshotSha256": "a" * 64,
        "delta": {"requestCount": 3},
    }
    record = ledger.build_run_from_turn(
        event="runtime_turn_completed",
        status="completed",
        agent_name="desktop-agent",
        session_id="session-1",
        turn_id="turn-2",
        client_turn_id="client-2",
        message="done",
        attachments=[],
        params={},
        top_plan={"reply": "done"},
        steps=[],
        shell_payload=None,
        skill_payload=None,
        write_payload=None,
        approval_id="",
        usage_reconciliation=reconciliation,
    )
    ledger.append(record)

    raw = ledger.read_events()[0]
    assert raw["usageReconciliation"] == reconciliation
    public = ledger.list_runs(session_id="session-1", limit=10)
    assert "usageReconciliation" not in json.dumps(public, ensure_ascii=False)


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


def test_list_runs_advances_from_append_and_invalidates_on_replacement(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    identity = {"sessionId": "session-cache", "turnId": "turn-1", "clientTurnId": "client-1"}
    ledger.append({**identity, "event": "runtime_turn_started", "status": "running"})

    first = ledger.list_runs(session_id="session-cache", limit=10)
    first_events = list(first["events"])
    assert [row["event"] for row in first_events] == ["runtime_turn_started"]
    first_offset = ledger._list_runs_cache[("session-cache", "", "", 10)]["offset"]

    ledger.append({**identity, "event": "runtime_turn_completed", "status": "completed"})
    second = ledger.list_runs(session_id="session-cache", limit=10)
    assert [row["event"] for row in second["events"]] == [
        "runtime_turn_started", "runtime_turn_completed"
    ]
    assert ledger._list_runs_cache[("session-cache", "", "", 10)]["offset"] > first_offset

    same_size = ledger.log_path.read_bytes().replace(b'"status": "running"', b'"status": "pending"')
    assert len(same_size) == ledger.log_path.stat().st_size
    ledger.log_path.write_bytes(same_size)
    rewritten_stat = ledger.log_path.stat()
    os.utime(ledger.log_path, ns=(rewritten_stat.st_atime_ns, rewritten_stat.st_mtime_ns + 2_000_000_000))
    same_size_rewrite = ledger.list_runs(session_id="session-cache", limit=10)
    assert same_size_rewrite["events"][0]["status"] == "pending"

    ledger.log_path.write_text(
        json.dumps({**identity, "event": "runtime_turn_failed", "status": "failed"}) + "\n",
        encoding="utf-8",
    )
    truncated = ledger.list_runs(session_id="session-cache", limit=10)
    assert [row["event"] for row in truncated["events"]] == ["runtime_turn_failed"]

    replacement = ledger.log_path.with_suffix(".replacement")
    replacement.write_text(
        json.dumps({**identity, "event": "runtime_turn_queued", "status": "queued"}) + "\n",
        encoding="utf-8",
    )
    os.replace(replacement, ledger.log_path)
    replaced = ledger.list_runs(session_id="session-cache", limit=10)
    assert [row["event"] for row in replaced["events"]] == ["runtime_turn_queued"]


def test_list_runs_append_reads_only_new_bytes_and_filters_unrelated_approvals(tmp_path: Path, monkeypatch) -> None:
    ledger, _lock = make_ledger(tmp_path)
    identity = {"sessionId": "session-isolated", "turnId": "turn-1", "clientTurnId": "client-1"}
    ledger.append({**identity, "event": "runtime_turn_started", "status": "running"})
    ledger.list_runs(session_id=identity["sessionId"], limit=10)
    offset = ledger._list_runs_cache[(identity["sessionId"], "", "", 10)]["offset"]

    for index in range(500):
        ledger.append({"event": "approval_applied", "status": "completed",
                       "approvalId": f"unrelated-{index}", "sessionId": "other-session"})
    ledger.append({**identity, "event": "runtime_turn_completed", "status": "completed",
                   "approvalIds": ["wanted-approval"]})
    ledger.append({"event": "approval_applied", "status": "completed",
                   "approvalId": "wanted-approval"})

    original_open = Path.open
    reads: list[tuple[int, int]] = []

    class SpyHandle:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def seek(self, *args):
            return self.handle.seek(*args)
        def tell(self):
            return self.handle.tell()
        def read(self, size=-1):
            reads.append((self.handle.tell(), size))
            return self.handle.read(size)

    def spy_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return SpyHandle(handle) if path == ledger.log_path and "b" in (args[0] if args else kwargs.get("mode", "r")) else handle

    monkeypatch.setattr(Path, "open", spy_open)
    projected = ledger.list_runs(session_id=identity["sessionId"], limit=10)
    assert {row["event"] for row in projected["events"]} == {
        "runtime_turn_started", "runtime_turn_completed", "approval_applied"
    }
    assert all(row.get("approvalId") != "unrelated-0" for row in projected["events"])
    assert reads and reads[0][0] == offset
    assert all(0 < size <= 1024 * 1024 for _, size in reads)
    assert sum(size for _, size in reads) == ledger.log_path.stat().st_size - offset


def test_list_runs_keeps_session_wide_approval_ids_across_client_queries(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_completed", "status": "completed",
                   "sessionId": "session-shared", "clientTurnId": "other-client",
                   "approvalIds": ["shared-approval"]})
    ledger.append({"event": "runtime_turn_started", "status": "running",
                   "sessionId": "session-shared", "clientTurnId": "target-client"})
    ledger.list_runs(session_id="session-shared", client_turn_id="target-client", limit=10)
    cache = ledger._list_runs_cache[("session-shared", "", "target-client", 10)]
    assert "shared-approval" in cache["approval_ids"]


def test_related_approvals_are_filtered_before_recent_window(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running",
                   "sessionId": "owner", "clientTurnId": "wanted", "projectRoot": "project-a"})
    ledger.append({"event": "runtime_turn_completed", "status": "completed",
                   "sessionId": "owner", "clientTurnId": "different", "approvalIds": ["shared"]})
    for index in range(150):
        ledger.append({"event": "approval_applied", "status": "completed", "approvalId": "shared",
                       "clientTurnId": "different", "projectRoot": "project-a"})
        ledger.append({"event": "approval_applied", "status": "completed", "approvalId": "shared",
                       "clientTurnId": "wanted", "projectRoot": "project-b"})
    result = ledger.list_runs(session_id="owner", client_turn_id="wanted", project_root="project-a", limit=10)
    assert len(result["runs"]) == 1
    assert result["runs"][0]["status"] == "running"
    assert len(result["events"]) == 1


def test_durable_runtime_continuations_replay_once_per_stable_client_turn(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    first = {
        "schema": "vrcforge.runtime_turn_event.v1",
        "continuationSource": "sub_agent_finished",
        "sessionId": "session-1",
        "turnId": "turn-first",
        "clientTurnId": "client-stable",
        "plan": {"reply": "first"},
    }
    replacement = {**first, "turnId": "turn-replayed", "plan": {"reply": "latest"}}
    ledger.append({"event": "runtime_turn_completed", "continuationEvent": first})
    ledger.append({"event": "runtime_turn_completed", "continuationEvent": replacement})

    assert ledger.list_runtime_continuations() == [replacement]


def test_shell_continuation_cas_is_durable_private_and_session_idempotent(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    seed = {
        "schema": "vrcforge.agent_task_loop.v2",
        "taskId": "task-shell-owner",
        "objective": "private task seed",
    }
    terminal = {
        "shellSessionId": "shell-stable-1",
        "runtimeSessionId": "runtime-owner",
        "turnId": "turn-owner",
        "clientTurnId": "client-owner",
        "status": "finished",
        "exitCode": 0,
        "result": {"stdoutSummary": "private-shell-output", "stderrSummary": ""},
    }

    assert ledger.stage_shell_continuation(
        shell_session_id="shell-stable-1",
        task_seed=seed,
        terminal_event=terminal,
    ) is True
    assert ledger.stage_shell_continuation(
        shell_session_id="shell-stable-1",
        task_seed={**seed, "taskId": "replacement-must-not-win"},
        terminal_event=terminal,
    ) is False
    assert ledger.shell_continuation_states(limit=0)[0]["shellContinuationState"] == "pending"

    public_projection = json.dumps(ledger.list_runs(limit=20), ensure_ascii=False)
    assert "continuationTaskSeed" not in public_projection
    assert "continuationTerminalEvent" not in public_projection
    assert "private task seed" not in public_projection
    assert "private-shell-output" not in public_projection

    claimed = ledger.claim_shell_continuation("shell-stable-1")
    assert claimed is not None
    assert claimed["taskSeed"]["taskId"] == "task-shell-owner"
    assert claimed["terminalEvent"]["result"]["stdoutSummary"] == "private-shell-output"
    assert ledger.shell_continuation_states(limit=0)[0]["shellContinuationState"] == "dispatching"
    assert ledger.claim_shell_continuation("shell-stable-1") is None

    assert ledger.deliver_shell_continuation("shell-stable-1") is True
    assert ledger.shell_continuation_states(limit=0)[0]["shellContinuationState"] == "delivered"
    assert ledger.deliver_shell_continuation("shell-stable-1") is False
    assert ledger.stage_shell_continuation(
        shell_session_id="shell-stable-1",
        task_seed=seed,
        terminal_event=terminal,
    ) is False


def test_shell_continuation_interrupted_is_terminal_and_never_reclaimed(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    terminal = {"shellSessionId": "shell-stable-2", "status": "finished", "exitCode": 0}
    assert ledger.stage_shell_continuation(
        shell_session_id="shell-stable-2",
        task_seed={"schema": "vrcforge.agent_task_loop.v2", "taskId": "task-2"},
        terminal_event=terminal,
    )
    assert ledger.claim_shell_continuation("shell-stable-2") is not None
    assert ledger.interrupt_shell_continuation(
        "shell-stable-2",
        reason="process_restart_after_dispatch_claim",
    ) is True
    state = ledger.shell_continuation_states(limit=0)[0]
    assert state["shellContinuationState"] == "interrupted"
    assert state["continuationInterruptedReason"] == "process_restart_after_dispatch_claim"
    assert ledger.claim_shell_continuation("shell-stable-2") is None


def test_gateway_startup_reconcile_dispatches_pending_shell_once_and_marks_delivered(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    shell_session_id = "shell-durable-pending"
    assert gateway.runtime_runs.stage_shell_continuation(
        shell_session_id=shell_session_id,
        task_seed={"schema": "vrcforge.agent_task_loop.v2", "taskId": "task-durable"},
        terminal_event={
            "shellSessionId": shell_session_id,
            "runtimeSessionId": "session-durable",
            "turnId": "turn-durable",
            "clientTurnId": "client-durable",
            "status": "finished",
            "exitCode": 0,
        },
    )
    continuation = {
        "sessionId": "session-durable",
        "turnId": "turn-continuation",
        "clientTurnId": "client-durable:shell:shell-durable-pending",
        "continuationSource": "shell_process_finished",
        "plan": {"reply": "done"},
    }

    with patch.object(
        gateway,
        "resume_runtime_task_after_shell",
        return_value=continuation,
    ) as resume, patch.object(gateway, "_runtime_turn_completed") as completed:
        reconciled = gateway.reconcile_runtime_shell_continuations()
        duplicate = gateway.reconcile_runtime_shell_continuations()

    assert reconciled["pendingDispatched"] == 1
    assert reconciled["delivered"] == 1
    assert reconciled["interrupted"] == 0
    assert duplicate["pendingDispatched"] == 0
    resume.assert_called_once()
    completed.assert_called_once_with(continuation)
    state = gateway.runtime_runs.shell_continuation_states(limit=0)[0]
    assert state["shellContinuationState"] == "delivered"


def test_gateway_live_shell_terminal_claims_before_callback_and_deduplicates_stable_id(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    shell_session_id = "shell-live-stable"
    continuation = {
        "sessionId": "session-live",
        "turnId": "turn-continuation",
        "clientTurnId": "client-live:shell:shell-live-stable",
        "continuationSource": "shell_process_finished",
        "plan": {"reply": "done"},
    }
    observed_states: list[str] = []

    def resume(event: dict[str, Any]) -> dict[str, Any]:
        observed_states.append(
            gateway.runtime_runs.shell_continuation_states(limit=0)[0]["shellContinuationState"]
        )
        assert "stdout" not in event["result"]
        assert event["result"]["stdoutSummary"] == "private-live-output"
        return continuation

    def completed(payload: dict[str, Any]) -> None:
        assert payload is continuation
        observed_states.append(
            gateway.runtime_runs.shell_continuation_states(limit=0)[0]["shellContinuationState"]
        )

    terminal = {
        "shellSessionId": shell_session_id,
        "runtimeSessionId": "session-live",
        "turnId": "turn-live",
        "clientTurnId": "client-live",
        "status": "finished",
        "exitCode": 0,
        "timedOut": False,
        "cancelled": False,
        "terminationFailed": False,
        "result": {"ok": True, "exitCode": 0, "stdout": "private-live-output", "stderr": ""},
        "taskSeed": {"schema": "vrcforge.agent_task_loop.v2", "taskId": "task-live"},
    }
    with patch.object(gateway, "resume_runtime_task_after_shell", side_effect=resume) as resume_mock, patch.object(
        gateway,
        "_runtime_turn_completed",
        side_effect=completed,
    ) as completed_mock:
        gateway.shell._ports.session_finished(terminal)
        gateway.shell._ports.session_finished(terminal)

    assert observed_states == ["dispatching", "dispatching"]
    resume_mock.assert_called_once()
    completed_mock.assert_called_once_with(continuation)
    assert gateway.runtime_runs.shell_continuation_states(limit=0)[0]["shellContinuationState"] == "delivered"
    public_projection = json.dumps(gateway.runtime_runs.list_runs(limit=50), ensure_ascii=False)
    assert "private-live-output" not in public_projection
    assert "task-live" not in public_projection


def test_gateway_dispatch_failure_and_restart_owned_dispatching_never_replay_shell(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    failed_id = "shell-durable-failed"
    abandoned_id = "shell-durable-dispatching"
    expected_actions: dict[str, str] = {}
    for shell_session_id in (failed_id, abandoned_id):
        requested_action_id = f"action_{shell_session_id.replace('-', '_')}"
        expected_actions[shell_session_id] = requested_action_id
        assert gateway.runtime_runs.stage_shell_continuation(
            shell_session_id=shell_session_id,
            task_seed={
                "schema": "vrcforge.agent_task_loop.v2",
                "taskId": shell_session_id,
                "sessionId": "session-interrupted",
                "clientTurnId": f"client-{shell_session_id}",
                "requestedActionId": requested_action_id,
                "requestedKind": "shell",
                "requestedTool": "shell",
            },
            terminal_event={
                "shellSessionId": shell_session_id,
                "status": "finished",
                "exitCode": 0,
            },
        )
    assert gateway.runtime_runs.claim_shell_continuation(abandoned_id) is not None

    with patch.object(
        gateway,
        "resume_runtime_task_after_shell",
        side_effect=RuntimeError("planner failed after the dispatch claim"),
    ) as resume, patch.object(gateway, "_runtime_turn_completed") as completed:
        first = gateway.reconcile_runtime_shell_continuations()
        second = gateway.reconcile_runtime_shell_continuations()

    assert first["pendingDispatched"] == 1
    assert first["delivered"] == 0
    assert first["interrupted"] == 2
    assert second["pendingDispatched"] == 0
    resume.assert_called_once()
    assert completed.call_count == 2
    interrupted_turns = [call.args[0] for call in completed.call_args_list]
    assert {
        turn["plan"]["nextStep"] for turn in interrupted_turns
    } == {"needs_user_action"}
    assert {
        turn["sessionId"] for turn in interrupted_turns
    } == {"session-interrupted"}
    assert {
        turn["plan"]["taskCompletion"]["actionId"] for turn in interrupted_turns
    } == set(expected_actions.values())
    assert {
        turn["plan"]["taskCompletion"]["kind"] for turn in interrupted_turns
    } == {"shell"}
    assert {
        turn["plan"]["taskCompletion"]["tool"] for turn in interrupted_turns
    } == {"shell"}
    assert len(gateway.runtime_runs.list_runtime_continuations(limit=64)) == 2
    states = {
        item["shellSessionId"]: item["shellContinuationState"]
        for item in gateway.runtime_runs.shell_continuation_states(limit=0)
    }
    assert states[failed_id] == "interrupted"
    assert states[abandoned_id] == "interrupted"


def test_gateway_shutdown_closes_continuation_admission_and_boundedly_drains_owner(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    shell_session_id = "shell-shutdown-inflight"
    assert gateway.runtime_runs.stage_shell_continuation(
        shell_session_id=shell_session_id,
        task_seed={"schema": "vrcforge.agent_task_loop.v2", "taskId": "task-shutdown"},
        terminal_event={
            "shellSessionId": shell_session_id,
            "runtimeSessionId": "session-shutdown",
            "status": "finished",
            "exitCode": 0,
        },
    )
    entered = threading.Event()
    release = threading.Event()

    def blocked_resume(_event: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(2)
        return {
            "sessionId": "session-shutdown",
            "turnId": "turn-shutdown",
            "continuationSource": "shell_process_finished",
            "plan": {"reply": "done"},
        }

    with patch.object(
        gateway,
        "resume_runtime_task_after_shell",
        side_effect=blocked_resume,
    ), patch.object(gateway, "_runtime_turn_completed"):
        worker = threading.Thread(target=gateway.reconcile_runtime_shell_continuations)
        worker.start()
        assert entered.wait(1)
        shutdown = gateway.shutdown_runtime_continuations(0.01)
        assert shutdown["ok"] is False
        assert shutdown["timedOutShellSessionIds"] == [shell_session_id]
        with pytest.raises(RuntimeError, match="work is active"):
            gateway.start_runtime_continuations()
        release.set()
        worker.join(2)

    assert gateway.runtime_runs.shell_continuation_states(limit=0)[0]["shellContinuationState"] == "delivered"
    gateway.start_runtime_continuations()


def test_shutdown_during_continuation_planning_blocks_the_next_tool_and_interrupts(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    shell_session_id = "shell-shutdown-before-next-tool"
    shell_arguments = {"command": "python worker.py", "background": True}
    task_loop = AgentTaskLoop(
        "run the worker, then inspect health",
        session_id="session-shutdown-before-tool",
        client_turn_id="client-shutdown-before-tool",
        agent_name="desktop-agent",
    )
    task_loop.require_action(kind="shell", tool="shell", arguments=shell_arguments)
    assert gateway.runtime_runs.stage_shell_continuation(
        shell_session_id=shell_session_id,
        task_seed=task_loop.approval_seed(
            tool_calls_used=1,
            exposure_layer="planning",
            requested_kind="shell",
            requested_tool="shell",
            requested_arguments=shell_arguments,
            continue_after_approval=True,
        ),
        terminal_event={
            "shellSessionId": shell_session_id,
            "runtimeSessionId": "session-shutdown-before-tool",
            "clientTurnId": "client-shutdown-before-tool",
            "status": "finished",
            "exitCode": 0,
            "result": {"ok": True, "exitCode": 0, "stdoutSummary": "worker finished"},
        },
    )
    planner_entered = threading.Event()
    release_planner = threading.Event()
    tool_calls: list[str] = []
    reconcile_results: list[dict[str, Any]] = []

    def blocked_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        planner_entered.set()
        assert release_planner.wait(2)
        return {
            "planner": "llm",
            "summary": "Inspect runtime health after the worker.",
            "skillNeeded": True,
            "skillTool": "vrcforge_health",
            "skillParams": {},
            "continueLoop": False,
            "nextStep": "call_skill",
        }

    planner = RuntimePlannerService(
        catalog=SimpleNamespace(
            read=lambda _layer, *, project_context_active=True: PlannerCatalogSnapshot()
        ),
        desktop=SimpleNamespace(summarize_action_result=lambda _value: ""),
        turn=SimpleNamespace(
            bind=lambda *_args, **_kwargs: nullcontext(
                PlannerTurnMetadata(verified_context_limit=None, planner_label="test")
            )
        ),
    )
    gateway.bind_runtime_planner(planner)

    def execute_skill(_owner: Any, tool: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        tool_calls.append(tool)
        return {"ok": True, "status": "executed"}

    with patch.object(
        planner,
        "plan_agent_turn",
        side_effect=blocked_plan,
    ), patch.object(
        type(gateway.runtime_skills),
        "execute",
        autospec=True,
        side_effect=execute_skill,
    ):
        worker = threading.Thread(
            target=lambda: reconcile_results.append(
                gateway.reconcile_runtime_shell_continuations()
            )
        )
        worker.start()
        assert planner_entered.wait(1)
        shutdown = gateway.shutdown_runtime_continuations(0.01)
        assert shutdown["ok"] is False
        assert shutdown["timedOutShellSessionIds"] == [shell_session_id]
        release_planner.set()
        worker.join(2)

    assert not worker.is_alive()
    assert tool_calls == []
    assert reconcile_results[0]["delivered"] == 0
    assert reconcile_results[0]["interrupted"] == 1
    state = gateway.runtime_runs.shell_continuation_states(limit=0)[0]
    assert state["shellContinuationState"] == "interrupted"
    continuations = gateway.runtime_runs.list_runtime_continuations(limit=64)
    assert len(continuations) == 1
    assert continuations[0]["plan"]["nextStep"] == "needs_user_action"


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
        continuation_event={
            "schema": "vrcforge.runtime_turn_event.v1",
            "sessionId": "session",
            "turnId": "turn",
        },
        context_usage={"totalTokens": 10},
        context_compaction={"applied": True},
    )

    assert record["messageSummary"] == "do work"
    assert record["planSummary"] == "waiting approval"
    assert record["computerUseVisualAccent"] == "green"
    assert record["approvalIds"] == ["approval-0", "approval-1", "approval-2"]
    assert record["contextUsage"] == {"totalTokens": 10}
    assert record["contextCompaction"] == {"applied": True}
    assert record["continuationEvent"]["turnId"] == "turn"


def test_foreground_completion_replay_is_idempotent_by_session_and_client_turn(tmp_path: Path) -> None:
    ledger, _lock = make_ledger(tmp_path)
    completion = {
        "schema": "vrcforge.runtime_turn_event.v1",
        "continuationSource": "foreground_completed",
        "sessionId": "session-replay",
        "turnId": "turn-replay",
        "clientTurnId": "client-replay",
        "plan": {"reply": "complete answer", "summary": "done"},
    }
    for _ in range(2):
        ledger.append({
            "event": "runtime_turn_completed",
            "status": "completed",
            "sessionId": "session-replay",
            "turnId": "turn-replay",
            "clientTurnId": "client-replay",
            "continuationEvent": completion,
        })
    replay = ledger.list_runtime_continuations(limit=10)
    assert replay == [{**completion, "completedAt": "2026-08-09T00:00:00+00:00"}]

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
        skill_payload={
            "ok": True,
            "status": "needs_user_action",
            "outcome": {
                "status": "needs_user_action",
                "verification": {"state": "needs_user_action"},
            },
        },
        write_payload=None,
        approval_id="",
    ) == "blocked"
    # A current ask-user boundary must remain resumable even when an earlier
    # write/diagnostic in this same turn failed (native-turn-007 shape).
    assert ledger.turn_run_status(
        top_plan={
            "nextStep": "needs_user_action",
            "completionGate": {"status": "needs_user_action", "reason": "question_pending"},
        },
        shell_payload=None,
        skill_payload={
            "tool": "vrcforge_ask_user",
            "ok": True,
            "status": "needs_user_action",
            "result": {"question": {"questionId": "question_20261012_215703_405097_478433"}},
        },
        write_payload={
            "tool": "vrcforge_import_outfit_package",
            "ok": False,
            "status": "failed",
        },
        approval_id="",
    ) == "blocked"
    assert ledger.turn_run_status(
        top_plan={"nextStep": "done"},
        shell_payload=None,
        skill_payload=None,
        write_payload=None,
        approval_id="",
    ) == "completed"


def test_shell_terminal_event_after_append_recovers_once_after_gateway_rebuild(tmp_path: Path) -> None:
    """A terminal event must retain a recoverable continuation across an interruption."""

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    shell_session_id = "shell-stage-interrupted-before-pending"
    terminal = {
        "shellSessionId": shell_session_id,
        "runtimeSessionId": "session-stage-interrupted",
        "turnId": "turn-stage-interrupted",
        "clientTurnId": "client-stage-interrupted",
        "status": "finished",
        "exitCode": 7,
        "timedOut": False,
        "cancelled": False,
        "terminationFailed": False,
        "result": {"ok": False, "exitCode": 7, "stdout": "bounded-output", "stderr": ""},
        "taskSeed": {
            "schema": "vrcforge.agent_task_loop.v2",
            "taskId": "task-stage-interrupted",
            "sessionId": "session-stage-interrupted",
            "clientTurnId": "client-stage-interrupted",
            "requestedKind": "shell",
            "requestedTool": "shell",
        },
    }

    ledger_type = type(gateway.runtime_runs)
    append = ledger_type.append

    def append_then_interrupt(ledger: AgentRuntimeRunLedger, record: dict[str, Any]) -> None:
        append(ledger, record)
        if record.get("event") == "runtime_shell_process_finished":
            raise BaseException("simulated process interruption after terminal append")

    with patch.object(ledger_type, "append", new=append_then_interrupt):
        with pytest.raises(BaseException, match="after terminal append"):
            gateway.shell._ports.session_finished(terminal)

    process_events = [
        event
        for event in gateway.runtime_runs.read_events(limit=0)
        if event.get("event") == "runtime_shell_process_finished"
        and event.get("shellSessionId") == shell_session_id
    ]
    assert len(process_events) == 1

    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    continuation = {
        "sessionId": "session-stage-interrupted",
        "turnId": "turn-stage-interrupted:shell",
        "clientTurnId": f"client-stage-interrupted:shell:{shell_session_id}",
        "continuationSource": "shell_process_finished",
        "plan": {"reply": "resumed once"},
    }
    with patch.object(
        restarted,
        "resume_runtime_task_after_shell",
        return_value=continuation,
    ) as resume, patch.object(restarted, "_runtime_turn_completed") as completed, patch.object(
        type(restarted.shell), "execute", autospec=True
    ) as shell_execute:
        reconciled = restarted.reconcile_runtime_shell_continuations()
        duplicate = restarted.reconcile_runtime_shell_continuations()

    assert reconciled["pendingDispatched"] == 1
    assert reconciled["delivered"] == 1
    assert duplicate["pendingDispatched"] == 0
    assert duplicate["delivered"] == 0
    resume.assert_called_once()
    completed.assert_called_once_with(continuation)
    shell_execute.assert_not_called()
    resumed_event = resume.call_args.args[0]
    assert resumed_event["shellSessionId"] == shell_session_id
    assert resumed_event["exitCode"] == 7
    assert resumed_event["result"]["stdoutSummary"] == "bounded-output"
    assert resumed_event["taskSeed"]["taskId"] == "task-stage-interrupted"
    public_projection = json.dumps(
        restarted.runtime_runs.list_runs(session_id="session-stage-interrupted", limit=20),
        ensure_ascii=False,
    )
    assert "task-stage-interrupted" not in public_projection
    assert "bounded-output" not in public_projection
    assert sum(
        event.get("event") == "runtime_shell_process_finished"
        and event.get("shellSessionId") == shell_session_id
        for event in restarted.runtime_runs.read_events(limit=0)
    ) == 1
