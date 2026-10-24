from agent_task_loop import AgentTaskLoop, approval_task_context
from agent_runtime_run_ledger import AgentRuntimeRunLedger
from background_goal_runtime import classify_runtime_plan_outcome


def test_honest_incomplete_reply_survives_missing_requirements_and_is_not_human_blocked():
    loop = AgentTaskLoop("finish outfit")
    loop.require_action(kind="write", tool="vrcforge_toggle_scene_object", arguments={"active": False})
    reply = "衣柜尚未完成：缺少处理网格的工具，左肩穿模仍未验证。"
    plan = loop.gate_terminal({"planner": "llm", "nextStep": "done", "reply": reply, "completionClaim": {"satisfied": False}})
    assert plan["reply"] == reply
    assert plan["completionGate"]["status"] == "failed"
    assert plan["completionGate"]["reason"] == "required_action_missing"
    assert classify_runtime_plan_outcome(plan)[0] == "failed"
    assert AgentRuntimeRunLedger.turn_run_status(top_plan=plan, shell_payload=None, skill_payload=None, write_payload=None, approval_id="") == "failed"
    assert "taskCompletion" not in plan


def test_honest_incomplete_reply_survives_unbound_claim():
    loop = AgentTaskLoop("finish outfit")
    loop.record_action(kind="skill", tool="inspect", arguments={}, raw_result={"ok": True}, outcome={"status": "ok"})
    reply = "检查完成，但衣服仍未修好。"
    plan = loop.gate_terminal({"planner": "llm", "nextStep": "done", "reply": reply, "completionClaim": {"satisfied": False}})
    assert plan["reply"] == reply
    assert plan["completionGate"]["status"] == "failed"


def test_approval_execution_context_preserves_planned_obligation_identity():
    loop = AgentTaskLoop("toggle", session_id="s")
    tool = "vrcforge_toggle_scene_object"
    planned = {"active": False, "targetRef": "bound-object"}
    requirement = loop.require_action(kind="write", tool=tool, arguments=planned)
    executed = {**planned, "projectPath": "injected-project"}
    seed = loop.approval_seed(requested_tool=tool, requested_arguments=executed, requested_action_id=requirement["actionId"])
    context = approval_task_context(seed, tool=tool, arguments=executed)
    assert context["actionId"] == requirement["actionId"]
    loop.record_action(kind="write", tool=tool, arguments=executed, action_id=context["actionId"], raw_result={"ok": True}, outcome={"status": "ok"})
    plan = loop.gate_terminal({"planner": "llm", "nextStep": "done", "completionClaim": {"satisfied": True, "evidenceActionIds": [context["actionId"]]}})
    assert plan["nextStep"] == "done"


def test_real_user_wait_is_still_blocked():
    plan = {"nextStep": "needs_user_action", "completionGate": {"status": "needs_user_action", "reason": "question_pending"}}
    assert classify_runtime_plan_outcome(plan)[0] == "parked"


def test_async_approval_continuation_keeps_same_obligation_identity():
    loop = AgentTaskLoop("toggle", session_id="s")
    tool = "vrcforge_toggle_scene_object"
    requirement = loop.require_action(kind="write", tool=tool, arguments={"active": False})
    seed = loop.approval_seed(requested_tool=tool, requested_arguments={"active": False, "projectPath": "injected-project"}, requested_action_id=requirement["actionId"])
    context = approval_task_context(seed, tool=tool, arguments={"active": False})
    resumed = AgentTaskLoop.from_approval_context(context, {"actionId": context["actionId"], "tool": tool, "status": "completed", "outcome": {"status": "ok"}})
    plan = resumed.gate_terminal({"planner": "llm", "nextStep": "done", "completionClaim": {"satisfied": True, "evidenceActionIds": [context["actionId"]]}})
    assert plan["nextStep"] == "done"


def test_original_receipt_replay_when_supplied():
    import os,json
    from pathlib import Path
    import pytest
    directory = os.environ.get("VRCFORGE_COMPLETION_REPLAY_DIR")
    if not directory:
        pytest.skip("Original receipt replay supplied separately")
    directory = Path(directory)
    response = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))["response"]
    original = json.loads((directory / "model-final-plan.json").read_text(encoding="utf-8"))
    task = response["task"]
    loop = AgentTaskLoop(task["objective"])
    loop._actions = {a["actionId"]: a for a in task["actions"]}
    loop._requirements = {r["requirementId"]: r for r in task["requirements"]}
    result = loop.gate_terminal(original)
    assert result["reply"] == original["reply"]
    assert result["completionGate"]["status"] == "failed"
    assert AgentRuntimeRunLedger.turn_run_status(top_plan=result, shell_payload=response.get("shell"), skill_payload=response.get("skill"), write_payload=response.get("write"), approval_id="") == "failed"
