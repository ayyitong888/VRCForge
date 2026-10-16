"""Current-turn transaction compaction keeps instructions and live authority intact."""
from copy import deepcopy
import json

import pytest

from tests.test_native_context_compaction import Compactor, fixture, plan


def settled_turn():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, prior=False, current="Inspect this avatar; do not write assets.")
    state.append_native_assistant("s", binding="b", message={
        "role": "assistant", "content": None, "reasoning_content": "PRIVATE_CURRENT_REASONING",
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "read", "arguments": "{}"}}
                       for call_id in ["read-a", "read-b"]],
    })
    for call_id in ["read-a", "read-b"]:
        state.settle_native_call("s", binding="b", call_id=call_id,
                                content=json.dumps({"status": "ok", "data": "observed property " * 3000}))
    state.append_native_user("s", binding="b", message="Keep the original materials and report only.")
    return state, planner, model, turn, compactor


def test_single_turn_settled_multicall_compacts_and_keeps_all_user_instructions():
    state, planner, model, turn, compactor = settled_turn()
    before = turn.snapshot()
    result = plan(planner, turn)
    assert result["nextStep"] == "done"
    assert turn.compaction["applied"] is True
    assert len(compactor.calls) == 1
    projected = compactor.calls[0][0]
    transactions = [entry["text"] for entry in projected if "Historical tool transaction" in entry["text"]]
    assert len(transactions) == 1 and all(call_id in transactions[0] for call_id in ["read-a", "read-b"])
    assert "PRIVATE_CURRENT_REASONING" not in str(projected)
    assert [m for m in turn.messages() if m["role"] == "user"] == [m for m in before["messages"] if m["role"] == "user"]
    assert not any(m["role"] == "tool" or m.get("tool_calls") for m in turn.messages())
    assert state._native_pending(turn.snapshot()) == set()
    assert turn.snapshot()["activeTurnStart"] == 1
    assert model.requests[-1]["messages"][-1]["role"] == "system"


@pytest.mark.parametrize("status,tool,result", [
    ("pending", "read", {}), ("running", "read", {}),
    ("approval_pending", "write", {}), ("pending_approval", "write", {}),
    ("needs_user_action", "vrcforge_ask_user", {"questionId": "question-live"}),
])
def test_current_turn_waits_never_enter_compactor(status, tool, result):
    state, planner, model, turn, compactor = settled_turn()
    turn.admit({"role": "assistant", "content": None, "tool_calls": [
        {"id": "waiting", "type": "function", "function": {"name": tool, "arguments": "{}"}}]})
    turn.next_receipt()
    turn._loop_state.append({"status": status, "tool": tool, "result": result})
    turn.settle()
    before = deepcopy(turn.snapshot())
    outcome = plan(planner, turn)
    assert outcome["nextStep"] == "context_compaction_required"
    assert turn.compaction["failureClass"] == "pending_tool_calls"
    assert turn.snapshot() == before and not compactor.calls and not model.requests
    assert state._native_pending(before) == {"waiting"}


@pytest.mark.parametrize("change", ["steering", "pending", "stop"])
def test_current_turn_compaction_race_does_not_overwrite_live_state(change):
    state, planner, model, turn, compactor = settled_turn()
    before = turn.snapshot()
    def race():
        if change == "steering":
            state.append_native_user("s", binding="b", message="New authoritative steering")
        elif change == "pending":
            state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": None,
                "tool_calls": [{"id": "new-pending", "type": "function", "function": {"name": "write", "arguments": "{}"}}]})
        else:
            state.mark_cancel_requested(session_id="s", turn_id="current")
    compactor.hook = race
    plan(planner, turn)
    assert turn.compaction["applied"] is False and len(compactor.calls) == 1
    assert turn.snapshot()["messages"][:len(before["messages"])] == before["messages"]
    assert not model.requests
