"""Match explicit TODO acceptance checks against runtime-owned receipts."""

from collections.abc import Mapping
from hashlib import sha256
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
           for key in ("preview", "mock", "mock_execute", "mockExecute", "partial", "truncated", "hasMore")):
        return None
    if (result.get("sourceComplete") is False or result.get("rangeComplete") is False
            or any(result.get(key) not in (None, "") for key in (
                "nextOffset", "nextTextOffset", "nextPage", "nextPageTextOffset",
            ))):
        return None
    if any(facts.get("ok") is False or facts.get("success") is False
           or facts.get("status") in ("partial", "incomplete", "preview", "planned", "pending",
                                       "needs_user_action", "running", "failed")
           for facts in (result, outcome)):
        return None
    if kind == "write":
        if outcome.get("targetEffectVerified") is False:
            return None
        committed = result.get("committed") is True or outcome.get("committed") is True
        verified = (
            outcome.get("readbackState") == "verified"
            or result.get("persistedReadback") is True
        )
        if (not committed or not verified or result.get("persistedReadback") is False
                or result.get("verified") is False
                or outcome.get("readbackState") in ("pending", "failed", "mismatch")):
            return None
    elif kind not in {"read", "skill"}:
        return None
    return {"id": receipt_id, "kind": kind, "tool": tool, "arguments": arguments,
            "result": result, "outcome": outcome}


def matched_checks(checks: list[dict[str, Any]], evidence: dict[str, str],
                   receipts: list[dict[str, Any]]) -> dict[str, str]:
    matched = dict(evidence)
    for index, check in enumerate(checks):
        key = str(index)
        if key in matched:
            continue
        for receipt in receipts:
            if receipt["tool"] != check["tool"] or not _contains(receipt["arguments"], check["arguments"]):
                continue
            if receipt["tool"] in {"read_text_file", "vrcforge_read_text_file"}:
                result = receipt["result"]
                if result.get("textOffset", 0) != 0 or result.get("startPage", 1) != 1:
                    continue
                if any(name in receipt["arguments"] and name not in check["arguments"]
                       for name in ("startLine", "endLine", "startPage", "maxPages", "pageTextOffset")):
                    continue
                if "endLine" in check["arguments"] and result.get("endLine") != check["arguments"]["endLine"]:
                    continue
                if result.get("documentType") == "pdf":
                    pages = result.get("pages")
                    if (not isinstance(pages, list) or len(pages) != result.get("pageCount")
                            or any(not isinstance(page, Mapping) or page.get("page") != index
                                   for index, page in enumerate(pages, 1))):
                        continue
                elif (not isinstance(result.get("text"), str) or type(result.get("bytes")) is not int
                      or (not {"startLine", "endLine"}.intersection(check["arguments"])
                          and len(result["text"].encode("utf-8")) != result["bytes"])):
                    continue
            if _contains(receipt["result"], check["result"]):
                matched[key] = receipt["id"]
                break
    return matched


def goal_acceptance_checks(value: Any) -> list[dict[str, Any]]:
    """Validate the structured criteria the goal owner registered before execution."""
    checks = completion_checks(value)
    for source, check in zip(value, checks, strict=True):
        kind = source.get("kind")
        tool = check["tool"]
        if kind == "gesture_manager":
            valid = (tool == "vrcforge_gesture_manager_status" and bool(check["arguments"].get("avatarPath"))
                     and bool(check["result"].get("managers")))
        elif kind == "visual_audit":
            avatar = source.get("avatarPath")
            valid = (tool == "vrcforge_vision_audit_multi" and isinstance(avatar, str) and bool(avatar.strip())
                     and bool(check["result"].get("angles")))
            if valid:
                check["result"] = {**check["result"], "avatarDigest": sha256(avatar.strip().encode("utf-8")).hexdigest()}
        elif kind == "subjective":
            valid = tool in {"vrcforge_ask_user", "vrcforge_call_user"} and bool(check["result"].get("question"))
        elif kind == "readback":
            valid = (bool(check["arguments"]) and bool(check["result"])
                     and tool not in {"vrcforge_vision_audit_multi", "vrcforge_gesture_manager_status",
                                      "vrcforge_ask_user", "vrcforge_call_user"})
        else:
            valid = False
        if not valid:
            raise ValueError("Each Goal acceptance criterion needs an exact supported tool and target selectors.")
        check["kind"] = kind
    return checks


def goal_acceptance_receipt(check: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    if not matched_checks([dict(check)], {}, [dict(receipt)]):
        return False
    outcome = receipt.get("outcome", {})
    result = receipt["result"]
    tool = receipt["tool"]
    kind = check.get("kind")
    if kind == "gesture_manager":
        return result.get("isPlayMode") is True and any(
            manager.get("moduleConnected") is True and manager.get("avatarPath") == check["arguments"]["avatarPath"]
            for manager in result.get("managers", []) if isinstance(manager, Mapping)
        )
    if kind == "visual_audit":
        return (outcome.get("verification", {}).get("state") == "passed"
                and all(result.get(key) is True for key in ("visualVerified", "coverageComplete", "captureEvidenceVerified"))
                and isinstance(result.get("captureEvidenceId"), str) and bool(result["captureEvidenceId"]))
    if kind == "subjective":
        return receipt.get("source") == "authenticated_question_answer" and result.get("resolution") == "pass"
    if kind == "readback":
        if receipt.get("kind") == "write":
            return outcome.get("targetEffectVerified") is True
        return (tool.startswith(("vrcforge_get_", "vrcforge_read_"))
                and not tool.startswith(("vrcforge_get_goal", "vrcforge_read_conversation", "vrcforge_read_tool_result")))
    return False
