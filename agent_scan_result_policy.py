"""Exact host policy for paging owner-validated scan results across turns."""

from __future__ import annotations

from typing import Any


SCAN_RESULT_CONTINUATION_POLICY = (
    "For scan summaries and continuations with scope=validated_scope only, this host policy supersedes "
    "the current-turn-only resultRef instruction. Owned scan references may be paged across turns; "
    "the host revalidates scope on every read. Old, unverified, and current_turn references remain "
    "current-turn only. Expired replies require a targeted rescan; inability to verify is not validity."
)


def is_scan_result_policy_message(value: Any) -> bool:
    return isinstance(value, dict) and value == {
        "role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY,
    }
