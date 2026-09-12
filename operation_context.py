"""Request-local operation identity carried from Gateway to Unity Core."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import secrets
from typing import Any, Iterator, Mapping

from execution_target import future_provenance_metadata


_CURRENT_OPERATION: ContextVar[dict[str, Any] | None] = ContextVar("vrcforge_operation_context", default=None)


@contextmanager
def bind_operation_context(operation_id: str, execution_target: Mapping[str, Any] | None = None) -> Iterator[None]:
    value: dict[str, Any] = {"operationId": str(operation_id)}
    if isinstance(execution_target, Mapping):
        value["executionTarget"] = dict(execution_target)
    token = _CURRENT_OPERATION.set(value)
    try:
        yield
    finally:
        _CURRENT_OPERATION.reset(token)


def current_operation_context() -> dict[str, Any] | None:
    value = _CURRENT_OPERATION.get()
    return dict(value) if isinstance(value, Mapping) else None


def ensure_operation_result(
    value: Any,
    *,
    write: bool,
    operation_kind: str = "tool",
) -> dict[str, Any]:
    """Complete the Stage 1 result envelope without inventing Stage 2/3 data."""

    result = dict(value) if isinstance(value, Mapping) else {"value": value}
    operation_id = str(result.get("operationId") or "").strip()
    if not operation_id:
        prefix = "mcpwrite" if write else "mcpread"
        operation_id = (
            f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_"
            f"{secrets.token_hex(4)}"
        )
        result["operationId"] = operation_id

    raw_status = str(result.get("status") or "").strip().casefold().replace("-", "_")
    status_aliases = {
        "": "success" if result.get("ok", True) is not False else "failed",
        "ok": "success",
        "completed": "success",
        "executed": "success",
        "loaded": "success",
        "unloaded": "success",
        "scheduled": "pending",
        "queued": "pending",
        "error": "failed",
        "failure": "failed",
        "blocked": "failed",
        "awaiting_user": "user_confirmation_required",
    }
    canonical_status = status_aliases.get(raw_status, raw_status)
    allowed_statuses = {
        "success",
        "no_change",
        "pending",
        "failed",
        "unknown",
        "user_confirmation_required",
        "preview",
        "applied",
    }
    if canonical_status not in allowed_statuses:
        canonical_status = "unknown"
    error_details = result.get("errorDetails")
    if (
        canonical_status == "unknown"
        and result.get("ok") is False
        and isinstance(error_details, Mapping)
        and error_details.get("status") == "failed"
        and error_details.get("toolRoutingStarted") is False
        and error_details.get("mutationStarted") is False
        and error_details.get("committed") is False
        and error_details.get("commitState") == "not_started"
        and result.get("toolRoutingStarted", False) is False
        and result.get("mutationStarted", False) is False
        and result.get("committed", False) is False
        and result.get("commitState", "not_started") == "not_started"
    ):
        # Explicit rejection before routing is a known failure, not an unknown write.
        canonical_status = "failed"
    result["operationStatus"] = canonical_status
    if not raw_status:
        result["status"] = canonical_status

    mutation_started = result.get("mutationStarted")
    if not isinstance(mutation_started, bool):
        # A failed response can follow a committed write; failure alone is not no-write evidence.
        mutation_started = False if not write or canonical_status in {"preview", "user_confirmation_required"} else None
        result["mutationStarted"] = mutation_started
    mutation_applied = result.get("mutationApplied")
    if not isinstance(mutation_applied, bool):
        mutation_applied = False if mutation_started is False else None
        result["mutationApplied"] = mutation_applied
    result.setdefault(
        "commitState",
        "not_started" if mutation_started is False else "unknown",
    )
    result.setdefault(
        "persistenceState",
        "not_applicable" if not write else ("not_started" if mutation_started is False else "unknown"),
    )
    result.setdefault(
        "readbackState",
        "failed" if canonical_status == "failed" else ("complete" if not write else "pending"),
    )
    result.setdefault("cleanupState", "not_applicable" if not write else "unknown")
    result.setdefault("retryable", False)
    result.setdefault("nextAction", None)
    for key in ("beforeResource", "afterResource", "diffResource", "operationResource"):
        result.setdefault(key, None)
    provenance = future_provenance_metadata()
    result.setdefault("resources", provenance["resources"])
    result.setdefault("promptSkillProvenance", provenance["promptSkillProvenance"])
    result.setdefault("operationKind", str(operation_kind or "tool"))
    return result
