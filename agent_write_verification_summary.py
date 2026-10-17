"""Project a bounded, privacy-safe summary of an authoritative FX write receipt.

The FX writer already emits a versioned persisted receipt.  This module only
projects that receipt; it does not call a tool, inspect a scan, or use approval
arguments as evidence that a write happened.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any


FX_WRITE_SCHEMA = "vrcforge.fx_animator_write.v1"
EVIDENCE_SOURCE = "persisted_authoritative_receipt"
MAX_SUMMARY_EDITS = 8
MAX_TEXT_BYTES = 1024
MAX_TARGET_TEXT_BYTES = 512

_STATE_SCALARS = (
    "speed",
    "writeDefaultValues",
    "timeParameterActive",
    "timeParameter",
    "motionPath",
)
_TARGET_FIELDS = ("layerName", "stateName", "sourceStateName", "destinationStateName")


def _bounded_text(value: Any, *, limit: int = MAX_TEXT_BYTES) -> str | None:
    """Return a bounded UTF-8 string, rejecting rather than splitting bytes."""

    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
        return None
    return value


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, Real):
        number = float(value)
        if math.isfinite(number):
            return value
        return None
    if isinstance(value, str):
        return _bounded_text(value, limit=MAX_TEXT_BYTES)
    return None


def _safe_target(value: Any, *, allow_layer_only: bool = False) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    target: dict[str, Any] = {}
    for key in _TARGET_FIELDS:
        if key not in value:
            continue
        text = _bounded_text(value[key], limit=MAX_TARGET_TEXT_BYTES)
        if text is None:
            return None
        target[key] = text
    if not target or not target.get("layerName"):
        return None
    if not allow_layer_only and not any(target.get(key) for key in _TARGET_FIELDS[1:]):
        return None
    return target


def _target_from_mapping(value: Any) -> dict[str, Any] | None:
    action = value.get("action") if isinstance(value, Mapping) else None
    allow_layer_only = action in {"delete_layer", "ensure_layer"}
    target = _safe_target(value, allow_layer_only=allow_layer_only)
    if target is not None:
        return target
    if not isinstance(value, Mapping):
        return None
    for key in ("target", "edit", "request", "arguments"):
        nested = _safe_target(value.get(key), allow_layer_only=allow_layer_only)
        if nested is not None:
            return nested
    direct = {key: value[key] for key in _TARGET_FIELDS if key in value}
    return _safe_target(direct, allow_layer_only=allow_layer_only)


def _walk_machine(machine: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Mapping[str, Any]]]:
    if not isinstance(machine, Mapping):
        return []
    found: list[tuple[tuple[str, ...], Mapping[str, Any]]] = []
    machine_name = machine.get("name")
    next_path = path
    if isinstance(machine_name, str) and machine_name:
        next_path = path + (machine_name,)
    states = machine.get("states")
    if isinstance(states, Sequence) and not isinstance(states, (str, bytes, bytearray)):
        for state in states:
            if isinstance(state, Mapping):
                found.append((next_path, state))
    machines = machine.get("machines")
    if isinstance(machines, Sequence) and not isinstance(machines, (str, bytes, bytearray)):
        for child in machines:
            found.extend(_walk_machine(child, next_path))
    return found


def _state_rows(state: Any) -> list[tuple[str, tuple[str, ...], Mapping[str, Any]]]:
    if not isinstance(state, Mapping):
        return []
    layers = state.get("layers")
    if not isinstance(layers, Sequence) or isinstance(layers, (str, bytes, bytearray)):
        return []
    rows: list[tuple[str, tuple[str, ...], Mapping[str, Any]]] = []
    for layer in layers:
        if not isinstance(layer, Mapping) or not isinstance(layer.get("name"), str):
            continue
        for path, state_row in _walk_machine(layer.get("machine")):
            rows.append((layer["name"], path, state_row))
    return rows


def _project_state(layer_name: str, state_name: str, row: Mapping[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    result: dict[str, Any] = {"layerName": layer_name, "stateName": state_name}
    for key in _STATE_SCALARS:
        if key not in row:
            continue
        value = _safe_scalar(row[key])
        if value is None:
            return None
        result[key] = value
    if "speed" not in result:
        return None
    if len(path) > 1:
        # The machine path is useful for nested states and remains bounded.
        machine_path = "/".join(path)
        bounded = _bounded_text(machine_path, limit=MAX_TARGET_TEXT_BYTES)
        if bounded is None:
            return None
        result["machinePath"] = bounded
    return result


def _find_projected_state(state: Any, target: Mapping[str, Any]) -> dict[str, Any] | None:
    layer_name = target.get("layerName")
    state_name = target.get("stateName")
    if not isinstance(layer_name, str) or not isinstance(state_name, str):
        return None
    matches = [
        (path, row)
        for layer, path, row in _state_rows(state)
        if layer == layer_name and row.get("name") == state_name
    ]
    if len(matches) != 1:
        return None
    return _project_state(layer_name, state_name, matches[0][1], matches[0][0])


def _evidence_state(result: Mapping[str, Any], readback: Mapping[str, Any]) -> tuple[bool | None, bool | None]:
    """Return durable applied/verified status from explicit receipt fields."""

    if result.get("preview") is True or result.get("pending") is True:
        return False, False
    if result.get("ok") is False or str(result.get("status") or "").casefold() in {"failed", "error", "preview", "pending"}:
        return False, False

    required = ("verified", "persistedReadback", "mutationApplied", "committed")
    values = [result.get(key) for key in required]
    if any(value is False for value in values) or readback.get("persisted") is False:
        return False, False
    if result.get("commitState") not in (None, "committed"):
        return False, False

    success = (
        result.get("ok") is True
        and result.get("preview") is False
        and all(value is True for value in values)
        and result.get("commitState") == "committed"
        and readback.get("persisted") is True
    )
    if not success:
        return None, None
    for key in ("commitStateKnown",):
        if key in result and result[key] is not True:
            return None, None
    for key in ("checkpointRecoveryRequired", "temporaryCleanupRequired"):
        if key in result and result[key] is not False:
            return None, None
    return True, True


def bounded_write_verification_summary(
    result: Mapping[str, Any],
    target_arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a bounded summary from an authoritative persisted FX write result.

    The function accepts only ``vrcforge.fx_animator_write.v1`` result objects.
    ``approval``, preview plans, requested scalar values, and scan output are
    never evidence of success. ``target_arguments`` is an optional selector
    copied from the same write step; it is used only to locate the persisted
    target, and none of its requested values are returned. The returned
    readback contains only whitelisted target/scalar fields; the caller remains
    responsible for retaining the original ``resultRef``.
    """

    if not isinstance(result, Mapping) or result.get("schema") != FX_WRITE_SCHEMA:
        return None
    readback = result.get("readback")
    if not isinstance(readback, Mapping):
        return {
            "schema": FX_WRITE_SCHEMA,
            "applied": False if any(result.get(key) is False for key in ("mutationApplied", "committed", "persistedReadback")) else None,
            "verified": False if result.get("verified") is False else None,
            "evidenceSource": EVIDENCE_SOURCE,
        }

    applied, verified = _evidence_state(result, readback)
    summary: dict[str, Any] = {
        "schema": FX_WRITE_SCHEMA,
        "applied": applied,
        "verified": verified,
        "evidenceSource": EVIDENCE_SOURCE,
    }
    bounded_readback: dict[str, Any] = {}
    persisted = readback.get("persisted")
    if isinstance(persisted, bool):
        bounded_readback["persisted"] = persisted
    controller_path = _bounded_text(readback.get("controllerPath"))
    if controller_path is not None:
        bounded_readback["controllerPath"] = controller_path

    state = readback.get("state")
    edits_value = result.get("edits")
    if not isinstance(edits_value, Sequence) or isinstance(edits_value, (str, bytes, bytearray)):
        selector_edits = target_arguments.get("edits") if isinstance(target_arguments, Mapping) else None
        if (result.get("batch") is True or "editCount" in result) and isinstance(selector_edits, Sequence) and not isinstance(selector_edits, (str, bytes, bytearray)):
            edits_value = selector_edits
    explicit_batch = isinstance(edits_value, Sequence) and not isinstance(edits_value, (str, bytes, bytearray))
    if explicit_batch:
        edits = edits_value
        total = len(edits)
        items: list[dict[str, Any]] = []
        for edit in edits[:MAX_SUMMARY_EDITS]:
            target = _target_from_mapping(edit)
            action = edit.get("action") if isinstance(edit, Mapping) else None
            action_text = _bounded_text(action, limit=MAX_TARGET_TEXT_BYTES)
            projected = (
                _find_projected_state(state, target)
                if target is not None and action not in {"delete_state", "delete_layer"}
                else None
            )
            if projected is None:
                projected = {"readbackUnavailable": True}
                if target is not None:
                    projected["selector"] = target
            if action_text is not None:
                projected = {"action": action_text, **projected}
            items.append(projected)
        bounded_readback["edits"] = items
        bounded_readback["total"] = total
        bounded_readback["truncated"] = total > MAX_SUMMARY_EDITS
        has_unavailable = any(item.get("readbackUnavailable") is True for item in items)
        if has_unavailable:
            bounded_readback["readbackUnavailable"] = True
        if verified is True and (total == 0 or len(items) != min(total, MAX_SUMMARY_EDITS) or has_unavailable):
            verified = None
            summary["verified"] = None
    else:
        target = _target_from_mapping(result)
        if target is None:
            target = _target_from_mapping(target_arguments)
        if target is not None:
            action = result.get("action")
            projected = (
                _find_projected_state(state, target)
                if action not in {"delete_state", "delete_layer"}
                else None
            )
            if projected is not None:
                bounded_readback["target"] = projected
            else:
                bounded_readback["targetUnavailable"] = True
                bounded_readback["readbackUnavailable"] = True
                bounded_readback["selector"] = target
                if verified is True:
                    summary["verified"] = None
        else:
            bounded_readback["targetUnavailable"] = True
    summary["readback"] = bounded_readback
    return summary


__all__ = ["bounded_write_verification_summary", "FX_WRITE_SCHEMA", "MAX_SUMMARY_EDITS"]
