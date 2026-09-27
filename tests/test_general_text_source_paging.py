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


def test_streaming_secret_states_cross_chunks_and_pages(tmp_path):
    path = tmp_path / "credentials-example.txt"
    path.write_text("prefix\napi_key='" + "SECRET" * 20000 + "'\nBearer " + "TOKEN" * 20000
                    + "\n-----BEGIN RSA PRIVATE KEY-----\n" + "PEMSECRET\n" * 10000
                    + "-----END RSA PRIVATE KEY-----\nTAIL", encoding="utf-8")
    parts, offset, digest = [], 0, None
    while True:
        result = read(path, text_offset=offset, page_chars=17, snapshot_digest=digest)
        assert all(secret not in result["text"] for secret in ("SECRET", "TOKEN", "PEMSECRET"))
        parts.append(result["text"])
        digest = result["snapshotDigest"]
        if not result["hasMore"]:
            break
        offset = result["nextTextOffset"]
    assert "".join(parts).replace("\r\n", "\n") == (
        "prefix\napi_key='[REDACTED]'\nBearer [REDACTED]\n[REDACTED PRIVATE KEY]\nTAIL")


def test_original_line_range_keeps_pem_body_hidden(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"-----BEGIN PRIVATE KEY-----\r\nsecret-body\r\n-----END PRIVATE KEY-----\r\nlast\r\n")
    result = read(path, text_offset=0, page_chars=50, start_line=4, end_line=4)
    assert result["text"] == "last\r\n"
    assert result["startLine"] == result["endLine"] == 4
    hidden = read(path, text_offset=0, start_line=2, end_line=2)
    assert "secret-body" not in hidden["text"]


@pytest.mark.parametrize("suffix", [b"\x00", b"\xff", b"-----BEGIN PRIVATE KEY-----\nunterminated"])
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


def test_ambiguous_unbounded_sensitive_header_fails_without_output(tmp_path):
    path = tmp_path / "header.txt"
    path.write_text("-----BEGIN " + "X" * 10000 + " PRIVATE KEY-----\nsecret", encoding="utf-8")
    with pytest.raises(ValueError, match="header|prefix"):
        read(path, text_offset=0, page_chars=5)


def test_utf8_decoder_and_unicode_case_private_key_boundaries(tmp_path):
    path = tmp_path / "unicode.txt"
    path.write_bytes(("x" * 16383 + "😀\n-----BEGİN PRıVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\nTAIL").encode())
    result = read(path, text_offset=16383, page_chars=100)
    assert result["text"] == "😀\n[REDACTED PRIVATE KEY]\nTAIL"
    path.write_text("Bearer ABCDEFGHſſSECRET\nTAIL", encoding="utf-8")
    assert read(path, text_offset=0)["text"].replace("\r\n", "\n") == "Bearer [REDACTED]\nTAIL"


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
def test_overlapping_credential_and_pem_fails_before_returning_page(tmp_path, prefix):
    path = tmp_path / "overlap.txt"
    path.write_text(prefix + "-----BEGIN PRIVATE KEY-----\nFAKE_PRIVATE_BODY_SENTINEL\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    with pytest.raises(ValueError, match="private key|PEM"):
        read(path, text_offset=0, page_chars=1000)
