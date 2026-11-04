import json

import dashboard_server as dashboard
from chat_transcript_archive import ARCHIVE_REF_KEY, blob_path
from fastapi import HTTPException
import pytest


def test_committed_chat_delete_prunes_its_archive_blob(tmp_path, monkeypatch):
    store = tmp_path / "chat-transcripts.json"
    index = tmp_path / "chat-project-index.json"
    monkeypatch.setattr(dashboard, "chat_transcripts_path", lambda: store)
    monkeypatch.setattr(dashboard, "chat_project_index_path", lambda: index)
    chat = {
        "id": "delete-large-chat",
        "items": [{
            "id": "large-agent-item",
            "type": "agent",
            "response": {"ok": True, "plan": {
                "summary": "done", "planner": "test", "shellNeeded": False,
                "reply": "retained transcript text " * 5000,
            }},
        }],
    }

    created, _chats, _groups = dashboard.write_chat_transcripts_storage(
        dashboard.ChatTranscriptsRequest(chats=[chat]),
    )
    ref = json.loads(store.read_text(encoding="utf-8"))["chats"][0]["items"][0][ARCHIVE_REF_KEY]
    archived_body = blob_path(store, ref["digest"])
    assert archived_body.is_file()

    deleted, _chats, _groups = dashboard.write_chat_transcripts_storage(
        dashboard.ChatTranscriptsRequest(chats=[], sourceRevisions=created["sourceRevisions"]),
    )

    assert json.loads(store.read_text(encoding="utf-8"))["chats"] == []
    assert not archived_body.exists()
    assert deleted["archiveCleanup"] == {"deletedCount": 1, "leftBehindCount": 0}


def test_failed_chat_snapshot_commit_preserves_previous_archive_blob(tmp_path, monkeypatch):
    store = tmp_path / "chat-transcripts.json"
    index = tmp_path / "chat-project-index.json"
    monkeypatch.setattr(dashboard, "chat_transcripts_path", lambda: store)
    monkeypatch.setattr(dashboard, "chat_project_index_path", lambda: index)
    chat = {
        "id": "keep-large-chat",
        "items": [{
            "id": "large-agent-item",
            "type": "agent",
            "response": {"ok": True, "plan": {
                "summary": "done", "planner": "test", "shellNeeded": False,
                "reply": "retained transcript text " * 5000,
            }},
        }],
    }
    created, _chats, _groups = dashboard.write_chat_transcripts_storage(
        dashboard.ChatTranscriptsRequest(chats=[chat]),
    )
    original = store.read_bytes()
    ref = json.loads(original)["chats"][0]["items"][0][ARCHIVE_REF_KEY]
    archived_body = blob_path(store, ref["digest"])
    original_atomic_write = dashboard.atomic_write_text

    def fail_transcript_write(path, payload):
        if path == store:
            raise OSError("isolated transcript write failure")
        return original_atomic_write(path, payload)

    monkeypatch.setattr(dashboard, "atomic_write_text", fail_transcript_write)
    with pytest.raises(HTTPException) as failure:
        dashboard.write_chat_transcripts_storage(
            dashboard.ChatTranscriptsRequest(chats=[], sourceRevisions=created["sourceRevisions"]),
        )

    assert failure.value.status_code == 500
    assert store.read_bytes() == original
    assert archived_body.is_file()


def test_committed_removal_of_last_project_chat_prunes_project_archive_blob(tmp_path, monkeypatch):
    app_store = tmp_path / "chat-transcripts.json"
    index = tmp_path / "chat-project-index.json"
    project = tmp_path / "Project"
    project.mkdir()
    project_store = project / ".vrcforge" / "chat-transcripts.json"
    monkeypatch.setattr(dashboard, "chat_transcripts_path", lambda: app_store)
    monkeypatch.setattr(dashboard, "chat_project_index_path", lambda: index)
    chat = {
        "id": "delete-large-project-chat",
        "projectPath": str(project),
        "items": [{
            "id": "large-agent-item",
            "type": "agent",
            "response": {"ok": True, "plan": {
                "summary": "done", "planner": "test", "shellNeeded": False,
                "reply": "retained transcript text " * 5000,
            }},
        }],
    }
    created, _chats, _groups = dashboard.write_chat_transcripts_storage(
        dashboard.ChatTranscriptsRequest(chats=[chat]),
    )
    ref = json.loads(project_store.read_text(encoding="utf-8"))["chats"][0]["items"][0][ARCHIVE_REF_KEY]
    archived_body = blob_path(project_store, ref["digest"])
    assert archived_body.is_file()

    deleted, _chats, _groups = dashboard.write_chat_transcripts_storage(
        dashboard.ChatTranscriptsRequest(chats=[], sourceRevisions=created["sourceRevisions"]),
    )

    assert not project_store.exists()
    assert not archived_body.exists()
    assert deleted["archiveCleanup"] == {"deletedCount": 1, "leftBehindCount": 0}
