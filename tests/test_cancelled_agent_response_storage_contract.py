"""Cancelled producer responses must remain valid saved-chat records."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from session_store_integrity import _is_valid_agent_response, is_valid_chat_record
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def cancelled_response(tmp_path, monkeypatch, boundary):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-before-stop", "vrcforge_read_text_file", {"path": "fixture.txt"}),
        finish,
    ])
    state = gateway.runtime_sessions
    consume = type(state).consume_cancel_request
    consumed_boundaries = 0

    def stop_at_boundary(owner, **identity):
        nonlocal consumed_boundaries
        if owner is not state:
            return consume(owner, **identity)
        consumed_boundaries += 1
        if consumed_boundaries == boundary:
            assert state.mark_cancel_requested(**identity)
        return consume(owner, **identity)

    # Inject Stop at an existing cancellation seam, then use real ownership and
    # cancellation consumption through the complete native Gateway loop.
    monkeypatch.setattr(type(state), "consume_cancel_request", stop_at_boundary)
    response = run(gateway, tmp_path)
    assert consumed_boundaries == boundary
    assert response["plan"]["nextStep"] == "cancelled"
    assert response["task"]["status"] == "cancelled"
    return response, model, invoked


@pytest.mark.parametrize("boundary,model_calls,tool_calls", [
    pytest.param(1, 0, 0, id="before-model"),
    pytest.param(2, 1, 0, id="after-model-before-dispatch"),
    pytest.param(4, 1, 1, id="between-iterations-after-compaction-check"),
])
def test_cancelled_native_response_is_valid_for_saved_chat(
    tmp_path, monkeypatch, boundary, model_calls, tool_calls,
):
    response, model, invoked = cancelled_response(tmp_path, monkeypatch, boundary)

    assert len(model.requests) == model_calls
    assert len(invoked) == tool_calls
    assert response["plan"].get("shellNeeded") is False
    assert _is_valid_agent_response(response)
    assert is_valid_chat_record({
        "id": "cancelled-chat", "sessionId": "native-session",
        "items": [{"id": "cancelled-agent-item", "type": "agent", "response": response}],
    })
    # A malformed producer still fails the unchanged strict storage contract.
    for malformed in (None, 0, "false"):
        broken = deepcopy(response)
        broken["plan"]["shellNeeded"] = malformed
        assert not _is_valid_agent_response(broken)


def test_original_cancelled_response_replays_with_producer_contract_only(tmp_path, monkeypatch):
    """Opt-in raw incident replay; private evidence never enters source fixtures."""
    source = os.environ.get("VRCFORGE_CANCELLED_RESPONSE_REPLAY")
    if not source:
        pytest.skip("Set VRCFORGE_CANCELLED_RESPONSE_REPLAY to retained raw result.json")
    raw = Path(source).read_bytes()
    recorded = json.loads(raw)["response"]
    original = deepcopy(recorded)
    assert recorded["plan"]["nextStep"] == "cancelled"
    assert "shellNeeded" not in recorded["plan"]
    assert not _is_valid_agent_response(recorded)

    produced, _, _ = cancelled_response(tmp_path, monkeypatch, 2)
    # Replay the actual original envelope with only the producer's missing bool;
    # do not normalize write results, steps, timeline, usage or task evidence.
    replay = deepcopy(recorded)
    replay["plan"]["shellNeeded"] = produced["plan"]["shellNeeded"]
    assert _is_valid_agent_response(replay)
    assert is_valid_chat_record({"id": "incident-replay", "items": [
        {"id": "incident-agent", "type": "agent", "response": replay},
    ]})
    del replay["plan"]["shellNeeded"]
    assert replay == original
    assert recorded == original
    assert Path(source).read_bytes() == raw


def test_plan_mode_guard_terminal_is_valid_for_saved_chat(tmp_path, monkeypatch):
    gateway, model, invoked = setup_gateway(tmp_path, [])
    # Exercise the host guard against a planner proposal that ignores Plan mode;
    # no Shell dispatch or provider request is permitted.
    monkeypatch.setattr(gateway.runtime_planner, "plan_agent_turn", lambda *a, **k: {
        "summary": "Planner proposed a command", "planner": "llm",
        "shellNeeded": True, "shellCommand": "must-not-execute",
    })
    response = run(gateway, tmp_path, planMode=True)

    assert response["plan"]["nextStep"] == "plan_mode_blocked"
    assert not model.requests and not invoked
    assert _is_valid_agent_response(response)
    assert response["plan"]["planner"] == "runtime"
    assert response["plan"]["shellNeeded"] is False
    assert is_valid_chat_record({"id": "plan-blocked-chat", "items": [
        {"id": "plan-blocked-agent", "type": "agent", "response": response},
    ]})


def test_compaction_guard_terminal_is_valid_for_saved_chat(tmp_path, monkeypatch):
    gateway, model, invoked = setup_gateway(tmp_path, [])
    planner = gateway.runtime_planner
    # The legacy compaction guard runs between completed tool iterations.
    monkeypatch.setattr(planner, "native_conversation_binding", lambda _root: "")
    monkeypatch.setattr(planner, "plan_agent_turn", lambda *a, **k: {
        "summary": "Read fixture", "planner": "llm", "shellNeeded": False,
        "skillNeeded": True, "skillTool": "vrcforge_read_text_file",
        "skillParams": {"path": "fixture.txt"}, "continueLoop": True,
        "nextStep": "call_skill",
    })
    monkeypatch.setattr(planner, "maybe_compact_runtime_history", lambda **k: (
        k.get("history") or [], None, True,
    ))
    response = run(gateway, tmp_path)

    assert response["plan"]["nextStep"] == "context_compaction_required"
    assert not model.requests and len(invoked) == 1
    assert _is_valid_agent_response(response)
    assert response["plan"]["planner"] == "runtime"
    assert response["plan"]["shellNeeded"] is False
    assert is_valid_chat_record({"id": "compaction-blocked-chat", "items": [
        {"id": "compaction-blocked-agent", "type": "agent", "response": response},
    ]})
