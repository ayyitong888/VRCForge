"""Request-owned, read-only UTF-8 source paging; no retained files or global store.

Paged input uses conservative streaming credential recognition. Ambiguous long
credential headers and unterminated private keys fail before any page is returned.
This is not an atomic filesystem snapshot or an exact malformed-regex emulator.
"""
from __future__ import annotations

from collections import deque
import codecs
import hashlib
import json
import os
from pathlib import Path
import re
from typing import BinaryIO, Iterator


CHUNK_BYTES = 16_384
PREFIX_LIMIT = 256
_KEY = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)\b")
_BEARER_CHAR = re.compile(r"[A-Za-z0-9._~+/=-]", re.IGNORECASE)
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


class _Characters:
    def __init__(self, chars: Iterator[str]):
        self.chars, self.buffer = chars, deque()

    def peek(self, count: int = 1) -> str:
        while len(self.buffer) < count:
            value = next(self.chars, None)
            if value is None:
                break
            self.buffer.append(value)
        if count == 1:
            return self.buffer[0] if self.buffer else ""
        return "".join(self.buffer)[:count]

    def take(self, count: int = 1) -> str:
        self.peek(count)
        return "".join(self.buffer.popleft() for _ in range(min(count, len(self.buffer))))


def _pem_header(chars: _Characters, prefix: str) -> tuple[str, bool]:
    header = chars.take(len(prefix))
    while chars.peek() and chars.peek() not in "-\r\n":
        if len(header) >= PREFIX_LIMIT:
            raise ValueError("sensitive PEM header exceeds safe recognition prefix")
        header += chars.take()
    if chars.peek(5) == "-----":
        header += chars.take(5)
    return header, re.search(r"PRIVATE KEY-----\Z", header, re.IGNORECASE) is not None


def _safe_chars(chars: _Characters, state: dict, *, preserve_lines: bool) -> Iterator[str]:
    previous = ""
    while current := chars.peek():
        boundary = not (previous.isalnum() or previous == "_")
        if current == "-" and re.fullmatch(r"-----BEGIN ", chars.peek(11), re.IGNORECASE):
            header, private = _pem_header(chars, "-----BEGIN ")
            if not private:
                if _KEY.search(header) or re.search(r"(?i)\bBearer\s", header):
                    raise ValueError("ambiguous sensitive PEM header")
                yield from header
                previous = header[-1:]
                continue
            state["redacted"] = True
            yield from "[REDACTED PRIVATE KEY]"
            while True:
                if not chars.peek():
                    raise ValueError("unterminated private key block")
                if chars.peek() == "-" and re.fullmatch(r"-----END ", chars.peek(9), re.IGNORECASE):
                    ending, closed = _pem_header(chars, "-----END ")
                    if closed:
                        break
                    if preserve_lines:
                        yield from (value for value in ending if value in _LINE_BREAKS)
                else:
                    value = chars.take()
                    if preserve_lines and value in _LINE_BREAKS:
                        yield value
            previous = "-"
            continue
        key = _KEY.match(chars.peek(20)) if boundary and current.casefold() in "aprs" else None
        if key:
            # Prefix/whitespace is public in the old projection, so emit it as
            # it arrives rather than retaining an unbounded whitespace run.
            yield from chars.take(key.end())
            while chars.peek() and chars.peek().isspace():
                yield chars.take()
            if chars.peek() not in {"=", ":"}:
                previous = " "
                continue
            yield chars.take()
            while chars.peek() and chars.peek().isspace():
                yield chars.take()
            if chars.peek() in {"'", '"'}:
                yield chars.take()
            if chars.peek() and chars.peek() not in "\r\n\"'":
                state["redacted"] = True
                yield from "[REDACTED]"
                while chars.peek() and chars.peek() not in "\r\n\"'":
                    if chars.peek() == "-" and re.fullmatch(r"-----BEGIN ", chars.peek(11), re.IGNORECASE):
                        raise ValueError("ambiguous private key inside credential assignment")
                    chars.take()
                if chars.peek() in {"'", '"'}:
                    yield chars.take()
            previous = " "
            continue
        if boundary and current.casefold() == "b" and chars.peek(6).casefold() == "bearer":
            prefix = chars.take(6)
            while chars.peek() and chars.peek().isspace():
                if len(prefix) >= PREFIX_LIMIT:
                    raise ValueError("sensitive Bearer prefix exceeds safe recognition bound")
                prefix += chars.take()
            if len(prefix) > 6 and len(chars.peek(8)) == 8 and all(_BEARER_CHAR.fullmatch(c) for c in chars.peek(8)):
                state["redacted"] = True
                yield from "Bearer [REDACTED]"
                if preserve_lines:
                    yield from (value for value in prefix if value in _LINE_BREAKS)
                while chars.peek() and _BEARER_CHAR.fullmatch(chars.peek()):
                    if chars.peek() == "-" and re.fullmatch(r"-----BEGIN ", chars.peek(11), re.IGNORECASE):
                        raise ValueError("ambiguous private key inside Bearer credential")
                    chars.take()
                previous = " "
            else:
                yield from prefix
                previous = prefix[-1:]
            continue
        previous = chars.take()
        yield previous


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
    state = {"redacted": False}
    page, total = [], 0
    line, previous_cr, last_break, saw_char = 1, False, False, False
    before_path = path.stat()
    # Authorization belongs to read_text_file. This handle is read-only and
    # belongs solely to this call; the with block closes it on every outcome.
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if _identity(before) != _identity(before_path):
            raise ValueError("file changed before source scan")
        chars = _safe_chars(_Characters(_decoded_chars(handle, digest)), state, preserve_lines=selected_lines)
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
                              "projection": "credential-stream-v1"}, sort_keys=True).encode())
    snapshot = digest.hexdigest()
    if snapshot_digest is not None and snapshot_digest != snapshot:
        raise ValueError("file snapshot changed; restart pagination at text_offset 0")
    more = text_offset + len(page) < total
    return {"path": str(path), "text": "".join(page), "bytes": before.st_size, **state,
            "textOffset": text_offset, "nextTextOffset": text_offset + len(page) if more else None,
            "hasMore": more, "totalChars": total, "snapshotDigest": snapshot, "truncated": more,
            **({"startLine": start, "endLine": min(end_line or line_count, line_count)} if selected_lines else {})}
