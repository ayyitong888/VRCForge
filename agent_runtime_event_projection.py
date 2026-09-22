"""Bounded desktop projection for asynchronous runtime task continuations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


RUNTIME_TURN_EVENT_SCHEMA = "vrcforge.runtime_turn_event.v1"
RUNTIME_CONTINUATION_SOURCES = frozenset(
    {"approval_finished", "shell_process_finished", "sub_agent_finished", "question_answered", "foreground_completed"}
)


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
                    for key in ("label", "summary", "status", "tool", "phase", "actionId", "subagentStatus")
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
