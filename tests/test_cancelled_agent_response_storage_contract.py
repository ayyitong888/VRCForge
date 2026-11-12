"""Cancelled producer responses must remain valid saved-chat records."""
from copy import deepcopy
import json
import os
from pathlib import Path
from threading import Event, Thread

import pytest

from agent_gateway import tool_result_digest
from session_store_integrity import _is_valid_agent_response, is_valid_chat_record
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def cancelled_response(tmp_path, monkeypatch, boundary):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-before-stop", "vrcforge_read_text_file", {"path": "fixture.txt"}),
        finish,
    ])
    state = gateway.runtime_sessions
    consume = type(state).consume_cancel_reason
    consumed_boundaries = 0

    def stop_at_boundary(owner, **identity):
        nonlocal consumed_boundaries
        if owner is not state or not state.owns_turn(**identity):
            return consume(owner, **identity)
        consumed_boundaries += 1
        if consumed_boundaries == boundary:
            assert state.mark_cancel_requested(**identity)
        return consume(owner, **identity)

    # Inject Stop at an existing cancellation seam, then use real ownership and
    # cancellation consumption through the complete native Gateway loop.
    monkeypatch.setattr(type(state), "consume_cancel_reason", stop_at_boundary)
    response = run(gateway, tmp_path)
    assert consumed_boundaries == boundary
    assert response["plan"]["nextStep"] == "cancelled"
    assert response["task"]["status"] == "cancelled"
    return response, model, invoked


@pytest.mark.parametrize("boundary,model_calls,tool_calls", [
    pytest.param(1, 0, 0, id="before-model"),
    pytest.param(2, 1, 0, id="after-model-before-dispatch"),
    pytest.param(5, 1, 1, id="between-iterations-after-compaction-check"),
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


def test_cancelled_after_model_reply_preserves_model_text(tmp_path, monkeypatch):
    """Cancellation at the post-provider seam keeps the arrived model text."""
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("stop-1", "vrcforge_runtime_action", {
            "action": "reply", "reply": "Checked the fixture before cancellation.",
        }),
    ])
    state = gateway.runtime_sessions
    consume = type(state).consume_cancel_reason
    consumed_boundaries = 0

    def stop_at_boundary(owner, **identity):
        nonlocal consumed_boundaries
        if owner is not state or not state.owns_turn(**identity):
            return consume(owner, **identity)
        consumed_boundaries += 1
        if consumed_boundaries == 2:
            assert state.mark_cancel_requested(**identity)
        return consume(owner, **identity)

    monkeypatch.setattr(type(state), "consume_cancel_reason", stop_at_boundary)
    response = run(gateway, tmp_path)
    assert consumed_boundaries == 2
    assert response["plan"]["nextStep"] == "cancelled"
    assert response["task"]["status"] == "cancelled"
    assert response["plan"]["reply"] == "Checked the fixture before cancellation."
    assert response["plan"]["summary"] == "Runtime turn was cancelled by the user."
    assert _is_valid_agent_response(response)
    assert is_valid_chat_record({
        "id": "cancelled-replied-chat", "sessionId": "native-session",
        "items": [{"id": "cancelled-replied-agent", "type": "agent", "response": response}],
    })


def test_stop_after_native_compaction_keeps_committed_compaction_receipt(tmp_path, monkeypatch):
    from runtime_planner_service import PlannerTurnMetadata
    from tests.test_native_context_compaction import Compactor
    from tests.test_native_context_guard import _Turn

    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-after-compact", "vrcforge_read_text_file", {"path": "fixture.txt"}),
        finish,
    ])
    gateway.runtime_planner._turn = _Turn(PlannerTurnMetadata(
        native_binding="fixture-provider-model-route", verified_context_limit=24_000,
    ))
    with gateway.runtime_planner.bind_turn({}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    compactor = Compactor()
    gateway.runtime_planner._compactor = compactor
    state = gateway.runtime_sessions
    state.begin_native_turn("native-session", binding=binding, turn_id="older-turn", message="Keep this instruction")
    state.append_native_assistant("native-session", binding=binding, message={
        "role": "assistant", "content": "old context " * 12_000,
    })
    consume = type(state).consume_cancel_reason

    def stop_after_tool(owner, **identity):
        if owner is state and invoked and state.owns_turn(**identity):
            assert state.mark_cancel_requested(**identity)
        return consume(owner, **identity)

    monkeypatch.setattr(type(state), "consume_cancel_reason", stop_after_tool)
    response = run(gateway, tmp_path)

    assert response["task"]["status"] == "cancelled"
    assert len(compactor.calls) == len(model.requests) == len(invoked) == 1
    stored = state.native_conversation("native-session", binding=binding)
    assert compactor.summary in stored["messages"][0]["content"]
    assert response["contextCompaction"]["applied"] is True
    assert response["contextCompaction"].get("failureClass") != "cancelled"
    assert response["contextUsage"]["compactionCount"] == 1
    assert any(event["kind"] == "context_compaction" for event in response["timeline"])


def test_stop_during_tool_keeps_result_and_skips_loop_analysis(tmp_path, monkeypatch):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-before-stop", "vrcforge_read_text_file", {"path": "fixture.txt"}), finish,
    ])
    tool_started = Event()
    release_tool = Event()
    identity = {}
    response = {}
    loop_analysis = []

    def record_tool_phase(payload):
        if payload["phase"] == "running_tool":
            identity.update({key: payload[key] for key in ("sessionId", "turnId", "clientTurnId")})

    def slow_read(params):
        invoked.append(params)
        tool_started.set()
        assert release_tool.wait(5)
        return {"text": "completed read before stop settled"}

    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", slow_read)
    monkeypatch.setattr(gateway, "_runtime_status_changed", record_tool_phase)

    def record_digest(value, **kwargs):
        if isinstance(value, dict) and value.get("text") == "completed read before stop settled":
            loop_analysis.append(value)
        return tool_result_digest(value, **kwargs)

    monkeypatch.setattr("agent_gateway.tool_result_digest", record_digest)
    worker = Thread(target=lambda: response.update(run(gateway, tmp_path)))
    worker.start()
    try:
        assert tool_started.wait(5)
        assert gateway.request_runtime_cancel({**identity, "reason": "user_stop"})["ok"]
        assert worker.is_alive(), "an already dispatched tool must settle before the turn ends"
    finally:
        release_tool.set()
        worker.join(5)

    assert response["task"]["status"] == "cancelled"
    assert len(model.requests) == len(invoked) == 1
    assert response["steps"][-1]["result"]["text"] == "completed read before stop settled"
    assert not loop_analysis


def test_stop_at_tool_dispatch_stops_work_and_survives_delayed_cleanup(tmp_path, monkeypatch):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-before-stop", "vrcforge_read_text_file", {"path": "fixture.txt"}), finish,
    ])
    cleanup_entered = Event()
    release_cleanup = Event()
    cleanup = gateway._cancel_runtime_owned_work
    cancellation = {}
    cancel_worker = None
    phases = []

    def delayed_cleanup(**identity):
        cleanup_entered.set()
        assert release_cleanup.wait(5)
        return cleanup(**identity)

    def observe_phase(payload):
        nonlocal cancel_worker
        phases.append(payload["phase"])
        if payload["phase"] == "running_tool" and cancel_worker is None:
            cancel_worker = Thread(target=lambda: cancellation.update(gateway.request_runtime_cancel({
                "sessionId": payload["sessionId"], "turnId": payload["turnId"],
                "clientTurnId": payload["clientTurnId"], "reason": "user_stop",
            })))
            cancel_worker.start()
            assert cleanup_entered.wait(5)

    monkeypatch.setattr(gateway, "_cancel_runtime_owned_work", delayed_cleanup)
    monkeypatch.setattr(gateway, "_runtime_status_changed", observe_phase)
    try:
        response = run(gateway, tmp_path)
        assert len(model.requests) == 1
        assert not invoked, "Stop was accepted before the tool was dispatched"
        assert response["plan"]["nextStep"] == "cancelled"
        assert phases[-1] == "running_tool", "a stopped turn cannot return to a running phase"
        # The terminal row is consumed independently from the Stop HTTP reply.
        terminal = gateway.runtime_runs.list_runs(session_id="native-session")["runs"][0]
        assert terminal["status"] == "cancelled"
        assert terminal["cancellationReason"] == "user_stop"
        assert not cancellation, "terminal row must have the reason before cleanup completes"
    finally:
        release_cleanup.set()
        if cancel_worker:
            cancel_worker.join(5)
    assert cancellation.get("ok") is True


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
