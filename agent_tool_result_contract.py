"""Small, transport-neutral result semantics for agent tool calls.

This module deliberately does not own execution, persistence, evidence storage,
or workflow state.  It only prevents an outer caller from turning a nested tool
failure or an unverified write into a successful completion claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


TOOL_RESULT_SCHEMA = "vrcforge.tool_result.v1"

_FAILED_STATUSES = frozenset(
    {"error", "failed", "failure", "timed_out", "timeout", "unavailable"}
)
_NEEDS_ACTION_STATUSES = frozenset(
    {
        "approval_required",
        "blocked",
        "needs_input",
        "needs_user_action",
        "pending_approval",
        "waiting_for_answer",
        "waiting_for_approval",
    }
)
_TOP_LEVEL_PENDING_STATUSES = frozenset(
    {"pending", "approval_pending", "queued_for_approval"}
)
_KNOWN_NESTED_KEYS = (
    "structuredContent",
    "result",
    "entrypoint",
    "toolResult",
)


def _status(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _views(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    pending: list[tuple[Mapping[str, Any], int]] = [(value, 0)]
    result: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    while pending and len(result) < 12:
        current, depth = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(current)
        if depth >= 3:
            continue
        for key in _KNOWN_NESTED_KEYS:
            nested = current.get(key)
            if isinstance(nested, Mapping):
                pending.append((nested, depth + 1))
    return result


def _first_text(views: list[Mapping[str, Any]], keys: tuple[str, ...]) -> str:
    for view in views:
        for key in keys:
            value = view.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:600]
    return ""


def _verification(
    views: list[Mapping[str, Any]],
    *,
    write: bool,
) -> tuple[dict[str, Any], bool]:
    checks: list[dict[str, str]] = []
    needs_user_action = False
    explicit_state = ""
    visual_required = any(
        view.get("visualRequired") is True
        or (
            isinstance(view.get("verification"), Mapping)
            and "visual"
            in {
                str(item).strip().casefold()
                for item in (
                    view["verification"].get("required")
                    if isinstance(view["verification"].get("required"), (list, tuple, set))
                    else [view["verification"].get("required")]
                )
                if item is not None
            }
        )
        for view in views
    )
    for view in views:
        if view.get("readbackVerified") is True:
            check = {"kind": "readback", "state": "passed"}
            if check not in checks:
                checks.append(check)
        elif view.get("readbackVerified") is False:
            check = {"kind": "readback", "state": "failed"}
            if check not in checks:
                checks.append(check)
            needs_user_action = True

        verification = view.get("verification")
        if isinstance(verification, Mapping):
            state = _status(verification.get("state"))
            if state:
                explicit_state = state
            if state in {"failed", "needs_user_action", "pending"}:
                needs_user_action = True

        visual = view.get("visualProof")
        if isinstance(visual, Mapping):
            visual_state = _status(visual.get("status"))
            if visual_state in {"failed", "missing", "unavailable"}:
                check = {"kind": "visual", "state": visual_state}
                if check not in checks:
                    checks.append(check)
                if write and visual_required:
                    needs_user_action = True
            elif visual_state in {"ok", "passed", "verified"}:
                check = {"kind": "visual", "state": "passed"}
                if check not in checks:
                    checks.append(check)

    if needs_user_action:
        state = "needs_user_action"
    elif explicit_state in {"ok", "passed", "verified"} or checks:
        state = "passed"
    else:
        state = "not_required"
    return {"state": state, "checks": checks}, needs_user_action


def _evidence_refs(views: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for view in views:
        evidence = view.get("evidence")
        if not isinstance(evidence, list):
            continue
        for item in evidence[:12]:
            if not isinstance(item, Mapping):
                continue
            ref = str(item.get("ref") or "").strip()
            if not ref:
                continue
            row = {"ref": ref[:160]}
            for key in ("kind", "sha256"):
                text = str(item.get(key) or "").strip()
                if text:
                    row[key] = text[:160]
            if row not in refs:
                refs.append(row)
    return refs


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:600]
    if depth >= 3:
        return "[bounded]"
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            bounded[str(key)[:120]] = _bounded_value(item, depth=depth + 1)
        return bounded
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in value[:24]]
    return str(value)[:600]


def normalize_agent_tool_result(
    value: Any,
    *,
    fallback_summary: str,
    write: bool,
) -> dict[str, Any]:
    """Return one bounded semantic envelope without copying arbitrary dumps."""

    views = _views(value)
    verification, verification_needs_action = _verification(views, write=write)

    failed_view: Mapping[str, Any] | None = None
    needs_action = verification_needs_action or bool(
        views and _status(views[0].get("status")) in _TOP_LEVEL_PENDING_STATUSES
    )
    for view in views:
        status = _status(view.get("status"))
        if status in _FAILED_STATUSES:
            failed_view = view
            break
        if status in _NEEDS_ACTION_STATUSES:
            needs_action = True
            continue
        if view.get("ok") is False or view.get("success") is False:
            failed_view = view
            break

    if failed_view is not None:
        status = "failed"
        summary = _first_text([failed_view, *views], ("error", "reason", "message", "summary"))
        summary = summary or str(fallback_summary or "Tool execution failed.").strip()
        code = _first_text([failed_view, *views], ("code", "errorCode", "reason"))
        error: dict[str, Any] | None = {
            "type": "tool",
            "code": code or "tool_failed",
            "likelyCauses": [],
            "nextActions": [],
            "retryable": False,
        }
    elif needs_action:
        status = "needs_user_action"
        if verification_needs_action:
            summary = (
                "The tool action was not accepted as complete because required verification "
                "did not pass; inspect the result and choose the next action."
            )
        else:
            summary = _first_text(views, ("error", "reason", "message", "summary")) or (
                "The tool action needs user input before it can continue."
            )
        error = {
            "type": "validation" if verification_needs_action else "user_action",
            "code": "verification_required" if verification_needs_action else "user_action_required",
            "likelyCauses": [],
            "nextActions": ["Inspect the tool result and complete the required verification."],
            "retryable": False,
        }
    else:
        status = "ok"
        summary = _first_text(views, ("summary", "message"))
        summary = summary or str(fallback_summary or "Tool execution completed.").strip()
        error = None

    data: dict[str, Any] = {}
    for view in views:
        candidate = view.get("data")
        if isinstance(candidate, Mapping):
            bounded = _bounded_value(candidate)
            data = bounded if isinstance(bounded, dict) else {}
            break

    return {
        "schema": TOOL_RESULT_SCHEMA,
        "status": status,
        "summary": summary[:600],
        "data": data,
        "error": error,
        "evidence": _evidence_refs(views),
        "verification": verification,
    }


def completion_gate_plan(
    plan: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Replace a completion claim when deterministic execution did not complete."""

    outcome_status = _status(outcome.get("status"))
    if outcome_status not in {"failed", "needs_user_action"}:
        return None
    default_summary = (
        "Tool execution failed."
        if outcome_status == "failed"
        else "Required verification needs user action."
    )
    summary = str(outcome.get("summary") or default_summary).strip()
    next_step = "tool_failed" if outcome_status == "failed" else "needs_user_action"
    gated = dict(plan)
    gated.update(
        {
            "summary": summary,
            "reply": summary,
            "continueLoop": False,
            "nextStep": next_step,
            "completionGate": {
                "status": outcome_status,
                "reason": "tool_failed" if outcome_status == "failed" else "verification_required",
            },
        }
    )
    return gated
