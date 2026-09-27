"""Lossless content-addressed blobs for oversized durable chat agent items."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

ARCHIVE_SCHEMA = "vrcforge.chat_transcript_archive.v1"
ARCHIVE_REF_KEY = "__vrcforge_chat_transcript_archive__"
ARCHIVE_INLINE_ITEM_MAX_BYTES = 64 * 1024
REF_FIELD = ARCHIVE_REF_KEY
ARCHIVE_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
DOCUMENT_VERSIONS = (1, 2)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ChatTranscriptArchiveError(ValueError):
    """Raised when an archive reference or blob fails integrity/scope checks."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ChatTranscriptArchiveError("chat item is not canonical JSON") from exc


def _chat_id(chat: Mapping[str, Any]) -> str:
    value = str(chat.get("id") or "").strip()
    if not value:
        raise ChatTranscriptArchiveError("chat id is required for archived items")
    return value


def _archive_digest(chat_id: str, item_bytes: bytes) -> str:
    return hashlib.sha256(chat_id.encode("utf-8") + b"\0" + item_bytes).hexdigest()


def _link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    # Windows junctions and other reparse points must not redirect the archive root.
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def blob_path(store_path: str | Path, digest: str) -> Path:
    """Return the only archive path allowed for a store and digest."""
    normalized = str(digest or "").strip()
    if not _DIGEST_RE.fullmatch(normalized):
        raise ChatTranscriptArchiveError("archive digest is invalid")
    store = Path(store_path)
    if not store.name or store.name in {".", ".."}:
        raise ChatTranscriptArchiveError("store path is invalid")
    root_unresolved = store.parent / f".{store.name}.archive"
    shard_unresolved = root_unresolved / normalized[:2]
    file_unresolved = shard_unresolved / f"{normalized}.json.gz"
    for candidate in (root_unresolved, shard_unresolved, file_unresolved):
        if _link_like(candidate):
            raise ChatTranscriptArchiveError("archive path contains a link-like component")
    root = root_unresolved.resolve(strict=False)
    candidate = (root / normalized[:2] / f"{normalized}.json.gz").resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ChatTranscriptArchiveError("archive path escapes store root") from exc
    return candidate


def _validate_ref(ref: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "digest", "chatId", "itemId", "itemType", "sha256", "uncompressedBytes", "compressedBytes", "codec"}
    if set(ref) != required or ref.get("schema") != ARCHIVE_SCHEMA or ref.get("codec") != "gzip" or ref.get("itemType") != "agent":
        raise ChatTranscriptArchiveError("archive reference shape is invalid")
    digest = ref.get("digest")
    content_hash = ref.get("sha256")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ChatTranscriptArchiveError("archive digest is invalid")
    if not isinstance(content_hash, str) or not _DIGEST_RE.fullmatch(content_hash):
        raise ChatTranscriptArchiveError("archive content hash is invalid")
    for field in ("chatId", "itemId"):
        value = ref.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 1024:
            raise ChatTranscriptArchiveError(f"archive {field} is invalid")
    for field in ("uncompressedBytes", "compressedBytes"):
        value = ref.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ChatTranscriptArchiveError(f"archive {field} is invalid")
    if ref["uncompressedBytes"] > ARCHIVE_MAX_UNCOMPRESSED_BYTES:
        raise ChatTranscriptArchiveError("archive uncompressed size exceeds cap")
    if ref["compressedBytes"] > ARCHIVE_MAX_UNCOMPRESSED_BYTES + 1024 * 1024:
        raise ChatTranscriptArchiveError("archive compressed size exceeds cap")
    return dict(ref)


def is_archived_item(item: Any) -> bool:
    """Return whether an index item has a structurally valid archive reference."""
    if not isinstance(item, Mapping) or REF_FIELD not in item:
        return False
    ref = item.get(REF_FIELD)
    try:
        normalized = _validate_ref(ref) if isinstance(ref, Mapping) else None
    except ChatTranscriptArchiveError:
        return False
    allowed = {"id", "type", "createdAt", REF_FIELD}
    created_at_ok = "createdAt" not in item or isinstance(item.get("createdAt"), str)
    return (set(item).issubset(allowed) and item.get("type") == "agent"
            and isinstance(item.get("id"), str) and bool(item["id"].strip())
            and created_at_ok and normalized is not None and normalized["itemId"] == item["id"])


def _reject_input_ref(item: Any) -> None:
    if isinstance(item, Mapping) and ARCHIVE_REF_KEY in item:
        raise ChatTranscriptArchiveError("encode_chats accepts hydrated items, not archive references")


def encode_chats(chats: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Pack oversized agent items and return `(index_chats, pending_blobs)`.

    The function is pure: it only returns bytes and does not create directories or files.
    """
    packed_chats: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for chat_value in chats:
        if not isinstance(chat_value, Mapping):
            raise ChatTranscriptArchiveError("chat must be an object")
        chat = copy.deepcopy(dict(chat_value))
        chat_id = _chat_id(chat)
        items = chat.get("items")
        if not isinstance(items, list):
            packed_chats.append(chat)
            continue
        packed_items: list[Any] = []
        for item in items:
            _reject_input_ref(item)
            if not isinstance(item, Mapping) or item.get("type") != "agent":
                packed_items.append(item)
                continue
            item_copy = copy.deepcopy(dict(item))
            raw = _canonical_bytes(item_copy)
            if len(raw) <= ARCHIVE_INLINE_ITEM_MAX_BYTES:
                packed_items.append(item_copy)
                continue
            if len(raw) > ARCHIVE_MAX_UNCOMPRESSED_BYTES:
                raise ChatTranscriptArchiveError("agent item exceeds archive size cap")
            item_id = str(item_copy.get("id") or "").strip()
            if not item_id:
                raise ChatTranscriptArchiveError("archived agent item id is required")
            if "createdAt" in item_copy and not isinstance(item_copy["createdAt"], str):
                raise ChatTranscriptArchiveError("archived agent createdAt is invalid")
            digest = _archive_digest(chat_id, raw)
            compressed = gzip.compress(raw, compresslevel=9, mtime=0)
            metadata = {
                "schema": ARCHIVE_SCHEMA,
                "digest": digest,
                "chatId": chat_id,
                "itemId": item_id,
                "itemType": "agent",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "uncompressedBytes": len(raw),
                "compressedBytes": len(compressed),
                "codec": "gzip",
            }
            _validate_ref(metadata)
            existing = pending.get(digest)
            if existing is not None and existing["data"] != compressed:
                raise ChatTranscriptArchiveError("archive digest collision")
            pending[digest] = {**metadata, "data": compressed}
            ref_item: dict[str, Any] = {"id": item_id, "type": "agent", ARCHIVE_REF_KEY: metadata}
            if "createdAt" in item_copy:
                ref_item["createdAt"] = item_copy["createdAt"]
            packed_items.append(ref_item)
        chat["items"] = packed_items
        packed_chats.append(chat)
    return packed_chats, pending


def _read_blob(store_path: str | Path, ref: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_ref(ref)
    digest = normalized["digest"]
    path = blob_path(store_path, digest)
    if not path.is_file() or _link_like(path):
        raise ChatTranscriptArchiveError("archive blob is missing")
    try:
        if path.stat().st_size != normalized["compressedBytes"]:
            raise ChatTranscriptArchiveError("archive compressed size mismatch")
        with path.open("rb") as handle:
            with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
                raw = stream.read(normalized["uncompressedBytes"] + 1)
                if len(raw) != normalized["uncompressedBytes"] or stream.read(1):
                    raise ChatTranscriptArchiveError("archive uncompressed size mismatch")
            if handle.read(1):
                raise ChatTranscriptArchiveError("archive has trailing bytes")
    except ChatTranscriptArchiveError:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ChatTranscriptArchiveError("archive gzip is invalid") from exc
    if hashlib.sha256(raw).hexdigest() != normalized["sha256"]:
        raise ChatTranscriptArchiveError("archive content hash mismatch")
    if _archive_digest(normalized["chatId"], raw) != digest:
        raise ChatTranscriptArchiveError("archive chat binding mismatch")
    try:
        item = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChatTranscriptArchiveError("archive JSON is invalid") from exc
    if not isinstance(item, dict) or item.get("id") != normalized["itemId"] or item.get("type") != "agent":
        raise ChatTranscriptArchiveError("archive item identity mismatch")
    return item


def decode_chats(chats: Sequence[Mapping[str, Any]], store_path: str | Path) -> list[dict[str, Any]]:
    """Hydrate packed chats, validating every referenced blob and scope."""
    hydrated: list[dict[str, Any]] = []
    for chat_value in chats:
        if not isinstance(chat_value, Mapping):
            raise ChatTranscriptArchiveError("chat must be an object")
        chat = copy.deepcopy(dict(chat_value)); chat_id = _chat_id(chat)
        items = chat.get("items")
        if not isinstance(items, list): hydrated.append(chat); continue
        result_items: list[Any] = []
        for item in items:
            if not isinstance(item, Mapping) or ARCHIVE_REF_KEY not in item:
                result_items.append(item); continue
            ref = item[ARCHIVE_REF_KEY]
            if not isinstance(ref, Mapping) or str(ref.get("chatId") or "") != chat_id:
                raise ChatTranscriptArchiveError("archive reference chat scope mismatch")
            allowed = {"id", "type", "createdAt", REF_FIELD}
            if set(item) - allowed or not is_archived_item(item):
                raise ChatTranscriptArchiveError("archive index placeholder is invalid")
            full = _read_blob(store_path, ref)
            if str(item.get("id") or "") != str(full.get("id") or "") or item.get("type") != full.get("type"):
                raise ChatTranscriptArchiveError("archive index identity mismatch")
            sentinel = object()
            if item.get("createdAt", sentinel) != full.get("createdAt", sentinel):
                raise ChatTranscriptArchiveError("archive index createdAt mismatch")
            result_items.append(full)
        chat["items"] = result_items; hydrated.append(chat)
    return hydrated
