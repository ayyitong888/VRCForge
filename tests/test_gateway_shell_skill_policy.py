from unittest.mock import patch

import pytest

from agent_gateway import AgentGateway, AgentGatewayConfig
from tests.test_dashboard_server import bind_test_runtime_planner


@pytest.mark.parametrize("allowed,denied,permitted", [
    (["vrcforge_execute_shell"], [], True),
    ([], ["vrcforge_execute_shell"], False),
    (["vrcforge_execute_shell"], ["vrcforge_execute_shell"], False),
])
def test_gateway_shell_policy_uses_registered_identity_before_approval(tmp_path, allowed, denied, permitted):
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    gateway.save_config(AgentGatewayConfig(enabled=True, allow_write_requests=True))
    bind_test_runtime_planner(gateway, lambda _prompt: '{"action":"reply","reply":"fixture"}')
    plans = iter([
        {"planner": "llm", "skillNeeded": True, "skillTool": "fixture-guide", "skillParams": {},
         "continueLoop": True, "nextStep": "call_skill"},
        {"planner": "llm", "shellNeeded": True, "shellCommand": "echo fixture", "shellParams": {},
         "continueLoop": True, "nextStep": "call_shell"},
        {"planner": "llm", "reply": "Stopped", "continueLoop": False, "nextStep": "done",
         "completionClaim": {"satisfied": False}},
    ])
    loaded = {"ok": True, "status": "loaded", "tool": "fixture-guide", "result": {
        "name": "fixture-guide", "instructions": "Use the shell only under normal approval rules.",
        "allowedTools": allowed, "disallowedTools": denied,
    }, "outcome": {"status": "ok", "summary": "Guide loaded"}}
    # The normal shell owner retains approval authority. No process is launched;
    # this fixture models its pending result, never an approved execution.
    with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=lambda *_a, **_k: next(plans)) as planner, \
         patch.object(type(gateway.runtime_skills), "execute", return_value=loaded), \
         patch.object(gateway.shell, "execute", return_value={"ok": True, "status": "pending_approval", "approval_id": "fixture"}) as execute:
        result = gateway.runtime_message({"message": "Use fixture guide", "cwd": str(tmp_path),
                                          "workspace_root": str(tmp_path), "_projectContextActive": False})
    if permitted:
        execute.assert_called_once()
        assert result["shell"]["status"] == "pending_approval"
        assert not any(step.get("kind") == "policy" for step in result["steps"])
    else:
        execute.assert_not_called()
        blocked = next(step for step in result["steps"] if step.get("kind") == "policy")
        assert blocked["tool"] == "vrcforge_execute_shell"
        observed = next(step for call in planner.call_args_list for step in call.kwargs.get("loop_state", [])
                        if step.get("status") == "blocked")
        assert observed["outcome"]["error"]["code"] == "skill_tool_disallowed"
