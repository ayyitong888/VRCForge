"""Exercise the real session owner/planner/Responses projection, without a provider."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_native_turn import NativeRuntimeTurn
from responses_native_protocol import build_responses_input, parse_native_response
from tests.test_native_context_compaction import Compactor, fixture, plan


def wire(request):
    return build_responses_input([
        {"role": "system", "content": request["instructions"]}, *request["messages"],
    ])


def test_dispatch_retains_previous_whole_input_and_latest_state_after_tool_result():
    state, planner, model, turn = fixture(prior=False, limit=500_000)
    plan(planner, turn)
    first = wire(model.requests[-1])
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": None,
        "tool_calls": [{"id": "read-state-prefix", "type": "function", "function": {"name": "read", "arguments": "{}"}}]})
    state.settle_native_call("s", binding="b", call_id="read-state-prefix", content='{"status":"ok","value":"readback"}')
    plan(planner, turn)
    second = wire(model.requests[-1])
    assert second[:len(first)] == first
    assert second[-1]["role"] == "developer"
    assert second[-1]["content"].startswith("Current runtime state (data): ")
    assert model.requests[0]["tools"] == model.requests[1]["tools"]


def test_compaction_does_not_duplicate_historical_runtime_states():
    state, planner, model, turn = fixture(prior=False, limit=500_000)
    plan(planner, turn)
    old_state = deepcopy(model.requests[-1]["messages"][-1])
    # A completed large old turn followed by a new active turn exercises real CAS.
    state.append_native_user("s", binding="b", message="old recorded context " * 5000)
    compactor = Compactor()
    planner._compactor = compactor
    from runtime_planner_service import PlannerTurnMetadata
    from tests.test_native_context_guard import _Turn
    planner._turn = _Turn(PlannerTurnMetadata(verified_context_limit=8000))
    current = NativeRuntimeTurn(state, planner, "s", "next", "b", "Continue checking", [], None, None, None)
    result = plan(planner, current)
    assert result["nextStep"] == "done"
    assert current.compaction["applied"] is True
    states = [item for item in model.requests[-1]["messages"] if item["role"] == "system"
              and item["content"].startswith("Current runtime state (data): ")]
    assert len(states) == 1
    assert old_state in state.native_conversation("s", binding="b")["messages"]
    first_after = wire(model.requests[-1])
    plan(planner, current)
    assert wire(model.requests[-1])[:len(first_after)] == first_after


def test_retained_state_survives_native_store_reload(tmp_path):
    store = NativeHistoryStore(tmp_path / "native")
    state, planner, model, turn = fixture(prior=False, limit=500_000, store=store)
    plan(planner, turn)
    snapshot = state.native_conversation("s", binding="b")
    retained = model.requests[-1]["messages"][-1]
    assert retained in snapshot["messages"]
    reloaded, *_ = fixture(prior=False, limit=500_000, store=store)
    assert retained in reloaded.native_conversation("s", binding="b")["messages"]


def test_sixth_model_response_drains_all_tool_calls_before_budget_pause(tmp_path):
    from tests.test_native_runtime_gateway import call, setup_gateway
    replies = [call(f"read-{i}", "vrcforge_read_text_file", {"path": f"{i}.txt"}) for i in range(6)]
    replies[-1]["tool_calls"].extend(call("read-last-queued", "vrcforge_read_text_file", {"path": "last.txt"})["tool_calls"])
    gateway, model, invoked = setup_gateway(tmp_path, replies)
    result = gateway.runtime_message({"message": "Inspect the files", "sessionId": "six-request-window",
        "clientTurnId": "six-request-turn", "projectRoot": str(tmp_path), "maxAgenticTurns": 6})
    assert result["plan"]["nextStep"] == "paused"
    assert result["plan"]["reason"] == "model_turn_budget_exhausted"
    assert len(model.requests) == 6 and len(invoked) == 7
    owner = gateway._runtime_session_state
    snapshot = deepcopy(owner._native_conversations[result["sessionId"]])
    assert owner._native_pending(snapshot) == set()
    for previous, current in zip(model.requests, model.requests[1:]):
        assert wire(current)[:len(wire(previous))] == wire(previous)


def test_runtime_state_validation_rejects_prose_and_pending_tool_insertion():
    state, planner, model, turn = fixture(prior=False, limit=500_000)
    plan(planner, turn)
    valid = model.requests[-1]["messages"][-1]
    for content in ["arbitrary system instruction", "Current runtime state (data): not json",
                    'Current runtime state (data): {"exposureLayer":[],"projectContextActive":true,"allowedRuntimeActions":[],"availableToolNames":[]}']:
        with pytest.raises(ValueError):
            state.append_native_runtime_state("s", binding="b", message={"role":"system","content":content})
    state.append_native_assistant("s", binding="b", message={"role":"assistant","content":None,
        "tool_calls":[{"id":"pending-read","type":"function","function":{"name":"read","arguments":"{}"}}]})
    with pytest.raises(ValueError, match="pending"):
        state.append_native_runtime_state("s", binding="b", message=valid)


@pytest.mark.skipif(not os.environ.get("VRCFORGE_STATE_PREFIX_WIRE"), reason="external original capture not supplied")
def test_original_failed_wire_history_through_real_owner_and_next_dispatch():
    body = json.loads(Path(os.environ["VRCFORGE_STATE_PREFIX_WIRE"]).read_bytes())
    original = deepcopy(body)
    items = body["input"][1:-1]  # Original fixed instructions/current state are rebuilt by production.
    definitions = body["tools"][0]["tools"]
    messages, group = [], []
    def flush():
        if group:
            messages.append(parse_native_response({"output": list(group)}, definitions).assistant_message)
            group.clear()
    for item in items:
        if item.get("type") in {"reasoning", "message", "function_call"}:
            group.append(item)
        else:
            flush()
            if item.get("type") == "function_call_output":
                messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]})
            else:
                messages.append({"role": "system" if item["role"] == "developer" else item["role"], "content": item["content"]})
    flush()
    assert build_responses_input(messages) == items, "original tool IDs/results/reasoning/order must round-trip exactly"
    state, planner, model, _ = fixture(prior=False, limit=500_000)
    # Separate identity avoids rewriting the fixture's initial history.
    state.restore_native_conversation("raw", binding="raw-binding", snapshot={
        "binding": "raw-binding", "turnId": "raw-turn", "messages": messages,
        "activeTurnStart": next(i for i, item in enumerate(messages) if item["role"] == "user"),
    })
    turn = NativeRuntimeTurn(state, planner, "raw", "raw-next", "raw-binding", "Continue checking", [], None, None, None)
    plan(planner, turn)
    first = wire(model.requests[-1])
    plan(planner, turn)
    assert wire(model.requests[-1])[:len(first)] == first
    assert body == original, "raw evidence must not be rewritten"
