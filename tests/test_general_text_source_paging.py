import hashlib
from pathlib import Path
import tracemalloc

import pytest

from general_agent_tools import FileReadLimitError, read_text_file, search_text


def read(path, **kwargs):
    return read_text_file(path, allowed_roots=[path], max_bytes=64,
                          enable_source_paging=True, **kwargs)


def test_large_file_pages_restore_all_unicode_and_long_line(tmp_path):
    path = tmp_path / "large.txt"
    text = "a" * (4 * 1024 * 1024) + "😀終端\nTAIL"
    path.write_bytes(text.encode())
    offset, digest, parts = 0, None, []
    while True:
        result = read(path, text_offset=offset, page_chars=1_000_000, snapshot_digest=digest)
        parts.append(result["text"])
        assert result["bytes"] == len(text.encode()) and result["totalChars"] == len(text)
        assert len(result["text"]) <= 1_000_000
        digest = result["snapshotDigest"]
        if not result["hasMore"]:
            assert result["nextTextOffset"] is None
            break
        assert result["nextTextOffset"] > offset
        offset = result["nextTextOffset"]
    assert "".join(parts) == text


def test_streaming_local_source_preserves_credential_shaped_text_across_chunks_and_pages(tmp_path):
    path = tmp_path / "credentials-example.txt"
    text = ("prefix\napi_key='" + "SECRET" * 20000 + "'\nBearer " + "TOKEN" * 20000
            + "\n-----BEGIN RSA PRIVATE KEY-----\n" + "PEMSECRET\n" * 10000
            + "-----END RSA PRIVATE KEY-----\nTAIL")
    path.write_bytes(text.encode("utf-8"))
    parts, offset, digest = [], 0, None
    while True:
        result = read(path, text_offset=offset, page_chars=16385, snapshot_digest=digest)
        assert result["redacted"] is False
        parts.append(result["text"])
        digest = result["snapshotDigest"]
        if not result["hasMore"]:
            break
        offset = result["nextTextOffset"]
    assert "".join(parts) == text


def test_original_line_range_preserves_source_lines(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"-----BEGIN PRIVATE KEY-----\r\nsecret-body\r\n-----END PRIVATE KEY-----\r\nlast\r\n")
    result = read(path, text_offset=0, page_chars=50, start_line=4, end_line=4)
    assert result["text"] == "last\r\n"
    assert result["startLine"] == result["endLine"] == 4
    selected = read(path, text_offset=0, start_line=2, end_line=2)
    assert selected["text"] == "secret-body\r\n"


@pytest.mark.parametrize("suffix", [b"\x00", b"\xff"])
def test_invalid_tail_rejects_entire_page(tmp_path, suffix):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"safe prefix\n" * 100 + suffix)
    with pytest.raises(ValueError):
        read(path, text_offset=0, page_chars=3)


def test_snapshot_change_and_invalid_offsets_are_rejected(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("text " * 100, encoding="utf-8")
    first = read(path, text_offset=0, page_chars=5)
    path.write_text("changed " * 100, encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot.*changed"):
        read(path, text_offset=5, snapshot_digest=first["snapshotDigest"])
    for value in (-1, True, 0.5):
        with pytest.raises(ValueError):
            read(path, text_offset=value)
    with pytest.raises(ValueError):
        read(path, page_chars=0)


def test_small_inline_and_search_limits_remain_compatible(tmp_path):
    path = tmp_path / "small.txt"
    path.write_text("short 😀", encoding="utf-8")
    assert read(path) == read_text_file(path, allowed_roots=[path], max_bytes=64)
    with pytest.raises(FileReadLimitError):
        read_text_file(path, allowed_roots=[path], max_bytes=2)
    skipped = search_text(path, "short", allowed_roots=[path], max_file_bytes=2)
    assert skipped["matches"] == [] and skipped["skipped_resource_limit"] == 1


def test_long_line_does_not_allocate_whole_file(tmp_path):
    path = tmp_path / "long.txt"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    tracemalloc.start()
    try:
        result = read(path, text_offset=1_000_000, page_chars=64)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["text"] == "x" * 64
    assert peak < 800_000


def test_long_credential_shaped_header_is_ordinary_local_text(tmp_path):
    path = tmp_path / "header.txt"
    path.write_text("-----BEGIN " + "X" * 10000 + " PRIVATE KEY-----\nsecret", encoding="utf-8")
    result = read(path, text_offset=0, page_chars=5)
    assert result["text"] == "-----"
    assert result["totalChars"] == path.stat().st_size
    assert result["redacted"] is False


def test_utf8_decoder_preserves_credential_shaped_text_at_chunk_boundary(tmp_path):
    path = tmp_path / "unicode.txt"
    path.write_bytes(("x" * 16383 + "😀\n-----BEGİN PRıVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\nTAIL").encode())
    result = read(path, text_offset=16383, page_chars=100)
    assert result["text"] == "😀\n-----BEGİN PRıVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\nTAIL"
    text = "Bearer ABCDEFGHſſSECRET\nTAIL"
    path.write_bytes(text.encode("utf-8"))
    assert read(path, text_offset=0)["text"] == text


def test_mid_scan_mutation_fails_without_publishing_page(tmp_path, monkeypatch):
    import general_text_stream
    original = general_text_stream._decoded_chars
    path = tmp_path / "changing.txt"
    path.write_bytes(b"safe " * 10000)
    def changing(handle, digest):
        chars = original(handle, digest)
        yield next(chars)
        with path.open("ab") as writer:
            writer.write(b"changed")
        yield from chars
    monkeypatch.setattr(general_text_stream, "_decoded_chars", changing)
    with pytest.raises(ValueError, match="changed during"):
        read(path, text_offset=0, page_chars=3)


@pytest.mark.parametrize("prefix", ["password=", "Bearer "])
def test_overlapping_credential_and_pem_markup_is_returned_unchanged(tmp_path, prefix):
    path = tmp_path / "overlap.txt"
    text = prefix + "-----BEGIN PRIVATE KEY-----\nFAKE_PRIVATE_BODY_SENTINEL\n-----END PRIVATE KEY-----\n"
    path.write_bytes(text.encode("utf-8"))
    result = read(path, text_offset=0, page_chars=1000)
    assert result["text"] == text
    assert result["redacted"] is False
