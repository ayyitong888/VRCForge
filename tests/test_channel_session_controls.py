import asyncio
import copy

import pytest

from channel_runtime_adapter import ChannelRuntimeAdapter
from channel_session_controls import SessionControlError


class Store:
    def __init__(self):
        self.revision = 0
        self.chats = [{"id": "c", "sessionId": "s", "projectPath": "", "projectType": "general", "items": []}]

    def load(self):
        return {"chats": copy.deepcopy(self.chats), "sourceRevisions": [self.revision]}

    def save(self, chats, revisions):
        if revisions != [self.revision]:
            raise RuntimeError("conflict")
        self.chats = copy.deepcopy(chats)
        self.revision += 1


class Runtime:
    async def start_turn(self, *_args): return {"status": "completed"}
    async def steer(self, *_args): return {"status": "accepted"}
    async def cancel(self, *_args): return {"status": "stopped"}


def adapter(store, *, catalog=None, compactor=None):
    return ChannelRuntimeAdapter(Runtime(), store, lambda _event: None, model_catalog=catalog, compactor=compactor)


def test_set_model_is_chat_scoped_and_uses_catalog():
    store = Store()
    async def run():
        a = adapter(store, catalog=lambda _scope: [{"provider": "openai", "id": "gpt-test"}])
        try:
            assert await a.model_options("c", "") == [{"provider": "openai", "model": "gpt-test", "id": "gpt-test", "label": "gpt-test"}]
            await a.set_model("c", "", "openai", "gpt-test")
            assert store.chats[0]["settings"] == {"provider": "openai", "model": "gpt-test"}
        finally:
            await a.shutdown()
    asyncio.run(run())


def test_set_model_rejects_active_and_unavailable_without_writing():
    store = Store()
    async def run():
        a = adapter(store, catalog=lambda _scope: [{"provider": "openai", "id": "gpt-test"}])
        try:
            with pytest.raises(SessionControlError) as error:
                await a.set_model("c", "", "openai", "other")
            assert error.value.code == "model_unavailable"
            before = store.revision
            a._coordinator._active["s"] = object()
            with pytest.raises(SessionControlError) as error:
                await a.set_model("c", "", "openai", "gpt-test")
            assert error.value.code == "active_run" and store.revision == before
            a._coordinator._active.clear()
        finally:
            await a.shutdown()
    asyncio.run(run())


def test_compact_requires_committed_chat_and_preserves_scope():
    store = Store()
    async def compact(chat, scope, instructions):
        assert scope == "" and instructions == "short"
        return {"ok": True, "chat": {**chat, "sessionId": "sess_compact_test", "items": [{"id": "compact", "type": "compact", "detail": "summary"}]}}
    async def run():
        a = adapter(store, compactor=compact)
        try:
            result = await a.compact("c", "", "short")
            assert result["ok"] and store.chats[0]["items"][0]["id"] == "compact"
            assert store.chats[0]["sessionId"].startswith("sess_compact_")
        finally:
            await a.shutdown()
    asyncio.run(run())


def test_compact_projection_reduces_next_turn_history_and_keeps_state_cards():
    store = Store()
    store.chats[0]["sessionId"] = "native-session"
    store.chats[0]["items"] = [
        {"id": "u1", "type": "user", "text": "old"},
        {"id": "a1", "type": "agent", "response": {"plan": {"reply": "old answer"}}},
        {"id": "r1", "type": "result", "approvalId": "approval"},
        {"id": "u2", "type": "user", "text": "latest"},
    ]
    async def compact(chat, scope, instructions):
        from channel_session_controls import compact_chat_items
        result = {"summary": "old context", "summaryDigest": "d", "sourceDigest": "s",
                  "recovery": {"schema": "vrcforge.context_compaction_recovery.v1",
                      "authority": "historical_context_data", "sourceEntries": [{"role": "user", "text": "old"}],
                      "sourceDigest": "s", "summary": "old context", "summaryDigest": "d", "summarySource": "provider"},
                  "entryCount": 3, "retainedEntryCount": 1, "provider": "openai", "model": "gpt-test"}
        return {"ok": True, "chat": compact_chat_items(chat, result), **result}
    async def run():
        a = adapter(store, compactor=compact)
        try:
            result = await a.compact("c", "")
            assert result["status"] == "applied"
            assert [item["id"] for item in store.chats[0]["items"]] == ["compact:d", "r1", "u2"]
            assert store.chats[0]["sessionId"].startswith("sess_compact_")
        finally:
            await a.shutdown()
    asyncio.run(run())
