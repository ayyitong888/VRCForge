from pathlib import Path
import copy
import pytest
import chat_transcript_archive as archive
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


def test_identical_item_is_content_addressed_once(tmp_path):
    store = tmp_path / "chat-transcripts.json"
    item = large_chat()[0]["items"][0]
    chats = [{"id": "chat-a", "items": [copy.deepcopy(item), copy.deepcopy(item)]}]
    packed, pending = encode_chats(chats)
    assert len(pending) == 1
    write_pending(store, pending)
    assert decode_chats(packed, store) == chats


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
