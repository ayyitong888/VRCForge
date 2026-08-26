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
_GENERIC_ERROR_CODES = frozenset(
    {
        "agent_gateway_rejected",
        "external_tool_rejected",
        "external_bridge_error",
        "unity_core_tool_rejected",
        "wrapper_unknown",
    }
)


def _source_error_code(source: Mapping[str, Any]) -> str:
    return str(
        source.get("errorCode")
        or source.get("causeCode")
        or source.get("code")
        or ""
    ).strip()


def _source_error_message(source: Mapping[str, Any]) -> str:
    for key in ("error", "message", "reason"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _result_source_priority(source: Mapping[str, Any]) -> int:
    """Rank exact domain errors ahead of generic transport/wrapper summaries."""

    code = _source_error_code(source).casefold()
    layer = str(source.get("failureLayer") or "").strip().casefold()
    phase = str(source.get("failurePhase") or "").strip().casefold()
    schema = str(source.get("schema") or "").strip()
    precise_code = bool(code and code not in _GENERIC_ERROR_CODES)
    precise_layer = bool(layer and layer != "unknown")
    precise_cause = False
    for key in ("failureCause", "rootCause"):
        nested_cause = source.get(key)
        if isinstance(nested_cause, Mapping):
            nested_code = _source_error_code(nested_cause).casefold()
            nested_layer = str(
                nested_cause.get("failureLayer")
                or nested_cause.get("layer")
                or ""
            ).strip().casefold()
            nested_phase = str(
                nested_cause.get("failurePhase")
                or nested_cause.get("phase")
                or ""
            ).strip().casefold()
            if (
                (nested_code and nested_code not in _GENERIC_ERROR_CODES)
                or (nested_layer and nested_layer != "unknown")
                or (nested_phase and "wrapper" not in nested_phase)
            ):
                precise_cause = True
                break
        elif nested_cause not in (None, "", [], {}):
            precise_cause = True
            break
    if schema == EXTERNAL_TOOL_ERROR_SCHEMA and (precise_code or precise_layer or precise_cause):
        return 0
    if precise_code or precise_layer or precise_cause:
        return 1
    if code or layer == "unknown" or "wrapper" in phase:
        return 3
    return 2


def prioritize_result_sources(sources: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return a stable exact-cause-first view without discarding wrappers."""

    return sorted(sources, key=_result_source_priority)


def _bounded_contract_value(value: Any, *, depth: int = 0) -> Any:
    """Keep shared result facts structured without copying arbitrary dumps."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.strip()[:600]
    if depth >= 3:
        return "[bounded]"
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded_contract_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_contract_value(item, depth=depth + 1) for item in value[:24]]
    return str(value)[:600]


def canonical_result_facts(
    sources: list[Mapping[str, Any]], *, success: bool | None = None, status: str | None = None
) -> dict[str, Any]:
    """Project the shared result/cause fields for internal and external agents."""

    expanded_sources: list[Mapping[str, Any]] = []
    for source in sources:
        expanded_sources.append(source)
        details = source.get("details")
        if isinstance(details, Mapping):
            expanded_sources.append(details)
    expanded_sources = prioritize_result_sources(expanded_sources)

    def first(*names: str) -> Any:
        for source in expanded_sources:
            for name in names:
                if name not in source:
                    continue
                value = source[name]
                # An unknown alias at a higher-priority boundary must not
                # hide an exact sibling fact from the structured domain result.
                if value in (None, ""):
                    continue
                return _bounded_contract_value(value)
        return None

    failed = success is False or status == "failed"
    readiness_blocked = first("ready") is False
    cause_relevant = failed or readiness_blocked
    cause = first("failureCause", "cause") if cause_relevant else None
    if isinstance(cause, Mapping):
        cause = {
            key: _bounded_contract_value(cause[key])
            for key in (
                "code", "errorCode", "causeCode", "message", "error", "reason",
                "failureLayer", "layer", "failurePhase", "phase", "category", "type",
                "rootCause", "causes",
            )
            if key in cause and cause[key] not in (None, "")
        }
    if cause_relevant and cause is None:
        cause_fields = {
            key: first(*names)
            for key, names in {
                "code": ("errorCode", "causeCode", "code"),
                "message": ("error", "message", "reason"),
                # Generic Unity payloads legitimately use `layer` for a
                # GameObject layer and `phase` for domain state. Only the
                # explicit failure names are safe at the shared top level.
                "failureLayer": ("failureLayer",),
                "failurePhase": ("failurePhase",),
            }.items()
        }
        cause_fields = {
            key: value
            for key, value in cause_fields.items()
            if value not in (None, "") and not isinstance(value, Mapping)
        }
        cause = {key: value for key, value in cause_fields.items() if value not in (None, "")}
    elif cause_relevant and not isinstance(cause, Mapping):
        cause = {"message": cause}

    facts: dict[str, Any] = {}
    if isinstance(success, bool):
        facts["success"] = success
        facts["status"] = "ok" if success else "failed"
    elif status in {"ok", "failed"}:
        facts["status"] = status
    cause_only_keys = {
        "errorCode",
        "failureLayer",
        "failurePhase",
        "failureCause",
        "rootCause",
        "causeChain",
    }
    for key, names in (
        ("ready", ("ready",)),
        ("blockingReasons", ("blockingReasons", "blocking_reasons")),
        ("errorCode", ("errorCode", "causeCode", "code")),
        ("failureLayer", ("failureLayer",)),
        ("failurePhase", ("failurePhase",)),
        ("failureCause", ("failureCause", "cause")),
        ("rootCause", ("rootCause", "root_cause")),
        ("observed", ("observed",)),
        ("expected", ("expected",)),
        ("delta", ("delta",)),
        ("evidence", ("evidence",)),
        ("causeChain", ("causeChain", "causes")),
        ("nextAction", ("nextAction", "nextActions")),
        ("recovery", ("recovery", "recoveryRequired", "checkpointRecoveryRequired")),
    ):
        if (
            key in cause_only_keys and not cause_relevant
        ):
            continue
        value = first(*names)
        if value not in (None, "", [], {}) and value != "unknown":
            facts[key] = value
    if cause and "failureCause" not in facts:
        facts["failureCause"] = _bounded_contract_value(cause)
    if cause and "rootCause" not in facts:
        root = cause.get("rootCause") if isinstance(cause, Mapping) else None
        facts["rootCause"] = root if root not in (None, "") else _bounded_contract_value(cause)
    if isinstance(cause, Mapping):
        for target, names in (
            ("errorCode", ("code", "errorCode", "causeCode")),
            ("failureLayer", ("failureLayer", "layer")),
            ("failurePhase", ("failurePhase", "phase")),
        ):
            if target in facts:
                continue
            for name in names:
                value = cause.get(name)
                if (
                    value not in (None, "")
                    and str(value).strip().casefold() != "unknown"
                ):
                    facts[target] = _bounded_contract_value(value)
                    break

    for key, names in (
        ("toolRoutingStarted", ("toolRoutingStarted",)),
        ("mutationStarted", ("mutationStarted",)),
        ("committed", ("committed",)),
    ):
        raw_value = _first_present(expanded_sources, *names)
        if raw_value is not _UNSET and (raw_value is None or isinstance(raw_value, bool)):
            facts[key] = raw_value

    for key in (
        "retryable",
        "checkpointRecoveryRequired",
        "temporaryCleanupRequired",
    ):
        raw_value = _first_present(expanded_sources, key)
        if isinstance(raw_value, bool):
            facts[key] = raw_value

    raw_commit_state = _first_present(expanded_sources, "commitState")
    commit_state = ""
    if raw_commit_state is not _UNSET:
        commit_state = str(raw_commit_state or "").strip().casefold()
        if commit_state == "committed":
            commit_state = "complete"
        if commit_state in _KNOWN_COMMIT_STATES:
            facts["commitState"] = commit_state
    if not commit_state and (
        "mutationStarted" in facts or "committed" in facts
    ):
        if facts.get("mutationStarted") is False and facts.get("committed") is False:
            commit_state = "not_started"
        else:
            commit_state = "unknown"
        facts["commitState"] = commit_state
    raw_commit_known = _first_present(expanded_sources, "commitStateKnown")
    if isinstance(raw_commit_known, bool):
        facts["commitStateKnown"] = raw_commit_known
    elif commit_state:
        facts["commitStateKnown"] = commit_state != "unknown"

    raw_safe_to_retry = _first_present(expanded_sources, "safeToRetry")
    if isinstance(raw_safe_to_retry, bool):
        facts["safeToRetry"] = raw_safe_to_retry
    if commit_state == "unknown":
        facts["commitStateKnown"] = False
        facts["safeToRetry"] = False
        facts.setdefault(
            "nextAction",
            "Read back the exact target state before retrying the write.",
        )
        facts.setdefault(
            "recovery",
            {
                "required": True,
                "reason": (
                    "Commit state is unknown; preserve the current state and read back "
                    "the exact target before any retry."
                ),
            },
        )

    precise_present = cause_relevant and any(
        _result_source_priority(source) <= 1 for source in expanded_sources
    )
    wrapper_traces: list[dict[str, Any]] = []
    if precise_present:
        for source in expanded_sources:
            code = _source_error_code(source)
            layer = str(source.get("failureLayer") or "").strip()
            phase = str(source.get("failurePhase") or "").strip()
            message = _source_error_message(source)
            is_wrapper = (
                code.casefold() in _GENERIC_ERROR_CODES
                or layer.casefold() == "unknown"
                or "wrapper" in phase.casefold()
            )
            if not is_wrapper or not any((code, layer, phase, message)):
                continue
            trace = {
                key: value
                for key, value in (
                    ("kind", "wrapper"),
                    ("code", code),
                    ("message", message),
                    ("failureLayer", layer),
                    ("failurePhase", phase),
                )
                if value
            }
            if trace not in wrapper_traces:
                wrapper_traces.append(trace)
    if wrapper_traces:
        existing_chain = facts.get("causeChain")
        chain = list(existing_chain) if isinstance(existing_chain, list) else []
        for trace in wrapper_traces:
            if trace not in chain:
                chain.append(trace)
        facts["causeChain"] = chain
    return facts


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
    nested_raw_result = raw.get("rawResult")
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
            nested_raw_result if isinstance(nested_raw_result, Mapping) else None,
            nested_result_details if isinstance(nested_result_details, Mapping) else None,
            nested_result_structured if isinstance(nested_result_structured, Mapping) else None,
        )
        if isinstance(source, Mapping)
    ]
    # Facts established by the current boundary must remain an authoritative
    # cause source even when an exception carries an older generic
    # agent_gateway_rejected external_error projection.
    boundary_source = {
        key: value
        for key, value in (
            ("errorCode", error_code),
            ("failureLayer", failure_layer),
            ("failurePhase", failure_phase),
            ("error", error),
        )
        if value not in (None, "")
    }
    # A boundary without an explicit code is only a transport fallback; do
    # not let its generic layer/message outrank an exact raw Core result.
    if error_code and failure_phase == "before_write_handler" and boundary_source:
        boundary_cause = {
            key: value
            for key, value in (
                ("code", error_code),
                ("message", error),
                ("failureLayer", failure_layer),
                ("failurePhase", failure_phase),
            )
            if value not in (None, "")
        }
        if boundary_cause:
            boundary_source["failureCause"] = boundary_cause
            boundary_source["rootCause"] = boundary_cause
        sources.append(boundary_source)

    exception_details = external_exception_details(exception) if exception is not None else None
    if isinstance(exception_details, dict):
        # rawResult is preserved once at the canonical object root.
        exception_details.pop("rawResult", None)
        # The transport exception is a traceable source, but an exact
        # structured Core rejection remains the authoritative domain cause.
        sources.append(exception_details)
    sources = prioritize_result_sources(sources)
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
        "success": False,
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
    resolved_contract_source: dict[str, Any] = {
        "mutationStarted": resolved_mutation_bool,
        "committed": resolved_committed_bool,
        "commitState": resolved_commit_state,
        "commitStateKnown": resolved_commit_state != "unknown",
    }
    if isinstance(resolved_retryable, bool):
        resolved_contract_source["safeToRetry"] = (
            False if resolved_commit_state == "unknown" else resolved_retryable
        )
    # Facts explicitly known by this boundary (for example
    # write_preparation/before_write_handler) participate in the same cause
    # ordering as nested payloads. This prevents an exception's generic
    # default from overwriting a more exact boundary diagnosis.
    shared_facts = canonical_result_facts(
        [result, *sources],
        success=False,
        status="failed",
    )
    resolved_write_facts = canonical_result_facts(
        [resolved_contract_source],
        success=False,
        status="failed",
    )
    for key in (
        "mutationStarted",
        "committed",
        "commitState",
        "commitStateKnown",
        "safeToRetry",
    ):
        if key in resolved_write_facts:
            shared_facts[key] = resolved_write_facts[key]
    if resolved_commit_state == "unknown":
        shared_facts.setdefault("nextAction", resolved_write_facts.get("nextAction"))
        shared_facts.setdefault("recovery", resolved_write_facts.get("recovery"))
    result.update(shared_facts)
    if exception_details is not None:
        result["exception"] = exception_details
    if raw:
        result["rawResult"] = raw
    return result


def external_write_failure_view(error_object: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility view derived from the canonical external error object."""

    keys = (
        "success",
        "status",
        "failureLayer",
        "failurePhase",
        "failureCause",
        "rootCause",
        "observed",
        "expected",
        "delta",
        "evidence",
        "causeChain",
        "nextAction",
        "recovery",
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
    safe_to_retry = error_object.get("safeToRetry")
    if isinstance(safe_to_retry, bool):
        result["safeToRetry"] = safe_to_retry
    return result
