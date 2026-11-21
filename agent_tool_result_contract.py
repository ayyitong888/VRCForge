"""Small, transport-neutral result semantics for agent tool calls.

This module deliberately does not own execution, persistence, evidence storage,
or workflow state.  It only prevents an outer caller from turning a nested tool
failure or an unverified write into a successful completion claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import re
from typing import Any

from external_tool_result_contract import (
    INCOMPLETE_OPERATION_STATES,
    PAGE_PROGRESS_FIELDS,
    canonical_result_facts,
    is_gesture_manager_entry_pending,
    prioritize_result_sources,
    receipt_operation_status,
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
        "user_confirmation_required",
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
TERMINAL_JOB_STATUSES = _FAILED_STATUSES | _SUCCESSFUL_EXECUTION_STATUSES | {
    "done", "cancelled", "canceled", "expired", "interrupted", "not_found", "no_change", "not_started",
    "partial", "incomplete", "rolled_back",
}
_UNSUCCESSFUL_JOB_STATUSES = {"expired", "not_found", "not_started"}
_CARRIER_KEYS = (
    "structuredContent",
    "result",
    "rawResult",
    "entrypoint",
    "toolResult",
)
_KNOWN_NESTED_KEYS = (*_CARRIER_KEYS, "errorDetails", "consoleVerification")


def _status(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _views(value: Any, *, nested_keys: tuple[str, ...] = _KNOWN_NESTED_KEYS) -> list[Mapping[str, Any]]:
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
        for key in nested_keys:
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


def _operation_owner(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap result carriers only; diagnostics and verification describe other scopes."""
    current = receipt
    for _ in range(4):
        if (current.get("operationStatus") or current.get("toolExecutionStatus")
                or "completionKnown" in current or current.get("partial") is True
                or current.get("commitState") in ("partial", "rolled_back")
                or _status(current.get("status")) in INCOMPLETE_OPERATION_STATES):
            return current
        child = next((current[k] for k in ("structuredContent", "result", "rawResult", "toolResult", "entrypoint")
                      if isinstance(current.get(k), Mapping)), None)
        if child is None:
            break
        current = child
    return receipt


def runtime_observation_receipt(value: Any) -> Mapping[str, Any] | None:
    """Find the domain receipt, never infer a job from generic pending status."""
    return next((view for view in _views(value)
        if view.get("schema") == "vrcforge.runtime_observation.v1"
        and re.fullmatch(r"[0-9a-f]{32}", str(view.get("jobId") or ""))
        and isinstance(view.get("avatarPath"), str) and view["avatarPath"]
        and isinstance(view.get("coreIdentity"), str) and view["coreIdentity"]), None)


def job_receipt(value: Any) -> Mapping[str, Any] | None:
    """Find an existing job handle, without guessing its owning tool."""
    return next((view for view in _views(value)
        if isinstance(view.get("jobId", view.get("job_id")), str)
        and view.get("jobId", view.get("job_id")).strip()), None)


def pending_execution_receipt(value: Any) -> Mapping[str, Any] | None:
    """Recognize accepted work whose callback or job has not finished."""
    for view in _views(value):
        if (view.get("triggered") is True and view.get("state") == "invoked"
                and view.get("resultVerification") == "pending"):
            return view
    job = job_receipt(value)
    if job is None or job.get("completedAt"):
        return None
    status = _status(job.get("status"))
    if status and status not in TERMINAL_JOB_STATUSES | _NEEDS_ACTION_STATUSES | {"unknown"}:
        return job
    return None


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
    # Use the same action aliases and source priority as the public result.
    # Structured tool requests stay intact in nextAction; this human-readable
    # error list contains only instructions that the tool actually supplied.
    next_actions = canonical_result_facts([source, *views]).get("nextAction")
    if isinstance(next_actions, str):
        next_actions = [next_actions]
    elif isinstance(next_actions, (list, tuple)):
        next_actions = [item for item in next_actions if isinstance(item, str)]
    projected = {
        "type": error_type,
        "code": code,
        "likelyCauses": _bounded_text_list(source.get("likelyCauses")),
        "nextActions": _bounded_text_list(next_actions),
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
    checks: list[dict[str, Any]] = []
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
        console = view.get("consoleVerification")
        if isinstance(console, Mapping) and _status(console.get("status")) in _FAILED_STATUSES:
            check = {"kind": "console", "state": "failed"}
            for key in ("newErrorCount", "newWarningCount"):
                if type(console.get(key)) is int:
                    check[key] = console[key]
            if check not in checks:
                checks.append(check)
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

    if any(check["kind"] == "console" and check["state"] == "failed" for check in checks):
        state = "failed"
    elif needs_user_action:
        state = "needs_user_action"
    elif explicit_state in {"ok", "passed", "verified"} or checks:
        state = "passed"
    elif explicit_state:
        state = explicit_state
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
    gm_entry_pending = any(
        is_gesture_manager_entry_pending(str(view.get("tool") or ""), view)
        for view in views
    )
    pending_execution = pending_execution_receipt(value)
    job = job_receipt(value)
    receipt = views[0] if views else {}
    if not views and not write and value is not None:
        receipt = {"ok": True}
    owner = _operation_owner(receipt)
    operation = receipt_operation_status(owner, write=write)
    reported_incomplete = operation in INCOMPLETE_OPERATION_STATES and (
        operation != "unknown" or not owner or owner.get("completionKnown") is False
        or _status(owner.get("status")) == "unknown" or _status(owner.get("operationStatus")) == "unknown"
    )
    dispatch = pending_execution if pending_execution and pending_execution.get("triggered") is True else None
    running_write_without_job = (
        write
        and pending_execution is None
        and not gm_entry_pending
        and any(
            _status(view.get("status")) == "running"
            and view.get("mutationStarted") is True
            and _status(view.get("commitState")) == "unknown"
            for view in views
        )
    )

    failed_view: Mapping[str, Any] | None = None
    top_level_execution_status = _status(
        views[0].get("toolExecutionStatus") if views else None
    )
    needs_action = (verification_needs_action and not gm_entry_pending) or bool(
        views
        and top_level_execution_status not in _SUCCESSFUL_EXECUTION_STATUSES
        and (_status(receipt.get("status")) in {"approval_pending", "queued_for_approval"}
             or (_status(receipt.get("status")) == "pending" and
                 (receipt.get("approval") or receipt.get("approvalId") or receipt.get("questionId"))))
        and not gm_entry_pending
    )
    for view in views:
        if view.get("isError") is True:
            failed_view = view
            break
        if view is job and _status(view.get("status")) in _UNSUCCESSFUL_JOB_STATUSES:
            failed_view = view
            break
        if (
            _status(view.get("persistenceState")) in _FAILED_STATUSES
            or _status(view.get("readbackState")) in _FAILED_STATUSES
        ):
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
        if ((view is owner or (view is receipt and view.get("result") is owner))
                and owner.get("completionKnown") is False
                and operation in {"unknown", "pending"}):
            continue
        if view.get("ok") is False or view.get("success") is False:
            failed_view = view
            break

    if failed_view is not None:
        status = "failed"
        failure_views = prioritize_result_sources([failed_view, *causal_views])
        if reported_incomplete and isinstance(owner.get("error"), Mapping):
            # An independently failed check can coexist with an unknown operation.
            # Its cause belongs to that check; the operation's diagnostic is retained below.
            failed_checks = [view for view in failure_views if view is not owner
                             and _status(view.get("status")) in _FAILED_STATUSES
                             and (view.get("code") or view.get("errorCode") or view.get("causeCode"))]
            if failed_checks:
                failure_views = failed_checks
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
        code = projected_error["code"] or _first_text(failure_views, ("code", "errorCode", "reason"))
        if not code and failed_view is job and (job_status := _status(job.get("status"))) in _UNSUCCESSFUL_JOB_STATUSES:
            code = f"job_{job_status}"
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
    elif reported_incomplete:
        status = operation
        summary = _first_text(views, ("reason", "error", "summary", "message")) or f"Operation status: {operation}."
        error = None
    elif verification_needs_action and not write and not pending_execution and not any(
        _status(view.get("status")) in _NEEDS_ACTION_STATUSES | _TOP_LEVEL_PENDING_STATUSES
        or _status(view.get("toolExecutionStatus")) in _NEEDS_ACTION_STATUSES | _TOP_LEVEL_PENDING_STATUSES
        or (
            isinstance(view.get("verification"), Mapping)
            and _status(view["verification"].get("state")) in {"needs_user_action", "pending"}
        )
        for view in views
    ):
        status = "failed"
        summary = "Required verification did not pass; inspect the evidence and correct the cause."
        verification = {**verification, "state": "failed"}
        error = {
            "type": "verification", "code": "verification_failed", "retryable": True,
            "likelyCauses": [],
            "nextActions": ["Inspect the failed checks, correct the cause, and verify again."],
        }
    elif pending_execution:
        status = "pending"
        summary = (
            "The command was invoked; inspect its actual result without repeating the dispatch."
            if dispatch else (
                f"Last reported job state: {pending_execution.get('lastKnownStatus') or pending_execution.get('status')}. "
                "Poll the existing job for its result."
            )
        )
        verification = {"state": "pending", "checks": []}
        error = None
    elif gm_entry_pending:
        status = "pending"
        summary = "Gesture Manager Play Mode entry was requested and is pending."
        error = None
    elif running_write_without_job:
        status = "failed"
        operation = "unknown"
        summary = "The write is still reported as running but has no job handle; completion is unknown."
        error = {
            "type": "execution",
            "code": "running_write_without_job_handle",
            "retryable": False,
            "likelyCauses": [],
            "nextActions": ["Inspect the exact target before retrying; do not repeat the write."],
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
    elif operation == "pending" and not owner.get("toolExecutionStatus"):
        status = "pending"
        summary = _first_text([owner], ("summary", "message")) or "Operation is pending; no final result has been observed."
        error = None
    else:
        status = "ok"
        summary = _first_text(views, ("summary", "message"))
        if write and operation == "unknown":
            summary = summary or "Handler returned. The resulting change is unconfirmed."
        else:
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
    execution_pending = bool(pending_execution and status == "pending")
    # Preserve an existing normalized receipt from the gateway or approval wrapper.
    gateway_outcome = views[0].get("outcome") if views else None
    if (
        isinstance(gateway_outcome, Mapping)
        and gateway_outcome.get("schema") == TOOL_RESULT_SCHEMA
        and gateway_outcome.get("status") == status
        and isinstance(views[0].get("result"), Mapping)
        and (
            isinstance(views[0].get("approvalId"), str)
            or (isinstance(views[0].get("requestId"), str) and isinstance(views[0].get("tool"), str))
        )
    ):
        causal_views = [gateway_outcome, *causal_views]
    # Only the operation owner can attest to an observed non-persistent effect.
    # An embedded window/process result cannot confirm its enclosing asset write.
    runtime_effect = write and status == "ok" and (
        (owner is receipt and receipt.get("persistent") is False
         and receipt.get("readbackVerified") is True and isinstance(receipt.get("readback"), Mapping)
         and bool(receipt["readback"]) and receipt.get("operationStatus") in {"success", "no_change"})
        or (receipt.get("tool") and receipt.get("operationId")
            and receipt.get("persistenceState") == "not_applicable" and receipt.get("readbackState") == "verified"
            and isinstance(gateway_outcome, Mapping) and gateway_outcome.get("schema") == TOOL_RESULT_SCHEMA
            and gateway_outcome.get("operationId") == receipt["operationId"]
            and gateway_outcome.get("targetEffectVerified") is True)
    )
    canonical_facts = canonical_result_facts(
        causal_views,
        success=status == "ok" if status in {"failed", "ok"} else None,
        status=status,
        write=write,
        persistent=not runtime_effect,
    )
    canonical_facts.pop("status", None)
    result.update(canonical_facts)
    if status == "pending":
        operation = "pending"
    elif status == "failed" and operation in {"success", "applied", "no_change"}:
        operation = "failed"
    elif status == "needs_user_action" and needs_action and not verification_needs_action:
        operation = "user_confirmation_required"
    result["operationStatus"] = operation
    if not write:
        if isinstance(owner.get("readbackState"), str):
            result["readbackState"] = "failed" if status == "failed" else owner["readbackState"]
        elif status in INCOMPLETE_OPERATION_STATES | {"pending"}:
            result["readbackState"] = status
    for key in ("completionKnown", "lastKnownStatus"):
        if key in owner:
            result[key] = _bounded_value(owner[key])
    page = next((view for view in _views(value, nested_keys=_CARRIER_KEYS) if type(view.get("hasMore")) is bool
                 or type(view.get("truncated")) is bool), None)
    if page is not None:
        for key in PAGE_PROGRESS_FIELDS:
            if key != "nextRequest" and key in page:
                result[key] = _bounded_value(page[key])
        if not write and (page.get("hasMore") or page.get("truncated")):
            result.setdefault("readbackState", "partial")
    else:
        for key in ("nextCursor", "remainingCount"):
            if key in owner:
                result[key] = _bounded_value(owner[key])
    # Continuations are executable coordinates. Summarizing their selectors
    # or trimming a list of clip paths changes the operation to be resumed.
    request_owner = page if page is not None and "nextRequest" in page else owner
    if "nextRequest" in request_owner:
        result["nextRequest"] = deepcopy(request_owner["nextRequest"])
    if job:
        result["jobId"] = job.get("jobId") or job.get("job_id")
        result["jobStatus"] = job.get("lastKnownStatus") or job.get("status")
    if write:
        result["targetEffectVerified"] = bool(
            status == "ok" and operation not in INCOMPLETE_OPERATION_STATES | {"pending", "preview"}
            and result.get("readbackState") == "verified"
            and (runtime_effect or (result.get("commitState") in {"complete", "no_change"}
                 and result.get("committed") is True))
            and not any(
                view.get("verified") is False or view.get("persistedReadback") is False
                or view.get("partial") is True or view.get("preview") is True
                or _status(view.get("status")) in {"partial", "interrupted", "rolled_back"}
                for view in causal_views
            )
        )
        for key in ("executionId", "checkpointId"):
            identity = _first_text(causal_views, (key,))
            if identity:
                result[key] = identity
    if (execution_pending or reported_incomplete) and isinstance(owner.get("error"), Mapping):
        observation_error = _structured_error([owner])
        if status == "failed" and error and error["code"] != observation_error["code"]:
            result["operationError"] = observation_error
            result["summary"] = " ".join(filter(None, (result["summary"], observation_error["summary"])))[:600]
        else:
            result["summary"] = observation_error["summary"] or result["summary"]
            result["error"] = error = observation_error
    if error is not None and isinstance(result.get("safeToRetry"), bool):
        error["retryable"] = result["safeToRetry"]
    if execution_pending:
        result.update({
            "persistenceState": owner.get("persistenceState") or "pending",
            "readbackState": owner.get("readbackState") or "pending",
        })
    if execution_pending and dispatch:
        result.update({
            "triggered": True,
            "dispatchState": "invoked",
            "resultVerification": "pending",
            "mutationMayHaveStarted": dispatch.get("mutationMayHaveStarted"),
            "commitState": "unknown",
            "nextAction": "Inspect the actual objects, assets or window. Do not repeat the dispatch to verify it.",
        })
    elif execution_pending:
        job_id = pending_execution.get("jobId", pending_execution.get("job_id"))
        result.update({
            "jobId": job_id,
            "jobStatus": pending_execution.get("lastKnownStatus") or pending_execution.get("status"),
            "nextAction": pending_execution.get("nextAction") or
                f"Poll the existing job {job_id} until it reaches a terminal state. Do not start it again.",
        })
    # These are deliberately shared diagnostic facts, not transport-only
    # decorations.  Keep them in the normalized outcome so the internal loop
    # and the external MCP boundary expose the same failed step and evidence
    # without forcing the planner to parse the raw result again.
    for key in ("failedStep", "diagnostics"):
        for view in causal_views:
            if key in view and view[key] not in (None, ""):
                result[key] = _bounded_value(view[key])
                break
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
    completion_claim = gated.get("completionClaim")
    if not isinstance(completion_claim, Mapping):
        completion_claim = gated.get("completion_claim")
    # The deterministic gate owns completion status, not assistant prose.
    # Keep a natural final or an honest incomplete reply; withhold only a
    # model claim explicitly contradicted by this outcome. The failure stays
    # in summary/error and must never masquerade as model-authored text.
    honest_failure_reply = (
        gated["reply"].strip()
        if not (isinstance(completion_claim, Mapping)
                and completion_claim.get("satisfied") is True)
        and isinstance(gated.get("reply"), str)
        and gated["reply"].strip()
        else ""
    )
    gated.update(
        {
            "summary": summary,
            "reply": honest_failure_reply,
            "continueLoop": False,
            "nextStep": next_step,
            "completionGate": {
                "status": outcome_status,
                "reason": (
                    "verification_failed"
                    if outcome_status == "failed"
                    and isinstance(outcome.get("error"), Mapping)
                    and outcome["error"].get("code") == "verification_failed"
                    else "tool_failed" if outcome_status == "failed" else "verification_required"
                ),
            },
        }
    )
    if isinstance(outcome.get("error"), Mapping):
        gated["error"] = dict(outcome["error"])
    if honest_failure_reply:
        gated["completionGate"]["modelFailureReplyPreserved"] = True
    return gated
