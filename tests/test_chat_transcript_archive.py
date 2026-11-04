from pathlib import Path
import copy
import pytest
import chat_transcript_archive as archive
from chat_snapshot_transport import chat_content_digest
from chat_storage_delta import materialize_chat_patches
from chat_transcript_archive import (
    ARCHIVE_REF_KEY, ChatTranscriptArchiveError, blob_path, decode_chats,
    encode_chats, is_archived_item,
)


def write_pending(store: Path, pending: dict) -> None:
    for digest, entry in pending.items():
        path = blob_path(store, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entry["data"])


def large_chat(chat_id="chat-a"):
    return [{"id": chat_id, "items": [{
        "id": "agent-1", "type": "agent", "createdAt": "2026-09-27T00:00:00Z",
        "response": {"steps": [{"result": "safe-result-" * 12000}], "plan": {"reply": "done"}},
        "timeline": [],
    }]}]


def test_roundtrip_full_item_and_metadata(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    chats = large_chat(); packed, pending = encode_chats(chats)
    assert len(pending) == 1
    ref = packed[0]["items"][0][ARCHIVE_REF_KEY]
    assert ref["uncompressedBytes"] > 64 * 1024
    assert is_archived_item(packed[0]["items"][0])
    write_pending(store, pending)
    assert decode_chats(packed, store) == chats


def test_transaction_reuse_skips_archive_recompression_and_blob_read(tmp_path, monkeypatch):
    store = tmp_path / "chat-transcripts.json"
    original = large_chat()
    packed, pending = encode_chats(original)
    write_pending(store, pending)
    hydrated, reuse = archive.decode_chats_with_reuse(packed, store)
    current = hydrated[0]
    patch = {
        "baseDigest": chat_content_digest(current),
        "metadataPatch": [{"op": "add", "path": "/title", "value": "metadata-only update"}],
        "itemOrder": [item["id"] for item in current["items"]],
        "itemPatches": {},
        "newItems": {},
    }

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unchanged archived item must be reused without recompression or blob read")

    monkeypatch.setattr(archive.gzip, "compress", unexpected)
    monkeypatch.setattr(archive, "_read_blob", unexpected)
    changed = materialize_chat_patches(
        hydrated,
        [{"id": current["id"]}],
        {current["id"]: patch},
    )
    rewritten, new_blobs = encode_chats(changed, reuse=reuse)

    assert rewritten[0]["items"][0] == packed[0]["items"][0]
    assert new_blobs == {}


def test_transaction_reuse_does_not_reuse_changed_item(tmp_path, monkeypatch):
    store = tmp_path / "chat-transcripts.json"
    packed, pending = encode_chats(large_chat())
    write_pending(store, pending)
    hydrated, reuse = archive.decode_chats_with_reuse(packed, store)
    hydrated[0]["items"][0]["response"]["plan"]["reply"] = "changed"
    calls = []
    original_compress = archive.gzip.compress

    def tracked_compress(*args, **kwargs):
        calls.append(1)
        return original_compress(*args, **kwargs)

    monkeypatch.setattr(archive.gzip, "compress", tracked_compress)
    rewritten, new_blobs = encode_chats(hydrated, reuse=reuse)

    assert calls == [1]
    assert rewritten[0]["items"][0][ARCHIVE_REF_KEY]["digest"] != packed[0]["items"][0][ARCHIVE_REF_KEY]["digest"]
    assert len(new_blobs) == 1


def test_prune_removes_only_previously_validated_blobs_unreferenced_by_committed_snapshot(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    prior, pending = encode_chats([*large_chat("chat-delete"), *large_chat("chat-keep")])
    write_pending(store, pending)
    _hydrated, reuse = archive.decode_chats_with_reuse(prior, store)
    retained = [prior[1]]
    rewritten, new_blobs = encode_chats(decode_chats(retained, store), reuse=reuse)
    assert new_blobs == {}
    keep_digest = rewritten[0]["items"][0][ARCHIVE_REF_KEY]["digest"]
    delete_digest = prior[0]["items"][0][ARCHIVE_REF_KEY]["digest"]
    unknown = blob_path(store, "f" * 64)
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_bytes(b"unknown user file")

    removed = archive.prune_unreferenced_blobs(store, prior, rewritten)

    assert removed == {"deletedCount": 1, "leftBehindCount": 0}
    assert not blob_path(store, delete_digest).exists()
    assert blob_path(store, keep_digest).exists()
    assert unknown.read_bytes() == b"unknown user file"


def test_prune_keeps_all_prior_blobs_when_snapshot_encoding_does_not_reference_reuse(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    prior, pending = encode_chats(large_chat())
    write_pending(store, pending)
    _hydrated, reuse = archive.decode_chats_with_reuse(prior, store)

    removed = archive.prune_unreferenced_blobs(store, prior, prior)

    assert removed == {"deletedCount": 0, "leftBehindCount": 0}
    assert blob_path(store, next(iter(pending))).exists()


def test_prune_preserves_changed_blob_even_when_its_old_reference_was_removed(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    prior, pending = encode_chats(large_chat())
    write_pending(store, pending)
    digest = next(iter(pending))
    path = blob_path(store, digest)
    path.write_bytes(path.read_bytes() + b"changed")

    removed = archive.prune_unreferenced_blobs(store, prior, [])

    assert removed == {"deletedCount": 0, "leftBehindCount": 1}
    assert path.read_bytes().endswith(b"changed")


def test_identical_item_is_content_addressed_once(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    item = large_chat()[0]["items"][0]
    chats = [{"id": "chat-a", "items": [copy.deepcopy(item), copy.deepcopy(item)]}]
    packed, pending = encode_chats(chats)
    assert len(pending) == 1
    write_pending(store, pending)
    assert decode_chats(packed, store) == chats


def test_encode_archived_agent_item_does_not_deepcopy_payload_twice(monkeypatch):
    chat = large_chat()[0]
    chat["items"].append({"id": "inline", "type": "agent", "response": {"plan": {"reply": "done"}}})
    original_deepcopy = archive.copy.deepcopy
    calls = []

    def tracked_deepcopy(value, memo=None):
        calls.append(value)
        return original_deepcopy(value, memo)

    monkeypatch.setattr(archive.copy, "deepcopy", tracked_deepcopy)
    packed, _pending = encode_chats([chat])

    # The chat-level copy already recursively detaches each item from caller
    # input. Re-copying each large agent item duplicates the expensive walk.
    assert len(calls) == 1
    assert packed[0] is not chat
    assert packed[0]["items"][0] is not chat["items"][0]
    packed[0]["items"][1]["response"]["plan"]["reply"] = "changed"
    assert chat["items"][1]["response"]["plan"]["reply"] == "done"


def test_cross_chat_reference_is_rejected(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    packed, pending = encode_chats(large_chat("chat-a")); write_pending(store, pending)
    foreign = copy.deepcopy(packed); foreign[0]["id"] = "chat-b"
    with pytest.raises(ChatTranscriptArchiveError, match="scope"):
        decode_chats(foreign, store)


@pytest.mark.parametrize("tamper", ["missing", "bytes"])
def test_missing_or_tampered_blob_fails_closed(tmp_path, tamper):
    store = tmp_path / "chat-transcripts.json"
    packed, pending = encode_chats(large_chat()); write_pending(store, pending)
    path = blob_path(store, next(iter(pending)))
    if tamper == "missing": path.unlink()
    else: path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ChatTranscriptArchiveError): decode_chats(packed, store)


def test_reserved_reference_input_is_rejected():
    packed, _ = encode_chats(large_chat())
    with pytest.raises(ChatTranscriptArchiveError, match="hydrated"):
        encode_chats(packed)


def test_encode_rejects_item_over_archive_cap(monkeypatch):
    monkeypatch.setattr(archive, "ARCHIVE_MAX_UNCOMPRESSED_BYTES", 65 * 1024)
    with pytest.raises(ChatTranscriptArchiveError, match="cap"):
        encode_chats(large_chat())


def test_decode_rejects_placeholder_and_created_at_mismatch(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    packed, pending = encode_chats(large_chat()); write_pending(store, pending)
    extra = copy.deepcopy(packed); extra[0]["items"][0]["placeholder"] = True
    with pytest.raises(ChatTranscriptArchiveError, match="placeholder"):
        decode_chats(extra, store)
    mismatch = copy.deepcopy(packed); mismatch[0]["items"][0]["createdAt"] = "other"
    with pytest.raises(ChatTranscriptArchiveError, match="createdAt"):
        decode_chats(mismatch, store)


def test_blob_path_rejects_escape_digest(tmp_path):
    with pytest.raises(ChatTranscriptArchiveError): blob_path(tmp_path / "chat.json", "../escape")


def test_encode_rejects_invalid_archive_identity_and_is_archived_shape():
    too_long = large_chat("c" * 1025)
    with pytest.raises(ChatTranscriptArchiveError, match="chatId"):
        encode_chats(too_long)
    packed, _ = encode_chats(large_chat())
    placeholder = copy.deepcopy(packed[0]["items"][0])
    placeholder["placeholder"] = True
    assert not is_archived_item(placeholder)
    placeholder = copy.deepcopy(packed[0]["items"][0])
    placeholder["createdAt"] = 42
    assert not is_archived_item(placeholder)


def test_archive_root_symlink_is_rejected(tmp_path):
    store = tmp_path / "chat.json"
    root = tmp_path / ".chat.json.archive"
    target = tmp_path / "redirect"
    target.mkdir()
    try:
        root.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ChatTranscriptArchiveError, match="link-like"):
        blob_path(store, "a" * 64)
