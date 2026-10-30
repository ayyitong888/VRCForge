"""Request-owned, read-only UTF-8 source paging; no retained files or global store.

Source text is returned unchanged. Privacy redaction belongs to log export.
The existing snapshot rejects source changes between page reads.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
from pathlib import Path
import re
from typing import BinaryIO, Iterator


CHUNK_BYTES = 16_384
_LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def _decoded_chars(handle: BinaryIO, digest) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    while chunk := handle.read(CHUNK_BYTES):
        digest.update(chunk)
        if b"\x00" in chunk:
            raise ValueError("binary file rejected")
        try:
            yield from decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise ValueError("file is not complete valid UTF-8") from exc
    try:
        yield from decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise ValueError("file is not complete valid UTF-8") from exc


def _identity(stat) -> tuple[int, ...]:
    # Windows stat/fstat may expose different ctime meanings. Compare ctime
    # only between two observations made through the same API below.
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def read_source_page(path: Path, *, text_offset: int, page_chars: int,
                     snapshot_digest: str | None, start_line: int | None,
                     end_line: int | None) -> dict:
    if type(text_offset) is not int or text_offset < 0:
        raise ValueError("text_offset must be a non-negative integer")
    if type(page_chars) is not int or not 1 <= page_chars <= 1_000_000:
        raise ValueError("page_chars must be between 1 and 1000000")
    if snapshot_digest is not None and (not isinstance(snapshot_digest, str) or
            re.fullmatch(r"[0-9a-f]{64}", snapshot_digest) is None):
        raise ValueError("snapshot_digest must be a SHA-256 digest")
    start = 1 if start_line is None else start_line
    if type(start) is not int or start < 1 or (end_line is not None and
            (type(end_line) is not int or end_line < start)):
        raise ValueError("startLine/endLine must be positive inclusive line numbers")
    selected_lines = start_line is not None or end_line is not None
    digest = hashlib.sha256()
    page, total = [], 0
    line, previous_cr, last_break, saw_char = 1, False, False, False
    before_path = path.stat()
    # Authorization belongs to read_text_file. This handle is read-only and
    # belongs solely to this call; the with block closes it on every outcome.
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if _identity(before) != _identity(before_path):
            raise ValueError("file changed before source scan")
        chars = _decoded_chars(handle, digest)
        for char in chars:
            saw_char = True
            char_line = line - 1 if previous_cr and char == "\n" else line
            if not selected_lines or start <= char_line and (end_line is None or char_line <= end_line):
                if text_offset <= total < text_offset + page_chars:
                    page.append(char)
                total += 1
            if char in _LINE_BREAKS:
                if not (previous_cr and char == "\n"):
                    line += 1
                last_break = True
            else:
                last_break = False
            previous_cr = char == "\r"
        after = os.fstat(handle.fileno())
    after_path = path.stat()
    if (_identity(before) != _identity(after) or _identity(before) != _identity(after_path)
            or before.st_ctime_ns != after.st_ctime_ns
            or before_path.st_ctime_ns != after_path.st_ctime_ns):
        raise ValueError("file changed during source scan; restart pagination")
    line_count = line - int(last_break) if saw_char else 0
    if selected_lines and start > line_count:
        raise ValueError("startLine exceeds the file's line count")
    digest.update(json.dumps({"path": str(path), "startLine": start_line, "endLine": end_line,
                              "projection": "utf8-source-v1"}, sort_keys=True).encode())
    snapshot = digest.hexdigest()
    if snapshot_digest is not None and snapshot_digest != snapshot:
        raise ValueError("file snapshot changed; restart pagination at text_offset 0")
    more = text_offset + len(page) < total
    return {"path": str(path), "text": "".join(page), "bytes": before.st_size, "redacted": False,
            "textOffset": text_offset, "nextTextOffset": text_offset + len(page) if more else None,
            "hasMore": more, "totalChars": total, "snapshotDigest": snapshot, "truncated": more,
            **({"startLine": start, "endLine": min(end_line or line_count, line_count)} if selected_lines else {})}
