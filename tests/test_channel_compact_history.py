import asyncio
import copy
import json

from channel_runtime_adapter import ChannelRuntimeAdapter


def compacted_items():
    return [
        {"id": "compact-2", "type": "compact", "detail": "latest summary",
         "summarizedItemIds": ["compact-1", "user-1", "agent-1"]},
        {"id": "compact-1", "type": "compact", "detail": "obsolete summary",
         "summarizedItemIds": ["user-1", "agent-1"],
         "compactionRecovery": [{"summaryDigest": "digest", "sourceEntries": [{"text": "original"}]}]},
        {"id": "user-1", "type": "user", "text": "original request"},
        {"id": "agent-1", "type": "agent", "response": {"plan": {"reply": "original answer"}}},
        {"id": "user-2", "type": "user", "text": "recent request"},
    ]


EXPECTED = [{"role": "agent", "text": "latest summary"}, {"role": "user", "text": "recent request"}]


def test_runtime_history_after_reload_excludes_covered_dialogue_and_old_markers():
    items = json.loads(json.dumps(compacted_items()))
    before = copy.deepcopy(items)
    assert ChannelRuntimeAdapter._runtime_history(items) == EXPECTED
    assert items == before


def test_legacy_compact_and_uncovered_history_remain_in_projection():
    items = [
        {"id": "legacy", "type": "compact", "text": "legacy summary"},
        {"id": "user", "type": "user", "text": "hello"},
        {"id": "agent", "type": "agent", "response": {"plan": {"reply": "answer"}}},
        {"id": "error", "type": "error", "text": "existing error"},
    ]
    assert ChannelRuntimeAdapter._runtime_history(items) == [
        {"role": "agent", "text": "legacy summary"},
        {"role": "user", "text": "hello"},
        {"role": "agent", "text": "answer"},
        {"role": "agent", "text": "existing error"},
    ]


def test_control_context_keeps_recovery_separate_from_compact_model_history():
    items = compacted_items()
    context = ChannelRuntimeAdapter._control_context({"id": "chat", "items": items}, "")
    assert context["history"] == EXPECTED
    assert context["compactionRecovery"] == items[1]["compactionRecovery"]


def test_actual_adapter_submit_projects_compact_history_without_rewriting_store():
    class Store:
        def __init__(self):
            self.chats = [{"id": "chat", "sessionId": "session", "projectRoot": "", "items": compacted_items()}]

        async def load(self):
            return {"chats": copy.deepcopy(self.chats), "sourceRevisions": []}

        async def save(self, chats, revisions):
            self.chats = copy.deepcopy(chats)
            return {"ok": True}

    class Runtime:
        async def start_turn(self, session_id, client_turn_id, message, context):
            self.context = context
            self.started.set()
            await self.release.wait()
            return {"reply": "done"}

        async def cancel(self, *args):
            self.release.set()

    async def scenario():
        store, runtime = Store(), Runtime()
        runtime.started, runtime.release = asyncio.Event(), asyncio.Event()
        adapter = ChannelRuntimeAdapter(runtime, store, lambda event: None)
        try:
            outcome = await adapter.submit("chat", "new-turn", "continue", "")
            assert outcome["status"] == "started"
            await asyncio.wait_for(runtime.started.wait(), timeout=1)
            assert runtime.context["history"] == EXPECTED
            assert runtime.context["compactionRecovery"] == compacted_items()[1]["compactionRecovery"]
            assert store.chats[0]["items"][:-1] == compacted_items()
            assert store.chats[0]["items"][-1]["text"] == "continue"
        finally:
            await adapter.shutdown()

    asyncio.run(scenario())


def test_compact_marker_ids_survive_actual_chat_store_write_and_load():
    from pathlib import Path

    import dashboard_server as server
    import test_dashboard_server as dashboard_fixture
    from dashboard_api_models import ChatTranscriptsRequest
    from session_store_integrity import is_valid_chat_record

    fixture = dashboard_fixture.DashboardServerTests(
        'test_chat_get_blocks_oversized_record_count_without_loading_unsavable_state'
    )
    fixture.setUp()
    try:
        items = compacted_items()
        for item in items:
            if item['type'] == 'compact':
                item['text'] = 'Context compacted'
            elif item['type'] == 'agent':
                item['response']['plan'].update(summary='original answer', planner='llm', shellNeeded=False)
        chat = {'id': 'compact-store-fixture', 'items': items}
        assert is_valid_chat_record(chat)
        path = server.chat_transcripts_path()
        assert path.resolve().is_relative_to(Path(fixture.tuning_store_dir.name).resolve())
        result, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[chat]))
        assert result['ok'] is True
        loaded, source, recovery = server.load_chat_transcript_file(
            server.chat_store_target(path, scope='app'), scope='app'
        )
        assert source['status'] == 'ok' and recovery is None
        assert loaded == [chat]
        assert loaded[0]['items'][0]['summarizedItemIds'] == ['compact-1', 'user-1', 'agent-1']
        assert ChannelRuntimeAdapter._runtime_history(loaded[0]['items']) == EXPECTED
    finally:
        fixture.tearDown()
