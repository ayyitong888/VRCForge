"""Permanent chat deletion must discard the runtime session state."""

import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

import dashboard_server
from agent_gateway import AgentGateway, AgentGatewayError
from dashboard_api_models import ChatTranscriptsRequest


def _delete(chat_id: str) -> dict:
    return asyncio.run(dashboard_server.app_delete_agent_chat(chat_id))


def _isolated_gateway(monkeypatch, tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit", load_mcp_resources=False)
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "chat_transcripts_path", lambda: tmp_path / "chat-transcripts.json")
    monkeypatch.setattr(dashboard_server, "chat_project_index_path", lambda: tmp_path / "chat-projects.json")
    return gateway


def _store_revisions():
    app_target = dashboard_server.chat_store_target(dashboard_server.chat_transcripts_path(), scope="app")
    return [
        {**dashboard_server.scan_session_store(app_target), "scope": "app"},
        {**dashboard_server.scan_session_store(dashboard_server.chat_project_index_target()), "scope": "app-index"},
    ]


def test_delete_one_of_two_durable_chats_keeps_shared_history(monkeypatch, tmp_path):
    gateway = _isolated_gateway(monkeypatch, tmp_path)
    session_id = "shared-session"
    store = gateway.runtime_sessions._ports.native_history_store
    store.save(session_id, {"binding": "fixture", "messages": [{"role": "user", "content": "retained history"}]}, {})
    removed, survivor = [
        {"id": chat_id, "sessionId": session_id, "items": [{"id": f"user-{chat_id}", "type": "user", "text": chat_id}]}
        for chat_id in ("removed", "survivor")
    ]
    dashboard_server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[removed, survivor]))
    dashboard_server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[survivor], source_revisions=_store_revisions()))

    assert _delete(f" {session_id} ") == {"ok": True, "removed": False}
    assert store.load(session_id)["native"]["messages"][0]["content"] == "retained history"

    dashboard_server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[], source_revisions=_store_revisions()))
    assert _delete(session_id) == {"ok": True, "removed": True}
    assert store.load(session_id) is None


def test_delete_rechecks_reference_added_while_runtime_settles(monkeypatch, tmp_path):
    gateway = _isolated_gateway(monkeypatch, tmp_path)
    session_id = "rebound-session"
    store = gateway.runtime_sessions._ports.native_history_store
    store.save(session_id, {"binding": "fixture", "messages": []}, {})
    real_guard = gateway.runtime_session_idle_guard
    chat = {"id": "rebound", "sessionId": session_id, "items": [{"id": "user-rebound", "type": "user", "text": "rebound"}]}

    @contextmanager
    def guard(guarded_session_id, timeout_seconds=None):
        with real_guard(guarded_session_id, timeout_seconds):
            yield
        if timeout_seconds == 15.0:
            dashboard_server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))

    monkeypatch.setattr(gateway, "runtime_session_idle_guard", guard)
    assert _delete(session_id) == {"ok": True, "removed": False}
    assert store.load(session_id) is not None


def test_delete_keeps_indexed_project_reference(monkeypatch, tmp_path):
    gateway = _isolated_gateway(monkeypatch, tmp_path)
    session_id = "project-shared-session"
    store = gateway.runtime_sessions._ports.native_history_store
    store.save(session_id, {"binding": "fixture", "messages": []}, {})
    project_path = tmp_path / "project"
    project_path.mkdir()
    chat = {"id": "project-owner", "sessionId": session_id, "projectPath": str(project_path),
            "items": [{"id": "user-project", "type": "user", "text": "project history"}]}
    dashboard_server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    assert _delete(session_id) == {"ok": True, "removed": False}
    assert store.load(session_id) is not None


def test_delete_fails_closed_when_an_indexed_project_is_unavailable(monkeypatch, tmp_path):
    gateway = _isolated_gateway(monkeypatch, tmp_path)
    session_id = "unknown-project-owner"
    store = gateway.runtime_sessions._ports.native_history_store
    store.save(session_id, {"binding": "fixture", "messages": []}, {})
    dashboard_server.chat_project_index_path().write_text(
        json.dumps({"version": 1, "projectPaths": [str(tmp_path / "unavailable")]}), encoding="utf-8",
    )
    with pytest.raises(HTTPException) as error:
        _delete(session_id)
    assert getattr(error.value, "status_code", None) == 409
    assert store.load(session_id) is not None


def test_delete_chat_discards_runtime_session_and_is_idempotent() -> None:
    sessions = dashboard_server.AGENT_GATEWAY.runtime_sessions
    sessions._sessions["chat-sess-test"] = {"id": "chat-sess-test", "turns": []}
    assert sessions.get_session("chat-sess-test") is not None

    result = _delete("chat-sess-test")
    assert result == {"ok": True, "removed": True}
    assert sessions.get_session("chat-sess-test") is None

    again = _delete("chat-sess-test")
    assert again == {"ok": True, "removed": True}
    assert sessions.get_session("chat-sess-test") is None


def test_delete_unknown_chat_is_a_noop_success() -> None:
    result = _delete("chat-never-existed")
    assert result == {"ok": True, "removed": True}


def _patch_delete_ports(monkeypatch, sessions, request_cancel, idle_guard):
    monkeypatch.setattr(type(dashboard_server.AGENT_GATEWAY), "runtime_sessions", property(lambda _self: sessions))
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "request_runtime_cancel", request_cancel)
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "runtime_session_idle_guard", idle_guard)


def test_delete_cancels_and_waits_under_idle_guard_before_discard(monkeypatch):
    calls = []

    class Sessions:
        def discard_session(self, session_id):
            calls.append(("discard", session_id))

    def request_cancel(payload):
        calls.append(("cancel", payload))
        return {"ok": True, "status": "cancel_requested"}

    @contextmanager
    def idle_guard(session_id, timeout_seconds=None):
        calls.append(("guard_enter", session_id, timeout_seconds))
        yield
        calls.append(("guard_exit", session_id))

    _patch_delete_ports(monkeypatch, Sessions(), request_cancel, idle_guard)

    result = _delete("runtime-session")

    assert result == {"ok": True, "removed": True}
    assert [call[0] for call in calls] == ["cancel", "guard_enter", "guard_exit", "guard_enter", "discard", "guard_exit"]
    assert calls[0][1] == {"sessionId": "runtime-session", "reason": "chat_deleted"}
    assert calls[1][2] is not None


def test_delete_timeout_returns_conflict_without_discard(monkeypatch):
    calls = []

    class Sessions:
        def discard_session(self, session_id):
            calls.append(("discard", session_id))

    def request_cancel(payload):
        calls.append(("cancel", payload))
        return {"ok": True, "status": "cancel_requested"}

    @contextmanager
    def idle_guard(_session_id, timeout_seconds=None):
        assert timeout_seconds is not None
        raise AgentGatewayError("session still busy", status_code=409)
        yield

    _patch_delete_ports(monkeypatch, Sessions(), request_cancel, idle_guard)

    try:
        _delete("runtime-session")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("busy deletion should return HTTP 409")

    assert [call[0] for call in calls] == ["cancel"]
