"""Bounded, digest-bound transport of the existing complete chat snapshot.

No retained cursor state: each page belongs to the current authenticated request.
Four million code points need at most 24 MiB when JSON-escaped, leaving ample
space for the fixed-size envelope under the native transport's 32 MiB cap.
"""
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any

from fastapi import HTTPException

from context_compaction import normalize_compaction_recovery, redact_context_text


PAGE_CHARACTERS = 4 * 1024 * 1024
SCHEMA = "vrcforge.chat_snapshot_page.v1"
SNAPSHOT_CACHE_TTL_SECONDS = 60.0
SNAPSHOT_CACHE_MAX_ENTRIES = 2
SNAPSHOT_CACHE_MAX_BYTES = 128 * 1024 * 1024
_VALIDATED_RECOVERY_MARKER = "_validatedCompactionRecoveryPaths"
_VALIDATED_TEXT_MARKER = "_validatedChatTextPaths"


@dataclass(frozen=True, slots=True)
class _SnapshotCacheEntry:
    """Immutable in-process continuation data for one authenticated scope."""

    text: str
    digest: str
    created_at: float
    byte_size: int


# This cache belongs to one dashboard process and is deliberately never
# persisted or shared.  The key includes an already-hashed auth principal,
# normalized requested project scope, and the canonical snapshot digest.
_SNAPSHOT_CACHE_LOCK = RLock()
_SNAPSHOT_CACHE: OrderedDict[tuple[str, str, str], _SnapshotCacheEntry] = OrderedDict()
_SNAPSHOT_CACHE_BYTES = 0


def normalize_snapshot_scope(project_paths: list[str] | tuple[str, ...] | None) -> str:
    """Canonicalize the requested project scope without retaining raw paths."""

    values = {
        str(value).strip().replace("/", "\\").casefold()
        for value in (project_paths or [])
        if str(value).strip()
    }
    return "\n".join(sorted(values))


def snapshot_principal_hash(auth_header: str | None) -> str:
    """Hash the authenticated principal; never store the raw bearer value."""

    principal = str(auth_header or "").strip()
    return hashlib.sha256(principal.encode("utf-8")).hexdigest()


def clear_snapshot_cache() -> None:
    """Clear process-local continuation state for bounded tests and shutdown."""

    global _SNAPSHOT_CACHE_BYTES
    with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE.clear()
        _SNAPSHOT_CACHE_BYTES = 0


def _snapshot_cache_key(
    *, principal_hash: str, project_paths: list[str] | tuple[str, ...] | None, digest: str
) -> tuple[str, str, str]:
    return (str(principal_hash), normalize_snapshot_scope(project_paths), str(digest))


def _evict_expired_snapshot_cache(now: float) -> None:
    global _SNAPSHOT_CACHE_BYTES
    expired = [
        key
        for key, entry in _SNAPSHOT_CACHE.items()
        if now - entry.created_at >= SNAPSHOT_CACHE_TTL_SECONDS
    ]
    for key in expired:
        entry = _SNAPSHOT_CACHE.pop(key)
        _SNAPSHOT_CACHE_BYTES -= entry.byte_size


def _store_snapshot_cache(
    text: str,
    digest: str,
    *,
    principal_hash: str,
    project_paths: list[str] | tuple[str, ...] | None,
) -> None:
    global _SNAPSHOT_CACHE_BYTES
    byte_size = len(text.encode("utf-8"))
    if byte_size > SNAPSHOT_CACHE_MAX_BYTES:
        return
    entry = _SnapshotCacheEntry(text, digest, time.monotonic(), byte_size)
    key = _snapshot_cache_key(principal_hash=principal_hash, project_paths=project_paths, digest=digest)
    with _SNAPSHOT_CACHE_LOCK:
        now = time.monotonic()
        _evict_expired_snapshot_cache(now)
        previous = _SNAPSHOT_CACHE.pop(key, None)
        if previous is not None:
            _SNAPSHOT_CACHE_BYTES -= previous.byte_size
        while _SNAPSHOT_CACHE and (
            len(_SNAPSHOT_CACHE) >= SNAPSHOT_CACHE_MAX_ENTRIES
            or _SNAPSHOT_CACHE_BYTES + byte_size > SNAPSHOT_CACHE_MAX_BYTES
        ):
            _old_key, old_entry = _SNAPSHOT_CACHE.popitem(last=False)
            _SNAPSHOT_CACHE_BYTES -= old_entry.byte_size
        _SNAPSHOT_CACHE[key] = entry
        _SNAPSHOT_CACHE_BYTES += byte_size


def cached_snapshot_page(
    *,
    text_offset: int,
    snapshot_digest: str,
    principal_hash: str,
    project_paths: list[str] | tuple[str, ...] | None,
) -> dict[str, Any] | None:
    """Return a continuation page only for the exact auth/scope/digest key."""

    if text_offset <= 0 or not snapshot_digest:
        return None
    key = _snapshot_cache_key(
        principal_hash=principal_hash,
        project_paths=project_paths,
        digest=snapshot_digest,
    )
    with _SNAPSHOT_CACHE_LOCK:
        _evict_expired_snapshot_cache(time.monotonic())
        entry = _SNAPSHOT_CACHE.get(key)
        if entry is None:
            return None
        _SNAPSHOT_CACHE.move_to_end(key)
        text = entry.text
    if text_offset < 0 or text_offset % PAGE_CHARACTERS:
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page offset")
    if text_offset >= len(text):
        raise HTTPException(status_code=400, detail="Chat snapshot page offset is outside the snapshot")
    end = min(text_offset + PAGE_CHARACTERS, len(text))
    return {
        "schema": SCHEMA,
        "text": text[text_offset:end],
        "textOffset": text_offset,
        "nextOffset": end if end < len(text) else None,
        "totalCharacters": len(text),
        "hasMore": end < len(text),
        "snapshotDigest": entry.digest,
    }


def chat_content_digest(chat: dict[str, Any]) -> str:
    """Hash one canonical durable chat record before any WebView projection."""
    encoded = json.dumps(chat, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def chat_digests(chats: list[dict[str, Any]]) -> dict[str, str]:
    """Return authoritative content digests keyed by non-empty chat id."""
    return {
        chat_id: chat_content_digest(chat)
        for chat in chats
        if isinstance(chat, dict)
        for chat_id in [str(chat.get("id") or "").strip()]
        if chat_id
    }


def chat_metadata_shapes(chats: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Expose persisted metadata field/container shape without metadata values."""
    def shape(value: Any, depth: int = 0) -> Any:
        if depth >= 64:
            return True
        if isinstance(value, dict):
            return {str(key): shape(child, depth + 1) for key, child in value.items()}
        return True

    result: dict[str, dict[str, Any]] = {}
    for chat in chats:
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        result[chat_id] = shape({key: value for key, value in chat.items() if key != "items"})
    return result


def _validated_chat_text_paths(snapshot: dict[str, Any]) -> list[str]:
    """Return fixed display-text paths unchanged by the existing privacy scan.

    The marker is rebuilt from the current snapshot so a stored or forged
    marker cannot authorize a value at another path.
    """
    paths: list[str] = []

    def append_if_safe(path: str, value: Any) -> None:
        if not isinstance(value, str):
            return
        safe_text, _report = redact_context_text(value, limit=None)
        if safe_text == value:
            paths.append(path)

    chats = snapshot.get("chats")
    if not isinstance(chats, list):
        return paths
    for chat_index, chat in enumerate(chats):
        if not isinstance(chat, dict):
            continue
        append_if_safe(f"/chats/{chat_index}/title", chat.get("title"))
        if not isinstance(chat.get("items"), list):
            continue
        for item_index, item in enumerate(chat["items"]):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"user", "streaming", "error", "compact"}:
                append_if_safe(f"/chats/{chat_index}/items/{item_index}/text", item.get("text"))
            if item_type == "compact":
                append_if_safe(f"/chats/{chat_index}/items/{item_index}/detail", item.get("detail"))
            if item_type == "agent":
                response = item.get("response")
                plan = response.get("plan") if isinstance(response, dict) else None
                if isinstance(plan, dict):
                    append_if_safe(
                        f"/chats/{chat_index}/items/{item_index}/response/plan/reply",
                        plan.get("reply"),
                    )
                    append_if_safe(
                        f"/chats/{chat_index}/items/{item_index}/response/plan/summary",
                        plan.get("summary"),
                    )
                    task = plan.get("task")
                    public_messages = plan.get("publicMessages")
                    if isinstance(public_messages, list):
                        for message_index, message in enumerate(public_messages):
                            if isinstance(message, dict):
                                append_if_safe(
                                    f"/chats/{chat_index}/items/{item_index}/response/plan/publicMessages/{message_index}/text",
                                    message.get("text"),
                                )
                    if isinstance(task, dict):
                        append_if_safe(
                            f"/chats/{chat_index}/items/{item_index}/response/plan/task/objective",
                            task.get("objective"),
                        )
                if isinstance(response, dict):
                    task = response.get("task")
                    if isinstance(task, dict):
                        append_if_safe(
                            f"/chats/{chat_index}/items/{item_index}/response/task/objective",
                            task.get("objective"),
                        )
                    timeline = response.get("timeline")
                    if isinstance(timeline, list):
                        for timeline_index, event in enumerate(timeline):
                            if not isinstance(event, dict) or event.get("kind") != "assistant":
                                continue
                            payload = event.get("payload")
                            if isinstance(payload, dict):
                                append_if_safe(
                                    f"/chats/{chat_index}/items/{item_index}/response/timeline/{timeline_index}/payload/summary",
                                    payload.get("summary"),
                                )
                timeline = item.get("timeline")
                if isinstance(timeline, list):
                    for timeline_index, event in enumerate(timeline):
                        if not isinstance(event, dict) or event.get("kind") != "assistant":
                            continue
                        payload = event.get("payload")
                        if isinstance(payload, dict):
                            append_if_safe(
                                f"/chats/{chat_index}/items/{item_index}/timeline/{timeline_index}/payload/summary",
                                payload.get("summary"),
                            )
    return paths


def _validated_compaction_recovery_paths(snapshot: dict[str, Any]) -> list[str]:
    """Return only server-validated recovery locations in the chat snapshot.

    The marker is transport metadata for the trusted desktop bridge.  It is
    rebuilt from the snapshot on every paged response so a client cannot
    preserve or forge a prior marker.  Equality is intentional: normalization
    must not have changed the recovery before the bridge is allowed to retain
    it byte-for-byte.
    """
    paths: list[str] = []
    chats = snapshot.get("chats")
    if not isinstance(chats, list):
        return paths
    for chat_index, chat in enumerate(chats):
        if not isinstance(chat, dict) or not isinstance(chat.get("items"), list):
            continue
        for item_index, item in enumerate(chat["items"]):
            if not isinstance(item, dict):
                continue
            candidates = [
                (f"/chats/{chat_index}/items/{item_index}/compactionRecovery", item.get("compactionRecovery")),
            ]
            response = item.get("response")
            if isinstance(response, dict):
                context_compaction = response.get("contextCompaction")
                if isinstance(context_compaction, dict):
                    candidates.append((
                        f"/chats/{chat_index}/items/{item_index}/response/contextCompaction/recovery",
                        context_compaction.get("recovery"),
                    ))
            for base_path, value in candidates:
                if isinstance(value, dict):
                    values = [(base_path, value)]
                elif isinstance(value, list):
                    values = [(f"{base_path}/{index}", entry) for index, entry in enumerate(value)]
                else:
                    continue
                for path, entry in values:
                    if not isinstance(entry, dict):
                        continue
                    try:
                        normalized = normalize_compaction_recovery(entry)
                    except (TypeError, ValueError):
                        continue
                    if normalized == entry:
                        paths.append(path)
    return paths


def snapshot_page(
    snapshot: dict[str, Any], *, text_offset: int = 0, snapshot_digest: str = "",
    cache_context: tuple[str, list[str] | tuple[str, ...] | None] | None = None,
) -> dict[str, Any]:
    if text_offset < 0 or text_offset % PAGE_CHARACTERS:
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page offset")
    if text_offset and not snapshot_digest:
        raise HTTPException(status_code=400, detail="Chat snapshot continuation requires snapshotDigest")
    projected = dict(snapshot)
    projected[_VALIDATED_RECOVERY_MARKER] = _validated_compaction_recovery_paths(snapshot)
    projected[_VALIDATED_TEXT_MARKER] = _validated_chat_text_paths(snapshot)
    text = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if snapshot_digest and snapshot_digest != digest:
        raise HTTPException(status_code=409, detail="Chat snapshot changed; restart from textOffset=0")
    if text_offset >= len(text):
        raise HTTPException(status_code=400, detail="Chat snapshot page offset is outside the snapshot")
    if text_offset == 0 and cache_context is not None:
        principal_hash, project_paths = cache_context
        _store_snapshot_cache(
            text,
            digest,
            principal_hash=principal_hash,
            project_paths=project_paths,
        )
    end = min(text_offset + PAGE_CHARACTERS, len(text))
    return {
        "schema": SCHEMA,
        "text": text[text_offset:end],
        "textOffset": text_offset,
        "nextOffset": end if end < len(text) else None,
        "totalCharacters": len(text),
        "hasMore": end < len(text),
        "snapshotDigest": digest,
    }


def snapshot_response(
    snapshot: dict[str, Any],
    query: Any,
    *,
    cache_context: tuple[str, list[str] | tuple[str, ...] | None] | None = None,
) -> dict[str, Any]:
    """Opt in on the existing route; preserve the default response unchanged."""
    if "snapshotPage" not in query:
        return snapshot
    if query.get("snapshotPage") != "1":
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page flag")
    try:
        offset = int(query.get("textOffset", "0"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page offset") from None
    return snapshot_page(
        snapshot,
        text_offset=offset,
        snapshot_digest=query.get("snapshotDigest", ""),
        cache_context=cache_context,
    )
