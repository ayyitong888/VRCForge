from __future__ import annotations

import asyncio
import threading

import dashboard_server as host
from tests.test_native_runtime_gateway import setup_gateway


def test_background_relay_dispatch_overlaps_independent_chats_and_serializes_each_chat(tmp_path, monkeypatch):
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    third_started = threading.Event()
    messages = {
        "first": "Verify the glossary wording.",
        "second": "Check the glossary examples.",
        "third": "Test the unrelated import workflow.",
    }

    def model_step(request):
        relay = next(row["content"] for row in reversed(request["messages"])
                     if row.get("role") == "user"
                     and isinstance(row.get("content"), str)
                     and row["content"].startswith("[VRCForge agent relay.")
                     and any(message in row["content"] for message in messages.values()))
        if messages["first"] in relay:
            first_started.set()
            assert release_first.wait(12), "The glossary turn was never released"
            reply = "Glossary wording verified."
        elif messages["second"] in relay:
            second_started.set()
            reply = "Glossary examples checked."
        else:
            third_started.set()
            reply = "Import workflow tested."
        return {"role": "assistant", "content": reply}

    gateway, model, _ = setup_gateway(tmp_path, [model_step] * 3)
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(host, "RUNTIME_QUEUE_PORT", gateway)
    monkeypatch.setattr(host, "EVENT_BUS", host.DashboardEventBus())
    monkeypatch.setattr(host, "_CROSS_SESSION_CONVERSATION_SERVICE", None)
    monkeypatch.setattr(host, "chat_transcripts_path", lambda: tmp_path / "app-chats.json")
    monkeypatch.setattr(host, "chat_project_index_path", lambda: tmp_path / "chat-projects.json")
    monkeypatch.setattr(host, "CHAT_REQUESTED_PROJECT_PATHS", {})

    for chat in ("requester", "glossary", "import-test"):
        host._create_handoff_chat({
            "id": chat, "sessionId": f"{chat}-session", "title": chat,
            "revision": 1, "projectPath": "", "projectType": "general", "items": [],
        }, "")

    service = host.cross_session_conversation_runtime()

    def request(action):
        return {"_trustedConversationContext": {
            "chatId": "requester", "sessionId": "requester-session", "clientTurnId": "request-turn",
            "actionId": action, "projectType": "general", "maxAgenticTurns": 3,
        }}

    def submit(action, target):
        result = service.followup_task({**request(action), "chatId": target, "message": messages[action]})
        assert result["status"] == "queued", result
        return result["clientTurnId"]

    async def result(target, turn, wait=0):
        return await asyncio.to_thread(service.read_conversation, {
            **request("read"), "chatId": target, "clientTurnId": turn, "waitSeconds": wait,
        })

    async def exercise():
        host.EVENT_BUS.set_loop(asyncio.get_running_loop())
        service.start()
        try:
            first = submit("first", "glossary")
            assert await asyncio.to_thread(first_started.wait, 5)
            second = submit("second", "glossary")
            third = submit("third", "import-test")
            assert await asyncio.to_thread(third_started.wait, 5), "The unrelated chat is blocked behind the glossary turn"
            third_result = await result("import-test", third, 5)
            assert third_result["completed"] and third_result["reply"]["text"] == "Import workflow tested."
            first_result = await result("glossary", first)
            assert first_result["status"] == "running" and first_result["completed"] is False
            second_result = await result("glossary", second)
            assert second_result["status"] == "queued" and second_result["completed"] is False
            assert not second_started.is_set()

            release_first.set()
            first_result = await result("glossary", first, 5)
            second_result = await result("glossary", second, 5)
            assert first_result["completed"] and first_result["reply"]["text"] == "Glossary wording verified."
            assert second_result["completed"] and second_result["reply"]["text"] == "Glossary examples checked."
        finally:
            release_first.set()
            assert (await asyncio.to_thread(service.shutdown))["ok"]

    asyncio.run(exercise())
    assert len(model.requests) == 3
    assert [item["status"] for item in gateway.list_runtime_followups(session_id="glossary")] == ["acked", "acked"]
    assert [item["status"] for item in gateway.list_runtime_followups(session_id="import-test")] == ["acked"]
