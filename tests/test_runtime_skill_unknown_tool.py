"""Unknown runtime tools return canonical failure through the real executor."""
from dataclasses import replace
from unittest.mock import Mock

from agent_runtime_skill_executor import AgentRuntimeSkillExecutor
from tests.test_agent_runtime_skill_executor import make_executor


def test_unknown_tool_returns_failure_without_invocation_or_permission_dereference():
    executor, events = make_executor()
    forbidden = Mock(side_effect=AssertionError("Unknown tool reached invocation or permission checks"))
    executor = AgentRuntimeSkillExecutor(replace(executor._ports,
        computer_use_model_invocable=forbidden, tool_visible=forbidden,
        inject_user_constraints=forbidden, invoke_tool=forbidden,
        request_supervised_write=forbidden))

    result = executor.execute("missing-runtime-tool", {"requested": "value"}, "agent", "owner")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["tool"] == "missing-runtime-tool"
    assert result["error"] == "Unknown skill: missing-runtime-tool"
    assert result["outcome"]["status"] == "needs_user_action"
    assert result["outcome"]["success"] is False
    assert result["outcome"]["summary"] == result["error"]
    assert events == ["lock-enter", ("prepare", "missing-runtime-tool", {"packageId": "missing-runtime-tool"}), "lock-exit"]
    forbidden.assert_not_called()
