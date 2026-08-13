from agent_budget_policy import (
    DEFAULT_AGENT_BUDGET_POLICY,
    AgentBudgetPolicy,
    freeze_agent_budget_policy,
)


def test_default_budget_does_not_make_tool_calls_the_normal_stop():
    policy = freeze_agent_budget_policy({})
    assert policy.max_model_turns is None
    assert policy.normal_tool_call_limit is None


def test_policy_is_bounded_and_legacy_keys_are_accepted():
    policy = freeze_agent_budget_policy(
        {"maxSteps": 7, "maxToolCalls": 9, "modelTurns": 11}
    )
    assert policy.max_model_turns == 11
    assert policy.normal_tool_call_limit is None
    assert freeze_agent_budget_policy({"maxSteps": 7}).max_model_turns == 7


def test_budget_exhaustion_explains_pause_without_claiming_done():
    policy = AgentBudgetPolicy(max_model_turns=2)
    decision = policy.check(model_turns_used=2, tool_calls_used=1, remaining_action={"tool": "read"})
    assert decision["paused"] is True
    assert decision["remainingAction"]["tool"] == "read"
    assert decision["reason"] == "model_turn_budget_exhausted"
    assert decision["nextStep"] == "paused"


def test_more_than_old_256_tool_calls_do_not_pause_the_turn():
    policy = freeze_agent_budget_policy({})
    decision = policy.check(model_turns_used=4097, tool_calls_used=257)
    assert decision["paused"] is False


def test_explicit_model_turn_limit_remains_a_non_completion_safety_stop():
    policy = freeze_agent_budget_policy({"maxAgenticTurns": 128})
    decision = policy.check(model_turns_used=128, tool_calls_used=1)
    assert decision["paused"] is True
    assert decision["reason"] == "model_turn_budget_exhausted"
    assert decision["nextStep"] == "paused"
    assert freeze_agent_budget_policy({"maxModelTurns": 128}).max_model_turns == 128
