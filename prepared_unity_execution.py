"""Deterministic sealed call payloads prepared before Unity approval.

This module intentionally has no secret or authority of its own.  It makes a
prepared call list immutable-by-convention while the gateway binds the outer
arguments digest and approval record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


PREPARED_UNITY_EXECUTION_ARGUMENT_KEY = "_vrcforge_prepared_unity_execution"
PREPARED_UNITY_EXECUTION_KEY = PREPARED_UNITY_EXECUTION_ARGUMENT_KEY
PREPARED_UNITY_EXECUTION_SCHEMA = "vrcforge.prepared-unity-execution.v1"
PREPARED_CALLS_KEY = "calls"
PREPARED_EVIDENCE_KEY = "evidence"
PREPARED_CALLS_SHA256_KEY = "callsSha256"
PREPARED_EVIDENCE_SHA256_KEY = "evidenceSha256"
PREPARED_SEAL_SHA256_KEY = "sealSha256"
MAX_PREPARED_CALLS = 64


def _json_copy(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError(f"{label} must be JSON-compatible.") from exc


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Prepared Unity execution data must be JSON-compatible.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _prepared_calls(calls: Any) -> list[dict[str, Any]]:
    if not isinstance(calls, (list, tuple)) or not 1 <= len(calls) <= MAX_PREPARED_CALLS:
        raise ValueError(f"Prepared Unity calls must contain 1 to {MAX_PREPARED_CALLS} items.")
    normalized: list[dict[str, Any]] = []
    for item in calls:
        if isinstance(item, tuple) and len(item) == 2:
            tool_name, arguments = item
        elif isinstance(item, dict):
            if set(item) != {"toolName", "arguments"}:
                raise ValueError("Prepared Unity call fields are invalid.")
            tool_name, arguments = item.get("toolName"), item.get("arguments")
        else:
            raise ValueError("Prepared Unity call is invalid.")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("Prepared Unity call toolName is invalid.")
        if not isinstance(arguments, dict):
            raise ValueError("Prepared Unity call arguments must be an object.")
        normalized.append({"toolName": tool_name.strip(), "arguments": _json_copy(arguments, label="Prepared Unity call arguments")})
    return normalized


def _seal(calls: list[dict[str, Any]], evidence: Any) -> dict[str, Any]:
    evidence_copy = _json_copy(evidence, label="Prepared Unity evidence")
    calls_hash = _canonical_sha256(calls)
    evidence_hash = _canonical_sha256(evidence_copy)
    seal = {
        "schema": PREPARED_UNITY_EXECUTION_SCHEMA,
        PREPARED_CALLS_KEY: calls,
        PREPARED_EVIDENCE_KEY: evidence_copy,
        PREPARED_CALLS_SHA256_KEY: calls_hash,
        PREPARED_EVIDENCE_SHA256_KEY: evidence_hash,
    }
    seal[PREPARED_SEAL_SHA256_KEY] = _canonical_sha256(seal)
    return seal


def install_prepared_calls(arguments: dict[str, Any], calls: Any, evidence: Any) -> dict[str, Any]:
    """Return copied arguments containing a verified prepared-call seal.

    A caller may not prepopulate the reserved internal key; accepting it would
    permit a stale or forged plan to survive a later preparation step.
    """
    if not isinstance(arguments, dict):
        raise ValueError("Prepared Unity execution arguments must be an object.")
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise ValueError("Caller may not provide the reserved prepared Unity execution key.")
    copied_arguments = _json_copy(arguments, label="Prepared Unity execution arguments")
    if not isinstance(copied_arguments, dict):  # Defensive; _json_copy preserves the root type.
        raise ValueError("Prepared Unity execution arguments must be an object.")
    copied_arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY] = _seal(_prepared_calls(calls), evidence)
    return copied_arguments


def _read_seal(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("Prepared Unity execution arguments must be an object.")
    seal = arguments.get(PREPARED_UNITY_EXECUTION_ARGUMENT_KEY)
    if not isinstance(seal, dict) or seal.get("schema") != PREPARED_UNITY_EXECUTION_SCHEMA:
        raise ValueError("Prepared Unity execution seal is invalid.")
    if set(seal) != {
        "schema",
        PREPARED_CALLS_KEY,
        PREPARED_EVIDENCE_KEY,
        PREPARED_CALLS_SHA256_KEY,
        PREPARED_EVIDENCE_SHA256_KEY,
        PREPARED_SEAL_SHA256_KEY,
    }:
        raise ValueError("Prepared Unity execution seal fields are invalid.")
    calls = _prepared_calls(seal.get(PREPARED_CALLS_KEY))
    evidence = _json_copy(seal.get(PREPARED_EVIDENCE_KEY), label="Prepared Unity evidence")
    if seal.get(PREPARED_CALLS_SHA256_KEY) != _canonical_sha256(calls):
        raise ValueError("Prepared Unity execution calls digest is invalid.")
    if seal.get(PREPARED_EVIDENCE_SHA256_KEY) != _canonical_sha256(evidence):
        raise ValueError("Prepared Unity execution evidence digest is invalid.")
    expected = {
        "schema": PREPARED_UNITY_EXECUTION_SCHEMA,
        PREPARED_CALLS_KEY: calls,
        PREPARED_EVIDENCE_KEY: evidence,
        PREPARED_CALLS_SHA256_KEY: seal[PREPARED_CALLS_SHA256_KEY],
        PREPARED_EVIDENCE_SHA256_KEY: seal[PREPARED_EVIDENCE_SHA256_KEY],
    }
    if seal.get(PREPARED_SEAL_SHA256_KEY) != _canonical_sha256(expected):
        raise ValueError("Prepared Unity execution seal digest is invalid.")
    return {**expected, PREPARED_SEAL_SHA256_KEY: seal[PREPARED_SEAL_SHA256_KEY]}


def build_prepared_execution_plan(arguments: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Read only the sealed call list and return independent exact call copies."""
    seal = _read_seal(arguments)
    return [
        (str(item["toolName"]), _json_copy(item["arguments"], label="Prepared Unity call arguments"))
        for item in seal[PREPARED_CALLS_KEY]
    ]


def prepared_call(arguments: dict[str, Any], index: int = 0) -> tuple[str, dict[str, Any]]:
    """Return one independent sealed call; out-of-range indexes are rejected."""
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("Prepared Unity call index is invalid.")
    calls = build_prepared_execution_plan(arguments)
    try:
        tool_name, call_arguments = calls[index]
    except IndexError as exc:
        raise ValueError("Prepared Unity call index is out of range.") from exc
    return tool_name, _json_copy(call_arguments, label="Prepared Unity call arguments")


def prepared_evidence(arguments: dict[str, Any]) -> Any:
    """Return an independent copy of the sealed approval-time evidence."""
    seal = _read_seal(arguments)
    return _json_copy(seal[PREPARED_EVIDENCE_KEY], label="Prepared Unity evidence")
