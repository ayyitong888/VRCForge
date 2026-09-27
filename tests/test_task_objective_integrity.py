"""Durable continuation objectives reach the planner without becoming previews."""
import pytest

from agent_task_loop import (
    AgentTaskLoop, approval_task_context, approval_completion,
    prepare_approval_task_continuation, prepare_shell_task_continuation,
    prepare_sub_agent_task_continuation,
)
from runtime_planner_service import PlannerModelResult
from tests.test_runtime_planner_service import FakeModel, service


OBJECTIVE = "Inspect every requested detail. " * 100 + "KEEP_FINAL_CONSTRAINT_SENTINEL"


def seed_and_context():
    loop = AgentTaskLoop(OBJECTIVE, session_id="objective-session", client_turn_id="objective-turn")
    seed = loop.approval_seed(requested_tool="fixture_read", requested_kind="skill", requested_arguments={})
    context = approval_task_context(seed, tool="fixture_read", arguments={})
    return loop, seed, context


def test_objective_survives_seed_context_completion_and_restoration():
    loop, seed, context = seed_and_context()
    completion = approval_completion(context, raw_result={"ok": True},
        outcome={"status": "ok", "summary": "Read complete", "verification": {"state": "not_required", "checks": []}})
    restored = AgentTaskLoop.from_approval_context(context, completion)
    assert [loop.objective, seed["objective"], context["objective"], completion["objective"],
            restored.objective, restored.planner_projection()["objective"]] == [OBJECTIVE] * 6
    assert restored.task_id == loop.task_id
    assert AgentTaskLoop(None).objective == ""


@pytest.mark.parametrize("lane", ["approval", "shell", "subagent"])
def test_continuation_objective_reaches_actual_legacy_model_request(lane):
    _, seed, context = seed_and_context()
    if lane == "approval":
        prepared = prepare_approval_task_continuation({"id": "approval", "taskContext": context},
            {"status": "applied", "taskCompletion": {"status": "completed", "outcome": {"status": "ok"}}})
    elif lane == "shell":
        prepared = prepare_shell_task_continuation(seed,
            {"shellSessionId": "shell-fixture", "status": "finished", "exitCode": 0})
    else:
        prepared = prepare_sub_agent_task_continuation(seed,
            {"subAgentTaskId": "sub-fixture", "parentSessionId": "objective-session",
             "status": "completed", "result": {"ok": True}, "summary": "Read complete"})
    assert prepared is not None
    assert prepared["taskContinuation"]["terminalPlan"] is None
    assert prepared["params"]["message"] == OBJECTIVE
    model = FakeModel(PlannerModelResult('{"action":"reply","reply":"Ready"}'))
    params = prepared["params"]
    service(model=model).plan_agent_turn(params["message"], params, {}, history=params["history"])
    assert len(model.prompts) == 1
    assert OBJECTIVE in model.prompts[0]
