"""Bounded desktop projection for asynchronous runtime task continuations."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


RUNTIME_TURN_EVENT_SCHEMA = "vrcforge.runtime_turn_event.v1"
RUNTIME_CONTINUATION_SOURCES = frozenset(
    {"approval_finished", "shell_process_finished", "sub_agent_finished", "question_answered", "foreground_completed", "foreground_interrupted"}
)

_CONTEXT_USAGE_FIELDS = (
    "schema", "source", "scope", "taskTotalAvailable", "exact",
    "provider", "providerLabel", "model", "inputTokens", "outputTokens",
    "totalTokens", "cumulativeInputTokens", "cumulativeOutputTokens",
    "cumulativeTotalTokens", "lastInputTokens", "lastOutputTokens",
    "lastTotalTokens", "peakInputTokens", "peakTotalTokens", "cacheReadTokens",
    "cacheWriteTokens", "cacheUsageComplete", "cacheUsageRequestCount",
    "cacheWriteUsageComplete", "cacheWriteUsageRequestCount",
    "requestCount", "priorRequestCount", "partial", "sentHistoryEntryCount",
    "sentHistoryCharacterCount", "promptCharacterCount", "lastPromptCharacterCount",
    "unavailableReason",
)
_CONTEXT_USAGE_INTEGER_FIELDS = frozenset(
    {
        "inputTokens", "outputTokens", "totalTokens", "cumulativeInputTokens",
        "cumulativeOutputTokens", "cumulativeTotalTokens", "lastInputTokens",
        "lastOutputTokens", "lastTotalTokens", "peakInputTokens", "peakTotalTokens",
        "cacheReadTokens", "cacheWriteTokens", "cacheUsageRequestCount",
        "cacheWriteUsageRequestCount",
        "requestCount", "priorRequestCount", "sentHistoryEntryCount", "sentHistoryCharacterCount",
        "promptCharacterCount", "lastPromptCharacterCount",
    }
)
_CONTEXT_USAGE_BOOLEAN_FIELDS = frozenset({"taskTotalAvailable", "exact", "cacheUsageComplete", "cacheWriteUsageComplete", "partial"})
_CONTEXT_USAGE_TEXT_FIELDS = frozenset({"schema", "source", "scope", "provider", "providerLabel", "model", "unavailableReason"})
_CONTEXT_USAGE_COVERAGE_FIELDS = ("requestCount", "reportedRequestCount", "complete")
_PLANNER_FAILURE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_PLANNER_FAILURE_RETRY_DECISIONS = frozenset({"NO_MORE_RETRY", "CANCELLED"})


def _usage_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, min(value, 10**15))
    if isinstance(value, float) and value.is_integer():
        return max(0, min(int(value), 10**15))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, min(int(value.strip()), 10**15))
    return None


def _project_coverage_bucket(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    projected: dict[str, Any] = {}
    for key in _CONTEXT_USAGE_COVERAGE_FIELDS:
        raw = value.get(key)
        if key == "complete":
            if isinstance(raw, bool):
                projected[key] = raw
        else:
            number = _usage_integer(raw)
            if number is not None:
                projected[key] = number
    return projected or None


def _project_context_usage(value: Any) -> dict[str, Any] | None:
    """Keep the bounded usage counters needed by the continuation UI."""

    if not isinstance(value, Mapping):
        return None
    projected: dict[str, Any] = {}
    for key in _CONTEXT_USAGE_FIELDS:
        if key not in value:
            continue
        raw = value[key]
        if key in _CONTEXT_USAGE_INTEGER_FIELDS:
            bounded = _usage_integer(raw)
            if bounded is not None:
                projected[key] = bounded
        elif key in _CONTEXT_USAGE_BOOLEAN_FIELDS:
            if isinstance(raw, bool):
                projected[key] = raw
        elif key in _CONTEXT_USAGE_TEXT_FIELDS and isinstance(raw, str):
            projected[key] = raw.strip()[:160]
    for key in ("coverage", "cacheCoverage"):
        raw = value.get(key)
        if not isinstance(raw, Mapping):
            continue
        if key == "coverage":
            coverage = {
                str(name)[:80]: bounded
                for name, bucket in list(raw.items())[:16]
                if (bounded := _project_coverage_bucket(bucket)) is not None
            }
            if coverage:
                projected[key] = coverage
        else:
            cache_coverage: dict[str, Any] = {}
            root_count = _usage_integer(raw.get("requestCount"))
            if root_count is not None:
                cache_coverage["requestCount"] = root_count
            for side in ("read", "write"):
                bounded = _project_coverage_bucket(raw.get(side))
                if bounded is not None:
                    cache_coverage[side] = bounded
            if cache_coverage:
                projected[key] = cache_coverage
    return projected or None


def _planner_failure_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _PLANNER_FAILURE_IDENTIFIER.fullmatch(candidate) else None


def _planner_failure_attempts(value: Any) -> int | None:
    if type(value) is not int or value < 0 or value > 100:
        return None
    return value


def _project_planner_failure(value: Any) -> dict[str, Any] | None:
    """Keep only bounded, non-secret terminal planner failure facts."""

    if not isinstance(value, Mapping):
        return None
    projected: dict[str, Any] = {}
    for key in ("code", "phase", "transportPhase"):
        identifier = _planner_failure_identifier(value.get(key))
        if identifier is not None:
            projected[key] = identifier
    if isinstance(value.get("retryable"), bool):
        projected["retryable"] = value["retryable"]
    attempts = _planner_failure_attempts(value.get("retryAttempts"))
    if attempts is not None:
        projected["retryAttempts"] = attempts
    decision = value.get("retryDecision")
    if isinstance(decision, str) and decision in _PLANNER_FAILURE_RETRY_DECISIONS:
        projected["retryDecision"] = decision

    provider_error = value.get("providerError")
    if isinstance(provider_error, Mapping):
        projected_provider_error: dict[str, Any] = {}
        status_code = provider_error.get("statusCode")
        if type(status_code) is int and 100 <= status_code <= 599:
            projected_provider_error["statusCode"] = status_code
        for key in ("type", "code"):
            identifier = _planner_failure_identifier(provider_error.get(key))
            if identifier is not None:
                projected_provider_error[key] = identifier
        if isinstance(provider_error.get("streamStarted"), bool):
            projected_provider_error["streamStarted"] = provider_error["streamStarted"]
        if projected_provider_error:
            projected["providerError"] = projected_provider_error
    return projected or None


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _sequence(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def project_runtime_turn_event(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Expose only the chat-owned fields needed to render one terminal reply."""

    if not isinstance(payload, Mapping):
        return None
    source = _text(payload.get("continuationSource"), 80)
    if source not in RUNTIME_CONTINUATION_SOURCES:
        return None
    session_id = _text(payload.get("sessionId") or payload.get("session_id"), 180)
    turn_id = _text(payload.get("turnId") or payload.get("turn_id"), 180)
    if not session_id or not turn_id:
        return None
    plan = payload.get("plan")
    plan = plan if isinstance(plan, Mapping) else {}
    completion = plan.get("taskCompletion")
    completion = completion if isinstance(completion, Mapping) else {}
    evidence = completion.get("evidenceActionIds")
    evidence = evidence if isinstance(evidence, list) else []
    reply_limit = 32_000 if source == "foreground_completed" else 6_000
    result = {
        "schema": RUNTIME_TURN_EVENT_SCHEMA,
        "continuationSource": source,
        "sessionId": session_id,
        "turnId": turn_id,
        "clientTurnId": _text(payload.get("clientTurnId"), 240),
        "plan": {
            "summary": _text(plan.get("summary"), 1200),
            "reply": _text(plan.get("reply"), reply_limit),
            "planner": _text(plan.get("planner"), 80),
            "nextStep": _text(plan.get("nextStep"), 80),
            "taskCompletion": {
                "status": _text(completion.get("status"), 40),
                "taskId": _text(completion.get("taskId"), 80),
                "evidenceActionIds": [
                    bounded
                    for item in evidence[:3]
                    if (bounded := _text(item, 80))
                ],
            },
        },
    }
    # This is the owning ledger's terminal time, never a message timestamp or
    # projection/reconnect time. Waiting boundaries do not supply this field.
    for field in ("completedAt", "startedAt"):
        timestamp = payload.get(field)
        if isinstance(timestamp, str) and timestamp.strip():
            result[field] = timestamp.strip()[:80]
    for key in ("provider", "providerLabel", "model", "projectRoot"):
        bounded = _text(payload.get(key), 1000 if key == "projectRoot" else 160)
        if bounded:
            result[key] = bounded
    planner_failure = _project_planner_failure(plan.get("plannerFailure"))
    consumed = payload.get("consumedSteerInputIds")
    if isinstance(consumed, list):
        confirmed_ids = [item for item in consumed[:256] if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", item)]
        if confirmed_ids:
            result["consumedSteerInputIds"] = confirmed_ids
    public_messages = plan.get("publicMessages")
    if isinstance(public_messages, list):
        result["plan"]["publicMessages"] = [
            {"id": _text(item.get("id"), 180), "text": _text(item.get("text"), 32_000),
             **({"phase": item["phase"]} if item.get("phase") in {"commentary", "final_answer"} else {})}
            for item in public_messages[:256]
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
    if plan.get("reason") == "native_final_answer_missing":
        result["plan"]["reason"] = "native_final_answer_missing"
    if planner_failure is not None:
        result["plan"]["plannerFailure"] = planner_failure
    context_usage = _project_context_usage(payload.get("contextUsage"))
    if context_usage is not None:
        result["contextUsage"] = context_usage
    timeline = payload.get("timeline")
    if isinstance(timeline, list):
        projected_timeline = []
        for index, raw in enumerate(timeline[:256]):
            if not isinstance(raw, Mapping):
                continue
            item = {
                "id": _text(raw.get("id"), 180) or f"runtime-event-{index}",
                "sequence": _sequence(raw.get("sequence"), index),
                "timestamp": _text(raw.get("timestamp"), 80),
                "kind": _text(raw.get("kind"), 40),
            }
            raw_payload = raw.get("payload")
            if isinstance(raw_payload, Mapping):
                item["payload"] = {
                    key: _text(raw_payload.get(key), 32_000 if key == "summary" and item["kind"] == "assistant" else 1000)
                    for key in ("label", "summary", "status", "tool", "phase", "actionId", "messageId", "subagentStatus")
                    if raw_payload.get(key) is not None
                }
            projected_timeline.append(item)
        if projected_timeline:
            result["timeline"] = projected_timeline
    projected_completion = result["plan"]["taskCompletion"]
    for key, limit in (("actionId", 80), ("kind", 32), ("tool", 160)):
        bounded = _text(completion.get(key), limit)
        if bounded:
            projected_completion[key] = bounded
    receipt = payload.get("harnessJourneyReceipt")
    if isinstance(receipt, Mapping):
        # Receipts contain only the already-bounded safe journey projection and
        # a process-local authentication envelope; raw results never enter it.
        result["harnessJourneyReceipt"] = dict(receipt)
    return result
