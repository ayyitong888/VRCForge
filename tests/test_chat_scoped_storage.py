import asyncio
import json
from unittest.mock import AsyncMock, Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import chat_transcript_archive as archive


def agent(item_id):
    return {"id": item_id, "type": "agent", "response": {
        "steps": [{"result": "result " * 12_000}],
        "plan": {"reply": "Done", "summary": "Done", "planner": "runtime", "shellNeeded": False},
    }}


def user(item_id):
    return {"id": item_id, "type": "user", "text": f"message {item_id}"}


def write_store(path, chats):
    packed, pending = archive.encode_chats(chats)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "chats": packed}), encoding="utf-8")
    for digest, entry in pending.items():
        blob = archive.blob_path(path, digest)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(entry["data"])
    return packed


@pytest.fixture
def chat_store(tmp_path, monkeypatch):
    import dashboard_server as server

    app_path = tmp_path / "app" / "chat-transcripts.json"
    index_path = app_path.with_name("chat-projects.json")
    project = tmp_path / "project"
    project.mkdir()
    project_path = project / ".vrcforge" / "chat-transcripts.json"
    monkeypatch.setattr(server, "chat_transcripts_path", lambda: app_path)
    monkeypatch.setattr(server, "chat_project_index_path", lambda: index_path)
    monkeypatch.setattr(server, "CHAT_REQUESTED_PROJECT_PATHS", {})
    monkeypatch.setattr(server, "CHAT_SAVE_RECEIPTS", {})
    monkeypatch.setattr(server, "capture_automatic_memory_after_chat_storage", lambda *_: {})

    class FakeVault:
        def retain_chat(self, *_):
            return {}

    monkeypatch.setattr(server, "chat_attachment_vault_store", FakeVault)
    archive_reads = []
    real_read_blob = archive._read_blob

    def read_blob(path, ref):
        archive_reads.append(ref["itemId"])
        return real_read_blob(path, ref)

    monkeypatch.setattr(archive, "_read_blob", read_blob)
    return TestClient(server.app), app_path, index_path, project, project_path, archive_reads


def test_catalog_and_pages_read_only_the_selected_page(chat_store):
    client, app_path, index_path, project, project_path, archive_reads = chat_store
    write_store(app_path, [{"id": "unopened", "title": "Unopened", "items": [agent("unopened-item")]}])
    recent = [user(str(index)) for index in range(13)]
    recent[-1].update(queueStatus="queued", clientTurnId="turn", queueEnvelope={"sessionId": "session"})
    reply = agent("latest-reply")
    write_store(project_path, [{
        "id": "selected", "title": "Selected", "isTestChat": True, "projectPath": "stale",
        "items": [agent("old-reply"), *recent, reply],
    }])
    before = {path: path.read_bytes() for path in (app_path, project_path)}

    catalog = client.get("/api/app/chats", params={"catalog": "1", "projectPath": str(project)}).json()
    assert not catalog["writeBlocked"]
    assert [(row["id"], row["sourceProjectPath"]) for row in catalog["entries"]] == [
        ("unopened", ""), ("selected", str(project)),
    ]
    assert catalog["entries"][1]["isTestChat"]
    assert catalog["pendingChatIds"] == ["selected"]
    assert archive_reads == []

    scope = {"chatId": "selected", "chatProjectPath": str(project)}
    first = client.get("/api/app/chats", params=scope).json()
    assert first["chats"][0]["items"] == [*recent[-11:], reply]
    assert first["hasOlder"] and first["nextBefore"]
    assert archive_reads == ["latest-reply"]

    page = client.get("/api/app/chats", params={**scope, "before": first["nextBefore"], "snapshotPage": "1"}).json()
    older = json.loads(page["text"])
    assert older["chats"][0]["items"] == [agent("old-reply"), *recent[:2]]
    assert not older["hasOlder"] and older["nextBefore"] is None
    assert archive_reads == ["latest-reply", "old-reply"]

    full = client.get("/api/app/chats", params={**scope, "full": "1"}).json()
    assert full["chats"][0]["items"] == [agent("old-reply"), *recent, reply]
    assert full["chatDigests"]["selected"] and full["chatMetadataShapes"]["selected"]
    assert archive_reads == ["latest-reply", "old-reply", "old-reply", "latest-reply"]
    assert not index_path.exists()
    assert {path: path.read_bytes() for path in before} == before


def patch_request(loaded, *, title, project_path="", move_to=None):
    chat = loaded["chats"][0]
    operations = [{"op": "replace", "path": "/title", "value": title}]
    if move_to is not None:
        operations.append({"op": "add", "path": "/projectPath", "value": str(move_to)})
    return {
        "chatId": chat["id"], "projectPath": project_path, "sourceRevisions": [loaded["sourceRevision"]],
        "chatPatch": {"baseDigest": loaded["chatDigests"][chat["id"]], "metadataPatch": operations,
                      "itemOrder": [item["id"] for item in chat["items"]], "itemPatches": {}, "newItems": {}},
    }


def test_scoped_write_race_and_delete_preserve_unopened_records_and_archives(chat_store):
    client, app_path, _, _, _, archive_reads = chat_store
    original = write_store(app_path, [
        {"id": "selected", "title": "Selected", "items": [agent("selected-item"), *[user(str(i)) for i in range(13)]]},
        {"id": "unopened", "title": "Unopened", "items": [agent("unopened-item")]},
    ])
    unopened_ref = original[1]["items"][0][archive.REF_FIELD]
    unopened_blob = archive.blob_path(app_path, unopened_ref["digest"])
    original_blob = unopened_blob.read_bytes()
    page = client.get("/api/app/chats", params={"chatId": "selected"}).json()
    loaded = client.get("/api/app/chats", params={"chatId": "selected", "full": "1"}).json()
    assert page["nextBefore"]

    barrier = Barrier(2)

    def write(title):
        barrier.wait(timeout=5)
        return client.post("/api/app/chats/single", json=patch_request(loaded, title=title))

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(write, ["First writer", "Second writer"]))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert next(result for result in results if result.status_code == 409).json()["detail"]["code"] == "chat_store_snapshot_changed"
    success = next(result for result in results if result.status_code == 200).json()
    assert success["chatDigests"]["selected"] != loaded["chatDigests"]["selected"]
    assert client.get("/api/app/chats", params={"chatId": "selected", "before": page["nextBefore"]}).status_code == 200
    assert json.loads(app_path.read_text(encoding="utf-8"))["chats"][1] == original[1]
    assert unopened_blob.read_bytes() == original_blob

    removed = client.post("/api/app/chats/single", json={
        "chatId": "selected", "sourceRevisions": success["sourceRevisions"], "delete": True,
    })
    assert removed.status_code == 200
    assert removed.json()["archiveCleanup"]["deletedCount"] == 1
    assert json.loads(app_path.read_text(encoding="utf-8"))["chats"] == [original[1]]
    assert unopened_blob.read_bytes() == original_blob
    assert "unopened-item" not in archive_reads


def test_scope_move_uses_destination_revision_and_preserves_other_chat(chat_store):
    client, app_path, index_path, project, project_path, archive_reads = chat_store
    write_store(app_path, [{"id": "selected", "title": "Selected", "items": [agent("selected-item")]}])
    other = write_store(project_path, [{"id": "other", "items": [agent("other-item")]}])[0]
    loaded = client.get("/api/app/chats", params={"chatId": "selected", "full": "1"}).json()
    body = patch_request(loaded, title="Moved", move_to=project)
    assert client.post("/api/app/chats/single", json=body).status_code == 409
    assert len(json.loads(app_path.read_text(encoding="utf-8"))["chats"]) == 1
    assert json.loads(project_path.read_text(encoding="utf-8"))["chats"] == [other]

    catalog = client.get("/api/app/chats", params={"catalog": "1", "projectPath": str(project)}).json()
    body["sourceRevisions"] = catalog["sources"]
    moved = client.post("/api/app/chats/single", json=body)
    assert moved.status_code == 200
    assert json.loads(app_path.read_text(encoding="utf-8"))["chats"] == []
    assert json.loads(index_path.read_text(encoding="utf-8"))["projectPaths"] == [str(project)]
    rows = json.loads(project_path.read_text(encoding="utf-8"))["chats"]
    assert rows[0] == other and rows[1]["title"] == "Moved"
    full = client.get("/api/app/chats", params={"chatId": "selected", "chatProjectPath": str(project), "full": "1"}).json()
    assert full["chats"][0]["items"] == [agent("selected-item")]
    assert "other-item" not in archive_reads


def test_history_cursor_survives_other_chat_write_and_rejects_removed_anchor(chat_store):
    client, app_path, _, _, _, _ = chat_store
    items = [user(str(index)) for index in range(13)]
    write_store(app_path, [
        {"id": "selected", "title": "Selected", "items": items},
        {"id": "neighbor", "title": "Neighbor", "items": [user("neighbor")]},
    ])
    selected = {"chatId": "selected"}
    first = client.get("/api/app/chats", params=selected).json()
    older = {**selected, "before": first["nextBefore"]}
    neighbor = client.get("/api/app/chats", params={"chatId": "neighbor", "full": "1"}).json()
    assert client.post("/api/app/chats/single", json=patch_request(neighbor, title="Renamed neighbor")).status_code == 200
    response = client.get("/api/app/chats", params=older)
    assert response.status_code == 200
    assert response.json()["chats"][0]["items"] == items[:1]

    latest = client.get("/api/app/chats", params={**selected, "full": "1"}).json()
    compact = patch_request(latest, title="Compacted selected")
    compact["chatPatch"]["itemOrder"] = [item["id"] for item in items[2:]]
    assert client.post("/api/app/chats/single", json=compact).status_code == 200
    response = client.get("/api/app/chats", params=older)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "chat_store_snapshot_changed"


def test_cancelled_send_now_retries_do_not_push_conversation_out_of_history(chat_store):
    client, _, _, project, project_path, _ = chat_store
    prior = [user("older-page"), user("original-question"), agent("original-reply")]
    cancelled = [
        {**user(f"send-now-retry-{index}"), "queuedFrom": True, "composerQueued": True, "queueStatus": "cancelled"}
        for index in range(12)
    ]
    recent = [user(f"recent-{index}") for index in range(10)]
    items = prior + cancelled + recent
    write_store(project_path, [{"id": "selected", "title": "Selected", "items": items}])
    scope = {"chatId": "selected", "chatProjectPath": str(project)}

    latest = client.get("/api/app/chats", params=scope).json()
    assert latest["chats"][0]["items"] == prior[1:] + recent
    older = client.get("/api/app/chats", params={**scope, "before": latest["nextBefore"]}).json()
    assert older["chats"][0]["items"] == prior[:1]
    assert older["nextBefore"] is None
    full = client.get("/api/app/chats", params={**scope, "full": "1"}).json()
    assert full["chats"][0]["items"] == items


def test_create_registers_new_project_and_rejects_cross_scope_duplicate(chat_store):
    client, app_path, index_path, project, project_path, archive_reads = chat_store
    body = {"chatId": "new", "projectPath": str(project), "chat": {
        "id": "new", "projectPath": str(project), "items": [user("message")],
    }}
    result = client.post("/api/app/chats/single", json=body)
    assert result.status_code == 200
    assert json.loads(index_path.read_text(encoding="utf-8"))["projectPaths"] == [str(project)]
    assert not app_path.exists()
    before = project_path.read_bytes()

    duplicate = client.post("/api/app/chats/single", json={
        **body, "projectPath": "", "chat": {**body["chat"], "projectPath": ""},
    })
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "chat_already_exists"
    assert project_path.read_bytes() == before and not app_path.exists() and not archive_reads

    deleted = client.post("/api/app/chats/single", json={
        "chatId": "new", "projectPath": str(project), "delete": True,
        "sourceRevisions": result.json()["sourceRevisions"],
    })
    assert deleted.status_code == 200
    assert json.loads(index_path.read_text(encoding="utf-8"))["projectPaths"] == []
    assert json.loads(project_path.read_text(encoding="utf-8"))["chats"] == []


@pytest.mark.parametrize("operation", ["create", "patch", "move", "delete"])
def test_single_save_replays_committed_receipt_without_repeating_side_effects(chat_store, monkeypatch, operation):
    import dashboard_server as server

    client, app_path, _, project, _, _ = chat_store
    chat = {"id": "selected", "title": "Selected", "items": [user("message")]}
    if operation == "create":
        body = {"chatId": "selected", "chat": chat}
    else:
        write_store(app_path, [chat])
        loaded = client.get("/api/app/chats", params={"chatId": "selected", "full": "1"}).json()
        body = patch_request(loaded, title="Updated", move_to=project if operation == "move" else None)
        if operation == "move":
            body["sourceRevisions"] = client.get("/api/app/chats", params={"catalog": "1", "projectPath": str(project)}).json()["sources"]
        if operation == "delete":
            body.pop("chatPatch")
            body["delete"] = True
    body.update(chatOriginId="single-save-origin", chatSaveId=f"single-{operation}")
    broadcast = AsyncMock()
    capture = Mock(return_value={})
    vault = Mock()
    vault.retain_chat.return_value = {}
    monkeypatch.setattr(server.EVENT_BUS, "broadcast", broadcast)
    monkeypatch.setattr(server, "capture_automatic_memory_after_chat_storage", capture)
    monkeypatch.setattr(server, "chat_attachment_vault_store", lambda: vault)

    committed = client.post("/api/app/chats/single", json=body)
    assert committed.status_code == 200, committed.text
    replayed = client.post("/api/app/chats/single", json=body)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == committed.json()
    assert sum(call.args[0] == "chatStorageChanged" for call in broadcast.await_args_list) == 1
    assert capture.call_count == (0 if operation == "delete" else 1)
    vault.retain_chat.assert_called_once()
    vault.retain.assert_not_called()
    distinct = client.post("/api/app/chats/single", json={**body, "chatSaveId": "different-save"})
    assert distinct.status_code == 409, distinct.text


def test_single_save_returns_receipt_before_notifications(chat_store, monkeypatch):
    import dashboard_server as server

    async def save():
        request = server.SingleChatTranscriptRequest(
            chatId="selected", chat={"id": "selected", "items": [user("message")]},
            chatOriginId="single-async-origin", chatSaveId="single-async-save",
        )
        broadcast = AsyncMock()
        capture = Mock(return_value={})
        monkeypatch.setattr(server.EVENT_BUS, "broadcast", broadcast)
        monkeypatch.setattr(server, "capture_automatic_memory_after_chat_storage", capture)
        tasks = server.BackgroundTasks()
        receipt = await server.write_single_chat(request, tasks)
        assert receipt["ok"] and receipt["chatDigests"]["selected"]
        broadcast.assert_not_awaited()
        capture.assert_not_called()
        await tasks()
        broadcast.assert_awaited_once()
        capture.assert_called_once()
        replay_tasks = server.BackgroundTasks()
        assert await server.write_single_chat(request, replay_tasks) == receipt
        assert replay_tasks.tasks == []

    asyncio.run(save())


def duplicate_chat_sources(chat_store, *, archived_project=True):
    client, app_path, index_path, project, project_path, archive_reads = chat_store
    selected = {"id": "selected", "title": "Selected", "projectPath": str(project),
                "items": [agent("selected-item")]}
    app_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.write_text(json.dumps({"version": 1, "chats": [selected, {
        "id": "app-neighbor", "items": [user("app-message")],
    }]}), encoding="utf-8")
    project_chats = [selected, {"id": "project-neighbor", "items": [agent("unopened-item")]}]
    if archived_project:
        write_store(project_path, project_chats)
    else:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text(json.dumps({"version": 1, "chats": project_chats}), encoding="utf-8")
    index_path.write_text(json.dumps({"version": 1, "projectPaths": [str(project)]}), encoding="utf-8")
    return selected


def test_catalog_compares_duplicate_chat_content_across_archive_encodings(chat_store):
    client, _, _, _, _, archive_reads = chat_store
    duplicate_chat_sources(chat_store)
    legacy = client.get("/api/app/chats").json()
    archive_reads.clear()
    catalog = client.get("/api/app/chats", params={"catalog": "1"}).json()
    assert not legacy["writeBlocked"]
    assert not catalog["writeBlocked"], catalog["recoveries"]
    assert [entry["id"] for entry in catalog["entries"]].count("selected") == 1
    assert "unopened-item" not in archive_reads


@pytest.mark.parametrize("operation", ["patch", "move", "delete"])
@pytest.mark.parametrize("archived_project", [False, True])
def test_single_save_coalesces_equivalent_sources_without_resurrecting_chat(chat_store, operation, archived_project):
    client, app_path, _, project, project_path, archive_reads = chat_store
    selected = duplicate_chat_sources(chat_store, archived_project=archived_project)
    destination = project.with_name("destination")
    destination.mkdir()
    catalog = client.get("/api/app/chats", params={"catalog": "1", "projectPath": str(destination)}).json()
    loaded = client.get("/api/app/chats", params={"chatId": "selected", "full": "1"}).json()
    body = patch_request(loaded, title="Changed", move_to=destination if operation == "move" else None)
    body["sourceRevisions"] = catalog["sources"]
    if operation == "delete":
        body.pop("chatPatch")
        body["delete"] = True
    neighbors = [json.loads(path.read_text(encoding="utf-8"))["chats"][1] for path in (app_path, project_path)]
    archive_reads.clear()
    result = client.post("/api/app/chats/single", json=body)
    assert result.status_code == 200, result.text
    after = client.get("/api/app/chats", params={"catalog": "1"}).json()
    assert not after["writeBlocked"], after["recoveries"]
    entries = [entry for entry in after["entries"] if entry["id"] == "selected"]
    assert len(entries) == (0 if operation == "delete" else 1)
    assert all(record["id"] != "selected" for record in json.loads(app_path.read_text())["chats"])
    for path, neighbor in zip((app_path, project_path), neighbors):
        assert neighbor in json.loads(path.read_text(encoding="utf-8"))["chats"]
    if operation != "delete":
        owner = str(destination if operation == "move" else project)
        assert entries[0]["sourceProjectPath"] == owner
        full = client.get("/api/app/chats", params={"chatId": "selected", "chatProjectPath": owner, "full": "1"}).json()
        assert full["chats"][0]["title"] == "Changed"
        assert full["chats"][0]["items"] == selected["items"]
    assert "unopened-item" not in archive_reads


def test_single_save_checks_duplicate_source_revision_before_removing_copy(chat_store):
    client, app_path, _, _, project_path, _ = chat_store
    duplicate_chat_sources(chat_store, archived_project=False)
    catalog = client.get("/api/app/chats", params={"catalog": "1"}).json()
    loaded = client.get("/api/app/chats", params={"chatId": "selected", "full": "1"}).json()
    body = patch_request(loaded, title="Changed")
    body["sourceRevisions"] = catalog["sources"]
    document = json.loads(project_path.read_text())
    document["chats"][1]["title"] = "Concurrent neighbor edit"
    project_path.write_text(json.dumps(document), encoding="utf-8")
    before = {path: path.read_bytes() for path in (app_path, project_path)}
    result = client.post("/api/app/chats/single", json=body)
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "chat_store_snapshot_changed"
    assert {path: path.read_bytes() for path in before} == before


def test_single_delete_preserves_conflicting_same_id_sources(chat_store):
    client, app_path, _, _, project_path, _ = chat_store
    duplicate_chat_sources(chat_store, archived_project=False)
    document = json.loads(project_path.read_text())
    document["chats"][0]["title"] = "Different conversation content"
    project_path.write_text(json.dumps(document), encoding="utf-8")
    catalog = client.get("/api/app/chats", params={"catalog": "1"}).json()
    assert catalog["writeBlocked"]
    before = {path: path.read_bytes() for path in (app_path, project_path)}
    result = client.post("/api/app/chats/single", json={
        "chatId": "selected", "delete": True, "sourceRevisions": catalog["sources"],
    })
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "chat_store_duplicate_id_conflict"
    assert {path: path.read_bytes() for path in before} == before


def test_duplicate_source_coalescing_rolls_back_all_sources_on_write_failure(chat_store, monkeypatch):
    import dashboard_server as server

    client, app_path, index_path, _, project_path, _ = chat_store
    duplicate_chat_sources(chat_store)
    catalog = client.get("/api/app/chats", params={"catalog": "1"}).json()
    loaded = client.get("/api/app/chats", params={"chatId": "selected", "full": "1"}).json()
    body = patch_request(loaded, title="Changed")
    body["sourceRevisions"] = catalog["sources"]
    before = {path: path.read_bytes() for path in (app_path, index_path, project_path)}
    write = server.atomic_write_bytes
    failed = False

    def fail_second_source(path, data):
        nonlocal failed
        if path == project_path and not failed:
            failed = True
            assert app_path.read_bytes() != before[app_path]
            raise OSError("Injected project source write failure")
        return write(path, data)

    monkeypatch.setattr(server, "atomic_write_bytes", fail_second_source)
    result = client.post("/api/app/chats/single", json=body)
    assert result.status_code == 500 and failed
    assert {path: path.read_bytes() for path in before} == before
