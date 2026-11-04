"""Permanent chat deletion must discard the runtime session state."""

import asyncio
from contextlib import contextmanager

import dashboard_server
from agent_gateway import AgentGatewayError


def _delete(chat_id: str) -> dict:
    return asyncio.run(dashboard_server.app_delete_agent_chat(chat_id))


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
    assert [call[0] for call in calls] == ["cancel", "guard_enter", "discard", "guard_exit"]
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
