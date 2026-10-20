"""Proof that a resumed task's usage is a delta of one trusted parent snapshot.

The runtime already exposes cumulative task usage to the UI.  This module is a
small, side-effect-free validator for the private run ledger: it keeps the
observed per-invocation delta separate from the cumulative projection and
refuses to infer missing counters as zero.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


RECONCILIATION_SCHEMA = "vrcforge.request_usage_reconciliation.v1"
_PRIMARY_FIELDS = ("requestCount", "inputTokens", "outputTokens", "totalTokens")
_CACHE_FIELDS = ("cacheReadTokens", "cacheWriteTokens")
_MAX_COUNTER = 10**15


def _counter(value: Any) -> int | None:
    if type(value) is not int or value < 0 or value > _MAX_COUNTER:
        return None
    return value


def _snapshot(parent_request_count: int, parent_usage: Mapping[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"requestCount": parent_request_count}
    for field in (*_PRIMARY_FIELDS[1:], *_CACHE_FIELDS):
        value = _counter(parent_usage.get(field))
        if value is not None:
            snapshot[field] = value
    return snapshot


def _snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def usage_snapshot_sha256(request_count: Any, usage: Mapping[str, Any] | None) -> str:
    """Return the bounded digest persisted with a trusted task seed."""

    count = _counter(request_count)
    if count is None or not isinstance(usage, Mapping):
        return ""
    return _snapshot_digest(_snapshot(count, usage))


def _unknown(
    *,
    chain_id: str,
    parent_turn_id: str,
    turn_id: str,
    parent_snapshot_sha256: str,
    reason: str,
    parent_request_count: int | None = None,
    cumulative_request_count: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": RECONCILIATION_SCHEMA,
        "status": "unknown",
        "chainId": chain_id,
        "parentTurnId": parent_turn_id,
        "turnId": turn_id,
        "parentSnapshotSha256": parent_snapshot_sha256,
        "reason": reason,
    }
    if parent_request_count is not None:
        result["parentRequestCount"] = parent_request_count
    if cumulative_request_count is not None:
        result["cumulativeRequestCount"] = cumulative_request_count
    return result


def reconcile_request_usage(
    *,
    chain_id: str,
    expected_chain_id: str | None,
    parent_turn_id: str,
    expected_parent_turn_id: str | None,
    turn_id: str,
    parent_request_count: Any,
    parent_usage: Mapping[str, Any] | None,
    observed_delta: Mapping[str, Any] | None,
    cumulative_usage: Mapping[str, Any] | None,
    expected_parent_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate a task-total response against its durable parent seed.

    ``observed_delta`` is the current invocation's local accumulator, while
    ``cumulative_usage`` is the post-merge task-total value exposed to the UI.
    The parent identity and snapshot are trusted only from the persisted task
    continuation; callers may provide expected values to detect tampering or a
    reset.  No numeric delta is emitted for an unknown or non-monotonic chain.
    """

    chain_id = str(chain_id or "").strip()
    parent_turn_id = str(parent_turn_id or "").strip()
    turn_id = str(turn_id or "").strip()
    parent_map = parent_usage if isinstance(parent_usage, Mapping) else {}
    snapshot_parent_count = _counter(parent_request_count)
    if snapshot_parent_count is None:
        # A malformed/negative parent must never become a synthetic zero.
        return _unknown(
            chain_id=chain_id,
            parent_turn_id=parent_turn_id,
            turn_id=turn_id,
            parent_snapshot_sha256="",
            reason=(
                "negative_parentRequestCount"
                if type(parent_request_count) is int and parent_request_count < 0
                else "parent_request_count_invalid"
            ),
        )
    parent_snapshot_sha256 = _snapshot_digest(_snapshot(snapshot_parent_count, parent_map))

    base = dict(
        chain_id=chain_id,
        parent_turn_id=parent_turn_id,
        turn_id=turn_id,
        parent_snapshot_sha256=parent_snapshot_sha256,
        parent_request_count=snapshot_parent_count,
    )
    if not chain_id or not turn_id:
        return _unknown(**base, reason="missing_identity")
    if snapshot_parent_count and not parent_turn_id:
        return _unknown(**base, reason="parent_turn_id_missing")
    if expected_chain_id is not None:
        expected_chain = str(expected_chain_id or "").strip()
        if not expected_chain:
            return _unknown(**base, reason="chain_id_missing")
        if expected_chain != chain_id:
            return _unknown(**base, reason="chain_id_mismatch")
    if expected_parent_turn_id is not None:
        expected_parent = str(expected_parent_turn_id or "").strip()
        if not expected_parent:
            return _unknown(**base, reason="parent_turn_id_missing")
        if expected_parent != parent_turn_id:
            return _unknown(**base, reason="parent_turn_id_mismatch")
    if expected_parent_snapshot_sha256 is not None:
        expected_snapshot = str(expected_parent_snapshot_sha256 or "").strip().lower()
        if not expected_snapshot:
            return _unknown(**base, reason="parent_snapshot_missing")
        if expected_snapshot != parent_snapshot_sha256:
            return _unknown(**base, reason="parent_snapshot_mismatch")
    if not isinstance(cumulative_usage, Mapping):
        return _unknown(**base, reason="cumulative_usage_missing")
    if not isinstance(observed_delta, Mapping):
        return _unknown(**base, reason="observed_delta_missing")
    if cumulative_usage.get("exact") is not True:
        return _unknown(**base, reason="cumulative_usage_not_exact")
    if observed_delta.get("exact") is not True:
        return _unknown(**base, reason="observed_delta_not_exact")
    if parent_turn_id and snapshot_parent_count and parent_map.get("exact") is not True:
        return _unknown(**base, reason="parent_usage_not_exact")

    cumulative_values: dict[str, int] = {}
    observed_values: dict[str, int] = {}
    parent_values: dict[str, int] = {"requestCount": snapshot_parent_count}
    for field in _PRIMARY_FIELDS:
        if field != "requestCount":
            if type(parent_map.get(field)) is int and parent_map[field] < 0:
                return _unknown(**base, reason=f"negative_parent_{field}")
            parent_value = _counter(parent_map.get(field))
            if parent_value is None and snapshot_parent_count:
                return _unknown(**base, reason=f"parent_{field}_missing")
            parent_values[field] = parent_value or 0
        if type(cumulative_usage.get(field)) is int and cumulative_usage[field] < 0:
            return _unknown(**base, reason=f"negative_cumulative_{field}")
        if type(observed_delta.get(field)) is int and observed_delta[field] < 0:
            return _unknown(**base, reason=f"negative_observed_{field}")
        cumulative_value = _counter(cumulative_usage.get(field))
        if cumulative_value is None:
            return _unknown(**base, reason=f"cumulative_{field}_missing", cumulative_request_count=_counter(cumulative_usage.get("requestCount")))
        observed_value = _counter(observed_delta.get(field))
        if observed_value is None:
            return _unknown(**base, reason=f"observed_{field}_missing", cumulative_request_count=cumulative_values.get("requestCount"))
        cumulative_values[field] = cumulative_value
        observed_values[field] = observed_value
        if cumulative_value < parent_values[field]:
            return _unknown(**base, reason=f"non_monotonic_{field}", cumulative_request_count=cumulative_value)
        if parent_values[field] + observed_value != cumulative_value:
            return _unknown(**base, reason=f"observed_delta_mismatch_{field}", cumulative_request_count=cumulative_value)

    delta = dict(observed_values)
    for field in _CACHE_FIELDS:
        coverage_key = "cacheUsageComplete" if field == "cacheReadTokens" else "cacheWriteUsageComplete"
        boundaries = (observed_delta, cumulative_usage, parent_map) if snapshot_parent_count else (observed_delta, cumulative_usage)
        if any(coverage_key in boundary and boundary[coverage_key] is not True for boundary in boundaries):
            return _unknown(**base, reason=f"incomplete_{coverage_key}", cumulative_request_count=cumulative_values["requestCount"])
        raw_parent = parent_map.get(field)
        raw_cumulative = cumulative_usage.get(field)
        raw_observed = observed_delta.get(field)
        if any(type(value) is int and value < 0 for value in (raw_parent, raw_cumulative, raw_observed)):
            return _unknown(**base, reason=f"negative_{field}", cumulative_request_count=cumulative_values["requestCount"])
        parent_value = _counter(parent_map.get(field))
        cumulative_value = _counter(cumulative_usage.get(field))
        observed_value = _counter(observed_delta.get(field))
        if parent_value is None and cumulative_value is None and observed_value is None:
            continue
        # An initial turn has no persisted parent.  Its empty parent is a
        # trusted origin, so a first reported cache counter is a real delta
        # from zero.  A resumed turn still requires both parent boundaries.
        if not parent_turn_id and snapshot_parent_count == 0 and parent_value is None:
            parent_value = 0
        if parent_value is None or cumulative_value is None or observed_value is None:
            cache_name = "read" if field == "cacheReadTokens" else "write"
            return _unknown(**base, reason=f"cache_{cache_name}_boundary_unknown", cumulative_request_count=cumulative_values["requestCount"])
        if cumulative_value < parent_value:
            return _unknown(**base, reason=f"non_monotonic_{field}", cumulative_request_count=cumulative_values["requestCount"])
        if parent_value + observed_value != cumulative_value:
            return _unknown(**base, reason=f"observed_delta_mismatch_{field}", cumulative_request_count=cumulative_values["requestCount"])
        delta[field] = observed_value

    return {
        "schema": RECONCILIATION_SCHEMA,
        "status": "verified",
        "chainId": chain_id,
        "parentTurnId": parent_turn_id,
        "turnId": turn_id,
        "parentSnapshotSha256": parent_snapshot_sha256,
        "parentRequestCount": snapshot_parent_count,
        "cumulativeRequestCount": cumulative_values["requestCount"],
        "cumulativeSnapshotSha256": usage_snapshot_sha256(
            cumulative_values["requestCount"], cumulative_usage
        ),
        "delta": delta,
    }
