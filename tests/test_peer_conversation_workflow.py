from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import Mock

import dashboard_server as host
from tests.test_native_runtime_gateway import call, setup_gateway


def test_app_turn_delegates_to_existing_conversation_and_uses_its_result(tmp_path, monkeypatch):
    source_id, target_id = "relay-source", "relay-target"
    source_session, target_session = "relay-source-session", "relay-target-session"
    evidence = "The target's glossary uses the term blend shape."
    state = {}
    calls = []
    release_target = threading.Event()

    def source_reads(request):
        content = next(row["content"] for row in request["messages"] if row.get("tool_call_id") == "delegate")
        observation = json.loads(content)["observations"][0]["observation"]
        evidence_payload = json.JSONDecoder().raw_decode(observation.split("structuredEvidence=", 1)[1])[0]
        receipt = evidence_payload["data"]
        assert receipt["status"] == "queued"
        release_target.set()
        return call("read-report", "read_conversation", {
            "chatId": target_id, "clientTurnId": receipt["clientTurnId"], "waitSeconds": 5,
        })

    def source_finishes(request):
        messages = [row for row in request["messages"] if row.get("tool_call_id") == "read-report"]
        assert evidence in json.dumps(messages), messages
        return {"role": "assistant", "content": f"Glossary verification: {evidence}"}

    def target_starts(request):
        wire = json.dumps(request["messages"])
        assert "I maintain the glossary." in wire
        assert f"delegated from conversation {source_id}" in wire
        assert "Please verify the glossary terminology." in wire
        assert release_target.wait(4)
        return call("target-note", "send_message", {
            "chatId": source_id, "message": "I checked the glossary and am returning the result.",
        })

    def model_step(request):
        # A and B can run concurrently; route the deterministic model by
        # the distinct turns it actually receives.
        as_target = any(isinstance(row.get("content"), str)
                        and row["content"].startswith("[VRCForge agent relay.")
                        and "Please verify the glossary terminology." in row["content"]
                        for row in request["messages"])
        lane = "target" if as_target else "source"
        index = state.get(lane, 0)
        state[lane] = index + 1
        if as_target:
            if index == 0:
                return call("target-tools", "load_internal_tool_block", {
                    "block": "project_environment/conversations", "tools": ["send_message"],
                })
            if index == 1:
                return target_starts(request)
            assert index == 2
            return {"role": "assistant", "content": evidence}
        if index == 0:
            return call("source-tools", "load_internal_tool_block", {
                "block": "project_environment/conversations", "tools": ["followup_task", "read_conversation"],
            })
        if index == 1:
            return call("delegate", "followup_task", {
                "chatId": target_id, "message": "Please verify the glossary terminology.",
            })
        if index == 2:
            return source_reads(request)
        assert index == 3
        return source_finishes(request)

    gateway, model, _ = setup_gateway(tmp_path, [model_step] * 7)
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(host, "RUNTIME_QUEUE_PORT", gateway)
    monkeypatch.setattr(host, "EVENT_BUS", host.DashboardEventBus())
    monkeypatch.setattr(host, "TELEGRAM_SERVICE", Mock())
    monkeypatch.setattr(host, "_CROSS_SESSION_CONVERSATION_SERVICE", None)
    monkeypatch.setattr(host, "chat_transcripts_path", lambda: tmp_path / "app-chats.json")
    monkeypatch.setattr(host, "chat_project_index_path", lambda: tmp_path / "chat-projects.json")
    monkeypatch.setattr(host, "CHAT_REQUESTED_PROJECT_PATHS", {})

    source = {
        "id": source_id, "sessionId": source_session, "revision": 1, "title": "Requester",
        "projectPath": "", "projectType": "general", "items": [],
    }
    target = {
        "id": target_id, "sessionId": target_session, "revision": 1, "title": "Glossary owner",
        "projectPath": "", "projectType": "general", "items": [
            {"id": "target-seed", "type": "user", "text": "I maintain the glossary."},
        ],
    }
    assert host._create_handoff_chat(source, "")["id"] == source_id
    assert host._create_handoff_chat(target, "")["id"] == target_id

    service = host.cross_session_conversation_runtime()

    def tool(method, params):
        result = host._invoke_cross_session_conversation(method, params)
        calls.append((method, result))
        return result

    for method in ("followup_task", "read_conversation", "send_message"):
        gateway.register_tool(
            f"vrcforge_{method}", "When to use: communicate with the scoped chat. "
            "When NOT to use: access another scope.",
            "plan/preview" if method != "read_conversation" else "read/debug",
            lambda params, name=method: tool(name, params),
        )

    gateway.register_tool(
        "vrcforge_load_internal_tool_block", "When to use: load. When NOT to use: write.",
        "read/debug", host.load_internal_tool_block,
    )
    gateway.runtime_planner._catalog = host._RuntimePlannerCatalog()

    async def exercise():
        host.EVENT_BUS.set_loop(asyncio.get_running_loop())
        service.start()
        try:
            return await host.app_agent_runtime_message(host.AgentRuntimeMessageRequest(
                message="Ask the existing glossary chat to verify its wording and summarize its answer.",
                session_id=source_session, chatId=source_id, clientTurnId="request-glossary",
                projectType="general", maxAgenticTurns=6,
            ))
        finally:
            release_target.set()
            await asyncio.to_thread(service.shutdown)

    result = asyncio.run(exercise())
    assert result["plan"]["reply"] == f"Glossary verification: {evidence}", (result["plan"], calls)
    assert sorted(name for name, _ in calls) == ["followup_task", "read_conversation", "send_message"]
    assert next(receipt for name, receipt in calls if name == "read_conversation")["status"] == "completed"
    assert len(model.requests) == 7
    saved = host._load_handoff_chat(target_id, "")
    assert next(item for item in saved["items"] if item.get("id", "").startswith("runtime-reply-"))["response"]["plan"]["reply"] == evidence
    note = host._load_handoff_chat(source_id, "")["items"][-1]
    assert note["forwardedFrom"]["sourceChatId"] == target_id
    assert note["forwardedByAgent"] is True
    assert "I checked the glossary" in note["text"]
    assert len(gateway.list_runtime_followups(session_id=target_id)) == 1
