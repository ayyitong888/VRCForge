import json
import pytest
import dashboard_server as d
from agent_task_loop import AgentTaskLoop
from runtime_planner_service import RuntimePlannerService

def native(planner, loop):
    o={"skillPolicy": loop.planner_projection()["skillPolicy"]}
    c=dict(observe=o, exposure_layer="execution", project_context_active=False, project_path="", internal_tool_blocks=["core"], global_instructions="", project_instructions="")
    return planner._build_native_plan_request([], **c)[0]
def legacy(planner, loop):
    o={"skillPolicy": loop.planner_projection()["skillPolicy"]}
    c=dict(observe=o, exposure_layer="execution", project_context_active=False, project_path="", internal_tool_blocks=["core"], global_instructions="", project_instructions="")
    return planner._build_llm_plan_prompt("Continue", [], **c)

@pytest.mark.parametrize("mode", ["native", "legacy"])
def test_inactive_exit_is_resident_noop(mode):
    p=RuntimePlannerService(catalog=d._RuntimePlannerCatalog(), desktop=None); loop=AgentTaskLoop("Continue")
    before=native(p,loop) if mode=="native" else legacy(p,loop)
    if mode=="native": assert "exit_skill" in [x["function"]["name"] for x in before["tools"]]
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

def test_skill_state_is_last_and_existing_content_stable():
    p=RuntimePlannerService(catalog=d._RuntimePlannerCatalog(), desktop=None); empty=AgentTaskLoop("Continue"); active=AgentTaskLoop("Continue")
    active.activate_skill_policy(name="guide", allowed_tools=["read_text_file"], disallowed_tools=[])
    en=native(p,empty); an=native(p,active); assert an["tools"]==en["tools"] and an["messages"][:-1]==en["messages"][:-1]
    es=json.loads(en["messages"][-1]["content"].split(": ",1)[1]); ass=json.loads(an["messages"][-1]["content"].split(": ",1)[1])
    assert ass.get("skillPolicy", {}).get("name") == "guide"; assert {k:v for k,v in ass.items() if k!="skillPolicy"}=={k:v for k,v in es.items() if k!="skillPolicy"}
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
