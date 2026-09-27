"""Regression for the gateway producer's loaded-skill context fidelity."""

from unittest.mock import patch

from test_agent_loop_p0 import AgentLoopP0Tests as _AgentLoopP0Tests

_AgentLoopP0Tests.__test__ = False


def test_loaded_skill_context_preserves_long_instruction_tail() -> None:
    fixture = _AgentLoopP0Tests("test_loaded_skill_instructions_reenter_planning_without_becoming_completion_evidence")
    fixture.setUp()
    try:
        gateway = fixture.gateway
        instruction = "HEAD|" + ("x" * 7000) + "|TAIL-SENTINEL"
        planner_calls = []

        def plan_next(_message, _params, _observe, _history=None, *, loop_state=None, **_kwargs):
            planner_calls.append(list(loop_state or []))
            if len(planner_calls) == 1:
                return {
                    "summary": "Load the installed workflow instructions.",
                    "reply": "",
                    "planner": "llm",
                    "skillNeeded": True,
                    "skillTool": "fixture-guidance",
                    "skillParams": {},
                    "writeNeeded": False,
                    "shellNeeded": False,
                    "continueLoop": False,
                    "nextStep": "call_skill",
                }
            return {
                "summary": "No real tool action was executed.",
                "reply": "I still need to execute the instructed tool.",
                "planner": "llm",
                "continueLoop": False,
                "nextStep": "done",
                "completionClaim": {"satisfied": True, "evidenceActionIds": []},
            }

        loaded = {
            "ok": True,
            "status": "loaded",
            "tool": "fixture-guidance",
            "result": {
                "name": "fixture-guidance",
                "instructions": instruction,
                "allowedTools": ["vrcforge_health"],
                "disallowedTools": ["vrcforge_shell_process"],
            },
            "outcome": {
                "status": "ok",
                "summary": "Skill instructions loaded.",
                "verification": {"state": "not_required", "checks": []},
            },
        }
        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan_next), patch.object(
            type(gateway.runtime_skills), "execute", autospec=True, return_value=loaded
        ):
            gateway.runtime_message({"message": "use fixture guidance", "session_id": "long-skill-session"})

        assert len(planner_calls) == 2
        skill_context = planner_calls[1][0]["skillContext"]
        assert skill_context["instructions"] == instruction
        assert skill_context["allowedTools"] == ["vrcforge_health"]
        assert skill_context["disallowedTools"] == ["vrcforge_shell_process"]
        assert skill_context["instructions"].endswith("TAIL-SENTINEL")
    finally:
        fixture.tearDown()





