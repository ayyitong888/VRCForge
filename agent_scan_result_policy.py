"""Exact host policy for paging owner-validated scan results across turns."""

from __future__ import annotations

from typing import Any


SCAN_RESULT_CONTINUATION_POLICY = (
    "For scan summaries and continuations with scope=validated_scope only, this host policy supersedes "
    "the current-turn-only resultRef instruction. Owned scan references may be paged across turns; "
    "the host revalidates scope on every read. Old, unverified, and current_turn references remain "
    "current-turn only. Expired replies require a targeted rescan; inability to verify is not validity."
)

# Keep v1 byte-exact for existing histories; upgrade only by appending v2.
SCAN_RESULT_CONTINUATION_POLICY_V2 = (
    "For scan summaries and continuations with scope=validated_scope only, this host policy supersedes "
    "the current-turn-only resultRef instruction and the earlier scan-reference policy. For a follow-up "
    "about the same target and scan parameters, read the existing reference before invoking a scan tool, "
    "including after Play or a domain reload. The host checks Core scope or compares the complete "
    "observable business result on read; a successful fresh read is current evidence, not merely cached "
    "history. If expired with replacementRef, the host already rescanned: page that new reference, do not "
    "scan again. Without a replacement, rescan the returned scope only as needed. Changed targets or "
    "parameters require a new scan. Old, unverified, and current_turn references remain current-turn only. "
    "Failure or inability to verify is not validity. Invoke the reader through native function.name=call_tool, "
    "with its exact loaded name in tool and reader parameters in arguments; never use the reader name "
    "as function.name. Tool loading, permissions and write approvals are unchanged."
)


def is_scan_result_policy_message(value: Any) -> bool:
    return isinstance(value, dict) and any(value == {"role": "system", "content": content}
        for content in (SCAN_RESULT_CONTINUATION_POLICY, SCAN_RESULT_CONTINUATION_POLICY_V2))


def restored_scan_result_policy(messages: list[dict], retained: list[dict]) -> list[dict]:
    """Restore only the latest trusted version when compaction removes it."""
    latest = next((message for message in reversed(messages) if is_scan_result_policy_message(message)), None)
    return [dict(latest)] if latest is not None and latest not in retained else []
