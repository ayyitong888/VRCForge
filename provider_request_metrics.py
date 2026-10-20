"""Request-scoped, content-free SDK payload evidence; no store or transport."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Mapping

from provider_request_ledger import (
    LEDGER_SCHEMA as _LEDGER_SCHEMA,
    ProviderRequestLedger,
    classify_request_phases,
    current_scope,
    safe_context as _safe_context,
    safe_id as _safe_id,
    safe_tool_calls as _safe_tool_calls,
    usage_row as _usage_row,
)


_SERVICE_TIERS = frozenset(
    {"auto", "default", "flex", "scale", "priority", "fast", "ultrafast"}
)
_APPROVED_TOP_LEVEL_FIELDS = (
    "model", "input", "tools", "instructions", "include", "store", "stream",
    "reasoning", "text", "prompt_cache_key", "extra_headers",
)
_INPUT_ROLES = ("developer", "system", "user", "assistant", "tool", "unknown")
_INPUT_TYPES = (
    "message", "function_call", "function_call_output", "reasoning",
    "input_text", "input_image", "unknown",
)
_MAX_ITEMS = 4096


def _normalize_service_tier(value: Any) -> str | None:
    if isinstance(value, str) and value in _SERVICE_TIERS:
        return value
    return None


def _serialize(value: Any) -> tuple[str, bool]:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False), True
    except (TypeError, ValueError, OverflowError):
        # Do not include repr(value): malformed values may contain credentials or paths.
        return '"[unserializable]"', False


def _digest(value: Any) -> dict[str, Any]:
    encoded, exact = _serialize(value)
    raw = encoded.encode("utf-8")
    return {
        "chars": len(encoded),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "exact": exact,
    }


def _bucket() -> dict[str, int]:
    return {"count": 0, "chars": 0, "bytes": 0, "contentChars": 0, "contentBytes": 0}


def _add_bucket(bucket: dict[str, int], item: Any, content: Any) -> None:
    encoded, _ = _serialize(item)
    content_encoded, _ = _serialize(content)
    bucket["count"] += 1
    bucket["chars"] += len(encoded)
    bucket["bytes"] += len(encoded.encode("utf-8"))
    bucket["contentChars"] += len(content_encoded)
    bucket["contentBytes"] += len(content_encoded.encode("utf-8"))


def _normalized_label(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed[:-1] else allowed[-1]


def _input_shape(items: list[Any]) -> dict[str, Any]:
    role_buckets = {name: _bucket() for name in _INPUT_ROLES}
    type_buckets = {name: _bucket() for name in _INPUT_TYPES}
    for item in items:
        if isinstance(item, Mapping):
            role = _normalized_label(item.get("role"), _INPUT_ROLES)
            item_type = _normalized_label(item.get("type"), _INPUT_TYPES)
            content = item.get("content")
        else:
            role = "unknown"
            item_type = "unknown"
            content = None
        _add_bucket(role_buckets[role], item, content)
        _add_bucket(type_buckets[item_type], item, content)
    return {
        "itemCount": len(items),
        "coverageComplete": len(items) <= _MAX_ITEMS,
        # roleBuckets and typeBuckets are independent partitions; do not add them together.
        "roleBuckets": role_buckets,
        "typeBuckets": type_buckets,
    }


def _tool_leaf_names(value: Any, names: list[str], state: dict[str, bool]) -> None:
    if state["truncated"]:
        return
    if isinstance(value, list):
        for item in value:
            _tool_leaf_names(item, names, state)
            if state["truncated"]:
                return
        return
    if not isinstance(value, Mapping):
        return

    functions = value.get("functions")
    if isinstance(functions, list):
        for item in functions:
            _tool_leaf_names(item, names, state)
            if state["truncated"]:
                return
        return

    nested_function = value.get("function")
    if isinstance(nested_function, Mapping):
        _tool_leaf_names(nested_function, names, state)
        return

    name = value.get("name")
    is_leaf = value.get("type") == "function" or (
        isinstance(name, str) and any(key in value for key in ("description", "parameters", "strict"))
    )
    if not is_leaf:
        for key in ("tools", "items", "definitions"):
            if key in value:
                _tool_leaf_names(value[key], names, state)
                if state["truncated"]:
                    return
        return

    if len(names) >= _MAX_ITEMS:
        state["truncated"] = True
        return
    names.append(name if isinstance(name, str) else "")


def _tool_shape(tools: Any) -> dict[str, Any]:
    top_level_count = len(tools) if isinstance(tools, list) else (1 if tools is not None else 0)
    names: list[str] = []
    state = {"truncated": False}
    _tool_leaf_names(tools, names, state)
    encoded_names, exact = _serialize(names)
    raw_names = encoded_names.encode("utf-8")
    return {
        "topLevelCount": top_level_count,
        "leafDefinitionCount": len(names),
        "coverageComplete": not state["truncated"] and exact,
        "nameList": {
            "count": len(names),
            "chars": len(encoded_names),
            "bytes": len(raw_names),
            "sha256": hashlib.sha256(raw_names).hexdigest(),
            "exact": exact,
        },
    }


def _top_level_shape(payload: Mapping[str, Any], serialized: str, serialized_exact: bool) -> dict[str, Any]:
    grouped: dict[str, dict[str, int]] = {}
    field_chars = field_bytes = 0
    for key, value in payload.items():
        key_name = key if isinstance(key, str) and key in _APPROVED_TOP_LEVEL_FIELDS else "other"
        encoded_value, exact = _serialize(value)
        encoded_key, key_exact = _serialize(str(key))
        field = f"{encoded_key}:{encoded_value}"
        raw_field = field.encode("utf-8")
        current = grouped.setdefault(key_name, {"count": 0, "chars": 0, "bytes": 0, "exact": 1})
        current["count"] += 1
        current["chars"] += len(field)
        current["bytes"] += len(raw_field)
        current["exact"] = int(bool(current["exact"] and exact and key_exact))
        field_chars += len(field)
        field_bytes += len(raw_field)
    overhead_chars = len(serialized) - field_chars
    overhead_bytes = len(serialized.encode("utf-8")) - field_bytes
    fields = {key: grouped[key] for key in _APPROVED_TOP_LEVEL_FIELDS if key in grouped}
    if "other" in grouped:
        fields["other"] = grouped["other"]
    return {
        "serializedChars": len(serialized),
        "serializedBytes": len(serialized.encode("utf-8")),
        "fieldChars": field_chars,
        "fieldBytes": field_bytes,
        "overhead": {"chars": overhead_chars, "bytes": overhead_bytes},
        "fields": fields,
        "exact": serialized_exact and overhead_chars >= 0 and overhead_bytes >= 0,
    }


def payload_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Measure the final SDK logical JSON request, never HTTP bytes or token LCP."""
    items = payload.get("input", [])
    if not isinstance(items, list):
        items = [items]
    header_value = payload.get("extra_headers")
    headers = header_value if isinstance(header_value, Mapping) else {}
    identity = {key: headers[key] for key in ("session-id", "thread-id") if key in headers}
    shape = {key: payload[key] for key in ("model", "tools", "instructions", "include", "store", "stream", "reasoning", "text", "prompt_cache_key") if key in payload}
    serialized, serialized_exact = _serialize(payload)
    return {
        "measurement": "sdk_payload",
        "serializedFormat": "compact_json_utf8",
        "serializedRequest": {
            "chars": len(serialized),
            "bytes": len(serialized.encode("utf-8")),
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "exact": serialized_exact,
        },
        "topLevel": _top_level_shape(payload, serialized, serialized_exact),
        "requestShape": _digest(shape),
        "identity": _digest(identity),
        "tools": _digest(payload.get("tools", [])),
        "toolsShape": _tool_shape(payload.get("tools", [])),
        "instructions": _digest(payload.get("instructions")),
        "input": _digest(items),
        "inputCount": len(items),
        "itemCoverageComplete": len(items) <= 4096,
        "inputShape": _input_shape(items),
        "items": [_digest(item) for item in items[:4096]],
    }


def compare_prefix(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Offline item-prefix evidence; it is not provider cache-token accounting."""
    for key in ("requestShape", "identity"):
        if previous[key]["sha256"] != current[key]["sha256"]:
            return {"comparable": False, "resetReason": key, "commonItems": 0, "commonBytes": 0}
    count = size = 0
    for left, right in zip(previous["items"], current["items"]):
        if left["sha256"] != right["sha256"]:
            break
        count += 1
        size += right["bytes"]
    return {"comparable": True, "commonItems": count, "commonBytes": size,
            "coverageComplete": previous["itemCoverageComplete"] and current["itemCoverageComplete"]}


class RequestMetrics:
    """Owned by one physical SDK create attempt; callbacks cannot fail it."""
    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None],
        payload: Mapping[str, Any],
    ):
        self.callback = callback
        scope = current_scope()
        self.context = _safe_context(scope.get("context"))
        request_ordinal = scope.get("requestOrdinal")
        if type(request_ordinal) is int and request_ordinal > 0:
            self.context.setdefault("requestOrdinal", request_ordinal)
        self.ledger = scope.get("ledger")
        self.request_id = _safe_id(self.context.get("localDispatchId"), 180) or uuid.uuid4().hex
        self.row_id = f"prq_{self.request_id}"
        self.started = time.monotonic()
        self.provider_request_id = _safe_id(self.context.get("providerRequestId"), 180)
        self._start_payload = payload_metrics(payload)
        self.finished = False
        try:
            self._emit("start", payloadMetrics=self._start_payload)
        except Exception:
            self._emit("start", telemetryMissing=True)

    def _emit(self, phase: str, **data: Any) -> None:
        event = {"kind": "provider_request_metrics", "requestId": self.request_id,
                 "localDispatchId": self.request_id, "requestPhase": phase, **self.context, **data}
        if self.provider_request_id:
            event["providerRequestId"] = self.provider_request_id
        try:
            self.callback(event)
        except Exception:
            pass
        if self.ledger is not None:
            try:
                row = {
                    "schema": _LEDGER_SCHEMA,
                    "rowId": self.row_id,
                    "eventId": self.ledger.event_id(self.row_id, phase),
                    "requestId": self.provider_request_id or self.request_id,
                    "localDispatchId": self.request_id,
                    "requestPhase": phase,
                    "status": data.get("status", "started" if phase == "start" else "unknown"),
                    **self.context,
                }
                if self.provider_request_id:
                    row["providerRequestId"] = self.provider_request_id
                for key in ("payloadMetrics", "usage", "usagePresent", "serviceTier", "failureClass", "latencyMs", "toolCalls", "phases", "primaryPhase", "phaseUnknown", "output"):
                    if key in data:
                        row[key] = data[key]
                if phase == "terminal":
                    row["tokenUsage"] = _usage_row(data.get("usage"))
                self.ledger.append(row)
            except Exception:
                # Ledger failure is diagnostic-only and must never alter the request.
                pass

    def finish(
        self,
        usage: Mapping[str, Any] | None,
        *,
        success: bool,
        error: str = "",
        service_tier: Any = None,
        provider_request_id: Any = None,
        tool_calls: Any = None,
        phases: list[str] | tuple[str, ...] | None = None,
        primary_phase: str | None = None,
        output: Mapping[str, Any] | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        usage = usage if isinstance(usage, Mapping) else {}
        safe = {key: value if type(value) is int and value >= 0 else None
                for key, value in ((key, usage.get(key)) for key in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"))}
        detail_sets = [
            usage.get("input_tokens_details"),
            usage.get("prompt_tokens_details"),
        ]
        for key in ("cached_tokens", "cache_write_tokens"):
            value = None
            for details in detail_sets:
                candidate = details.get(key) if isinstance(details, Mapping) else None
                if type(candidate) is int and candidate >= 0:
                    value = candidate
                    break
            safe[key] = value
        self.provider_request_id = _safe_id(provider_request_id, 180) or self.provider_request_id
        normalized_calls = _safe_tool_calls(tool_calls)
        tool_metadata = self.context.get("toolMetadata")
        if isinstance(tool_metadata, Mapping):
            for call in normalized_calls:
                metadata = tool_metadata.get(call.get("name"))
                if isinstance(metadata, Mapping):
                    for key in ("permission", "effect", "actionKind", "intent", "stage"):
                        value = metadata.get(key)
                        if isinstance(value, str) and value and key not in call:
                            call[key] = value
        normalized_phases, inferred_primary = classify_request_phases(normalized_calls, {
            **self.context,
            "phases": list(phases or ()),
            "primaryPhase": primary_phase,
        })
        primary_phase = inferred_primary
        phase_unknown = "unknown" in normalized_phases
        self._emit("terminal", status="completed" if success else "failed", failureClass=error,
                   latencyMs=round((time.monotonic()-self.started)*1000), usage=safe,
                   serviceTier=_normalize_service_tier(service_tier),
                   usagePresent=any(value is not None for value in safe.values()),
                   toolCalls=normalized_calls,
                   phases=normalized_phases,
                   phaseUnknown=phase_unknown,
                   primaryPhase=primary_phase,
                   output=dict(output) if isinstance(output, Mapping) else {})
