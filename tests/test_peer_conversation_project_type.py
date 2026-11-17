from __future__ import annotations

from tests.test_cross_session_conversation_service import Harness, trusted_context


def test_followup_with_workspace_directory_keeps_target_general_and_plan_mode():
    harness = Harness()
    target = harness.chats["offscreen"]
    target["projectType"] = "general"
    target["planMode"] = True
    service = harness.service()
    context = trusted_context()
    context["_trustedConversationContext"]["projectType"] = "unity"
    sent = service.followup_task({
        **context,
        "chatId": target["id"],
        "message": "Read the glossary in this workspace.",
    })
    assert sent["status"] == "queued"
    assert service.process_pending() == 1
    request = harness.dispatched[0]
    assert request["projectPath"] == "C:/Unity/Project"
    assert request["projectType"] == "general"
    assert request["_projectContextActive"] is False
    assert request["planMode"] is True


def test_create_conversation_keeps_trusted_general_workspace_type():
    harness = Harness()
    service = harness.service()
    context = trusted_context()
    context["_trustedConversationContext"]["projectType"] = "general"
    created = service.create_conversation({**context, "title": "Glossary"})
    saved = harness.chats[created["chatId"]]
    assert saved["projectPath"] == "C:/Unity/Project"
    assert saved["projectType"] == "general"
