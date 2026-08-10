from __future__ import annotations

import json
import os
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from agent_gateway import AgentGateway
from agent_runtime_run_ledger import AgentRuntimeRunLedger, AgentRuntimeRunLedgerPorts
from agent_task_loop import AgentTaskLoop
from runtime_planner_service import RuntimePlannerService


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
    for shell_session_id in (failed_id, abandoned_id):
        assert gateway.runtime_runs.stage_shell_continuation(
            shell_session_id=shell_session_id,
            task_seed={
                "schema": "vrcforge.agent_task_loop.v2",
                "taskId": shell_session_id,
                "sessionId": "session-interrupted",
                "clientTurnId": f"client-{shell_session_id}",
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
        catalog=SimpleNamespace(read=lambda _layer: SimpleNamespace()),
        desktop=SimpleNamespace(summarize_action_result=lambda _value: ""),
        turn=SimpleNamespace(
            bind=lambda _params: nullcontext(
                SimpleNamespace(verified_context_limit=None, planner_label="test")
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
    assert ledger.turn_run_status(
        top_plan={"nextStep": "done"},
        shell_payload=None,
        skill_payload=None,
        write_payload=None,
        approval_id="",
    ) == "completed"
