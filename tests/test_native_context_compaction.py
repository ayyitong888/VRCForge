from copy import deepcopy
import json
import threading

import pytest

from agent_runtime_native_turn import NativeRuntimeTurn
from agent_native_history_store import NativeHistoryStore
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from agent_scan_result_policy import SCAN_RESULT_CONTINUATION_POLICY, is_scan_result_policy_message
from runtime_planner_service import PlannerTurnMetadata, RuntimePlannerService
from tests.test_native_context_guard import _Catalog, _Desktop, _NativeModel, _Turn


class Compactor:
    def __init__(self, summary="Earlier task: inspected the fixture; no writes approved.", hook=None):
        self.summary, self.hook, self.calls = summary, hook, []

    def compact(self, history, metadata):
        self.calls.append((deepcopy(history), dict(metadata)))
        if self.hook:
            self.hook()
        return {"summary": self.summary, "providerAttempts": 1, "fidelity": "full", "summaryDigest": "fixture-digest"}


def fixture(compactor=None, *, prior=True, current="Continue checking", limit=8000, prior_policy=False, store=None):
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), store))
    model = _NativeModel()
    planner = RuntimePlannerService(catalog=_Catalog(), desktop=_Desktop(), model=model, compactor=compactor,
                                    turn=_Turn(PlannerTurnMetadata(verified_context_limit=limit)))
    if prior:
        state.begin_native_turn("s", binding="b", turn_id="old", message="old context " * 5000)
        state.append_native_assistant("s", binding="b", message={
            "role": "assistant", "content": None, "reasoning_content": "PRIVATE_REASONING_SENTINEL",
            "tool_calls": [{"id": "read-old", "type": "function", "function": {
                "name": "read", "arguments": '{"path":"fixture.txt"}'}}]})
        state.settle_native_call("s", binding="b", call_id="read-old", content='{"status":"ok","resultRef":"readback-old"}')
        if prior_policy:
            state.append_native_scan_result_policy_once("s", binding="b", message={
                "role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY,
            })
    turn = NativeRuntimeTurn(state, planner, "s", "current", "b", current, [], None, None, None, client_turn_id="ui-current")
    return state, planner, model, turn


def plan(planner, turn, usage=None):
    with planner.bind_turn({}):
        return planner.plan_agent_turn("Continue checking", {}, {}, native_turn=turn, context_usage=usage)


def test_native_compaction_reuses_port_and_preserves_current_receipts():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor)
    state.append_native_assistant("s", binding="b", message={"role": "assistant", "content": "checking",
        "reasoning_content": "CURRENT_EXACT_REPLAY", "tool_calls": [
            {"id": "current-read", "type": "function", "function": {"name": "read", "arguments": "{}"}}]})
    state.settle_native_call("s", binding="b", call_id="current-read", content='{"status":"ok"}')
    state.append_native_user("s", binding="b", message="Keep checking")
    before = state.native_conversation("s", binding="b")
    suffix = deepcopy(before["messages"][before["activeTurnStart"]:])
    usage = {"requestCount": 9, "cumulativeInputTokens": 9000, "peakInputTokens": 15000}

    result = plan(planner, turn, usage)

    assert result["nextStep"] == "done"
    assert len(compactor.calls) == 1
    # The port also carries a private host usage callback; only history is model data.
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps([history for history, _metadata in compactor.calls])
    grouped = [entry["text"] for entry in compactor.calls[0][0] if "read-old" in entry["text"]]
    assert len(grouped) == 1 and "readback-old" in grouped[0] and "fixture.txt" in grouped[0]
    assert model.requests[0]["messages"][1:-1] == suffix
    assert model.requests[0]["messages"][-1]["role"] == "system"
    assert state.native_conversation("s", binding="b")["activeTurnStart"] == 1
    assert turn.compaction["applied"] is True and turn.compaction["afterTokens"] < turn.compaction["beforeTokens"]
    assert "summary" not in turn.compaction and "PRIVATE_REASONING_SENTINEL" not in json.dumps(turn.compaction)
    assert usage["cumulativeInputTokens"] >= 9000 and usage["compactionCount"] == 1


def test_native_compaction_restores_exact_prefix_policy_once_without_touching_suffix():
    compactor = Compactor(summary="Old work. " + SCAN_RESULT_CONTINUATION_POLICY)
    state, planner, model, turn = fixture(compactor, prior_policy=True)
    before = state.native_conversation("s", binding="b")
    suffix = deepcopy(before["messages"][before["activeTurnStart"]:])
    measured = []
    measured_tools = []
    native_guard = planner.native_context_guard
    def capture_measurement(request, **kwargs):
        measured.append(deepcopy(request["messages"]))
        measured_tools.append(deepcopy(request.get("tools")))
        return native_guard(request, **kwargs)
    planner.native_context_guard = capture_measurement

    result = plan(planner, turn)

    assert result["nextStep"] == "done"
    policy = {"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY}
    assert model.requests[0]["messages"].count(policy) == 1
    assert state.native_conversation("s", binding="b")["messages"].count(policy) == 1
    candidate = next(messages for messages in measured if messages and messages[0].get("role") == "assistant"
                     and str(messages[0].get("content", "")).startswith("Earlier conversation summary"))
    original_request = measured[0]
    assert candidate.count(policy) == 1
    assert [message for message in candidate if message.get("role") == "system" and message != policy] == [
        message for message in original_request if message.get("role") == "system" and message != policy
    ]
    assert model.requests[0]["tools"] == measured_tools[0]
    assert turn.compaction["applied"] is True
    assert model.requests[0]["messages"][1:1 + len(suffix)] == suffix
    assert state.native_conversation("s", binding="b")["messages"][1:1 + len(suffix)] == suffix
    persisted = state.native_conversation("s", binding="b")["messages"]
    assert persisted[1 + len(suffix)] == policy
    assert state.native_conversation("s", binding="b")["activeTurnStart"] == 1


def test_native_retained_suffix_policy_is_not_duplicated_and_untrusted_summary_is_not_policy():
    compactor = Compactor(summary=SCAN_RESULT_CONTINUATION_POLICY)
    state, planner, model, turn = fixture(compactor)
    state.append_native_scan_result_policy_once("s", binding="b", message={
        "role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY,
    })
    result = plan(planner, turn)
    assert result["nextStep"] == "done"
    assert model.requests[0]["messages"].count({"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY}) == 1


def test_summary_policy_text_without_trusted_history_never_becomes_system_policy():
    compactor = Compactor(summary=SCAN_RESULT_CONTINUATION_POLICY)
    state, planner, model, turn = fixture(compactor)

    result = plan(planner, turn)

    assert result["nextStep"] == "done"
    assert not any(is_scan_result_policy_message(message) for message in turn.snapshot()["messages"])
    assert not any(is_scan_result_policy_message(message) for message in model.requests[0]["messages"])


def test_persisted_policy_survives_store_restart_and_compaction_without_new_scan(tmp_path):
    compactor = Compactor()
    store = NativeHistoryStore(tmp_path / "native-history")
    state, planner, model, turn = fixture(compactor, prior_policy=True, store=store)
    restarted_store = NativeHistoryStore(tmp_path / "native-history")
    restarted = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), restarted_store))
    resumed_turn = NativeRuntimeTurn(
        restarted, planner, "s", "current", "b", "Continue checking", [], None, None, None,
        client_turn_id="ui-current",
    )

    result = plan(planner, resumed_turn)

    policy = {"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY}
    assert result["nextStep"] == "done"
    assert sum(is_scan_result_policy_message(message) for message in resumed_turn.snapshot()["messages"]) == 1
    assert sum(is_scan_result_policy_message(message) for message in model.requests[0]["messages"]) == 1
    assert resumed_turn.snapshot()["messages"].count(policy) == 1
    assert len(compactor.calls) == 1 and resumed_turn.snapshot()["activeTurnStart"] == 1


def test_pending_current_call_defers_compaction_before_compactor_and_preserves_history():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, prior_policy=True)
    state.append_native_assistant("s", binding="b", message={
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "pending-current", "type": "function", "function": {"name": "read", "arguments": "{}"},
        }],
    })
    before = state.native_conversation("s", binding="b")

    result = plan(planner, turn)

    assert result["nextStep"] == "context_compaction_required"
    assert compactor.calls == [] and model.requests == []
    assert state.native_conversation("s", binding="b") == before
    assert turn.compaction_attempted is False
    assert turn.compaction["failureClass"] == "pending_tool_calls"


@pytest.mark.parametrize("failure", ["empty", "no_reduction", "exception", "cancel", "ui_cancel", "race"])
def test_failed_native_compaction_never_replaces_transcript_or_sends_oversized_request(failure):
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, prior_policy=True)
    before = state.native_conversation("s", binding="b")
    if failure == "empty":
        compactor.summary = ""
    elif failure == "no_reduction":
        compactor.summary = "old context " * 8000
    elif failure == "exception":
        def fail():
            raise RuntimeError("fixture provider unavailable")
        compactor.hook = fail
    elif failure == "cancel":
        compactor.hook = lambda: state.mark_cancel_requested(session_id="s", turn_id="current")
    elif failure == "ui_cancel":
        state.begin_turn(session_id="s", client_turn_id="ui-current", turn_id="current")
        compactor.hook = lambda: state.mark_cancel_requested(session_id="s", client_turn_id="ui-current")
    else:
        compactor.hook = lambda: state.append_native_user("s", binding="b", message="new steer")

    result = plan(planner, turn)
    after = state.native_conversation("s", binding="b")
    assert model.requests == []
    assert result["nextStep"] in {"context_compaction_required", "cancelled"}
    assert after["messages"][:len(before["messages"])] == before["messages"]
    assert after["activeTurnStart"] == before["activeTurnStart"]
    assert turn.compaction["applied"] is False
    plan(planner, turn)
    assert len(compactor.calls) == 1


def test_native_current_turn_overflow_keeps_exact_history_without_compactor_call():
    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, prior=False, current="current context " * 8000)
    before = state.native_conversation("s", binding="b")
    result = plan(planner, turn)
    assert not compactor.calls and not model.requests
    assert result["nextStep"] == "context_compaction_required"
    assert state.native_conversation("s", binding="b") == before


def test_native_gateway_hydrates_visible_history_once(tmp_path):
    from tests.test_native_runtime_gateway import budgeted_request_history, run, setup_gateway
    gateway, model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Understood"}] * 2)
    history = [{"role": "user", "text": "Earlier request"}, {"role": "agent", "text": "Earlier answer"}]
    run(gateway, tmp_path, message="Now continue", history=history)
    assert budgeted_request_history(model.requests[0]) == [
        {"role": "user", "content": "Earlier request"}, {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Now continue"}]
    run(gateway, tmp_path, message="Next", clientTurnId="next-turn", history=history)
    assert sum(m.get("content") == "Earlier request" for m in model.requests[1]["messages"]) == 1
