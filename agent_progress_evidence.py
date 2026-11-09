"""Match explicit TODO acceptance checks against runtime-owned receipts."""

from collections.abc import Mapping
from typing import Any


def completion_checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError("completionChecks must contain 1 to 16 receipt checks.")
    checks = []
    for check in value:
        if not isinstance(check, dict):
            raise ValueError("Each completion check must name a tool and its expected arguments or result.")
        tool = str(check.get("tool") or "").strip()
        selectors = {key: check.get(key, {}) for key in ("arguments", "result")}
        if (not tool or len(tool) > 160 or not all(isinstance(part, dict) for part in selectors.values())
                or not any(selectors.values())):
            raise ValueError("Each completion check requires a tool and nonempty arguments or result selectors.")
        checks.append({"tool": tool, **selectors})
    return checks


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, Mapping) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains(left, right) for left, right in zip(actual, expected)
        )
    return type(actual) is type(expected) and actual == expected


def accepted_receipt(
    tool: str, kind: str, receipt_id: str, arguments: Mapping[str, Any], result: Any,
    outcome: Mapping[str, Any], *, status: str,
) -> dict[str, Any] | None:
    if (not receipt_id or status not in {"executed", "applied"} or outcome.get("status") != "ok"
            or not isinstance(result, Mapping) or tool.startswith("vrcforge_progress_")
            or "preview" in tool.split("_") or "plan" in tool.split("_")):
        return None
    verification = outcome.get("verification")
    verification_state = verification.get("state") if isinstance(verification, Mapping) else None
    if verification_state not in (None, "", "passed", "not_required"):
        return None
    if any(facts.get(key) is True for facts in (arguments, result, outcome)
           for key in ("preview", "mock", "mock_execute", "mockExecute", "partial", "truncated")):
        return None
    if any(facts.get("ok") is False or facts.get("success") is False
           or facts.get("status") in ("partial", "incomplete", "preview", "planned", "pending",
                                       "needs_user_action", "running", "failed")
           for facts in (result, outcome)):
        return None
    if kind == "write":
        committed = result.get("committed") is True or outcome.get("committed") is True
        verified = (
            verification_state == "passed"
            or outcome.get("readbackState") == "verified"
            or result.get("persistedReadback") is True
        )
        if (not committed or not verified or result.get("persistedReadback") is False
                or result.get("verified") is False
                or outcome.get("readbackState") in ("pending", "failed", "mismatch")):
            return None
    elif kind != "skill":
        return None
    return {"id": receipt_id, "tool": tool, "arguments": arguments, "result": result}


def matched_checks(checks: list[dict[str, Any]], evidence: dict[str, str],
                   receipts: list[dict[str, Any]]) -> dict[str, str]:
    matched = dict(evidence)
    for index, check in enumerate(checks):
        key = str(index)
        if key in matched:
            continue
        for receipt in receipts:
            if (receipt["tool"] == check["tool"] and _contains(receipt["arguments"], check["arguments"])
                    and _contains(receipt["result"], check["result"])):
                matched[key] = receipt["id"]
                break
    return matched
