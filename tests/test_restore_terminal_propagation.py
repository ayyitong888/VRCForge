"""Recovery restore failures must terminate the owning runtime turn."""

from __future__ import annotations

import json

from agent_task_loop import prepare_approval_task_continuation
from checkpoint_unity_evidence import restore_failure_terminal_plan
from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
from tests.test_native_runtime_continuations import _binding
from tests.test_native_runtime_gateway import call, run


def _restore_failure_gateway(tmp_path, replies, *, manual_approval=True):
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, replies)

    def restore(arguments):
        invoked.append(dict(arguments))
        return {
            "ok": False,
            "status": "restored_unity_reload_failed",
            "checkpointRecoveryRequired": True,
            "currentStateDigest": "d" * 64,
            "error": "raw machine restore rejection",
            "failureSummary": "恢复失败：Unity 重载未完成，已停止当前回合。",
            "turnControl": {"stopCurrentTurn": True, "automaticRetry": False},
        }

    gateway.register_tool(
        "vrcforge_restore_checkpoint",
        "When to use: restore the exact approved checkpoint. When NOT to use: ordinary reads.",
        "write/checkpoint",
        restore,
        write=True,
    )
    gateway.register_tool(
        "vrcforge_read_text_file",
        "When to use: read a text file. When NOT to use: write a file.",
        "read/fixture",
        lambda arguments: invoked.append(dict(arguments)) or {"text": "must-not-run"},
    )
    if manual_approval:
        gateway.approval_transactions.register_write_handler(
            "vrcforge_restore_checkpoint", "Restore the approved checkpoint.", "high", restore,
        )
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _root: {"ok": True}
    return gateway, model, invoked


def _approve_restore_failure(gateway, initial):
    approval = gateway._approvals[initial["approval_id"]]
    gateway.approval_transactions.approve(initial["approval_id"])
    applied = gateway.approval_transactions.apply_approved(
        {"approval_id": initial["approval_id"]},
    )
    return gateway.resume_runtime_task_after_approval(approval, applied)


def test_single_restore_failure_plan_overrides_first_plan_and_stops(tmp_path):
    gateway, model, invoked = _restore_failure_gateway(tmp_path, [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("restore", "vrcforge_restore_checkpoint", {"checkpointId": "cp", "confirmRestore": True}),
    ])
    _set_mode(gateway, "roslyn_full_auto")

    initial = run(gateway, tmp_path, maxAgenticTurns=5)
    assert initial["plan"]["nextStep"] == "needs_user_action"
    result = _approve_restore_failure(gateway, initial)

    assert result["plan"]["nextStep"] == "tool_failed"
    assert result["plan"]["restoreFailureTurnStop"] is True
    assert result["plan"]["failureSummary"] == "恢复失败：Unity 重载未完成，已停止当前回合。"
    assert result["plan"]["summary"] == result["plan"]["failureSummary"]
    assert result["plan"]["restoreFailure"]["error"] == "raw machine restore rejection"
    assert result["plan"]["restoreFailure"]["currentStateDigest"] == "d" * 64
    assert len(model.requests) == 2
    assert len(invoked) == 1


def test_auto_restore_failure_does_not_execute_a_followup_model_choice(tmp_path):
    gateway, model, invoked = _restore_failure_gateway(tmp_path, [
        call("restore", "vrcforge_restore_checkpoint", {"checkpointId": "cp", "confirmRestore": True}),
        call("after", "vrcforge_read_text_file", {"path": "must-not-run"}),
    ], manual_approval=False)
    gateway.checkpoint_recovery._append_checkpoint({
        "id": "cp", "projectRoot": str(tmp_path), "status": "ready",
    })
    gateway.checkpoint_recovery._append_apply_recovery_entry({
        "id": "recovery-auto", "checkpointId": "cp", "projectRoot": str(tmp_path),
        "targetTool": "vrcforge_restore_checkpoint", "status": "applying",
    })
    _set_mode(gateway, "roslyn_full_auto")

    initial = run(gateway, tmp_path, maxAgenticTurns=5)

    assert initial["plan"]["nextStep"] == "tool_failed"
    assert initial["plan"]["restoreFailureTurnStop"] is True
    assert initial["plan"]["failureSummary"] == "恢复失败：Unity 重载未完成，已停止当前回合。"
    assert not gateway._approvals
    assert len(model.requests) == 1
    assert len(invoked) == 1


def test_restore_failure_cancels_every_remaining_native_call(tmp_path):
    receipt = call(
        "restore", "vrcforge_restore_checkpoint", {"checkpointId": "cp", "confirmRestore": True},
    )
    receipt["tool_calls"].append(
        call("later", "vrcforge_read_text_file", {"path": "must-not-run"})[
            "tool_calls"
        ][0]
    )
    receipt["tool_calls"].append(
        call("later-read-2", "vrcforge_read_text_file", {"path": "must-not-run-2"})[
            "tool_calls"
        ][0]
    )
    gateway, model, invoked = _restore_failure_gateway(tmp_path, [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        receipt,
    ])
    _set_mode(gateway, "roslyn_full_auto")

    initial = run(gateway, tmp_path, maxAgenticTurns=5)
    result = _approve_restore_failure(gateway, initial)

    assert result["plan"]["nextStep"] == "tool_failed"
    assert len(model.requests) == 2
    assert len(invoked) == 1
    native_snapshot = gateway.runtime_sessions.native_conversation(
        "native-session", binding=_binding(tmp_path),
    )
    later = next(
        item for item in native_snapshot["messages"]
        if item.get("tool_call_id") == "later"
    )
    assert json.loads(later["content"])["status"] == "not_executed"
    later_read_2 = next(
        item for item in native_snapshot["messages"]
        if item.get("tool_call_id") == "later-read-2"
    )
    assert json.loads(later_read_2["content"])["status"] == "not_executed"


def test_manual_approval_continuation_adds_plain_summary_preserves_raw_failure():
    prepared = prepare_approval_task_continuation(
        {
            "id": "approval-restore",
            "taskContext": {
                "tool": "vrcforge_restore_checkpoint",
                "objective": "restore checkpoint",
                "sessionId": "session",
                "clientTurnId": "turn",
                "projectRoot": "C:/project",
            },
        },
        {
            "status": "failed",
            "result": {
                "execution": {
                    "failureSummary": "恢复失败：当前回合已停止。",
                    "error": "raw machine rejection",
                    "currentStateDigest": "e" * 64,
                    "turnControl": {"stopCurrentTurn": True, "automaticRetry": False},
                },
            },
            "taskCompletion": {
                "status": "failed",
                "outcome": {"status": "failed", "summary": "raw machine rejection"},
            },
        },
    )
    assert prepared is not None
    continuation = prepared["taskContinuation"]
    plan = continuation["terminalPlan"]
    observation = continuation["plannerObservation"]["result"]
    assert plan["summary"] == "恢复失败：当前回合已停止。"
    assert plan["reply"] == plan["summary"]
    assert plan["failureSummary"] == plan["summary"]
    assert observation["error"] == "raw machine rejection"
    assert observation["currentStateDigest"] == "e" * 64
    assert observation["turnControl"] == {"stopCurrentTurn": True, "automaticRetry": False}


def test_successful_preview_with_active_recovery_does_not_trigger_terminal_stop():
    # Sanitized replay of PREVIEW_FALSE_STOP.json: the preview succeeded, while
    # its retained recovery record still carried checkpointRecoveryRequired.
    payload = {
        "status": "executed",
        "result": {
            "ok": True,
            "schema": "vrcforge.interrupted_apply_recovery.v1",
            "writeFailure": {"checkpointRecoveryRequired": True},
            "recovery": {"status": "needs_recovery", "blockingWrites": True},
        },
        "outcome": {"status": "ok", "success": True},
    }
    assert restore_failure_terminal_plan(
        {"nextStep": "continue", "continueLoop": True},
        "vrcforge_preview_interrupted_apply_recovery",
        payload,
    ) is None


def test_successful_restore_with_historical_recovery_flags_does_not_stop():
    payload = {
        "ok": True,
        "status": "executed",
        "outcome": {"status": "ok", "success": True},
        "result": {
            "ok": True,
            "writeFailure": {"checkpointRecoveryRequired": True},
            "recovery": {"status": "needs_recovery", "blockingWrites": True},
        },
    }
    assert restore_failure_terminal_plan(
        {"nextStep": "continue", "continueLoop": True},
        "vrcforge_restore_checkpoint",
        payload,
    ) is None
