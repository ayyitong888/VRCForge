"""Bounded, digest-bound transport of the existing complete chat snapshot.

No retained cursor state: each page belongs to the current authenticated request.
Four million code points need at most 24 MiB when JSON-escaped, leaving ample
space for the fixed-size envelope under the native transport's 32 MiB cap.
"""
import hashlib
import json
from typing import Any

from fastapi import HTTPException


PAGE_CHARACTERS = 4 * 1024 * 1024
SCHEMA = "vrcforge.chat_snapshot_page.v1"


def snapshot_page(
    snapshot: dict[str, Any], *, text_offset: int = 0, snapshot_digest: str = ""
) -> dict[str, Any]:
    if text_offset < 0 or text_offset % PAGE_CHARACTERS:
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page offset")
    if text_offset and not snapshot_digest:
        raise HTTPException(status_code=400, detail="Chat snapshot continuation requires snapshotDigest")
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if snapshot_digest and snapshot_digest != digest:
        raise HTTPException(status_code=409, detail="Chat snapshot changed; restart from textOffset=0")
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
        "snapshotDigest": digest,
    }


def snapshot_response(snapshot: dict[str, Any], query: Any) -> dict[str, Any]:
    """Opt in on the existing route; preserve the default response unchanged."""
    if "snapshotPage" not in query:
        return snapshot
    if query.get("snapshotPage") != "1":
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page flag")
    try:
        offset = int(query.get("textOffset", "0"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid chat snapshot page offset") from None
    return snapshot_page(snapshot, text_offset=offset, snapshot_digest=query.get("snapshotDigest", ""))
