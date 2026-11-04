"""Cross-turn obligation carryover: turn N's unmet work constrains turn N+1.

Codex keeps failed tool calls as durable history the model physically sees in
every later provider request; VRCForge's native history already does that for
the model side. This covers the gate side: the per-turn task loop is rebuilt
fresh, so without carryover a later turn could claim completion while the owed
write never happened. Unmet obligations are re-seeded as inherited
requirements with a bounded generation so abandoned work self-expires.
"""
from __future__ import annotations

from pathlib import Path

from agent_task_loop import AgentTaskLoop


def _record_outcome(loop: AgentTaskLoop, *, kind: str, tool: str, status: str) -> str:
    record = loop.record_action(
        kind=kind,
        tool=tool,
        arguments={"target": "fixture"},
        raw_result={"ok": status == "ok"},
        outcome={"status": status, "summary": "fixture outcome"},
    )
    return str(record["actionId"])


def test_failed_write_exports_next_turn_obligation() -> None:
    first = AgentTaskLoop("swap the texture")
    _record_outcome(first, kind="write", tool="vrcforge_swap_body_texture", status="failed")
    obligations = first.pending_obligations()
    assert obligations == [{
        "kind": "write",
        "tool": "vrcforge_swap_body_texture",
        "verificationProfile": "",
        "generation": 0,
        "target": {"target": "fixture"},
    }]


def test_seeded_obligation_blocks_done_until_the_write_completes() -> None:
    second = AgentTaskLoop("continue the task")
    second.require_action(
        kind="write", tool="vrcforge_swap_body_texture", inherited=True, generation=0,
    )
    gated = second.gate_terminal({
        "planner": "llm", "nextStep": "done",
        "completionClaim": {"satisfied": True},
    })
    assert gated["nextStep"] == "completion_unverified"
    assert gated["completionGate"]["reason"] == "required_action_missing"

    _record_outcome(second, kind="write", tool="vrcforge_swap_body_texture", status="ok")
    gated = second.gate_terminal({
        "planner": "llm", "nextStep": "done",
        "completionClaim": {"satisfied": True},
    })
    assert gated["nextStep"] == "done"
    assert second.pending_obligations() == []


def test_inherited_unmet_obligations_bump_generation() -> None:
    loop = AgentTaskLoop("continue the task")
    loop.require_action(
        kind="write", tool="vrcforge_swap_body_texture", inherited=True, generation=1,
    )
    obligations = loop.pending_obligations()
    assert obligations and obligations[0]["generation"] == 2


def test_matched_requirements_and_completed_turns_export_nothing() -> None:
    loop = AgentTaskLoop("swap the texture")
    loop.require_action(kind="write", tool="vrcforge_swap_body_texture")
    _record_outcome(loop, kind="write", tool="vrcforge_swap_body_texture", status="ok")
    assert loop.pending_obligations() == []


def test_session_state_round_trip_and_clear() -> None:
    import threading

    from agent_runtime_session_state import (
        AgentRuntimeSessionState,
        AgentRuntimeSessionStatePorts,
    )

    state = AgentRuntimeSessionState(
        AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock())
    )
    state.set_pending_obligations("s1", [{"kind": "write", "tool": "t", "generation": 0}])
    assert state.pending_obligations("s1") == [
        {"kind": "write", "tool": "t", "verificationProfile": "", "generation": 0}
    ]
    state.set_pending_obligations("s1", [])
    assert state.pending_obligations("s1") == []


def test_gateway_carries_failed_write_into_next_turn(tmp_path: Path) -> None:
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
    from tests.test_native_runtime_gateway import call

    results: list[dict] = [
        {"ok": False, "status": "failed", "error": "unity rejected the write",
         "mutationStarted": False, "saved": False, "safeToRetry": True,
         "committed": False, "commitState": "not_started"},
        {"ok": True, "status": "completed", "committed": True, "commitState": "complete",
         "checkpointRecoveryRequired": False, "result": {"applied": True}},
    ]
    calls = iter(results)

    def handler(_arguments: dict) -> dict:
        return dict(next(calls))

    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [
        call("enter", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-fail", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "a"}),
        # Reconsideration round after the failed write: honest natural final.
        {"role": "assistant", "content": "写入被 Unity 拒绝,任务尚未完成。"},
        # Turn 2: blind done claim with zero actions.
        call("claim", "vrcforge_runtime_action", {
            "action": "reply", "reply": "已完成替换。", "completion_claim": {"satisfied": True}}),
        # Turn 3: retry the owed write, then claim with grounding.
        call("write-ok", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "a"}),
        call("final", "vrcforge_runtime_action", {
            "action": "reply", "reply": "这次真的写入了。", "completion_claim": {"satisfied": True}}),
    ])
    gateway._tools["fixture_write"].handler = handler  # noqa: SLF001
    gateway.approval_transactions.register_write_handler(  # noqa: SLF001
        "fixture_write", "Write the fixture.", "medium", handler)
    _set_mode(gateway, "full_auto")
    session = "carry-session"

    first = gateway.runtime_message({
        "message": "替换贴图", "sessionId": session, "clientTurnId": "carry-1",
        "projectRoot": str(tmp_path), "maxAgenticTurns": 5,
    })
    assert str((first.get("plan") or {}).get("nextStep")) != "done"
    carried = gateway._runtime_session_state.pending_obligations(session)  # noqa: SLF001
    assert carried and carried[0]["kind"] == "write" and carried[0]["tool"] == "fixture_write"

    second = gateway.runtime_message({
        "message": "继续", "sessionId": session, "clientTurnId": "carry-2",
        "projectRoot": str(tmp_path), "maxAgenticTurns": 5,
    })
    second_plan = second.get("plan") or {}
    assert str(second_plan.get("nextStep")) == "completion_unverified"
    gate = second_plan.get("completionGate") or {}
    assert gate.get("reason") == "required_action_missing"
    assert gate.get("requirementIds"), "the carried obligation must be the blocking requirement"

    third = gateway.runtime_message({
        "message": "重试写入", "sessionId": session, "clientTurnId": "carry-3",
        "projectRoot": str(tmp_path), "maxAgenticTurns": 5,
    })
    assert str((third.get("plan") or {}).get("nextStep")) == "done"
    assert gateway._runtime_session_state.pending_obligations(session) == []  # noqa: SLF001
