"""Permanent chat deletion must discard the runtime session state."""

import asyncio

import dashboard_server


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
