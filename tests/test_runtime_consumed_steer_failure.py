"""Confirmed steer receipts survive an unexpected terminal runtime failure."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_gateway import AgentGateway
from agent_runtime_native_turn import NativeRuntimeTurn


def _native(gateway, sid="receipt-session", cid="receipt-client", callback=None):
    gateway.runtime_sessions.begin_turn(session_id=sid, turn_id="receipt-turn", client_turn_id=cid)
    return NativeRuntimeTurn(
        gateway.runtime_sessions, SimpleNamespace(native_result_observation=lambda item, **_kw: item), sid, "receipt-turn",
        "offline-binding", "original task", [], None, None, None, client_turn_id=cid,
        on_steer_consumed=callback,
    )


def test_native_receipts_require_successful_nonterminal_context_insertion(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    events = []
    native = _native(gateway, callback=events.append)
    native.queue_steers([{"inputId": "confirmed-1", "message": "same text"}, {"inputId": "not-confirmed-2", "message": "same text"}])
    assert native.consumed_steer_input_ids == []
    original = gateway.runtime_sessions.__class__.append_native_user

    def append(state, *args, **kwargs):
        if len(native.consumed_steer_input_ids) == 1:
            raise ValueError("context insertion failed")
        return original(state, *args, **kwargs)

    monkeypatch.setattr(gateway.runtime_sessions.__class__, "append_native_user", append)
    with pytest.raises(ValueError, match="context insertion failed"):
        native.settle()
    assert native.consumed_steer_input_ids == ["confirmed-1"]
    assert events == [["confirmed-1"]], "publish immediately after the first successful insertion, before terminal"
    returned = native.consumed_steer_input_ids
    returned.append("forged")
    assert native.consumed_steer_input_ids == ["confirmed-1"]


@pytest.mark.parametrize("terminal", ["cancelled", "interrupted"])
def test_terminal_cleanup_never_confirms_queued_but_unconsumed_inputs(tmp_path, terminal):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    events = []
    native = _native(gateway, callback=events.append)
    native.queue_steers([{"inputId": "not-confirmed", "message": "pending input"}])
    native.settle({"nextStep": terminal})
    assert native.consumed_steer_input_ids == []
    assert events == []


def test_live_delta_receipt_is_published_before_the_next_model_request(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, run
    events = []
    holder = {}

    def first_request(_request):
        assert holder["gateway"].runtime_sessions.submit_steer(
            session_id="native-session", target_client_turn_id="native-turn",
            input_id="live-confirmed", message="ordinary added direction",
        )["accepted"]
        return {"role": "assistant", "content": "Earlier response"}

    def second_request(request):
        receipts = [event for event in events if event.get("consumedSteerInputIds")]
        assert receipts == [{"sessionId": "native-session", "turnId": receipts[0]["turnId"],
                             "clientTurnId": "native-turn", "consumedSteerInputIds": ["live-confirmed"]}]
        assert any(message.get("role") == "user" and message.get("content") == "ordinary added direction"
                   for message in request["messages"])
        return {"role": "assistant", "content": "Final response"}

    gateway, _, _ = setup_gateway(tmp_path, [first_request, second_request])
    holder["gateway"] = gateway
    gateway._runtime_status_changed = events.append
    result = run(gateway, tmp_path)
    assert result["consumedSteerInputIds"] == ["live-confirmed"]


def test_presentation_callback_failure_does_not_retract_consumption(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    def failed_callback(_ids):
        raise RuntimeError("event listener unavailable")
    native = _native(gateway, callback=failed_callback)
    native.queue_steers([{"inputId": "confirmed", "message": "added direction"}])
    native.settle()
    assert native.consumed_steer_input_ids == ["confirmed"]


def test_original_unexpected_failure_preserves_exact_confirmed_ids_and_replay(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    receipt_ids = ["original-steer-1", "original-steer-2"]
    error_message = "'ExecutionTargetBindingRegistry' object has no attribute 'bound_target_snapshot'"
    fixture_path = os.environ.get("VRCFORGE_STEER_FAILURE_REPLAY")
    if fixture_path:
        raw = json.loads(Path(fixture_path).read_text(encoding="utf-8-sig"))
        error_message = raw["terminal"]["error"]["message"]
        if raw.get("replayInputIds"):
            receipt_ids = raw["replayInputIds"]
    published = []
    monkeypatch.setattr(gateway, "_runtime_turn_completed", published.append)

    def body(params, **_kwargs):
        native = _native(gateway)
        params.update(_runtimeTurnStarted=True, _resolvedRuntimeSessionId="receipt-session",
                      _resolvedRuntimeTurnId="receipt-turn", _resolvedRuntimeClientTurnId="receipt-client")
        params["_runtimeFailureProgress"] = {"_nativeTurn": native, "_nativeBinding": True, "steps": [], "timeline": []}
        native.queue_steers([{"inputId": input_id, "message": "same input text"} for input_id in receipt_ids])
        native.settle()
        gateway.runtime_sessions.submit_steer(session_id="receipt-session", target_client_turn_id="receipt-client",
                                              input_id="late-unconsumed", message="same input text")
        raise AttributeError(error_message)

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", body)
    with pytest.raises(AttributeError, match="bound_target_snapshot"):
        gateway._runtime_message_impl({"message": "ordinary task"})
    terminal = next(row for row in reversed(gateway._runtime_run_ledger.read_events(limit=20))
                    if row.get("event") == "runtime_turn_completed")
    event = terminal["continuationEvent"]
    assert event["consumedSteerInputIds"] == receipt_ids
    assert event["plan"]["nextStep"] == "failed"
    assert event["plan"]["reply"] == error_message
    assert published == [event]
    turn = gateway.get_runtime_session("receipt-session")["session"]["turns"][-1]
    assert turn["consumedSteerInputIds"] == receipt_ids
    assert "late-unconsumed" not in event["consumedSteerInputIds"]
    replay = gateway._runtime_run_ledger.list_runtime_continuations(limit=20)
    assert any(row.get("consumedSteerInputIds") == receipt_ids for row in replay)
    replay_output = os.environ.get("VRCFORGE_STEER_FAILURE_OUTPUT")
    if replay_output:
        Path(replay_output).write_text(json.dumps({
            "events": [event],
            "chat": {"id": "failure-replay-chat", "sessionId": "receipt-session", "items": [
                {"id": "original-user", "type": "user", "text": "ordinary task"},
            ]},
        }), encoding="utf-8")
