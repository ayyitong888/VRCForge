"""Read-only context usage projection shared by channel status views."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


_USAGE_FIELDS = (
    "schema", "source", "exact", "provider", "providerLabel", "model",
    "inputTokens", "outputTokens", "totalTokens", "lastInputTokens",
    "lastOutputTokens", "lastTotalTokens", "peakInputTokens", "peakTotalTokens",
    "cacheReadTokens", "cacheUsageComplete", "cacheUsageRequestCount",
    "requestCount", "sentHistoryEntryCount", "sentHistoryCharacterCount",
    "promptCharacterCount", "lastPromptCharacterCount", "unavailableReason",
    "scope", "taskTotalAvailable",
)


def project_context_usage(chat: Mapping[str, Any], run: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Return exact usage plus a bounded occupancy field, or explicit unavailable state.

    Context occupancy is the latest request's peak input window. Cumulative token
    counters remain visible as accounting fields and are never used as occupancy.
    """
    items = chat.get("items") if isinstance(chat.get("items"), list) else []
    compact_index = max((index for index, item in enumerate(items)
                         if isinstance(item, Mapping) and item.get("type") == "compact"), default=-1)
    candidate: Mapping[str, Any] | None = None
    candidate_index = -1
    candidate_source = "run"
    if isinstance(run, Mapping) and isinstance(run.get("contextUsage"), Mapping):
        candidate = run["contextUsage"]
    else:
        candidate_source = "chat"
        for index in range(len(items) - 1, -1, -1):
            item = items[index]
            response = item.get("response") if isinstance(item, Mapping) else None
            usage = response.get("contextUsage") if isinstance(response, Mapping) else None
            if not isinstance(usage, Mapping):
                continue
            if compact_index >= 0 and (index <= compact_index or not _response_matches_chat(response, chat)):
                continue
            candidate, candidate_index = usage, index
            break
        if candidate is None:
            for item in reversed(items):
                response = item.get("response") if isinstance(item, Mapping) else None
                usage = response.get("contextUsage") if isinstance(response, Mapping) else None
                if isinstance(usage, Mapping):
                    candidate = usage
                    break
    if candidate is None:
        return None

    projected = {key: candidate[key] for key in _USAGE_FIELDS if key in candidate}
    expected = _session_config(chat)
    actual_provider = str(candidate.get("provider") or "").strip()
    actual_model = str(candidate.get("model") or "").strip()
    expected_provider = str(expected.get("provider") or "").strip()
    expected_model = str(expected.get("model") or "").strip()
    reason = ""
    if ((expected_provider and actual_provider and expected_provider != actual_provider)
            or (expected_model and actual_model and expected_model != actual_model)):
        reason = "model_changed"
    elif candidate_source == "chat" and (candidate_index <= compact_index or candidate_index < 0):
        reason = "compaction_applied"
    elif candidate.get("exact") is not True:
        reason = str(candidate.get("unavailableReason") or "provider_usage_missing")
    used, source = _occupancy(candidate)
    if not reason and used is None:
        reason = "provider_usage_missing"
    if reason:
        projected.update({"status": "unavailable", "unavailableReason": reason})
        return projected

    projected.update({"status": "available", "usedTokens": used, "occupancySource": source})
    limit = _context_limit(candidate, run)
    if limit is not None:
        projected["limitTokens"] = limit
    return projected


def _context_limit(usage: Mapping[str, Any], run: Mapping[str, Any] | None) -> int | None:
    guard = usage.get("nativeContextGuard")
    if isinstance(guard, Mapping):
        limit = _positive_int(guard.get("contextLimit"))
        if limit is not None:
            return limit
    compaction = run.get("contextCompaction") if isinstance(run, Mapping) else None
    if isinstance(compaction, Mapping):
        limit = _positive_int(compaction.get("contextLimit"))
        if limit is not None:
            return limit
    return None


def _session_config(chat: Mapping[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for key in ("provider", "model", "contextLimit"):
        if chat.get(key) not in (None, ""):
            config[key] = chat[key]
    settings = chat.get("settings")
    if isinstance(settings, Mapping):
        for key in ("provider", "model", "contextLimit"):
            if settings.get(key) not in (None, ""):
                config[key] = settings[key]
    return config


def _response_matches_chat(response: Mapping[str, Any], chat: Mapping[str, Any]) -> bool:
    chat_session = str(chat.get("sessionId") or chat.get("session_id") or "").strip()
    response_session = str(response.get("sessionId") or response.get("session_id") or "").strip()
    return bool(chat_session and response_session and chat_session == response_session)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _occupancy(usage: Mapping[str, Any]) -> tuple[int | None, str]:
    for key in ("peakInputTokens", "lastInputTokens", "inputTokens"):
        if key == "inputTokens" and _positive_int(usage.get("requestCount")) not in (None, 0, 1):
            continue
        value = _positive_int(usage.get(key))
        if value is not None:
            return value, key
    return None, ""
