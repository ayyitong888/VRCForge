"""Interpret a receipt for one submitted write; never submit or infer a mutation."""
from __future__ import annotations

from typing import Any

from unity_mcp_core_auth import UnityMcpCoreError


SCHEMA = "vrcforge.write-operation.v1"

# Receipts bind to one submission by executionId. Core re-states the tool name
# on terminal receipts; an argument digest cannot be re-derived across the
# language boundary, so submission uniqueness rests on the one-time executionId.
IN_FLIGHT_STATUSES = {"queued", "running", "unknown", "not_found", "expired"}


def write_operation_result(
    snapshot: Any, *, execution_id: str, tool_name: str,
) -> dict[str, Any] | None:
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA
            or snapshot.get("executionId") != execution_id):
        raise UnityMcpCoreError("The write-operation receipt does not match the submitted execution.")
    status = snapshot.get("status")
    if status in IN_FLIGHT_STATUSES:
        # In-flight or unknown means no authoritative receipt is available;
        # it never proves failure.
        return None
    if status not in {"done", "failed"}:
        raise UnityMcpCoreError("The write-operation receipt has an invalid state.")
    if snapshot.get("toolName") != tool_name:
        raise UnityMcpCoreError("The write-operation receipt does not match the submitted tool.")
    result = snapshot.get("result")
    return result if isinstance(result, dict) else None
