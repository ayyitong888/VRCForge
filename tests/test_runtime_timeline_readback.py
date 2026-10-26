"""Exact durable public timeline readback, independent of global ledger churn."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_agent_runtime_run_ledger import make_ledger
from agent_runtime_event_projection import project_runtime_turn_event


def add_timeline(ledger, sequence, *, session="session", client="client", **extra):
    ledger.append_timeline_event(
        session_id=session, turn_id="turn", client_turn_id=client,
        event={"id": f"event-{sequence}", "sequence": sequence, "kind": "assistant",
               "timestamp": "2026-10-23T13:00:00Z",
               "payload": {"summary": f"Visible {sequence}", "status": "streaming", **extra}},
    )


def test_long_timeline_keeps_first_page_and_run_start(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    rows = [{"event": "runtime_turn_started", "status": "running",
             "sessionId": "session", "turnId": "turn", "clientTurnId": "client",
             "createdAt": "2026-10-23T13:00:00Z"}]
    for sequence in range(10_005):
        rows.append({"event": "runtime_turn_timeline_event", "sessionId": "session",
                     "turnId": "turn", "clientTurnId": "client",
                     "timelineEvent": {"id": f"event-{sequence}", "sequence": sequence,
                                       "kind": "assistant", "payload": {"summary": str(sequence)}}})
    ledger.log_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.log_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    first = ledger.read_timeline(session_id="session", client_turn_id="client", limit=2)
    assert [row["sequence"] for row in first["events"]] == [0, 1]
    assert first["run"]["startedAt"] == rows[0]["createdAt"]
    last = ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=10_003)
    assert [row["sequence"] for row in last["events"]] == [10_003, 10_004]


def test_exact_timeline_pages_survive_unrelated_global_churn(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running", "sessionId": "session",
                   "turnId": "turn", "clientTurnId": "client", "model": "model"})
    for sequence in range(5):
        add_timeline(ledger, sequence)
    for sequence in range(150):
        add_timeline(ledger, sequence, session="other")
    first = ledger.read_timeline(session_id="session", client_turn_id="client", limit=2)
    assert [event["sequence"] for event in first["events"]] == [0, 1]
    assert first["nextSequence"] == 2 and first["hasMore"] is True
    assert first["run"]["status"] == "running"
    assert first["run"]["startedAt"] == "2026-08-09T00:00:00+00:00"
    second = ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=2, limit=2)
    last = ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=4, limit=2)
    assert [e["sequence"] for e in first["events"] + second["events"] + last["events"]] == list(range(5))
    assert last["nextSequence"] == 5 and last["hasMore"] is False
    empty = ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=5)
    assert empty["events"] == [] and empty["nextSequence"] == 5
    assert empty["run"]["status"] == "running"
    add_timeline(ledger, 5)
    assert ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=5)["events"][0]["sequence"] == 5


def test_timeline_append_uses_cached_byte_cursor(tmp_path, monkeypatch):
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running", "sessionId": "session",
                   "turnId": "turn", "clientTurnId": "client"})
    add_timeline(ledger, 0)
    ledger.read_timeline(session_id="session", client_turn_id="client", limit=2)
    cache = ledger._list_runs_cache[("timeline", "session", "client", "")]
    offset = cache["offset"]
    add_timeline(ledger, 1)

    original_open = Path.open
    reads = []

    class SpyHandle:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): return self.handle.__exit__(*args)
        def seek(self, *args): return self.handle.seek(*args)
        def tell(self): return self.handle.tell()
        def read(self, size=-1):
            reads.append((self.handle.tell(), size))
            return self.handle.read(size)

    def spy_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        return SpyHandle(handle) if path == ledger.log_path and "b" in mode else handle

    monkeypatch.setattr(Path, "open", spy_open)
    page = ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=1)
    assert [event["sequence"] for event in page["events"]] == [1]
    assert reads and reads[0][0] == offset
    assert all(0 < size <= 1024 * 1024 for _, size in reads)
    assert sum(size for _, size in reads) == ledger.log_path.stat().st_size - offset


@pytest.mark.parametrize("filters", [{"session_id": "session"}, {"client_turn_id": "client"},
                                    {"session_id": "session", "client_turn_id": "client"}])
def test_run_discovery_filters_before_global_tail(tmp_path, filters):
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running", "sessionId": "session",
                   "turnId": "turn", "clientTurnId": "client"})
    for sequence in range(150):
        add_timeline(ledger, sequence, session="other", client="other-client")
    assert ledger.list_runs(limit=1, **filters)["runs"][0]["clientTurnId"] == "client"


def test_timeline_readback_is_exact_bounded_public_and_read_only(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running", "sessionId": "session",
                   "turnId": "turn", "clientTurnId": "client", "runtimeOwnerNonce": "PRIVATE",
                   "reasoning": "COT", "arguments": {"secret": "RAW"}})
    add_timeline(ledger, 0, arguments={"secret": "RAW"}, reasoning="COT")
    add_timeline(ledger, 0, session="session-prefix")
    add_timeline(ledger, 0, client="client-prefix")
    size = ledger.log_path.stat().st_size
    result = ledger.read_timeline(session_id="session", client_turn_id="client", limit=5000)
    assert len(result["events"]) == 1
    assert result["run"]["turnId"] == "turn"
    assert not any(private in json.dumps(result) for private in ("PRIVATE", "COT", "RAW", "arguments", "runtimeOwnerNonce"))
    assert ledger.log_path.stat().st_size == size
    assert ledger.read_timeline(session_id="unknown", client_turn_id="client")["run"] is None
    with pytest.raises(Exception):
        ledger.read_timeline(session_id="", client_turn_id="client")


def test_terminal_state_survives_later_cancel_and_has_exact_public_continuation(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    identity = {"sessionId": "session", "turnId": "turn", "clientTurnId": "client"}
    ledger.append({**identity, "event": "runtime_turn_started", "status": "running"})
    add_timeline(ledger, 0)
    terminal = ledger.append({**identity, "event": "runtime_turn_completed", "status": "completed",
                             "continuationEvent": {**identity, "schema": "vrcforge.runtime_turn_event.v1",
                                 "continuationSource": "foreground_completed", "plan": {"nextStep": "done", "reply": "Finished"}}})
    ledger.append({**identity, "event": "runtime_turn_cancel_requested", "status": "cancel_requested"})
    result = ledger.read_timeline(session_id="session", client_turn_id="client", after_sequence=1)
    assert result["run"]["status"] == "completed"
    assert result["run"]["completedAt"] == terminal["createdAt"]
    continuation = ledger.read_terminal_continuation(session_id="session", client_turn_id="client", turn_id="turn")
    assert continuation["schema"] == "vrcforge.runtime_turn_event.v1"
    assert continuation["continuationSource"] == "foreground_completed"
    assert continuation["completedAt"] == terminal["createdAt"]
    assert ledger.read_terminal_continuation(session_id="session", client_turn_id="client", turn_id="other") is None


@pytest.mark.parametrize("terminal_status", ["completed", "awaiting_question"])
def test_app_broadcast_reuses_persisted_terminal_without_mutating_response(tmp_path, monkeypatch, terminal_status):
    import dashboard_server
    ledger, _ = make_ledger(tmp_path)
    identity = {"sessionId": "session", "turnId": "turn", "clientTurnId": "client"}
    terminal = ledger.append({**identity, "event": "runtime_turn_completed", "status": terminal_status,
        **({"continuationEvent": {**identity, "schema": "vrcforge.runtime_turn_event.v1",
            "continuationSource": "foreground_completed", "plan": {"nextStep": "done", "reply": "Finished"}}}
           if terminal_status == "completed" else {})})
    response = {**identity, "ok": True, "plan": {"nextStep": "done", "reply": "Finished"}}
    published = []
    async def broadcast(name, payload):
        published.append((name, payload))
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", SimpleNamespace(
        runtime_message=lambda *_args, **_kwargs: response, runtime_runs=ledger,
        approval_transactions=SimpleNamespace(list_approvals=lambda: [])))
    monkeypatch.setattr(dashboard_server, "EVENT_BUS", SimpleNamespace(broadcast=broadcast))
    monkeypatch.setattr(dashboard_server, "TELEGRAM_SERVICE", SimpleNamespace(notify_foreground_result=lambda _: None))
    monkeypatch.setattr(dashboard_server, "attach_agent_harness_journey_receipt", lambda value: value)
    returned = asyncio.run(dashboard_server.app_agent_runtime_message(
        dashboard_server.AgentRuntimeMessageRequest(message="Finish", session_id="session", client_turn_id="client")))
    event = next(payload for name, payload in published if name == "agentRuntimeTurn")
    assert event == (project_runtime_turn_event(terminal["continuationEvent"])
                     if terminal_status == "completed" else response)
    assert returned is response and "continuationSource" not in response and "schema" not in response


def test_authenticated_app_route_uses_exact_ledger_readback(tmp_path, monkeypatch):
    import dashboard_server
    ledger, _ = make_ledger(tmp_path)
    add_timeline(ledger, 0)
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", SimpleNamespace(runtime_runs=ledger))
    assert dashboard_server.app_agent_runtime_timeline(sessionId="session", clientTurnId="client")["events"][0]["sequence"] == 0
    assert any(route.path == "/api/app/agent/runs/timeline" for route in dashboard_server.app.routes)


def test_timeline_route_inherits_existing_app_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import dashboard_server
    ledger, _ = make_ledger(tmp_path)
    add_timeline(ledger, 0)
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", SimpleNamespace(runtime_runs=ledger))
    monkeypatch.setattr(dashboard_server, "APP_AUTH_REQUIRED", True)
    monkeypatch.setattr(dashboard_server, "APP_SESSION_TOKEN", "fixture-session-token")
    monkeypatch.setattr(dashboard_server, "record_debug_interaction", lambda *_args, **_kwargs: None)
    # No lifespan context: the request must not bootstrap any live services.
    client = TestClient(dashboard_server.app, client=("127.0.0.1", 51000))
    url = "/api/app/agent/runs/timeline?sessionId=session&clientTurnId=client"
    assert client.get(url).status_code == 401
    authorized = client.get(url, headers={"Authorization": "Bearer fixture-session-token"})
    assert authorized.status_code == 200
    assert authorized.json()["events"][0]["sequence"] == 0
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(url + "-unknown", headers={"Authorization": "Bearer fixture-session-token"}).json()["run"] is None
    client.close()
