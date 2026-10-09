from threading import Event
from types import SimpleNamespace
from time import monotonic

import pytest

import dashboard_server as dashboard
from runtime_planner_service import PlannerModelResult
from tests.test_native_runtime_gateway import setup_gateway, run, call
from tests.test_runtime_planner_dashboard_wiring import fixture_config
from runtime_provider_steering import interrupt_for_steer, ProviderSteeredError


USAGE = {"exact": True, "inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 40}


@pytest.mark.parametrize("receipt", ["unknown", "failed_usage", "completed"])
def test_inflight_steer_closes_request_and_replans_without_old_actions(tmp_path, monkeypatch, receipt):
    gateway, _, invoked = setup_gateway(tmp_path, [])
    model = dashboard._RuntimePlannerModel(SimpleNamespace(current_config=fixture_config))
    gateway.runtime_planner._model = model
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard.PROVIDER_TEXT_PROBE, "probe_settings", lambda _: SimpleNamespace())
    requests, observed = [], {}

    def request(_settings, _prompt, *, cancel_event, native_messages, **kwargs):
        requests.append(native_messages)
        if len(requests) == 1:
            started = monotonic()
            assert gateway.runtime_sessions.submit_steer(
                session_id="native-session", target_client_turn_id="native-turn",
                input_id="direction-1", message="Stop reading and just reply",
            )["accepted"]
            observed["interrupted"] = cancel_event.wait(1.0)
            observed["latency"] = monotonic() - started
            if receipt == "completed":
                return PlannerModelResult("", usage=USAGE, assistant_message=call(
                    "obsolete", "vrcforge_read_text_file", {"path": "old.txt"}), finish_reason="tool_calls")
            error = RuntimeError("fixture request closed")
            if receipt == "failed_usage":
                error.provider_usage = USAGE
            raise error
        assert not cancel_event.is_set()
        return PlannerModelResult("", usage=USAGE, assistant_message={"role": "assistant", "content": "Changed direction"},
                                  finish_reason="stop")

    monkeypatch.setattr(dashboard, "request_llm_plan_with_metadata", request)
    try:
        result = run(gateway, tmp_path)
    finally:
        assert model.shutdown(1)["ok"]
    assert observed["interrupted"], "steering must signal the in-flight provider, not wait for its reply"
    assert observed["latency"] < 0.5
    assert len(requests) == 2
    assert sum(m.get("content") == "Stop reading and just reply" for m in requests[1]) == 1
    assert result["plan"]["reply"] == "Changed direction"
    assert invoked == []
    usage = result["contextUsage"]
    assert usage["requestCount"] == 2
    assert usage["inputTokens"] == (100 if receipt == "unknown" else 200)
    assert usage["exact"] is (receipt != "unknown")


def owner_fixture():
    return SimpleNamespace(cancellation=Event(), done=Event(), response=None, error=None)


@pytest.mark.parametrize("excluded", ["review_or_compaction", "other_turn", "stop", "drained"])
def test_steering_does_not_cancel_unrelated_or_excluded_work(tmp_path, excluded):
    gateway, _, _ = setup_gateway(tmp_path, [])
    state = gateway.runtime_sessions
    state.begin_turn(session_id="s", turn_id="server", client_turn_id="c")
    state.submit_steer(session_id="s", target_client_turn_id="c", input_id="one", message="adjust")
    context = {"sessionId": "s", "turnId": "server", "clientTurnId": "c"}
    if excluded == "other_turn":
        context["clientTurnId"] = "other"
    elif excluded == "stop":
        state.mark_cancel_requested(session_id="s", client_turn_id="c")
    elif excluded == "drained":
        state.drain_steer(session_id="s", client_turn_id="c")
    owner = owner_fixture()
    interrupt_for_steer(owner, state, context, enabled=excluded != "review_or_compaction", join_seconds=0)
    assert not owner.cancellation.is_set()


def test_unfinished_worker_stays_owned_and_steer_is_not_consumed(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    state = gateway.runtime_sessions
    state.begin_turn(session_id="s", turn_id="server", client_turn_id="c")
    state.submit_steer(session_id="s", target_client_turn_id="c", input_id="one", message="adjust")
    context = {"sessionId": "s", "turnId": "server", "clientTurnId": "c"}
    owner = owner_fixture()
    interrupt_for_steer(owner, state, context, enabled=True, join_seconds=0)
    assert owner.cancellation.is_set()
    assert state.steer_pending(session_id="s", client_turn_id="c")
    assert not state.cancel_requested(session_id="s", client_turn_id="c")
    owner.done.set()
    with pytest.raises(ProviderSteeredError) as failure:
        interrupt_for_steer(owner, state, context, enabled=True, join_seconds=0)
    assert failure.value.provider_usage is None
    assert state.drain_steer(session_id="s", client_turn_id="c") == [{"inputId": "one", "message": "adjust"}]
