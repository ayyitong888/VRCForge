from copy import deepcopy
import json

import pytest

from session_store_integrity import is_valid_chat_record


def lifecycle_chat():
    # Same public event mapping used by App.reconcileSubAgentHandoffs.
    return {"id": "parent-chat", "items": [
        {"id": f"subagent-event-task-{revision}", "type": "timeline_event",
         "createdAt": "2026-09-17T00:27:39.907643+00:00",
         "event": {"id": f"subagent-event-task-{revision}", "sequence": revision,
                   "timestamp": "2026-09-17T00:27:39.907643+00:00", "kind": "subagent",
                   "payload": {"label": "Reader", "summary": summary, "status": status,
                               "tool": "explorer", "subagentStatus": status}}}
        for revision, status, summary in (
            (1, "created", "Read supplied source"), (2, "started", ""),
            (3, "completed", "Read completed"))
    ]}


def test_lifecycle_events_survive_actual_chat_storage_write_and_read(tmp_path, monkeypatch):
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest

    monkeypatch.setattr(server, "chat_transcripts_path", lambda: tmp_path / "chats.json")
    monkeypatch.setattr(server, "chat_project_index_path", lambda: tmp_path / "projects.json")
    chat = lifecycle_chat()
    assert is_valid_chat_record(chat)
    result, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
    assert result["ok"] is True
    saved = json.loads((tmp_path / "chats.json").read_text(encoding="utf-8"))["chats"]
    assert saved == [chat]
    assert all(is_valid_chat_record(row) for row in saved)


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", 3), ("sequence", -1), ("sequence", True),
    ("sequence", 1.5), ("timestamp", None), ("timestamp", ""),
    ("kind", "unrecognized"), ("payload", []),
    ("payload", {"summary": {"nested": "raw"}}),
    ("payload", {"summary": "x" * 1001}),
    ("payload", {"subagentStatus": "unknown"}),
    ("payload", {"arguments": {"raw": "hidden"}}),
    ("payload", {"reasoning": "private"}),
])
def test_malformed_lifecycle_event_is_rejected(field, value):
    chat = deepcopy(lifecycle_chat())
    chat["items"][0]["event"][field] = value
    assert not is_valid_chat_record(chat)


def test_lifecycle_event_requires_complete_public_envelope():
    for field in ("id", "sequence", "timestamp", "kind", "payload"):
        chat = lifecycle_chat()
        del chat["items"][0]["event"][field]
        assert not is_valid_chat_record(chat), field

