from __future__ import annotations

import json
import threading

import pytest

from agent_task_loop import AgentTaskLoop
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from agent_action_targets import action_target


def _write(loop, path, status="ok", value="new"):
    return loop.record_action(
        kind="write", tool="fixture_write", arguments={"materialPath": path, "value": value},
        raw_result={"ok": status == "ok"}, outcome={"status": status, "summary": status},
    )


def test_bound_execution_references_remain_distinct_targets():
    one = {"schema": "vrcforge.execution_target_ref.v1", "ref": "one"}
    two = {**one, "ref": "two"}
    assert action_target({"executionTarget": one}) != action_target({"executionTarget": two})


@pytest.mark.parametrize("status", ["failed", "needs_user_action"])
def test_other_target_write_and_read_cannot_discharge_carried_write(status):
    first = AgentTaskLoop("change body material")
    _write(first, "Assets/Body.mat", status)
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
    state.set_pending_obligations("s", first.pending_obligations())
    second = AgentTaskLoop("continue")
    for obligation in state.pending_obligations("s"):
        second.require_action(kind=obligation["kind"], tool=obligation["tool"],
                              target=obligation.get("target"), inherited=True)
    _write(second, "Assets/Hair.mat")
    read = second.record_action(
        kind="skill", tool="fixture_read", arguments={"materialPath": "Assets/Body.mat"},
        raw_result={"ok": True}, outcome={"status": "ok"}, native_read_observation=True,
    )
    plan = {"planner": "llm", "nextStep": "done", "completionClaim": {
        "satisfied": True, "evidenceActionIds": [read["actionId"]],
    }}
    assert second.gate_terminal(plan)["completionGate"]["reason"] == "required_action_missing"
    assert second.pending_obligations()[0]["target"] == {"materialPath": "Assets/Body.mat"}
    # Corrected values on the owed target are a real retry, not a new obligation.
    _write(second, "Assets/Body.mat", value="corrected")
    assert second.gate_terminal(plan)["nextStep"] == "done"
    assert second.pending_obligations() == []


def test_failed_targets_using_same_tool_remain_distinct_without_silent_limit():
    loop = AgentTaskLoop("change two materials")
    for index in range(5):
        _write(loop, f"Assets/Material{index}.mat", "failed")
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
    state.set_pending_obligations("s", loop.pending_obligations())
    assert len(state.pending_obligations("s")) == 5


def test_gateway_does_not_expire_unmet_write_after_more_continuations(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, call
    gateway, _model, _invoked = setup_gateway(tmp_path, [
        call(f"claim-{index}", "vrcforge_runtime_action", {
            "action": "reply", "reply": "done", "completion_claim": {"satisfied": True},
        }) for index in range(5)
    ])
    gateway._runtime_session_state.set_pending_obligations("s", [{
        "kind": "write", "tool": "fixture_write", "generation": 2,
        "target": {"materialPath": "Assets/Body.mat"},
    }])
    for index in range(5):
        result = gateway.runtime_message({"message": "continue", "sessionId": "s",
                                          "clientTurnId": f"next-{index}", "maxAgenticTurns": 2})
        assert result["plan"]["nextStep"] == "completion_unverified"
        assert result["plan"]["completionGate"]["reason"] == "required_action_missing"
    assert gateway._runtime_session_state.pending_obligations("s")[0]["generation"] == 7


def test_approval_preview_and_reviewer_receive_only_same_session_target_failures(tmp_path):
    from agent_gateway import AgentGateway
    from approval_auto_review import review_auto_approval

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.approval_transactions
    gateway.ensure_config().allow_write_requests = True
    service.register_write_handler("fixture_write", "Fixture write", "medium", lambda args: {"ok": True})
    for index, (session, target, status) in enumerate([
        ("s", "Assets/Body.mat", "rejected"), ("s", "Assets/Body.mat", "failed"),
        ("other", "Assets/Body.mat", "rejected"), ("s", "Assets/Hair.mat", "failed"),
        ("s", "Assets/Body.mat", "applied"),
    ]):
        service._ports.state.approvals[str(index)] = {
            "id": str(index), "taskContext": {"sessionId": session},
            "targetTool": "fixture_write", "arguments": {"materialPath": target}, "status": status,
        }
    loop = AgentTaskLoop("change material", session_id="s")
    _write(loop, "Assets/Body.mat", "failed")
    context = loop.approval_seed(requested_tool="fixture_write", requested_kind="write",
                                    requested_arguments={"materialPath": "Assets/Body.mat"})
    result = service.create_apply_request({
        "targetTool": "fixture_write", "arguments": {"materialPath": "Assets/Body.mat"},
        "preview": {"priorTargetWrites": {"forged": True}},
    }, task_context=context)
    approval = result["approval"]
    history = approval["preview"]["priorTargetWrites"]
    assert history["totalMatches"] == 3
    assert {entry.get("approvalId") for entry in history["entries"] if "approvalId" in entry} == {"0", "1"}
    assert history["entries"][-1]["actionId"]
    captured = []
    review_auto_approval(approval, lambda prompt: captured.append(prompt) or '{"decision":"manual"}')
    assert json.loads(captured[0].splitlines()[-1])["preview"]["priorTargetWrites"] == history
