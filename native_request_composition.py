"""Content-free composition measurements for one native planner request.

This module deliberately measures the host-built request shape without making
any authorization decision from user supplied text.  ``origin`` is a private
sidecar created by the native request builder; generated message digests are
the only way runtime-context messages are classified.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA = "vrcforge.native_request_composition.v1"
_ROLES = ("user", "assistant", "tool", "system")
_TOOL_CALL_ALIASES = {
    "vrcforge_load_internal_tool_block": "vrcforge_load_internal_tool_block",
    "load_internal_tool_block": "vrcforge_load_internal_tool_block",
    "vrcforge_read_tool_result": "vrcforge_read_tool_result",
    "read_tool_result": "vrcforge_read_tool_result",
}


class NativePlannerRequest(dict):
    """Dict-compatible request with non-enumerable local diagnostics.

    Keeping diagnostics outside the mapping preserves the established native
    request shape while allowing the runtime provider worker to read the
    sidecar through ``request.get``.  JSON serialization and iteration expose
    only instructions/messages/tools.
    """

    def __init__(self, value: Mapping[str, Any], *, origin: Mapping[str, Any] | None = None,
                 composition: Mapping[str, Any] | None = None,
                 provider_turn_state: dict[str, str] | None = None) -> None:
        super().__init__(value)
        self._diagnostic_origin = dict(origin or {})
        self._diagnostic_composition = dict(composition or {})
        self._provider_turn_state = provider_turn_state

    def get(self, key: str, default: Any = None) -> Any:  # noqa: D401 - dict compatibility
        if key == "_providerTurnState":
            return self._provider_turn_state if self._provider_turn_state is not None else default
        if key == "_diagnosticOrigin":
            return self._diagnostic_origin if self._diagnostic_origin else default
        if key == "_diagnosticComposition":
            return self._diagnostic_composition if self._diagnostic_composition else default
        return super().get(key, default)


def copy_native_request(request: Mapping[str, Any], updates: Mapping[str, Any]) -> NativePlannerRequest:
    """Copy a request while retaining its non-enumerable diagnostic origin."""
    return NativePlannerRequest(
        {**dict(request), **dict(updates)},
        origin=request.get("_diagnosticOrigin") if isinstance(request, Mapping) else None,
        composition=request.get("_diagnosticComposition") if isinstance(request, Mapping) else None,
        provider_turn_state=request.get("_providerTurnState"),
    )


def _projection(value: object) -> object:
    if isinstance(value, str):
        if value.startswith("data:image/") and "," in value:
            return value.split(",", 1)[0] + ",<image-payload-omitted>"
        return value
    if isinstance(value, list):
        return [_projection(item) for item in value]
    if isinstance(value, Mapping):
        # Diagnostics live on NativePlannerRequest attributes, so every real
        # mapping key (including a tool-schema key beginning with an
        # underscore) remains part of the measurement projection.
        return {key: _projection(item) for key, item in value.items()}
    return value


def _encoded(value: object) -> str:
    return json.dumps(_projection(value), ensure_ascii=False, separators=(",", ":"))


def _contains_image_payload(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith("data:image/") and "," in value
    if isinstance(value, list):
        return any(_contains_image_payload(item) for item in value)
    if isinstance(value, Mapping):
        return any(_contains_image_payload(item) for item in value.values())
    return False


def _metrics(value: object) -> dict[str, Any]:
    encoded = _encoded(value) if not isinstance(value, str) else value
    raw = encoded.encode("utf-8")
    return {"chars": len(encoded), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _aggregate(values: Sequence[object]) -> dict[str, Any]:
    rows = [_metrics(value) for value in values]
    raw = b"".join(bytes.fromhex(row["sha256"]) for row in rows)
    return {
        "count": len(rows),
        "chars": sum(row["chars"] for row in rows),
        "bytes": sum(row["bytes"] for row in rows),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _message_digest(message: Mapping[str, Any]) -> str:
    return _metrics(dict(message))["sha256"]


def composition_metrics(value: object) -> dict[str, Any]:
    """Expose only the safe scalar measurement used by the request builder."""
    return _metrics(value)


def generated_message_digest(message: Mapping[str, Any]) -> str:
    """Digest one host-generated message for the private provenance sidecar."""
    return _message_digest(message)


def result_page_ledger(descriptor: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project page coordinates without retaining page values."""
    descriptor = descriptor if isinstance(descriptor, Mapping) else {}
    page = descriptor.get("page") if isinstance(descriptor.get("page"), Mapping) else descriptor
    ref = page.get("resultRef") or descriptor.get("resultRef") or ""
    pointer = page.get("jsonPointer") or descriptor.get("jsonPointer") or ""
    items = page.get("items") if isinstance(page.get("items"), list) else []
    reported_returned_chars = page.get("returnedChars")
    if type(reported_returned_chars) is not int and isinstance(descriptor, Mapping):
        reported_returned_chars = descriptor.get("returnedChars")
    if type(reported_returned_chars) is int and reported_returned_chars >= 0:
        returned_chars = reported_returned_chars
    elif items and all(isinstance(item, Mapping) and isinstance(item.get("value"), str) for item in items):
        returned_chars = sum(len(item["value"]) for item in items)
    else:
        # A metadata-only page does not prove how many characters were
        # returned.  Preserve that uncertainty instead of manufacturing 0.
        returned_chars = None
    return {
        "resultRefSha256": hashlib.sha256(str(ref).encode("utf-8")).hexdigest() if ref else None,
        "jsonPointerSha256": hashlib.sha256(str(pointer).encode("utf-8")).hexdigest(),
        "offset": page.get("offset") if type(page.get("offset")) is int and page["offset"] >= 0 else None,
        "returnedChars": returned_chars,
        "totalChars": page.get("textTotalChars") if type(page.get("textTotalChars")) is int and page["textTotalChars"] >= 0 else None,
        "hasMore": page.get("hasMore") if type(page.get("hasMore")) is bool else None,
        "returnedItems": page.get("returnedItems") if type(page.get("returnedItems")) is int and page["returnedItems"] >= 0 else None,
    }


def _safe_origin_digests(origin: Mapping[str, Any] | None) -> dict[str, str]:
    raw = origin if isinstance(origin, Mapping) else {}
    values = raw.get("generatedMessageDigests")
    if not isinstance(values, Mapping):
        return {}
    return {
        str(kind): str(digest)
        for kind, digest in values.items()
        if isinstance(kind, str) and isinstance(digest, str) and len(digest) == 64
    }


def _safe_origin_positions(origin: Mapping[str, Any] | None) -> dict[str, int]:
    raw = origin if isinstance(origin, Mapping) else {}
    values = raw.get("generatedMessagePositions")
    if not isinstance(values, Mapping):
        return {}
    return {
        str(kind): value
        for kind, value in values.items()
        if isinstance(kind, str) and type(value) is int and value >= 0
    }


def _origin_metric(origin: Mapping[str, Any] | None, key: str) -> dict[str, Any] | None:
    value = (origin or {}).get(key) if isinstance(origin, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else None


def _paired_tool_results(
    messages: Sequence[object], names: frozenset[str],
) -> tuple[list[tuple[str, Mapping[str, Any]]], bool]:
    """Return structured tool results paired with their assistant call name."""
    calls: dict[str, str] = {}
    unknown = False
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function") if isinstance(call.get("function"), Mapping) else call
            raw_name = function.get("name") if isinstance(function, Mapping) else None
            if raw_name == "call_tool":
                arguments = function.get("arguments") if isinstance(function, Mapping) else None
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (TypeError, ValueError):
                    arguments = None
                raw_name = arguments.get("tool") if isinstance(arguments, Mapping) else None
                if not isinstance(raw_name, str):
                    unknown = True
                    continue
            name = _TOOL_CALL_ALIASES.get(raw_name) if isinstance(raw_name, str) else None
            if name in names:
                if not isinstance(call.get("id"), str) or not call["id"]:
                    unknown = True
                    continue
                calls[call["id"]] = name
    paired: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        name = calls.get(call_id) if isinstance(call_id, str) else None
        if not name:
            continue
        seen.add(call_id)
        if not isinstance(message.get("content"), str):
            unknown = True
            continue
        try:
            payload = json.loads(message["content"])
        except (TypeError, ValueError):
            unknown = True
            continue
        if isinstance(payload, Mapping):
            paired.append((name, payload))
    unknown = unknown or bool(set(calls) - seen)
    return paired, unknown


def _history_loaded_definitions(messages: Sequence[object]) -> dict[str, Any]:
    pairs, pairing_unknown = _paired_tool_results(messages, frozenset({"vrcforge_load_internal_tool_block"}))
    definitions: list[Mapping[str, Any]] = []
    unknown = pairing_unknown
    for _, payload in pairs:
        observations = payload.get("observations")
        if not isinstance(observations, list):
            unknown = True
            continue
        found = False
        for observation in observations:
            if not isinstance(observation, Mapping) or "loadedToolDefinitions" not in observation:
                continue
            found = True
            rows = observation.get("loadedToolDefinitions")
            if not isinstance(rows, list):
                unknown = True
                continue
            for row in rows:
                if (isinstance(row, Mapping)
                        and isinstance(row.get("name"), str)
                        and isinstance(row.get("parameters"), Mapping)):
                    definitions.append(row)
                else:
                    unknown = True
        if not found:
            unknown = True
    if not pairs:
        result = {"count": 0, "chars": 0, "bytes": 0,
                  "sha256": hashlib.sha256(b"").hexdigest(), "complete": not unknown}
        if unknown:
            result["knownCount"] = 0
            result["unknown"] = True
        return result
    result = _aggregate(definitions)
    result["complete"] = not unknown
    if unknown:
        result["knownCount"] = result["count"]
        result["unknown"] = True
    return result


def _history_result_pages(messages: Sequence[object]) -> tuple[list[dict[str, Any]], bool]:
    pages: list[dict[str, Any]] = []
    pairs, pairing_unknown = _paired_tool_results(messages, frozenset({"vrcforge_read_tool_result"}))
    unknown = pairing_unknown
    for _, payload in pairs:
        observations = payload.get("observations")
        if not isinstance(observations, list):
            unknown = True
            continue
        found = False
        for observation in observations:
            text = observation.get("observation") if isinstance(observation, Mapping) else None
            if not isinstance(text, str) or "retainedResultPage=" not in text:
                continue
            found = True
            try:
                page = json.loads(text.rsplit("retainedResultPage=", 1)[1])
            except (TypeError, ValueError):
                unknown = True
                continue
            if isinstance(page, Mapping):
                pages.append(result_page_ledger(page))
            else:
                unknown = True
        if not found:
            unknown = True
    return pages, unknown


def build_native_request_composition(
    request: Mapping[str, Any],
    *,
    origin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, non-secret composition ledger for ``request``.

    The three transport children (instructions/messages/tools) are disjoint
    top-level measurements.  Runtime context, state, definitions, pages and
    recovery are nested provenance details and are intentionally excluded from
    the top-level byte sum.
    """
    projected = dict(request)
    instructions = projected.get("instructions", "")
    messages = projected.get("messages", [])
    tools = projected.get("tools", [])
    if not isinstance(messages, list):
        messages = []
    if not isinstance(tools, list):
        tools = []
    loaded_definitions = _history_loaded_definitions(messages)
    history_pages, history_pages_unknown = _history_result_pages(messages)

    generated = _safe_origin_digests(origin)
    positions = _safe_origin_positions(origin)
    generated_messages: dict[str, list[Mapping[str, Any]]] = {kind: [] for kind in ("runtimeContext", "runtimeState")}
    ordinary_messages: list[Mapping[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        digest = _message_digest(message)
        kind = next((candidate for candidate in generated_messages
                     if generated.get(candidate) == digest and positions.get(candidate) == index), None)
        if kind is None:
            ordinary_messages.append(message)
        else:
            generated_messages[kind].append(message)

    role_rows: dict[str, list[Mapping[str, Any]]] = {role: [] for role in _ROLES}
    for message in ordinary_messages:
        role = message.get("role")
        role_rows[role if isinstance(role, str) and role in role_rows else "system"].append(message)
    history_by_role = {
        role: {**_aggregate(rows), "count": len(rows)}
        for role, rows in role_rows.items()
    }

    message_array = _metrics(messages)
    message_children = _aggregate([dict(message) for message in ordinary_messages])
    context_children = _aggregate([dict(message) for message in generated_messages["runtimeContext"]])
    state_children = _aggregate([dict(message) for message in generated_messages["runtimeState"]])
    message_overhead = {
        "chars": message_array["chars"] - message_children["chars"] - context_children["chars"] - state_children["chars"],
        "bytes": message_array["bytes"] - message_children["bytes"] - context_children["bytes"] - state_children["bytes"],
    }
    instruction_metric = _metrics(instructions)
    tools_metric = _metrics(tools)
    serialized_metric = _metrics(projected)
    request_overhead = {
        "chars": serialized_metric["chars"] - instruction_metric["chars"] - message_array["chars"] - tools_metric["chars"],
        "bytes": serialized_metric["bytes"] - instruction_metric["bytes"] - message_array["bytes"] - tools_metric["bytes"],
    }

    resident = _origin_metric(origin, "residentDefinitions") or {"count": 0, "chars": 0, "bytes": 0}
    activated = _origin_metric(origin, "activatedDefinitions") or {"count": 0, "chars": 0, "bytes": 0}
    state_values = [dict(message) for message in generated_messages["runtimeState"]]
    recovery_values: list[object] = []
    for state_message in state_values:
        content = state_message.get("content")
        if isinstance(content, str) and content.startswith("Current runtime state (data): "):
            try:
                state = json.loads(content.split(": ", 1)[1])
            except (TypeError, ValueError):
                state = {}
            if isinstance(state, Mapping) and isinstance(state.get("compactionRecoveryInformation"), list):
                recovery_values.extend(state["compactionRecoveryInformation"])

    origin_pages = list((origin or {}).get("resultPages", [])) if isinstance(origin, Mapping) else []
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "measurementSemantics": {
            "nativeRequest": "serialized native projection",
            "imagePayloadsOmitted": _contains_image_payload(projected),
            "finalSdkLogicalPayload": "provided by provider_request_metrics",
        },
        "instructions": instruction_metric,
        "messages": message_array,
        "nativeHistoryByRole": history_by_role,
        "runtimeContextSnapshot": context_children,
        "runtimeState": state_children,
        "residentDefinitions": resident,
        "activatedDefinitions": activated,
        "loadedToolDefinitions": loaded_definitions,
        "tools": {**tools_metric, "definitionCount": len(tools)},
        "messageArrayOverhead": message_overhead,
        "requestOverhead": request_overhead,
        "serializedNativeRequest": serialized_metric,
        "resultPages": [
            *origin_pages,
            *history_pages,
        ],
        "resultPagesComplete": not history_pages_unknown,
        "compactionRecovery": {**_aggregate(recovery_values), "count": len(recovery_values)},
    }
    return result


def attach_native_request_composition(
    request: Mapping[str, Any],
    *,
    origin: Mapping[str, Any] | None = None,
)-> NativePlannerRequest:
    """Attach diagnostics without changing the provider-facing three fields."""
    effective_origin = origin if origin is not None else request.get("_diagnosticOrigin")
    return NativePlannerRequest(
        dict(request),
        origin=effective_origin,
        composition=build_native_request_composition(request, origin=effective_origin),
        provider_turn_state=request.get("_providerTurnState"),
    )


def attach_native_request_origin(
    request: Mapping[str, Any], *, origin: Mapping[str, Any] | None = None,
) -> NativePlannerRequest:
    """Attach only construction provenance, without measuring yet."""
    return NativePlannerRequest(dict(request), origin=origin,
                                provider_turn_state=request.get("_providerTurnState"))
