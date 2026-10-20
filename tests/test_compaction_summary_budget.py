from copy import deepcopy
import pytest
from context_compaction import compact_context
from tests.test_native_context_compaction import Compactor, fixture, plan


def test_prompt_honors_real_100_token_target_without_256_floor():
    prompts = []
    result = compact_context([{"role":"user","text":"older history"}], target_tokens=100,
        summarizer=lambda prompt: prompts.append(prompt) or "summary")
    assert result["targetTokens"] == 100
    assert "below 100 tokens" in prompts[0]
    assert "below 256 tokens" not in prompts[0]


def test_native_summary_budget_reserves_retained_request_and_schema_overhead():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, prior_policy=True)
    plan(planner, turn)
    assert turn.compaction["applied"] is True
    target = compactor.calls[0][1]["targetTokens"]
    assert 64 <= target < turn.compaction["targetAfterTokens"]
    assert "Continue checking" not in str(compactor.calls[0][0])
    assert state._native_pending(turn.snapshot()) == set()


def test_native_no_room_for_minimum_summary_never_calls_compactor():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, current="retained current data " * 1200, limit=8000)
    before = deepcopy(turn.snapshot())
    plan(planner, turn)
    assert compactor.calls == []
    assert turn.compaction["applied"] is False
    assert turn.snapshot()["messages"][:len(before["messages"])] == before["messages"]


@pytest.mark.parametrize("requested,effective", [(1,64),(64,64),(100,100)])
def test_existing_summary_minimum_is_preserved(requested,effective):
    prompts=[]
    result=compact_context([{"role":"user","text":"old"}],target_tokens=requested,
        summarizer=lambda prompt: prompts.append(prompt) or "summary")
    assert result["targetTokens"] == effective
    assert f"below {effective} tokens" in prompts[0]
