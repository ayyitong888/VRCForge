"""Bounded, untrusted model evidence from structured tool results.

This projection never changes execution outcomes or grants authority. The caller
supplies the same redactor used by its existing observation boundary. No files,
processes, transport, or retained-output capabilities are created here.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any


_SECRET_KEYS = frozenset({
    "apikey", "key", "token", "accesstoken", "refreshtoken", "authtoken", "apptoken",
    "authorization", "password", "passwd", "secret", "clientsecret", "privatekey",
    "cookie", "cookies", "headers", "approvaltoken", "artifacttoken", "artifactsig",
    "artifactsignature", "sessiontoken", "executiontargethandle",
})
_OPAQUE_KEYS = frozenset({
    "stdout", "stderr", "output", "outputs", "content", "body", "traceback", "stack",
    "arguments", "params", "attachments", "images", "pixels", "imagedata", "imageurl",
    "screenshot", "screenshots", "base64", "binary", "blob", "bytes",
})
_CONTROL_KEYS = frozenset({
    "ok", "success", "status", "code", "errorcode", "committed", "commitstate",
    "commitstateknown", "completionknown", "mutationstarted", "mutationapplied",
    "persistencestate", "readbackstate", "verification", "retryable", "safetoretry",
    "paging", "pagination", "nextrequest", "nextkeyrequest", "nextoffset", "hasmore",
    "truncated", "readhints", "fingerprint", "expectedsnapshotdigest", "snapshotdigest",
    "classification", "recognitioncoverage", "blockingreasons", "rejectionreasons",
})
_ABSENT = object()


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _private_or_opaque(name: object, value: object) -> bool:
    normalized = _key(name)
    return (
        normalized in _SECRET_KEYS or normalized in _OPAQUE_KEYS
        or normalized.startswith(("private", "raw", "credential"))
        or normalized.endswith(("dump", "traceback"))
        # Structured protocol envelopes may contain domain evidence. Their raw
        # serialized/wire text is not a second way around dedicated readers.
        or (normalized in {"payload", "result", "data"} and not isinstance(value, (Mapping, list)))
    )


def _identity_key(name: object) -> bool:
    normalized = _key(name)
    return normalized.endswith((
        "id", "ids", "guid", "guids", "path", "paths", "name", "names",
        "digest", "digests", "cursor", "cursors", "fingerprint", "fingerprints",
        "handle", "handles", "selector", "selectors",
    )) or normalized in {"defaultstate", "sourcestate", "destinationstate"}


def project_structured_tool_evidence(
    result: object,
    *,
    sanitize_text: Callable[[object, int], str],
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Keep useful structured data within a complete serialized JSON budget.

    Omission counters count fields/subtrees or list items at the point omitted;
    they do not claim an exhaustive count of all descendants. Source pagination
    and projection truncation are separate. No cursor is invented.
    """
    if not isinstance(result, (Mapping, list)):
        return {}
    if type(max_chars) is not int or not 1500 <= max_chars <= 12000:
        raise ValueError("max_chars must be an integer between 1500 and 12000")
    stats = {"omittedFields": 0, "omittedItems": 0, "omittedChars": 0, "redactedFields": 0}
    source_truncated = False

    def priority(item: tuple[object, object]) -> tuple[int, str]:
        name, value = item
        normalized = _key(name)
        if normalized in _CONTROL_KEYS or "ambiguous" in normalized:
            return (0, str(name))
        if _identity_key(name):
            return (1, str(name))
        if not isinstance(value, (Mapping, list)):
            return (2, str(name))
        return (3, str(name))

    def visit(value: object, budget: int, depth: int, *, exact_strings: bool = False) -> object:
        nonlocal source_truncated
        if budget < 8:
            return _ABSENT
        if value is None or isinstance(value, bool) or type(value) is int:
            return value if _size(value) <= budget else _ABSENT
        if isinstance(value, float):
            return value if math.isfinite(value) and _size(value) <= budget else _ABSENT
        if isinstance(value, str):
            if exact_strings:
                # A shortened/redacted target is not a usable target. Omit it
                # wholly, including list items and continuation selectors.
                if len(value) > 12000 or _size(value) > budget:
                    stats["omittedChars"] += len(value)
                    return _ABSENT
                safe = sanitize_text(value, max(1, len(value)))
                if safe != value:
                    stats["omittedChars"] += len(value)
                    return _ABSENT
                return value
            # Never send an unlimited string to the sanitizer or model.
            text = sanitize_text(value[:12000], 12000)
            kept = text[:500]
            while kept and _size(kept) > budget:
                kept = kept[:max(0, len(kept) - max(1, (_size(kept) - budget + 1) // 2))]
            stats["omittedChars"] += max(0, len(text) - len(kept)) + max(0, len(value) - 12000)
            return kept
        if not isinstance(value, (Mapping, list)) or depth >= 8:
            return _ABSENT
        if isinstance(value, list):
            selected: list[object] = []
            for index, item in enumerate(value[:6]):
                available = budget - _size(selected) - 2
                projected = visit(item, min(1800, available), depth + 1, exact_strings=exact_strings)
                if projected is _ABSENT or _size([*selected, projected]) > budget:
                    stats["omittedItems"] += len(value) - index
                    break
                selected.append(projected)
            else:
                stats["omittedItems"] += max(0, len(value) - 6)
            return selected
        selected_dict: dict[str, object] = {}
        entries = sorted(value.items(), key=priority)
        for index, (name, item) in enumerate(entries):
            normalized = _key(name)
            if _private_or_opaque(name, item):
                stats["redactedFields"] += 1
                continue
            if (normalized.endswith("truncated") or normalized == "hasmore") and item is True:
                source_truncated = True
            if len(selected_dict) >= 24:
                stats["omittedFields"] += 1
                continue
            # Field names are also external data and must remain bounded/safe.
            safe_name = sanitize_text(str(name), 100)
            if safe_name != str(name):
                stats["omittedFields"] += 1
                continue
            available = budget - _size(selected_dict) - _size(safe_name) - 3
            if isinstance(item, (Mapping, list)):
                # A large first collection must not hide all later domains
                # (for example clips swallowing layers, parameters and paging).
                siblings = sum(
                    isinstance(other, (Mapping, list)) and not _private_or_opaque(other_name, other)
                    for other_name, other in entries[index + 1:24]
                )
                available //= siblings + 1
            whole_request = normalized in {"nextrequest", "nextkeyrequest"}
            previous_omissions = tuple(stats.values())
            projected = visit(
                item, available, depth + 1,
                exact_strings=exact_strings or _identity_key(name) or whole_request,
            )
            # A partially projected request is not a safe continuation request.
            if whole_request and tuple(stats.values()) != previous_omissions:
                projected = _ABSENT
            if projected is _ABSENT or _size({**selected_dict, safe_name: projected}) > budget:
                stats["omittedFields"] += 1
                continue
            selected_dict[safe_name] = projected
        return selected_dict

    data = visit(result, max_chars - 800, 0)
    truncated = any(stats[key] for key in ("omittedFields", "omittedItems", "omittedChars"))
    evidence = {
        "authority": "untrusted_tool_output",
        "data": {} if data is _ABSENT else data,
        "truncated": truncated,
        "sourceTruncated": source_truncated,
        **stats,
        "continuation": (
            "Use the source tool's returned paging/nextRequest or narrow selectors to inspect omitted data. "
            "Do not assume omitted fields are absent, invent a cursor, or replay a mutation to recover output."
            if truncated or source_truncated else ""
        ),
    }
    # The fixed metadata allowance exceeds the longest metadata envelope above.
    assert _size(evidence) <= max_chars
    return evidence
