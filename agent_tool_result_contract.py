"""Small, transport-neutral result semantics for agent tool calls.

This module deliberately does not own execution, persistence, evidence storage,
or workflow state.  It only prevents an outer caller from turning a nested tool
failure or an unverified write into a successful completion claim.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from external_tool_result_contract import (
    canonical_result_facts,
    prioritize_result_sources,
)


TOOL_RESULT_SCHEMA = "vrcforge.tool_result.v1"
INTERNAL_TOOL_DIAGNOSTICS_SCHEMA = "vrcforge.internal_tool_diagnostics.v1"

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
_SUCCESSFUL_EXECUTION_STATUSES = frozenset(
    {"ok", "success", "executed", "completed", "passed"}
)
_KNOWN_NESTED_KEYS = (
    "structuredContent",
    "result",
    "rawResult",
    "entrypoint",
    "toolResult",
    "errorDetails",
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


def _bounded_text_list(value: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = str(item or "").strip()
        if text and text[:240] not in result:
            result.append(text[:240])
    return result


def _structured_error(views: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Project only the bounded error fields that tools deliberately expose."""

    source: Mapping[str, Any] | None = None
    for view in views:
        candidate = view.get("error")
        if isinstance(candidate, Mapping):
            source = candidate
            break
    source = source or {}
    error_type = str(source.get("type") or "").strip()[:80]
    code = str(
        source.get("code")
        or source.get("causeCode")
        or source.get("errorCode")
        or _first_text(views, ("causeCode", "code", "errorCode"))
        or ""
    ).strip()[:120]
    retryable_value = source.get("retryable")
    if not isinstance(retryable_value, bool):
        retryable_value = next(
            (
                view.get("retryable")
                for view in views
                if isinstance(view.get("retryable"), bool)
            ),
            None,
        )
    projected = {
        "type": error_type,
        "code": code,
        "likelyCauses": _bounded_text_list(source.get("likelyCauses")),
        "nextActions": _bounded_text_list(source.get("nextActions")),
        "retryable": retryable_value,
        "summary": str(source.get("summary") or source.get("message") or "").strip()[:600],
    }
    for key, limit in (
        ("provider", 80),
        ("providerLabel", 120),
        ("model", 180),
        ("source", 40),
        ("disposition", 80),
    ):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            projected[key] = value.strip()[:limit]
    retain_images = source.get("retainImages")
    if isinstance(retain_images, bool):
        projected["retainImages"] = retain_images
    return projected


def _structured_error_route_fields(projected_error: Mapping[str, Any]) -> dict[str, Any]:
    route: dict[str, Any] = {}
    for key in (
        "provider",
        "providerLabel",
        "model",
        "source",
        "retainImages",
        "disposition",
    ):
        value = projected_error.get(key)
        if value not in (None, ""):
            route[key] = value
    return route


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


def _bounded_value(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:600]
    if depth >= 3:
        return "[bounded]"
    active = seen if seen is not None else set()
    marker = id(value)
    if marker in active:
        return "[cycle]"
    active.add(marker)
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            bounded[str(key)[:120]] = _bounded_value(item, depth=depth + 1, seen=active)
        active.discard(marker)
        return bounded
    if isinstance(value, (list, tuple)):
        bounded_list = [_bounded_value(item, depth=depth + 1, seen=active) for item in value[:24]]
        active.discard(marker)
        return bounded_list
    active.discard(marker)
    return str(value)[:600]


def _canonical_cause(views: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Project the exact bounded failure cause shared by every transport."""
    source: Mapping[str, Any] | None = None
    for view in views:
        for key in ("failureCause", "cause"):
            candidate = view.get(key)
            if isinstance(candidate, Mapping):
                source = candidate
                break
        if source is None and (
            any(
                view.get(key) not in (None, "")
                for key in (
                    "failureLayer",
                    "failurePhase",
                    "category",
                    "code",
                    "causeCode",
                    "errorCode",
                    "reason",
                )
            )
            or (
                isinstance(view.get("error"), str)
                and bool(str(view.get("error") or "").strip())
            )
        ):
            source = view
        if source is None:
            for key in ("errorDetails", "exception", "error"):
                candidate = view.get(key)
                if isinstance(candidate, Mapping):
                    source = candidate
                    break
        if source is not None:
            break
    if source is None:
        source = next(
            (
                view
                for view in views
                if any(
                    view.get(key) not in (None, "")
                    for key in (
                        "failureLayer",
                        "failurePhase",
                        "category",
                        "code",
                        "causeCode",
                        "errorCode",
                        "error",
                        "reason",
                    )
                )
            ),
            None,
        )
    if source is None:
        return None
    nested_details = source.get("details")
    nested_details = nested_details if isinstance(nested_details, Mapping) else {}
    fields = {
        "layer": source.get("failureLayer") or source.get("layer") or nested_details.get("failureLayer") or nested_details.get("layer"),
        "phase": source.get("failurePhase") or source.get("phase") or nested_details.get("failurePhase") or nested_details.get("phase"),
        "category": source.get("category") or source.get("type") or nested_details.get("category") or nested_details.get("type"),
        "code": source.get("errorCode") or source.get("causeCode") or source.get("code") or nested_details.get("errorCode") or nested_details.get("causeCode") or nested_details.get("code"),
        "message": source.get("message") or source.get("error") or source.get("reason") or nested_details.get("message") or nested_details.get("error") or nested_details.get("reason"),
    }
    cause = {
        key: str(value).strip()[:600]
        for key, value in fields.items()
        if value not in (None, "")
    }
    for key in ("mutationStarted", "committed", "commitState"):
        value = source.get(key)
        if isinstance(value, (bool, str)):
            cause[key] = str(value).strip()[:80] if isinstance(value, str) else value
    nested = source.get("causes")
    if isinstance(nested, (list, tuple)):
        cause["causes"] = _bounded_value(nested)[:6]
    return cause or None


def _internal_failure_diagnostics(views: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    # A domain result may already carry the authoritative diagnostics (for
    # example Unity exception type/message/innerChain).  Preserve that exact
    # projection across transports instead of replacing it with a wrapper
    # error snapshot merely because one boundary also emits errorDetails.
    for view in views:
        explicit = view.get("diagnostics")
        if isinstance(explicit, Mapping):
            bounded = _bounded_value(explicit)
            return bounded if isinstance(bounded, dict) else None
    source: Mapping[str, Any] | None = None
    for view in views:
        candidate = view.get("errorDetails")
        if isinstance(candidate, Mapping) and str(candidate.get("schema") or "") == "vrcforge.external_tool_error.v1":
            source = candidate
            break
        if str(view.get("schema") or "") == "vrcforge.external_tool_error.v1":
            source = view
            break
    if source is None:
        return None
    bounded = _bounded_value(source)
    return {
        "schema": INTERNAL_TOOL_DIAGNOSTICS_SCHEMA,
        "sourceError": bounded if isinstance(bounded, dict) else {},
    }


def normalize_agent_tool_result(
    value: Any,
    *,
    fallback_summary: str,
    write: bool,
) -> dict[str, Any]:
    """Return one bounded semantic envelope without copying arbitrary dumps."""

    views = _views(value)
    causal_views = prioritize_result_sources(views)
    verification, verification_needs_action = _verification(views, write=write)

    failed_view: Mapping[str, Any] | None = None
    top_level_execution_status = _status(
        views[0].get("toolExecutionStatus") if views else None
    )
    needs_action = verification_needs_action or bool(
        views
        and top_level_execution_status not in _SUCCESSFUL_EXECUTION_STATUSES
        and _status(views[0].get("status")) in _TOP_LEVEL_PENDING_STATUSES
    )
    for view in views:
        if view.get("isError") is True:
            failed_view = view
            break
        execution_status = _status(view.get("toolExecutionStatus"))
        if execution_status in _FAILED_STATUSES:
            failed_view = view
            break
        if execution_status in _NEEDS_ACTION_STATUSES or execution_status in _TOP_LEVEL_PENDING_STATUSES:
            needs_action = True
            continue
        if execution_status in _SUCCESSFUL_EXECUTION_STATUSES:
            continue
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
        failure_views = prioritize_result_sources([failed_view, *causal_views])
        projected_error = _structured_error(failure_views)
        summary = _first_text(
            failure_views,
            ("error", "reason", "message", "summary"),
        )
        if failed_view.get("isError") is True and not summary:
            content = failed_view.get("content")
            if isinstance(content, (list, tuple)):
                for item in content[:6]:
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str) and item["text"].strip():
                        summary = item["text"].strip()[:600]
                        break
        summary = summary or projected_error["summary"]
        summary = summary or str(fallback_summary or "Tool execution failed.").strip()
        code = projected_error["code"] or _first_text(
            failure_views, ("code", "errorCode", "reason")
        )
        error: dict[str, Any] | None = {
            "type": projected_error["type"] or "tool",
            "code": code or "tool_failed",
            "likelyCauses": projected_error["likelyCauses"],
            "nextActions": projected_error["nextActions"],
            "retryable": (
                projected_error["retryable"]
                if isinstance(projected_error["retryable"], bool)
                else False
            ),
            **_structured_error_route_fields(projected_error),
        }
    elif needs_action:
        status = "needs_user_action"
        projected_error = _structured_error(causal_views)
        if verification_needs_action:
            summary = (
                "The tool action was not accepted as complete because required verification "
                "did not pass; inspect the result and choose the next action."
            )
        else:
            summary = (
                _first_text(causal_views, ("error", "reason", "message", "summary"))
                or projected_error["summary"]
                or (
                "The tool action needs user input before it can continue."
                )
            )
        error = {
            "type": projected_error["type"] or (
                "validation" if verification_needs_action else "user_action"
            ),
            "code": projected_error["code"] or (
                "verification_required" if verification_needs_action else "user_action_required"
            ),
            "likelyCauses": projected_error["likelyCauses"],
            "nextActions": projected_error["nextActions"] or [
                "Inspect the tool result and complete the required verification."
            ],
            "retryable": (
                projected_error["retryable"]
                if isinstance(projected_error["retryable"], bool)
                else False
            ),
            **_structured_error_route_fields(projected_error),
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

    result = {
        "schema": TOOL_RESULT_SCHEMA,
        "success": status == "ok",
        "status": status,
        "summary": summary[:600],
        "data": data,
        "error": error,
        "evidence": _evidence_refs(views),
        "verification": verification,
    }
    canonical_facts = canonical_result_facts(
        causal_views,
        success=status == "ok",
        status="ok" if status == "ok" else "failed",
    )
    canonical_status = canonical_facts.pop("status", None)
    result.update(canonical_facts)
    # These are deliberately shared diagnostic facts, not transport-only
    # decorations.  Keep them in the normalized outcome so the internal loop
    # and the external MCP boundary expose the same failed step and evidence
    # without forcing the planner to parse the raw result again.
    for key in ("failedStep", "diagnostics"):
        for view in causal_views:
            if key in view and view[key] not in (None, ""):
                result[key] = _bounded_value(view[key])
                break
    # Preserve the historical needs_user_action status while exposing the
    # shared binary execution status for cross-surface consumers.
    if status not in {"ok", "failed"} and canonical_status:
        result["canonicalStatus"] = canonical_status
    cause = _canonical_cause(causal_views) if status == "failed" else None
    if cause is not None:
        for key in ("mutationStarted", "committed", "commitState"):
            value = result.get(key)
            if key not in cause and isinstance(value, (bool, str)):
                cause[key] = value
        result["cause"] = cause
    diagnostics = _internal_failure_diagnostics(views) if status == "failed" else None
    if diagnostics is not None:
        result["diagnostics"] = diagnostics
    return result


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
