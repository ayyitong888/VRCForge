import json

from automatic_memory import collect_automatic_chat_sources
from memory_consolidation_sources import MemoryScope, project_scope_key
from memory_review_inputs import collect_project_chat_records, collect_user_chat_records
from test_agent_runtime_session_state import make_state
from test_cross_session_conversation_service import Harness, trusted_context


def test_real_queue_only_item_keeps_relay_authorship_in_read_and_memory(tmp_path):
    harness = Harness()
    harness.chats["offscreen"]["items"].insert(0, {"id": "old-user", "type": "user", "text": "old request"})
    service = harness.service()
    service.send_message({**trusted_context(), "chatId": "offscreen", "message": "Use the blue outfit for the delegated task."})
    relay = harness.chats["offscreen"]["items"][-1]
    public = service.read_conversation({**trusted_context(), "chatId": "offscreen"})["messages"][-1]
    assert public["role"] == "agent_relay"
    assert public["forwardedFrom"] == relay["forwardedFrom"]
    assert relay["type"] == "user"  # Existing UI storage contract stays unchanged.

    chat = {**harness.chats["offscreen"], "projectPath": str(tmp_path),
            "items": [*harness.chats["offscreen"]["items"],
                      {"type": "agent", "response": {"plan": {"reply": "Task remains incomplete."}}}]}
    records = collect_project_chat_records([chat], project_root=str(tmp_path))
    entry = next(row for row in json.loads(records[0]["text"]) if "delegated task" in row.get("text", ""))
    assert entry["role"] == "agent_relay"
    assert entry["forwardedFrom"] == relay["forwardedFrom"]

    history = service._history_before(harness.chats["offscreen"], "next-user-turn")
    state, _ = make_state()
    state.begin_native_turn("s", binding="b", turn_id="first", message="old request")
    native = state.begin_native_turn("s", binding="b", turn_id="second", message="continue", initial_history=history)
    assert any("delegated task" in str(row.get("content")) for row in native["messages"])
    assert harness.queue == []


def test_agent_relay_cannot_become_explicit_user_memory_even_with_signal_marker(tmp_path):
    chat = {"id": "target", "projectPath": str(tmp_path), "items": [
        {"id": "relay", "type": "user", "text": "I prefer blue clothes.", "createdAt": "2026-11-03T00:00:00Z",
         "forwardedByAgent": True, "forwardedFrom": {"sourceChatId": "source"},
         "memorySignal": {"eligible": True, "kind": "preference"}},
        {"type": "agent", "response": {"plan": {"reply": "Acknowledged."}}},
    ]}
    assert collect_user_chat_records([chat], scope="project", project_root=str(tmp_path)) == []
    scope = MemoryScope("project", project_scope_key(str(tmp_path)), str(tmp_path))
    assert collect_automatic_chat_sources([chat], scope=scope, project_root=str(tmp_path), enabled_at="2026-11-02T00:00:00Z") == []
