"""A provider token boundary is not a transport disconnect or a valid receipt."""
import sys
import types

import pytest

from runtime_planner_service import RuntimePlannerService
from tests.test_provider_protocol_clients import _native_chat_settings
from tests.test_runtime_planner_service import FakeModel, PlannerModelResult, service
from vrchat_blendshape_agent import request_openai_compatible_plan_with_metadata


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("finish, expected", [
    ("length", "provider_token_limit"),
    (None, None),
    ("content_filter", None),
])
def test_native_token_limit_is_distinct_without_reissue(monkeypatch, streaming, finish, expected):
    calls = []
    partial = {"role": "assistant", "reasoning_content": "private-reasoning",
               "tool_calls": [{"index": 0, "id": "partial", "type": "function",
                               "function": {"name": "read", "arguments": "{"}}]}

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            choice = {"finish_reason": finish, "delta" if streaming else "message": partial}
            response = {"choices": [choice], "usage": {"prompt_tokens": 12, "completion_tokens": 8}}
            return iter([response]) if streaming else response

    class Client:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=Client))
    with pytest.raises(RuntimeError) as caught:
        request_openai_compatible_plan_with_metadata(
            _native_chat_settings(), "continue", native_tools=[],
            stream_callback=(lambda text: None) if streaming else None,
        )
    code = RuntimePlannerService._planner_failure_code(caught.value)
    if expected:
        assert code == expected
        assert "finish_reason=length" in str(caught.value)
        assert "private-reasoning" not in str(caught.value)
        model = FakeModel(PlannerModelResult("unused"), error=caught.value)
        plan = service(model=model)._llm_plan_agent_turn(
            "continue", {}, [], loop_state=[{"tool": "vrcforge_scan_materials", "status": "executed",
                                            "result": {"materialCount": 3}}],
            context_usage={"requestCount": 1},
        )
        assert plan["plannerFailure"]["code"] == expected
        assert plan["plannerFailure"]["retryable"] is False
        assert plan["continueLoop"] is False
        assert plan["skillNeeded"] is False and plan["writeNeeded"] is False
        assert plan["providerConnected"] is True
        assert "结果也已保留" in plan["reply"]
        assert "token" in plan["reply"]
        assert "连接或账户" not in plan["reply"]
    else:
        assert code != "provider_token_limit"
    assert len(calls) == 1


def test_token_limit_initial_failure_has_no_claim_of_prior_tools():
    plan = service()._planner_failure_plan(
        cause_code="provider_token_limit", phase="initial", planner_label="fixture")
    assert plan["plannerFailure"]["retryable"] is False
    assert "结果也已保留" not in plan["reply"]
    assert "token" in plan["reply"]
