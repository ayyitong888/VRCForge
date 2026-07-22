"""Narrow runtime source projection for Memory Review.

This module only recognizes explicit, durable semantic records. It never
walks arbitrary provider, tool, attachment, or diagnostic payloads.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from memory_consolidation_sources import project_scope_key


_SIGNAL_KINDS = frozenset({"preference", "fact", "correction", "decision"})
_EXPLICIT_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "correction",
        re.compile(
            r"^\s*(?:correction\s*:|please\s+correct\s+this\s*:|更正(?:一下)?[：:]?|纠正(?:一下)?[：:]?|訂正[：:]?|修正[：:]?)",
            re.IGNORECASE,
        ),
    ),
    (
        "decision",
        re.compile(
            r"^\s*(?:decision\s*:|we(?:'|’)ve\s+decided\s*:|决定(?:是|为)?[：:]?|決定(?:是|為)?[：:]?|方針[：:]?)",
            re.IGNORECASE,
        ),
    ),
    (
        "preference",
        re.compile(
            r"^\s*(?:please\s+remember\b|remember\s+(?:that\b|this\b)|i\s+prefer\b|my\s+preference\s+is\b|"
            r"请记住|請記住|记住(?:这一点|这点)?[：:]?|記住(?:這一點|這點)?[：:]?|覚えて(?:おいて)?|好みは)",
            re.IGNORECASE,
        ),
    ),
    (
        "fact",
        re.compile(
            r"^\s*(?:fact\s*:|a\s+fact\s+to\s+remember\s*:|事实(?:是)?[：:]?|事實(?:是)?[：:]?|事実[：:]?)",
            re.IGNORECASE,
        ),
    ),
)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *values: Any) -> str:
    return f"{prefix}_{_digest(list(values))[:40]}"


def explicit_memory_signal(item: Mapping[str, Any]) -> str:
    """Return an allowlisted signal kind or an empty string.

    Structured markers must opt in with ``eligible=true``. Natural-language
    fallback is deliberately anchored to explicit remember/correction/
    decision/fact phrases so ordinary chat cannot silently become Memory.
    """

    marker = item.get("memorySignal")
    if isinstance(marker, Mapping) and marker.get("eligible") is True:
        kind = str(marker.get("kind") or "").strip().casefold().replace("-", "_")
        if kind in _SIGNAL_KINDS:
            return kind
    text = str(item.get("text") or "")
    if not text.strip():
        return ""
    for kind, pattern in _EXPLICIT_SIGNAL_PATTERNS:
        if pattern.search(text):
            return kind
    return ""


def _has_durable_terminal_reply(items: Sequence[Any], item_index: int) -> bool:
    unsafe_statuses = {"blocked", "cancelled", "denied", "failed", "incomplete", "pending", "timed_out"}
    for later in items[item_index + 1 :]:
        if not isinstance(later, Mapping):
            continue
        item_type = str(later.get("type") or "").strip().casefold()
        if item_type == "user":
            return False
        if item_type != "agent":
            continue
        status = str(later.get("status") or "completed").strip().casefold().replace("-", "_")
        response = later.get("response")
        durable_response = (
            isinstance(response, Mapping)
            and response.get("ok") is not False
            and isinstance(response.get("plan"), Mapping)
        )
        legacy_text = bool(str(later.get("text") or "").strip())
        if status in unsafe_statuses or not (durable_response or legacy_text):
            return False
        return True
    return False


def collect_user_chat_records(
    chats: Iterable[Mapping[str, Any]],
    *,
    scope: str,
    project_root: str = "",
) -> list[dict[str, Any]]:
    """Project explicit user-authored signals from one authoritative chat file."""

    normalized_scope = str(scope or "").strip().casefold()
    if normalized_scope not in {"user", "project"}:
        raise ValueError("scope must be user or project")
    if normalized_scope == "project":
        # Resolve before inspecting any chat content. The caller loads the
        # transcript from this exact authoritative project root.
        project_scope_key(project_root)
    elif str(project_root or "").strip():
        raise ValueError("user scope cannot include project_root")

    records: list[dict[str, Any]] = []
    for chat in chats:
        if not isinstance(chat, Mapping):
            continue
        embedded_project = str(chat.get("projectPath") or chat.get("projectRoot") or "").strip()
        if normalized_scope == "user" and embedded_project:
            continue
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        items = chat.get("items")
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping) or str(item.get("type") or "").strip().casefold() != "user":
                continue
            signal_kind = explicit_memory_signal(item)
            text = str(item.get("text") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if not signal_kind or not text or not item_id or not _has_durable_terminal_reply(items, item_index):
                continue
            source_id = _stable_id("chat", chat_id, item_id)
            revision = _digest(
                {
                    "chatId": chat_id,
                    "itemId": item_id,
                    "itemRevision": item.get("revision"),
                    "updatedAt": item.get("updatedAt"),
                    "text": text,
                    "signalKind": signal_kind,
                }
            )
            record: dict[str, Any] = {
                "sourceType": "user_chat",
                "sourceId": source_id,
                "sourceRevision": revision,
                "role": "user",
                "status": "completed",
                "signalKind": signal_kind,
                "text": text,
                "observedAt": str(item.get("updatedAt") or item.get("createdAt") or "")[:80],
                "originGroup": _stable_id("chat_group", chat_id),
            }
            if normalized_scope == "user":
                record["memoryScope"] = "user"
            else:
                record["projectRoot"] = project_root
            records.append(record)
    return records


def collect_adopted_task_records(
    tasks: Iterable[Mapping[str, Any]],
    *,
    project_root: str,
) -> list[dict[str, Any]]:
    """Project only adopted, completed task summaries for one exact project."""

    expected_scope = project_scope_key(project_root)
    records: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        if str(task.get("status") or "").strip().casefold() != "completed":
            continue
        if str(task.get("mergeDecision") or "").strip().casefold() != "adopted":
            continue
        if not str(task.get("parentChatId") or "").strip():
            continue
        task_project = str(task.get("projectPath") or task.get("projectRoot") or "").strip()
        try:
            if project_scope_key(task_project) != expected_scope:
                continue
        except (OSError, ValueError):
            continue
        task_id = str(task.get("id") or "").strip()
        summary = str(task.get("summary") or "").strip()
        if not task_id or not summary:
            continue
        records.append(
            {
                "sourceType": "adopted_task",
                "sourceId": _stable_id("task", task_id),
                "sourceRevision": _digest(
                    {
                        "taskId": task_id,
                        "revision": task.get("revision"),
                        "summary": summary,
                        "mergedAt": task.get("mergedAt"),
                    }
                ),
                "status": "completed",
                "mergeDecision": "adopted",
                "parentChatId": "bound",
                "projectRoot": project_root,
                "summary": summary,
                "completedAt": str(task.get("stoppedAt") or task.get("updatedAt") or "")[:80],
            }
        )
    return records


def collect_validated_project_records(
    audit_events: Iterable[Mapping[str, Any]],
    *,
    project_root: str,
) -> list[dict[str, Any]]:
    """Project explicitly declared applied-and-validated semantic evidence.

    A generic applied event is intentionally insufficient. Producers must emit
    the narrow ``vrcforge.memory_evidence.v1`` envelope with a stable summary
    digest; no raw tool or result payload is traversed.
    """

    expected_scope = project_scope_key(project_root)
    records: list[dict[str, Any]] = []
    for event in audit_events:
        if not isinstance(event, Mapping):
            continue
        evidence = event.get("memoryEvidence")
        if not isinstance(evidence, Mapping):
            continue
        if evidence.get("schema") != "vrcforge.memory_evidence.v1":
            continue
        if evidence.get("applied") is not True or evidence.get("validated") is not True:
            continue
        evidence_project = str(evidence.get("projectRoot") or "").strip()
        try:
            if project_scope_key(evidence_project) != expected_scope:
                continue
        except (OSError, ValueError):
            continue
        summary = str(evidence.get("summary") or "").strip()
        summary_digest = str(evidence.get("summaryDigest") or "").strip().casefold()
        expected_digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        if (
            not summary
            or not re.fullmatch(r"[0-9a-f]{64}", summary_digest)
            or summary_digest != expected_digest
        ):
            continue
        source_seed = str(evidence.get("sourceId") or event.get("id") or summary_digest).strip()
        records.append(
            {
                "sourceType": "validated_project_result",
                "sourceId": _stable_id("evidence", source_seed),
                "sourceRevision": _digest(
                    {
                        "source": source_seed,
                        "revision": evidence.get("revision"),
                        "summaryDigest": summary_digest,
                    }
                ),
                "sourceDigest": summary_digest,
                "status": "completed",
                "applied": True,
                "validated": True,
                "projectRoot": project_root,
                "summary": summary,
                "completedAt": str(evidence.get("completedAt") or event.get("timestamp") or "")[:80],
            }
        )
    return records


__all__ = [
    "collect_adopted_task_records",
    "collect_user_chat_records",
    "collect_validated_project_records",
    "explicit_memory_signal",
]
