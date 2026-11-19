from __future__ import annotations

import json
from threading import RLock

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_native_turn import NativeRuntimeTurn
from agent_runtime_session_state import (
    AgentRuntimeSessionState, AgentRuntimeSessionStatePorts, NATIVE_RUNTIME_CONTEXT_PREFIX, native_model_history,
)
from context_compaction import MODEL_HANDOFF_PREFIX


def _state(store):
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=RLock(), native_history_store=store))


def _call(call_id, name="read", arguments="{}"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _compact(state, before, *, budget=500, measure=len, context=None):
    _source, blocks = state.native_handoff_projection(before, character_budget=budget, measure=measure)
    return state.replace_native_compacted_history(
        "owner", binding="binding", expected_snapshot=before, summary=MODEL_HANDOFF_PREFIX + "graph",
        current_context=context, handoff_blocks=blocks,
    )


def test_handoff_orders_verbatim_messages_and_recovers_materials_after_restart(tmp_path):
    store = NativeHistoryStore(tmp_path)
    state = _state(store)
    first = "  First instruction with leading whitespace.  \n"
    state.begin_native_turn("owner", binding="binding", turn_id="turn", input_id="first",
                            message=first.strip(), source_text=first)
    state.append_native_user("owner", binding="binding", input_id="middle", message="older message " * 160)
    state.append_native_assistant("owner", binding="binding",
                                  message={"role": "assistant", "tool_calls": [_call("old")]})
    state.settle_native_call("owner", binding="binding", call_id="old", content="old result")
    state.append_native_user("owner", binding="binding", input_id="latest", message="Keep exact whitespace.  ",
                             attachments=[{"name": "Selected text", "id": "quote-1", "text": "Exact quoted material"},
                                          {"name": "Screenshot", "id": "att-image", "dataUrl": "data:image/png;base64,xxx",
                                           "vaultPayloadHash": "f" * 64}])
    reply = "Latest public reply  " + "word " * 30
    state.append_native_assistant("owner", binding="binding", message={"role": "assistant", "content": reply})
    before = state.native_conversation("owner", binding="binding")
    context = {"role": "user", "content": NATIVE_RUNTIME_CONTEXT_PREFIX + '{"activeTodo":[]}'}
    compacted = _compact(state, before, budget=225, measure=lambda value: len(value) // 4, context=[context])
    projected = native_model_history(compacted["messages"])
    assert projected[0] == context
    assert 'messageId="first"' in projected[1]["content"]
    assert 'messageId="middle"' in projected[2]["content"]
    assert projected[3]["content"].startswith("[User message latest]\nKeep exact whitespace.  \n\n")
    assert 'messageId="latest"' in projected[3]["content"]
    assert projected[4]["content"] == MODEL_HANDOFF_PREFIX + "graph"
    assert projected[5] == {"role": "assistant", "content": reply}
    assert "Exact quoted material" not in str(projected)
    assert "tool_call_id" not in str(projected)
    restarted = _state(store)
    assert restarted.native_handoff_archive("owner", binding="binding", message_id="first")["text"] == first
    record = restarted.native_handoff_archive("owner", binding="binding", message_id="latest")
    assert record["references"][0]["text"] == "Exact quoted material"
    stored_quote = restarted.native_handoff_archive("owner", binding="binding", message_id=record["attachments"][0]["messageId"])
    assert stored_quote["source"] == "attachment" and stored_quote["text"] == "Exact quoted material"
    assert record["attachments"][1]["vaultPayloadHash"] == "f" * 64
    assert "data:image" not in str(record)
    assert restarted.native_handoff_archive("other", binding="binding", message_id="latest") is None
    again = _compact(restarted, compacted, budget=10000)
    assert native_model_history(again["messages"])[0]["content"] == "[User message first]\n" + first
    assert again["messages"][again["activeTurnStart"]]["messageId"] == "first"
    assert again["handoffArchive"]["middle"]["text"] == "older message " * 160
    assert [entry["messageId"] for entry in again["messages"] if entry.get("handoffUser")] == ["first", "middle", "latest"]


def test_open_transaction_archive_keeps_results_paired_and_does_not_replay(tmp_path):
    store = NativeHistoryStore(tmp_path)
    state = _state(store)
    state.begin_native_turn("owner", binding="binding", turn_id="turn", input_id="first", message="First")
    state.append_native_assistant("owner", binding="binding", message={
        "role": "assistant", "reasoning_content": "private provider text", "tool_calls": [
            _call("settled"), _call("open", "vrcforge_runtime_action", json.dumps({"action": "reply", "reply": "Public"})),
        ],
    })
    state.mark_native_dispatch("owner", binding="binding", call_id="open")
    state.settle_native_call("owner", binding="binding", call_id="settled", content="result " * 1000)
    before = state.native_conversation("owner", binding="binding")
    compacted = _compact(state, before)
    assert compacted["messages"][-2:] == before["messages"][-2:]
    assert state._native_pending(compacted) == {"open"}
    pointer_id = next(key for key, item in compacted["handoffArchive"].items() if item["source"] == "tool")
    assert "private provider text" not in compacted["handoffArchive"][pointer_id]["text"]
    assert compacted["activeDispatchCallId"] == "open"
    restarted = _state(store)
    restored = restarted.restore_native_conversation("owner", binding="binding", snapshot=before)
    assert restored == compacted
    restarted.settle_native_call("owner", binding="binding", call_id="open", content="Final result")
    after = restarted.native_conversation("owner", binding="binding")
    archive = restarted.native_handoff_archive("owner", binding="binding", message_id=pointer_id)
    assert [item["tool_call_id"] for item in json.loads(archive["text"])[1:]] == ["settled", "open"]
    assert json.loads(archive["text"])[-1]["content"] == "Final result"
    assert not any(row.get("tool_calls") or row["role"] == "tool" for row in native_model_history(after["messages"]))
    assert [row["content"] for row in native_model_history(after["messages"]) if row["role"] == "assistant"][-1] == "Public"


def test_over_budget_keeps_latest_instructions_in_order_after_restart(tmp_path):
    store = NativeHistoryStore(tmp_path)
    state = _state(store)
    state.begin_native_turn("owner", binding="binding", turn_id="turn", input_id="first", message="Original task")
    state.append_native_user("owner", binding="binding", input_id="middle", message="An older instruction. " * 40)
    latest = "Keep the original selection. " * 12
    state.append_native_user("owner", binding="binding", input_id="latest", message=latest)
    before = state.native_conversation("owner", binding="binding")
    for _ in range(2):
        _compact(state, before, budget=200)
        state = _state(store)
        before = state.native_conversation("owner", binding="binding")
        users = [item["content"] for item in native_model_history(before["messages"]) if item["role"] == "user"]
        assert 'messageId="first"' in users[0]
        assert 'messageId="middle"' in users[1]
        assert users[-1] == "[User message latest]\n" + latest
        assert state.native_handoff_archive("owner", binding="binding", message_id="first")["text"] == "Original task"


def test_latest_unconsumed_group_is_indivisible_even_after_host_context(tmp_path):
    state = _state(NativeHistoryStore(tmp_path))
    state.begin_native_turn("owner", binding="binding", turn_id="turn", message="First")
    state.append_native_assistant("owner", binding="binding", message={"role": "assistant", "tool_calls": [_call("consumed")]})
    state.settle_native_call("owner", binding="binding", call_id="consumed", content="first result")
    state.append_native_assistant("owner", binding="binding",
                                  message={"role": "assistant", "tool_calls": [_call("fresh-a"), _call("fresh-b")]})
    state.settle_native_calls("owner", binding="binding", results={"fresh-a": "a", "fresh-b": "b"})
    state.append_native_user("owner", binding="binding", human=False, message=NATIVE_RUNTIME_CONTEXT_PREFIX + "{}")
    before = state.native_conversation("owner", binding="binding")
    source, blocks = state.native_handoff_projection(before, character_budget=1000)
    assert source[-1]["tool_call_id"] == "consumed"
    assert blocks["messages"][-3:] == before["messages"][-4:-1]
    assert state._native_pending(_compact(state, before, budget=1000)) == set()
    with pytest.raises(ValueError, match="compare-and-replace"):
        _compact(state, before, budget=1000)


def test_inline_app_image_uses_chat_vault_and_rejects_invalid_material(tmp_path, monkeypatch):
    import dashboard_server
    from chat_attachment_vault import ChatAttachmentVault, bind_attachment_inspection_chat

    store = ChatAttachmentVault(tmp_path)
    monkeypatch.setattr(dashboard_server, "chat_attachment_vault_store", lambda: store)
    data = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m"
            "P8/x8AAwMCAO+/xLkAAAAASUVORK5CYII=")
    original = [{"name": "screenshot", "dataUrl": data}, {"name": "broken.png", "dataUrl": "data:image/png;base64,?"}]
    archived = dashboard_server._vault_inline_runtime_images(original, "owner-chat")
    assert archived[0]["dataUrl"] == data and "vaultPayloadHash" not in original[0]
    assert archived[1] == original[1]
    with bind_attachment_inspection_chat(("owner-chat",)):
        material = store.resolve_for_inspection(archived[0]["vaultPayloadHash"])
    assert material["kind"] == "image_png"


@pytest.mark.parametrize("key,with_attachments", [
    ("messageId", False), ("messageId", True), ("executionId", False),
])
def test_handoff_reader_delivers_lossless_pages_to_native_model(key, with_attachments):
    from agent_tool_result_reader import MAX_PAGE_CHARS, TOOL_NAME, bind_tool_result_context, read_tool_result
    from runtime_planner_service import sanitize_planner_observation_text
    from tests.test_native_context_compaction import fixture

    _, planner, _, _ = fixture(prior=False)
    body = ('正文\\\"\n' * 3_000) + "original ending"
    record = {"id": "owned-record", "source": "user", "text": body}
    if with_attachments:
        record["attachments"] = [
            {"name": "archive.zip", "payloadKind": "vault_file", "payloadHash": "a" * 64},
            {"name": "old-screen.png", "dataUrl": "data:image/png;base64,unrecoverable"},
            {"name": "notes.txt", "messageId": "attachment:notes"},
        ]
    expected = body if key == "messageId" else json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    parts = []
    args = {key: record["id"]}
    with bind_tool_result_context("owner", "turn", "project", [], handoff_lookup=lambda _key, _id: record):
        while True:
            page = read_tool_result(args, sanitize=sanitize_planner_observation_text)
            native = planner.native_result_observation({"tool": TOOL_NAME, "status": "completed", "result": page})
            assert json.loads(native["observation"].split("; retainedResultPage=", 1)[1]) == page
            assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= MAX_PAGE_CHARS
            if with_attachments and not parts:
                assert [item["availability"] for item in page["attachments"]] == ["vault", "unavailable", "retained"]
                assert not any("dataUrl" in item for item in page["attachments"])
            parts.append(page["text"])
            if not page["hasMore"]:
                break
            assert page["nextRequest"]["tool"] == TOOL_NAME
            args = page["nextRequest"]["arguments"]
            assert args == {key: record["id"], "textOffset": sum(map(len, parts))}
    assert len(parts) > 1 and "".join(parts) == expected


def test_handoff_reader_schema_rejects_mixed_selector_arguments_before_execution():
    from agent_tool_result_reader import INPUT_SCHEMA, TOOL_NAME
    from runtime_planner_service import validate_planner_tool_arguments

    ref = "result_" + "a" * 32
    for key in ("messageId", "executionId"):
        assert validate_planner_tool_arguments(INPUT_SCHEMA, {key: "owned", "textOffset": 3}, tool_name=TOOL_NAME)["ok"]
        for result_only in ({"limit": 2}, {"jsonPointer": "/items"}, {"source": "owner_model"}, {"resultRef": ref}):
            assert not validate_planner_tool_arguments(INPUT_SCHEMA, {key: "owned", **result_only},
                                                       tool_name=TOOL_NAME)["ok"]
    assert not validate_planner_tool_arguments(INPUT_SCHEMA, {"messageId": "m", "executionId": "e"},
                                               tool_name=TOOL_NAME)["ok"]
    validated = validate_planner_tool_arguments(INPUT_SCHEMA, {"resultRef": ref, "jsonPointer": "items", "limit": 90},
                                                tool_name=TOOL_NAME)
    assert validated["nextRequest"]["arguments"] == {"resultRef": ref, "jsonPointer": "/items", "limit": 90}
