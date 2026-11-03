"""Unified runtime error and turn-status schema for every exit surface.

Modeled on the proven designs this repository was audited against:
- Established CLI agent protocols keep model text and failure detail in
  separate terminal-event fields, carry a human-bounded message plus a
  machine classification enum with an unknown-fallback, and keep sensitive
  explanations out of stored history.
- Anthropic tool results: raw stdout/stderr stays in the model-facing result
  with an ``is_error`` flag; presentation cleanup happens only on user-facing
  surfaces.

Contract enforced across all exits (HTTP details, event payloads, tool
envelopes, retained timelines, frontend rendering):

- ``code``  stable snake_case identifier; the frontend renders by code and owns
  localized copy. Never contains exception text.
- ``message`` neutral bounded sentence, safe to display as fallback. Raw
  ``str(exc)`` may only reach debug retention (audit/emit_log), never a
  returned payload.
- ``detail`` structured context (statusCode, exceptionType, phase, tool...).
- ``retryable`` optional triage hint.

Turn statuses (revision: in-flight and terminal are distinct groups):

- In-flight: ``running``, ``waiting_user``. ``waiting_user`` is a durable
  decision point: startup recovery must NOT clear it.
- Terminal: ``completed``, ``failed``, ``cancelled``, ``interrupted``.
  Startup recovery only rewrites residual ``running`` records to
  ``interrupted``.

Error routing (revision: non-tool failures are turn-level):

- Tool failures -> tool-result envelope (raw output kept, ``isError`` true).
- Non-tool stop causes (provider 5xx, context overflow, budget pause) ->
  ``runtime_notice`` carried into the next turn's context as runtime feedback,
  never as a tool result and never as assistant-authored text.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

RUNTIME_ERROR_SCHEMA = "vrcforge.runtime_error.v1"

RUNTIME_TURN_ACTIVE = frozenset({"running", "waiting_user"})
RUNTIME_TURN_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_MESSAGE_MAX_CHARS = 500
_DETAIL_MAX_ITEMS = 12
_DETAIL_VALUE_MAX_CHARS = 200


def _safe_code(code: str) -> str:
    cleaned = str(code or "").strip().lower().replace("-", "_")
    if not _CODE_PATTERN.match(cleaned):
        return "internal_error"
    return cleaned


def _safe_message(message: str) -> str:
    compact = " ".join(str(message or "").split())
    if not compact:
        return ""
    if len(compact) <= _MESSAGE_MAX_CHARS:
        return compact
    return compact[: _MESSAGE_MAX_CHARS - 1] + "…"


def _safe_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(detail, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key, value in list(detail.items())[:_DETAIL_MAX_ITEMS]:
        cleaned_key = re.sub(r"[^a-zA-Z0-9_]", "", str(key))[:64]
        if not cleaned_key:
            continue
        if isinstance(value, bool):
            safe[cleaned_key] = value
        elif isinstance(value, int):
            safe[cleaned_key] = value
        elif value is None:
            continue
        else:
            compact = _safe_message(str(value))
            if compact:
                safe[cleaned_key] = compact
    return safe


def runtime_error(
    code: str,
    *,
    message: str = "",
    detail: Mapping[str, Any] | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    """Schema object for any exit surface; safe to return or broadcast."""
    payload: dict[str, Any] = {
        "schema": RUNTIME_ERROR_SCHEMA,
        "code": _safe_code(code),
        "message": _safe_message(message),
    }
    structured = _safe_detail(detail)
    if structured:
        payload["detail"] = structured
    if retryable is not None:
        payload["retryable"] = bool(retryable)
    return payload


def runtime_error_from_exception(
    code: str,
    exc: BaseException,
    *,
    message: str = "",
    phase: str = "",
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Schema object from an exception without leaking its text.

    Structured facts (exception type name, HTTP status, retry hint) survive;
    ``str(exc)`` never enters the payload — it belongs to debug retention only.
    """
    merged: dict[str, Any] = dict(detail or {})
    merged.setdefault("exceptionType", type(exc).__name__)
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and 100 <= status_code <= 599:
        merged.setdefault("statusCode", status_code)
    if phase:
        merged.setdefault("phase", str(phase)[:64])
    retryable = getattr(exc, "retryable", None)
    return runtime_error(
        code,
        message=message,
        detail=merged,
        retryable=bool(retryable) if isinstance(retryable, bool) else None,
    )


def runtime_turn_status(
    status: str,
    *,
    reply: str = "",
    error: Mapping[str, Any] | None = None,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn status envelope; ``reply`` carries model-authored text only.

    Failure detail goes to ``error`` (a runtime_error object): the two
    never mix, and a failed turn may still retain the model text it already
    produced.
    """
    cleaned = _safe_code(status) if status in RUNTIME_TURN_ACTIVE | RUNTIME_TURN_TERMINAL else ""
    if not cleaned:
        raise ValueError(f"Unknown runtime turn status: {status!r}")
    payload: dict[str, Any] = {"schema": RUNTIME_ERROR_SCHEMA, "status": cleaned}
    compact_reply = _safe_message(reply) if reply else ""
    if compact_reply:
        payload["reply"] = compact_reply
    if isinstance(error, Mapping) and error.get("schema") == RUNTIME_ERROR_SCHEMA:
        payload["error"] = dict(error)
    elif error is not None:
        payload["error"] = runtime_error("internal_error", message="Malformed turn error payload.")
    structured = _safe_detail(detail)
    if structured:
        payload["detail"] = structured
    return payload


def runtime_notice(stop_reason: str, *, status: str = "failed") -> dict[str, Any]:
    """Non-tool stop cause for the next turn's context (runtime feedback).

    Provider 5xx, context overflow and budget pauses are turn-level events:
    they must not masquerade as tool results and must never enter model context
    as assistant-authored text. The serialized form is prefixed as runtime
    feedback data when appended to native history by the caller.
    """
    return runtime_turn_status(
        status if status in RUNTIME_TURN_TERMINAL else "failed",
        error=runtime_error(
            "turn_stopped",
            message=f"The previous turn stopped: {stop_reason}",
            detail={"stopReason": _safe_code(stop_reason)},
        ),
    )
