"""Bounded desktop projection for asynchronous runtime task continuations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


RUNTIME_TURN_EVENT_SCHEMA = "vrcforge.runtime_turn_event.v1"
RUNTIME_CONTINUATION_SOURCES = frozenset(
    {"shell_process_finished", "sub_agent_finished"}
)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


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
    return {
        "schema": RUNTIME_TURN_EVENT_SCHEMA,
        "continuationSource": source,
        "sessionId": session_id,
        "turnId": turn_id,
        "clientTurnId": _text(payload.get("clientTurnId"), 240),
        "plan": {
            "summary": _text(plan.get("summary"), 1200),
            "reply": _text(plan.get("reply"), 6000),
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
