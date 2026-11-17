from __future__ import annotations

import asyncio
from unittest.mock import Mock

import dashboard_server as host
from tests.test_native_runtime_gateway import setup_gateway


def test_expired_relay_lease_recovers_existing_terminal_result_without_dispatch(tmp_path, monkeypatch):
    turn_id = "agent-forward-expired-lease"
    session_id = "session-b"
    lane = "chat-b"
    input_text = "Review the glossary index."
    reply = "The index was reviewed."
    first_gateway, _, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": reply}])
    first_gateway._runtime_followup_queue.LEASE_SECONDS = 0
    queued = first_gateway.enqueue_runtime_followup({
        "sessionId": lane, "runtimeSessionId": session_id, "clientTurnId": turn_id, "message": input_text,
    })
    assert first_gateway.claim_runtime_followups(session_id=lane, owner_id="old-process")
    first = first_gateway.runtime_message({"session_id": session_id, "chatId": lane,
                                           "clientTurnId": turn_id, "message": input_text})
    assert first["plan"]["reply"] == reply
    assert first_gateway.list_runtime_followups(session_id=lane)[0]["status"] == "claimed"

    # A restart happens after the runtime commits, before the queue ACK.
    restored, duplicate_model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Duplicate dispatch."}])
    assert restored.get_runtime_session(session_id, client_turn_id=turn_id)["status"] == "completed"
    monkeypatch.setattr(host, "AGENT_GATEWAY", restored)
    monkeypatch.setattr(host, "RUNTIME_QUEUE_PORT", restored)
    monkeypatch.setattr(host, "TELEGRAM_SERVICE", Mock())
    monkeypatch.setattr(host, "EVENT_BUS", host.DashboardEventBus())
    monkeypatch.setattr(host, "_CROSS_SESSION_CONVERSATION_SERVICE", None)
    request = host.AgentRuntimeMessageRequest(message=input_text, session_id=session_id, chatId=lane,
                                             clientTurnId=turn_id, followupQueueId=queued["queueId"], followupLaneId=lane)
    recovered = asyncio.run(host._execute_app_agent_runtime_message(request, human_user_message=False))
    assert recovered["plan"]["reply"] == reply
    assert duplicate_model.requests == []
    assert restored.list_runtime_followups(session_id=lane)[0]["status"] == "acked"
