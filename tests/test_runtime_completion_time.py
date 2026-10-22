"""Terminal timestamps use durable run time across delivery and reconnect."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from agent_runtime_event_projection import project_runtime_turn_event
from tests.test_agent_runtime_run_ledger import make_ledger
from tests.test_native_runtime_gateway import run, setup_gateway


def continuation(**fields):
    return {
        "continuationSource": "foreground_completed",
        "sessionId": "session",
        "turnId": "turn",
        "clientTurnId": "client",
        "plan": {"nextStep": "done"},
        **fields,
    }


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "denied"])
def test_terminal_time_matches_durable_event_and_reconnect(tmp_path, status):
    ledger, _ = make_ledger(tmp_path)
    terminal = ledger.append({
        "event": "runtime_turn_completed", "status": status,
        "continuationEvent": continuation(completedAt="wrong-message-time"),
    })
    expected = terminal["createdAt"]
    assert terminal["completedAt"] == expected
    assert terminal["continuationEvent"]["completedAt"] == expected
    assert project_runtime_turn_event(terminal["continuationEvent"])["completedAt"] == expected
    restarted, _ = make_ledger(tmp_path)
    assert restarted.list_runtime_continuations()[0]["completedAt"] == expected


@pytest.mark.parametrize("status", ["blocked", "awaiting_question", "needs_user_action", "pending_approval", "running"])
def test_waiting_runtime_boundary_never_marks_task_complete(tmp_path, status):
    ledger, _ = make_ledger(tmp_path)
    terminal = ledger.append({
        "event": "runtime_turn_completed", "status": status,
        "continuationEvent": continuation(
            continuationSource="question_answered", completedAt="stale-prior-turn-time",
            plan={"nextStep": "needs_user_action"},
        ),
    })
    assert "completedAt" not in terminal
    assert "completedAt" not in terminal["continuationEvent"]
    assert "completedAt" not in ledger.list_runtime_continuations()[0]


def test_legacy_replay_keeps_terminal_time_when_later_receipt_replaces_projection(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    ledger.log_path.parent.mkdir(parents=True)
    events = [
        {"event": "runtime_turn_completed", "status": "failed",
         "createdAt": "2026-10-22T22:05:23.405718+00:00", "continuationEvent": continuation()},
        {"event": "runtime_turn_journey_receipt_issued", "status": "completed",
         "createdAt": "2026-10-22T22:05:30+00:00",
         "continuationEvent": continuation(harnessJourneyReceipt={"receiptId": "receipt"})},
    ]
    ledger.log_path.write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
    replay = ledger.list_runtime_continuations()[0]
    assert replay["completedAt"] == events[0]["createdAt"]
    assert replay["harnessJourneyReceipt"]["receiptId"] == "receipt"
    assert ledger.list_runtime_continuations() == [replay]


@pytest.mark.parametrize("cancel", [False, True])
def test_gateway_http_terminal_and_replay_share_one_time(tmp_path, cancel):
    def reply(_request):
        if cancel:
            gateway.runtime_sessions.mark_cancel_requested(
                session_id="native-session", client_turn_id="native-turn",
            )
        return {"role": "assistant", "content": "Finished"}

    gateway, _, _ = setup_gateway(tmp_path, [reply])
    result = run(gateway, tmp_path)
    terminal = next(row for row in gateway.runtime_runs.read_events(limit=0)
                    if row.get("event") == "runtime_turn_completed")
    assert terminal["status"] == ("cancelled" if cancel else "completed")
    assert result["completedAt"] == terminal["createdAt"]
    assert gateway.runtime_runs.list_runtime_continuations()[0]["completedAt"] == result["completedAt"]
    frozen = gateway.get_runtime_session("native-session", client_turn_id="native-turn")
    assert frozen["response"]["completedAt"] == result["completedAt"]
    started = next(row for row in gateway.runtime_runs.read_events(limit=0)
                   if row.get("event") == "runtime_turn_started")
    assert result["startedAt"] == started["createdAt"]
    assert gateway.runtime_runs.list_runs()["runs"][0]["startedAt"] == started["createdAt"]
    assert gateway.runtime_runs.list_runtime_continuations()[0]["startedAt"] == started["createdAt"]


def test_app_completion_broadcast_uses_same_time_as_http_and_frozen_response(tmp_path, monkeypatch):
    import dashboard_server

    gateway, _, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Finished"}])
    published = []

    async def broadcast(name, payload):
        published.append((name, payload))

    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "EVENT_BUS", SimpleNamespace(broadcast=broadcast))
    monkeypatch.setattr(dashboard_server, "TELEGRAM_SERVICE", SimpleNamespace(notify_foreground_result=lambda _: None))
    monkeypatch.setattr(dashboard_server, "attach_agent_harness_journey_receipt", lambda payload: payload)
    response = asyncio.run(dashboard_server.app_agent_runtime_message(
        dashboard_server.AgentRuntimeMessageRequest(
            message="Finish", session_id="native-session", client_turn_id="native-turn",
            project_root=str(tmp_path), max_agentic_turns=5,
        )
    ))
    event = next(payload for name, payload in published if name == "agentRuntimeTurn")
    terminal = next(row for row in gateway.runtime_runs.read_events(limit=0)
                    if row.get("event") == "runtime_turn_completed")
    assert event["completedAt"] == response["completedAt"] == terminal["createdAt"]
    assert gateway.get_runtime_session("native-session", client_turn_id="native-turn")["response"]["completedAt"] == terminal["createdAt"]


def test_gateway_exception_broadcast_and_replay_share_terminal_time(tmp_path, monkeypatch):
    published = []
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit", runtime_turn_completed=published.append)

    def fail(params, **_kwargs):
        params.update(_runtimeTurnStarted=True, _resolvedRuntimeSessionId="session",
                      _resolvedRuntimeTurnId="turn", _resolvedRuntimeClientTurnId="client")
        raise AgentGatewayError("Offline fixture failure", status_code=409)

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", fail)
    with pytest.raises(AgentGatewayError):
        gateway._runtime_message_impl({"message": "Fixture"})
    terminal = next(row for row in gateway.runtime_runs.read_events(limit=0)
                    if row.get("event") == "runtime_turn_completed")
    assert published[0]["completedAt"] == terminal["createdAt"]
    assert gateway.runtime_runs.list_runtime_continuations()[0]["completedAt"] == published[0]["completedAt"]
    assert gateway.get_runtime_session("session", client_turn_id="client")["response"]["completedAt"] == terminal["createdAt"]


@pytest.mark.parametrize("reported_model", ["provider-actual-model", ""])
@pytest.mark.parametrize("source", ["foreground_completed", "question_answered"])
def test_completion_model_uses_provider_receipt_not_configured_model(tmp_path, monkeypatch, reported_model, source):
    gateway, model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Finished"}])
    original = model.plan_native
    monkeypatch.setattr(model, "plan_native", lambda request: replace(original(request), model=reported_model))
    params = {"message": "Finish", "sessionId": "native-session", "clientTurnId": "native-turn",
              "projectRoot": str(tmp_path), "model": "configured-alias", "maxAgenticTurns": 5}
    if source == "foreground_completed":
        response = gateway.runtime_message(params)
    else:
        with gateway.runtime_planner.bind_turn(params):
            response = gateway._runtime_message_impl(params, task_continuation={
                "source": source,
                "context": {"objective": "Finish", "sessionId": "native-session", "projectRoot": str(tmp_path)},
                "completion": {"status": "completed"},
            })
    event = gateway.runtime_runs.list_runtime_continuations()[0]
    assert response.get("model", "") == reported_model
    assert event.get("model", "") == reported_model
    assert event["continuationSource"] == source


@pytest.mark.parametrize("reported_model", ["provider-actual-model", ""])
def test_exception_model_uses_last_actual_response_without_config_fallback(tmp_path, monkeypatch, reported_model):
    published = []
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit", runtime_turn_completed=published.append)

    def fail(params, **_kwargs):
        params.update(_runtimeTurnStarted=True, _resolvedRuntimeSessionId="session",
                      _resolvedRuntimeTurnId="turn", _resolvedRuntimeClientTurnId="client",
                      _runtimeFailureProgress={"model": reported_model})
        raise AgentGatewayError("Offline fixture failure", status_code=409)

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", fail)
    with pytest.raises(AgentGatewayError):
        gateway._runtime_message_impl({"message": "Fixture", "model": "configured-alias"})
    assert published[0].get("model", "") == reported_model
    assert gateway.runtime_runs.list_runtime_continuations()[0].get("model", "") == reported_model


def test_run_start_is_stable_even_after_progress_evicts_start_from_recent_window(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    started_at = "2026-10-22T22:28:16.234138+00:00"
    identity = {"sessionId": "session", "turnId": "turn", "clientTurnId": "client"}
    ledger.append({**identity, "event": "runtime_turn_started", "status": "running", "createdAt": started_at})
    for _ in range(110):
        ledger.append({**identity, "event": "runtime_turn_timeline_event", "status": "running",
                       "createdAt": "2026-10-22T22:28:44.353736+00:00"})
    restarted, _ = make_ledger(tmp_path)
    run = restarted.list_runs(limit=1)["runs"][0]
    assert run["createdAt"] != started_at
    assert run["startedAt"] == started_at
    assert ledger.list_runs(limit=1)["runs"][0]["startedAt"] == started_at


@pytest.mark.parametrize("projection", ["list_runs", "list_runtime_continuations"])
def test_poll_projection_extracts_start_times_without_a_second_scan(tmp_path, monkeypatch, projection):
    ledger, _ = make_ledger(tmp_path)
    identity = {"sessionId": "session", "turnId": "turn", "clientTurnId": "client"}
    started = ledger.append({**identity, "event": "runtime_turn_started", "status": "running"})
    ledger.append({**identity, "event": "runtime_turn_completed", "status": "completed",
                   "continuationEvent": continuation()})
    cls = type(ledger)
    original = cls.read_events
    scans = []

    def read(state, **kwargs):
        scans.append(kwargs)
        return original(state, **kwargs)

    monkeypatch.setattr(cls, "read_events", read)
    # Recovery has its own existing scan; this checks the projection's read.
    monkeypatch.setattr(cls, "recover_interrupted_runs", lambda self: 0)
    monkeypatch.setattr(cls, "_start_times", lambda self: pytest.fail("extra full-ledger start scan"))
    result = getattr(ledger, projection)()
    projected = result["runs"][0] if projection == "list_runs" else result[0]
    assert projected["startedAt"] == started["createdAt"]
    assert len(scans) == 1
    assert scans[0]["start_times"][("session", "turn")] == started["createdAt"]
