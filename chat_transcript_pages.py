"""Sidebar metadata and bounded transcript reads from packed chat records."""

import json
from pathlib import Path
from typing import Any

from chat_transcript_archive import REF_FIELD, decode_chats
from session_store_integrity import is_valid_chat_record


PAGE_ITEMS = 12
PAGE_BYTES = 2 * 1024 * 1024


def sidebar_entry(chat: dict[str, Any]) -> dict[str, Any]:
    project_path = chat.get("projectPath", "")
    project_type = chat.get("projectType")
    if project_type not in ("general", "unity"):
        project_type = "unity" if project_path.strip() else "general"
    return {
        "id": chat["id"],
        "sessionId": chat.get("sessionId", ""),
        "title": chat.get("title", ""),
        "projectPath": project_path,
        "projectType": project_type,
        **{key: chat[key] for key in ("pinned", "archived", "isTestChat") if key in chat},
        **{key: chat[key] for key in ("createdAt", "updatedAt", "lastViewedAt") if isinstance(chat.get(key), str)},
    }


def _item_bytes(item: dict[str, Any]) -> int:
    if REF_FIELD in item:
        return item[REF_FIELD]["uncompressedBytes"]
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def hydrate_chat(chat: dict[str, Any], path: Path) -> dict[str, Any]:
    [hydrated] = decode_chats([chat], path)
    if not is_valid_chat_record(hydrated):
        raise ValueError("Invalid archived chat")
    return hydrated


def read_chat_page(chat: dict[str, Any], path: Path, end: int) -> tuple[dict[str, Any], int]:
    items = chat["items"]
    start = end
    remaining_bytes = PAGE_BYTES
    tail = []
    while start > 0:
        item = items[start - 1]
        # The conversation UI hides retired queue entries. Keep their receipts
        # in the complete record without spending a history slot on them.
        if item["type"] == "user" and item.get("queueStatus") == "cancelled" and (
            item.get("composerQueued") or item.get("queuedFrom")
        ):
            start -= 1
            continue
        if len(tail) >= PAGE_ITEMS:
            break
        size = _item_bytes(item)
        # A single large item stays whole; the existing IPC transport pages it.
        if tail and size > remaining_bytes:
            break
        start -= 1
        remaining_bytes -= size
        tail.append(item)

    tail.reverse()
    page = {key: value for key, value in chat.items() if key not in {"items", "attachmentPayloads", "compactedAttachmentRefs"}}
    page["items"] = tail
    payload_hashes = {
        attachment.get("payloadHash")
        for item in tail if item["type"] == "user"
        for attachment in item.get("attachments", ())
        if attachment.get("payloadHash")
    }
    if payload_hashes:
        page["attachmentPayloads"] = {
            key: value for key, value in chat.get("attachmentPayloads", {}).items()
            if key in payload_hashes
        }
        page["compactedAttachmentRefs"] = [
            reference for reference in chat.get("compactedAttachmentRefs", ())
            if reference.get("payloadHash") in payload_hashes
        ]
    return hydrate_chat(page, path), start
