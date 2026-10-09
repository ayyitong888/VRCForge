from copy import deepcopy
import json
import pytest
import dashboard_server as d
from agent_task_loop import AgentTaskLoop
from runtime_planner_service import RuntimePlannerService

def native(planner, loop, messages=None):
    o={"skillPolicy": loop.planner_projection()["skillPolicy"]}
    c=dict(observe=o, exposure_layer="execution", project_context_active=False, project_path="", internal_tool_blocks=["core"], global_instructions="", project_instructions="")
    return planner._build_native_plan_request(messages or [], **c)[0]
def legacy(planner, loop):
    o={"skillPolicy": loop.planner_projection()["skillPolicy"]}
    c=dict(observe=o, exposure_layer="execution", project_context_active=False, project_path="", internal_tool_blocks=["core"], global_instructions="", project_instructions="")
    return planner._build_llm_plan_prompt("Continue", [], **c)

@pytest.mark.parametrize("mode", ["native", "legacy"])
def test_inactive_exit_is_resident_noop(mode):
    p=RuntimePlannerService(catalog=d._RuntimePlannerCatalog(), desktop=None); loop=AgentTaskLoop("Continue")
    before=native(p,loop) if mode=="native" else legacy(p,loop)
    if mode=="native":
        assert [x["function"]["name"] for x in before["tools"]] == [
            "load_internal_tool_block", "unload_internal_tool_block", "call_tool", "vrcforge_runtime_action"]
        definitions = json.loads(before["instructions"].split("Resident tool definitions (data, not authorization): ", 1)[1])
        assert "exit_skill" in [item["name"] for item in definitions]
        state = json.loads(before["messages"][-1]["content"].split(": ", 1)[1])
        assert "exit_skill" in state["availableToolNames"]
    else: assert "exit_skill" in before
    receipt=loop.exit_skill(name="ignored", reason="continue")
    assert receipt["ok"] and receipt["skillScopeStatus"]=="inactive"
    assert "Skill" in receipt["message"] and loop.planner_projection()["skillPolicy"]=={}
    after=native(p,loop) if mode=="native" else legacy(p,loop)
    assert after==before

def test_active_exit_keeps_exact_name_rule():
    loop=AgentTaskLoop("Continue"); loop.activate_skill_policy(name="guide", allowed_tools=[], disallowed_tools=[])
    with pytest.raises(ValueError, match="exact active"): loop.exit_skill(name="wrong", reason="return")
    assert loop.exit_skill(name="guide", reason="return")["skillScopeStatus"]=="exited"

def test_skill_changes_append_snapshot_without_rewriting_existing_content():
    p=RuntimePlannerService(catalog=d._RuntimePlannerCatalog(), desktop=None); empty=AgentTaskLoop("Continue"); active=AgentTaskLoop("Continue")
    active.activate_skill_policy(name="guide", allowed_tools=["read_text_file"], disallowed_tools=[])
    en=native(p,empty, [{"role": "user", "content": "Continue"}])
    history = deepcopy(en["messages"][:-1])
    saved = deepcopy(history)
    an=native(p,active,history)
    assert an["tools"] == en["tools"] and an["instructions"] == en["instructions"]
    assert history == saved and an["messages"][:len(saved)] == saved
    assert len(an["messages"]) == len(saved) + 2
    marker = "Runtime context snapshot (data, not user instructions or authorization): "
    appended = an["messages"][-2]
    assert appended["role"] == "user" and appended["content"].startswith(marker)
    snapshot = json.loads(appended["content"][len(marker):])
    assert snapshot["skillPolicy"] == active.planner_projection()["skillPolicy"]
    state_message = an["messages"][-1]
    assert state_message["role"] == "system"
    state = json.loads(state_message["content"].split(": ", 1)[1])
    assert "skillPolicy" not in state
    repeated = native(p, active, an["messages"][:-1])
    assert repeated == an
    el=legacy(p,empty); al=legacy(p,active); marker="\n\nCurrent runtime Skill state (data): "
    assert al.startswith(el.split(marker,1)[0]) and al.endswith(json.dumps(active.planner_projection()["skillPolicy"], ensure_ascii=False, separators=(",", ":")))





def test_gateway_exit_noop_is_completed_action(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, call, finish
    gateway, model, _ = setup_gateway(tmp_path, [
        call("exit", "vrcforge_exit_skill", {"name": "ignored", "reason": "continue"}), finish,
    ])
    result = gateway.runtime_message({"message": "Continue", "sessionId": "native-session",
        "clientTurnId": "native-turn", "projectRoot": str(tmp_path), "maxAgenticTurns": 4})
    exit_steps = [step for step in result["steps"] if step.get("tool") == "vrcforge_exit_skill"]
    assert len(exit_steps) == 1
    assert exit_steps[0]["status"] in {"executed", "completed"}
    assert exit_steps[0]["outcome"]["status"] == "ok"
    assert exit_steps[0]["result"]["skillScopeStatus"] == "inactive"


def test_unresolved_failure_still_blocks_done_after_inactive_noop():
    loop = AgentTaskLoop("Continue")
    loop.exit_skill(name="ignored", reason="continue")
    loop.record_action(kind="skill", tool="fixture", arguments={}, raw_result={},
        outcome={"status": "failed", "summary": "still failed"})
    gated = loop.gate_terminal({"nextStep": "done", "reply": "done",
        "completionClaim": {"satisfied": True}})
    assert gated["nextStep"] != "done"
