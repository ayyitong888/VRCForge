from __future__ import annotations

from collections.abc import Mapping
from typing import Any


EXTERNAL_EXCEPTION_SCHEMA = "vrcforge.external_tool_exception.v1"
EXTERNAL_TOOL_ERROR_SCHEMA = "vrcforge.external_tool_error.v1"
WRITE_FAILURE_SCHEMA = "vrcforge.write_failure.v1"
_EXCEPTION_CHAIN_LIMIT = 6
_KNOWN_COMMIT_STATES = frozenset(
    {"not_started", "partial", "complete", "rolled_back", "unknown"}
)
_UNSET = object()


def external_exception_details(exc: BaseException) -> dict[str, Any]:
    """Serialize a bounded exception chain without inventing a new reason."""

    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    raw_result: dict[str, Any] | None = None

    while current is not None and len(chain) < _EXCEPTION_CHAIN_LIMIT:
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)

        entry: dict[str, Any] = {
            "type": type(current).__name__,
            "message": str(current),
        }
        for source_name, target_name in (
            ("cause_code", "errorCode"),
            ("error_code", "errorCode"),
            ("code", "errorCode"),
            ("core_tool", "coreTool"),
            ("failure_layer", "failureLayer"),
            ("failure_phase", "failurePhase"),
        ):
            value = getattr(current, source_name, None)
            if value not in (None, "") and target_name not in entry:
                entry[target_name] = value
        retryable = getattr(current, "retryable", None)
        if isinstance(retryable, bool):
            entry["retryable"] = retryable
        details = getattr(current, "details", None)
        if isinstance(details, Mapping) and details:
            entry["details"] = dict(details)
        candidate_result = getattr(current, "raw_result", None)
        if raw_result is None and isinstance(candidate_result, Mapping):
            raw_result = dict(candidate_result)
        chain.append(entry)
        current = current.__cause__ or current.__context__

    primary = dict(chain[0]) if chain else {"type": type(exc).__name__, "message": str(exc)}
    result: dict[str, Any] = {
        "schema": EXTERNAL_EXCEPTION_SCHEMA,
        **primary,
        "causes": chain[1:],
    }
    if raw_result is not None:
        result["rawResult"] = raw_result
    return result


def external_exception_raw_result(details: Mapping[str, Any]) -> dict[str, Any] | None:
    value = details.get("rawResult")
    return dict(value) if isinstance(value, Mapping) else None


def _first_present(sources: list[Mapping[str, Any]], *names: str) -> Any:
    for source in sources:
        for name in names:
            if name in source:
                return source[name]
    return _UNSET


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _first_bool(sources: list[Mapping[str, Any]], *names: str) -> Any:
    for source in sources:
        for name in names:
            value = source.get(name)
            if isinstance(value, bool):
                return value
    return _UNSET


def _first_commit_state(sources: list[Mapping[str, Any]]) -> str:
    saw_unknown = False
    for source in sources:
        value = str(source.get("commitState") or "").strip().casefold()
        if value == "committed":
            value = "complete"
        if value in _KNOWN_COMMIT_STATES:
            if value != "unknown":
                return value
            saw_unknown = True
    return "unknown" if saw_unknown else ""


def build_external_tool_error(
    *,
    error: Any = "",
    error_code: str = "",
    failure_layer: str = "",
    failure_phase: str = "",
    operation_kind: str = "unknown",
    tool: str = "",
    tool_routing_started: bool | None = None,
    mutation_started: Any = _UNSET,
    committed: Any = _UNSET,
    commit_state: str = "",
    retryable: Any = _UNSET,
    checkpoint_recovery_required: Any = _UNSET,
    temporary_cleanup_required: Any = _UNSET,
    checkpoint_id: str = "",
    recovery_id: str = "",
    console_before: Mapping[str, Any] | None = None,
    console_after: Mapping[str, Any] | None = None,
    raw_result: Mapping[str, Any] | None = None,
    exception: BaseException | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the one lossless external rejection object.

    Callers must supply facts known at the rejection point.  This function
    never classifies failures from message text or from an error-code allowlist.
    Missing write facts remain unknown instead of being guessed.
    """

    exception_error = getattr(exception, "external_error", None)
    exception_raw_result = (
        exception_error.get("rawResult")
        if isinstance(exception_error, Mapping)
        and isinstance(exception_error.get("rawResult"), Mapping)
        else None
    )
    direct_exception_raw_result = getattr(exception, "raw_result", None)
    raw = (
        dict(raw_result)
        if isinstance(raw_result, Mapping)
        else dict(direct_exception_raw_result)
        if isinstance(direct_exception_raw_result, Mapping)
        else dict(exception_raw_result)
        if isinstance(exception_raw_result, Mapping)
        else {}
    )
    nested_error = raw.get("errorDetails")
    nested_write_failure = raw.get("writeFailure")
    nested_details = raw.get("details")
    nested_structured = raw.get("structuredContent")
    nested_result = raw.get("result")
    nested_result_structured = (
        nested_result.get("structuredContent")
        if isinstance(nested_result, Mapping)
        else None
    )
    nested_result_details = (
        nested_result.get("details")
        if isinstance(nested_result, Mapping)
        else None
    )
    sources = [
        source
        for source in (
            nested_error if isinstance(nested_error, Mapping) else None,
            nested_write_failure if isinstance(nested_write_failure, Mapping) else None,
            raw,
            exception_error if isinstance(exception_error, Mapping) else None,
            nested_details if isinstance(nested_details, Mapping) else None,
            nested_structured if isinstance(nested_structured, Mapping) else None,
            nested_result if isinstance(nested_result, Mapping) else None,
            nested_result_details if isinstance(nested_result_details, Mapping) else None,
            nested_result_structured if isinstance(nested_result_structured, Mapping) else None,
        )
        if isinstance(source, Mapping)
    ]

    exception_details = external_exception_details(exception) if exception is not None else None
    if isinstance(exception_details, dict):
        # rawResult is preserved once at the canonical object root.
        exception_details.pop("rawResult", None)
    exception_code = (
        str(exception_details.get("errorCode") or "")
        if isinstance(exception_details, Mapping)
        else ""
    )

    source_error = _first_present(sources, "error", "message", "reason")
    error_value = source_error if source_error is not _UNSET else error
    if error_value is _UNSET or error_value in (None, ""):
        error_value = str(exception or "External tool request was rejected.")

    source_code = _first_present(sources, "errorCode", "code")
    source_code_value = "" if source_code is _UNSET else str(source_code)
    if source_code_value in {"external_tool_rejected", "agent_gateway_rejected"}:
        source_code_value = ""
    resolved_code = str(
        source_code_value
        or error_code
        or exception_code
        or "external_tool_rejected"
    )
    source_layer = _first_present(sources, "failureLayer")
    source_phase = _first_present(sources, "failurePhase", "phase")
    source_routing = _first_bool(sources, "toolRoutingStarted")

    resolved_mutation = (
        _first_bool(sources, "mutationStarted")
        if mutation_started is _UNSET
        else mutation_started
    )
    resolved_committed = (
        _first_bool(sources, "committed") if committed is _UNSET else committed
    )
    resolved_retryable = (
        _first_bool(sources, "retryable") if retryable is _UNSET else retryable
    )
    resolved_checkpoint_recovery = (
        _first_bool(sources, "checkpointRecoveryRequired")
        if checkpoint_recovery_required is _UNSET
        else checkpoint_recovery_required
    )
    resolved_temporary_cleanup = (
        _first_bool(sources, "temporaryCleanupRequired")
        if temporary_cleanup_required is _UNSET
        else temporary_cleanup_required
    )

    source_commit_state = _first_commit_state(sources)
    resolved_commit_state = str(
        commit_state or source_commit_state
    ).strip().casefold()
    if resolved_commit_state == "committed":
        resolved_commit_state = "complete"
    resolved_mutation_bool = _optional_bool(resolved_mutation)
    resolved_committed_bool = _optional_bool(resolved_committed)
    if resolved_commit_state not in _KNOWN_COMMIT_STATES:
        resolved_commit_state = (
            "not_started"
            if resolved_mutation_bool is False and resolved_committed_bool is False
            else "unknown"
        )

    source_console = _first_present(sources, "console")
    source_console = source_console if isinstance(source_console, Mapping) else {}
    before = (
        dict(console_before)
        if isinstance(console_before, Mapping)
        else dict(source_console.get("before"))
        if isinstance(source_console.get("before"), Mapping)
        else {}
    )
    after = (
        dict(console_after)
        if isinstance(console_after, Mapping)
        else dict(source_console.get("after"))
        if isinstance(source_console.get("after"), Mapping)
        else {}
    )

    source_tool = _first_present(sources, "tool", "toolName")
    source_checkpoint_id = _first_present(sources, "checkpointId")
    source_recovery_id = _first_present(sources, "recoveryId")
    source_details = _first_present(sources, "details")
    merged_details = dict(source_details) if isinstance(source_details, Mapping) else {}
    if isinstance(details, Mapping):
        merged_details.update(details)

    source_layer_value = "" if source_layer is _UNSET else str(source_layer)
    if source_layer_value == "unknown":
        source_layer_value = ""
    result: dict[str, Any] = {
        "schema": EXTERNAL_TOOL_ERROR_SCHEMA,
        "tool": str(tool or ("" if source_tool is _UNSET else source_tool) or ""),
        "operationKind": str(operation_kind or "unknown"),
        "errorCode": resolved_code,
        "error": dict(error_value) if isinstance(error_value, Mapping) else str(error_value),
        "failureLayer": str(
            source_layer_value or failure_layer
            or "unknown"
        ),
        "failurePhase": str(
            ("" if source_phase is _UNSET else source_phase) or failure_phase
        ),
        "toolRoutingStarted": (
            tool_routing_started
            if isinstance(tool_routing_started, bool)
            else _optional_bool(source_routing)
        ),
        "mutationStarted": resolved_mutation_bool,
        "committed": resolved_committed_bool,
        "commitState": resolved_commit_state,
        "commitStateKnown": resolved_commit_state != "unknown",
        "retryable": _optional_bool(resolved_retryable),
        "checkpointRecoveryRequired": (
            _optional_bool(resolved_checkpoint_recovery)
            if resolved_checkpoint_recovery is not _UNSET
            else None
        ),
        "temporaryCleanupRequired": (
            _optional_bool(resolved_temporary_cleanup)
            if resolved_temporary_cleanup is not _UNSET
            else None
        ),
        "checkpointId": str(
            checkpoint_id
            or ("" if source_checkpoint_id is _UNSET else source_checkpoint_id)
            or ""
        ),
        "recoveryId": str(
            recovery_id
            or ("" if source_recovery_id is _UNSET else source_recovery_id)
            or ""
        ),
        "console": {"before": before, "after": after},
        "details": merged_details,
    }
    if exception_details is not None:
        result["exception"] = exception_details
    if raw:
        result["rawResult"] = raw
    return result


def external_write_failure_view(error_object: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility view derived from the canonical external error object."""

    keys = (
        "failureLayer",
        "failurePhase",
        "errorCode",
        "error",
        "toolRoutingStarted",
        "mutationStarted",
        "committed",
        "commitState",
        "commitStateKnown",
        "checkpointRecoveryRequired",
        "temporaryCleanupRequired",
        "checkpointId",
        "recoveryId",
        "console",
    )
    result = {"schema": WRITE_FAILURE_SCHEMA, **{key: error_object.get(key) for key in keys}}
    retryable = error_object.get("retryable")
    if isinstance(retryable, bool):
        result["retryable"] = retryable
    return result
