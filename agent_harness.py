from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
from typing import Any

from agent_tool_result_contract import completion_gate_plan, normalize_agent_tool_result
from agent_task_loop import AgentTaskLoop


AGENT_HARNESS_MATRIX_SCHEMA = "vrcforge.agent_harness_matrix.v5"
AGENT_HARNESS_REPORT_SCHEMA = "vrcforge.agent_harness_report.v5"
AGENT_HARNESS_JOURNEY_SCHEMA = "vrcforge.agent_harness_journey.v1"
_COMPLETION_STATUSES = frozenset({"ok", "failed", "needs_user_action"})


class AgentHarnessError(ValueError):
    pass


def load_agent_harness_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != AGENT_HARNESS_MATRIX_SCHEMA:
        raise AgentHarnessError(f"matrix schema must be {AGENT_HARNESS_MATRIX_SCHEMA}")

    selection_cases = value.get("selectionCases")
    completion_cases = value.get("completionCases")
    loop_cases = value.get("loopCases")
    chain_cases = value.get("chainCases")
    if not isinstance(selection_cases, list) or not selection_cases:
        raise AgentHarnessError("selectionCases must be a non-empty list")
    if not isinstance(completion_cases, list) or not completion_cases:
        raise AgentHarnessError("completionCases must be a non-empty list")
    if not isinstance(loop_cases, list) or not loop_cases:
        raise AgentHarnessError("loopCases must be a non-empty list")
    if not isinstance(chain_cases, list) or not chain_cases:
        raise AgentHarnessError("chainCases must be a non-empty list")

    seen_ids: set[str] = set()
    for case in selection_cases:
        case_id = _case_id(case, seen_ids)
        prompt = case.get("prompt")
        expected_tool = case.get("expectedTool")
        expected_action_kind = str(case.get("expectedActionKind") or "").strip().lower()
        forbidden_tools = case.get("forbiddenTools", [])
        exposure_layer = str(case.get("exposureLayer") or "planning")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentHarnessError(f"selection case {case_id} requires a prompt")
        if not isinstance(expected_tool, str) or not expected_tool.strip():
            raise AgentHarnessError(f"selection case {case_id} requires expectedTool")
        if expected_action_kind not in {"skill", "write"}:
            raise AgentHarnessError(
                f"selection case {case_id} requires expectedActionKind skill or write"
            )
        if not isinstance(forbidden_tools, list) or any(
            not isinstance(item, str) or not item.strip() for item in forbidden_tools
        ):
            raise AgentHarnessError(f"selection case {case_id} has invalid forbiddenTools")
        if expected_tool in forbidden_tools:
            raise AgentHarnessError(f"selection case {case_id} forbids its expected tool")
        if exposure_layer not in {"planning", "execution"}:
            raise AgentHarnessError(f"selection case {case_id} has invalid exposureLayer")
        if expected_action_kind == "write" and exposure_layer != "execution":
            raise AgentHarnessError(
                f"selection case {case_id} must expose writes only in execution"
            )
        case["expectedActionKind"] = expected_action_kind
        case["exposureLayer"] = exposure_layer

    for case in completion_cases:
        case_id = _case_id(case, seen_ids)
        if not isinstance(case.get("write"), bool):
            raise AgentHarnessError(f"completion case {case_id} requires a boolean write field")
        if not isinstance(case.get("result"), Mapping):
            raise AgentHarnessError(f"completion case {case_id} requires an object result")
        expected_status = str(case.get("expectedStatus") or "")
        if expected_status not in _COMPLETION_STATUSES:
            raise AgentHarnessError(f"completion case {case_id} has invalid expectedStatus")
        expected_next_step = case.get("expectedNextStep")
        if not isinstance(expected_next_step, str) or not expected_next_step.strip():
            raise AgentHarnessError(f"completion case {case_id} requires expectedNextStep")
    for case in loop_cases:
        case_id = _case_id(case, seen_ids)
        if not isinstance(case.get("objective"), str) or not case["objective"].strip():
            raise AgentHarnessError(f"loop case {case_id} requires an objective")
        actions = case.get("actions")
        if not isinstance(actions, list) or not actions or len(actions) > 3:
            raise AgentHarnessError(f"loop case {case_id} requires 1-3 actions")
        for action in actions:
            if not isinstance(action, Mapping):
                raise AgentHarnessError(f"loop case {case_id} actions must be objects")
            if not str(action.get("kind") or "").strip() or not str(action.get("tool") or "").strip():
                raise AgentHarnessError(f"loop case {case_id} actions require kind and tool")
            if not isinstance(action.get("arguments"), Mapping) or not isinstance(action.get("result"), Mapping):
                raise AgentHarnessError(f"loop case {case_id} actions require arguments and result")
        if case.get("claim") not in {"exact", "missing", "unknown", "first_only"}:
            raise AgentHarnessError(f"loop case {case_id} has an invalid claim")
        if not str(case.get("expectedNextStep") or "").strip():
            raise AgentHarnessError(f"loop case {case_id} requires expectedNextStep")
        if str(case.get("expectedTaskStatus") or "") not in {
            "completed",
            "failed",
            "needs_user_action",
            "running",
            "completion_unverified",
        }:
            raise AgentHarnessError(f"loop case {case_id} has invalid expectedTaskStatus")
    for case in chain_cases:
        case_id = _case_id(case, seen_ids)
        if not isinstance(case.get("objective"), str) or not case["objective"].strip():
            raise AgentHarnessError(f"chain case {case_id} requires an objective")
        requirements = case.get("requirements", [])
        if not isinstance(requirements, list) or len(requirements) > 3:
            raise AgentHarnessError(f"chain case {case_id} has invalid requirements")
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                raise AgentHarnessError(f"chain case {case_id} requirements must be objects")
            if not str(requirement.get("kind") or "").strip() or not str(requirement.get("tool") or "").strip():
                raise AgentHarnessError(f"chain case {case_id} requirements need kind and tool")
            if not isinstance(requirement.get("arguments"), Mapping):
                raise AgentHarnessError(f"chain case {case_id} requirements need arguments")
        actions = case.get("actions")
        if not isinstance(actions, list) or not actions or len(actions) > 3:
            raise AgentHarnessError(f"chain case {case_id} requires 1-3 actions")
        action_keys: set[str] = set()
        for action in actions:
            if not isinstance(action, Mapping):
                raise AgentHarnessError(f"chain case {case_id} actions must be objects")
            action_key = str(action.get("key") or "").strip()
            if not action_key or action_key in action_keys:
                raise AgentHarnessError(f"chain case {case_id} actions require unique keys")
            action_keys.add(action_key)
            if not str(action.get("kind") or "").strip() or not str(action.get("tool") or "").strip():
                raise AgentHarnessError(f"chain case {case_id} actions require kind and tool")
            if not isinstance(action.get("arguments"), Mapping) or not isinstance(action.get("result"), Mapping):
                raise AgentHarnessError(f"chain case {case_id} actions require arguments and result")
            correction_for = str(action.get("correctionFor") or "").strip()
            if correction_for and correction_for not in action_keys:
                raise AgentHarnessError(f"chain case {case_id} correctionFor must reference an earlier action")
        if case.get("claim") not in {"exact", "missing"}:
            raise AgentHarnessError(f"chain case {case_id} has an invalid claim")
        if not str(case.get("expectedNextStep") or "").strip():
            raise AgentHarnessError(f"chain case {case_id} requires expectedNextStep")
        if str(case.get("expectedTaskStatus") or "") not in {
            "completed",
            "failed",
            "needs_user_action",
            "running",
            "completion_unverified",
        }:
            raise AgentHarnessError(f"chain case {case_id} has invalid expectedTaskStatus")
    return value


def evaluate_agent_harness(
    matrix: Mapping[str, Any],
    *,
    select_tool: Callable[[str, str], Any],
    verify_selection: Callable[[str, Mapping[str, Any], str], bool] | None = None,
    selection_source: str = "offline-runtime",
    trusted_selection_receipts: bool = False,
    runtime_journeys: list[Mapping[str, Any]] | None = None,
    verify_runtime_journey: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    trusted_runtime_journey_receipts: bool = False,
) -> dict[str, Any]:
    selection_results: list[dict[str, Any]] = []
    for case in matrix["selectionCases"]:
        prompt = str(case["prompt"])
        exposure_layer = str(case.get("exposureLayer") or "planning")
        raw_selection = select_tool(prompt, exposure_layer)
        if isinstance(raw_selection, Mapping):
            raw_calls = raw_selection.get("toolCalls") or raw_selection.get("tool_calls") or []
            selected_action_kind = str(
                raw_selection.get("actionKind") or raw_selection.get("action_kind") or ""
            ).strip().lower()
            selected_calls = (
                [str(item).strip() for item in raw_calls if str(item).strip()]
                if isinstance(raw_calls, list)
                else []
            )
        else:
            selected_action_kind = "skill" if str(raw_selection or "").strip() else ""
            selected_calls = [str(raw_selection).strip()] if str(raw_selection or "").strip() else []
        selected_tool = selected_calls[0] if len(selected_calls) == 1 else ""
        provider_evidence_valid = False
        if verify_selection is not None and isinstance(raw_selection, Mapping):
            try:
                provider_evidence_valid = bool(
                    verify_selection(prompt, raw_selection, exposure_layer)
                )
            except Exception:
                provider_evidence_valid = False
        forbidden_tools = tuple(str(item) for item in case.get("forbiddenTools", []))
        passed = (
            selected_tool == case["expectedTool"]
            and selected_tool not in forbidden_tools
            and selected_action_kind == case["expectedActionKind"]
        )
        selection_results.append(
            {
                "id": case["id"],
                "passed": passed,
                "selectedTool": selected_tool,
                "expectedTool": case["expectedTool"],
                "selectedActionKind": selected_action_kind,
                "expectedActionKind": case["expectedActionKind"],
                "exposureLayer": exposure_layer,
                "providerEvidenceValid": provider_evidence_valid,
            }
        )

    completion_results: list[dict[str, Any]] = []
    for case in matrix["completionCases"]:
        outcome = normalize_agent_tool_result(
            case["result"],
            fallback_summary="Harness tool result.",
            write=case["write"],
        )
        claimed_plan = case.get("claimedPlan")
        if not isinstance(claimed_plan, Mapping):
            claimed_plan = {"nextStep": "done", "reply": "Done."}
        gated = completion_gate_plan(claimed_plan, outcome)
        final_plan = gated if gated is not None else dict(claimed_plan)
        actual_next_step = str(final_plan.get("nextStep") or "")
        passed = (
            outcome["status"] == case["expectedStatus"]
            and actual_next_step == case["expectedNextStep"]
        )
        completion_results.append(
            {
                "id": case["id"],
                "passed": passed,
                "outcomeStatus": outcome["status"],
                "expectedStatus": case["expectedStatus"],
                "nextStep": actual_next_step,
                "expectedNextStep": case["expectedNextStep"],
            }
        )

    loop_results: list[dict[str, Any]] = []
    for case in matrix["loopCases"]:
        loop = AgentTaskLoop(str(case["objective"]))
        action_ids: list[str] = []
        for action in case["actions"]:
            outcome = normalize_agent_tool_result(
                action["result"],
                fallback_summary="Harness action result.",
                write=bool(action.get("write")),
            )
            record = loop.record_action(
                kind=str(action["kind"]),
                tool=str(action["tool"]),
                arguments=action["arguments"],
                raw_result=action["result"],
                outcome=outcome,
            )
            action_ids.append(record["actionId"])
        claim_mode = str(case["claim"])
        plan: dict[str, Any] = {
            "planner": "llm",
            "nextStep": "done",
            "reply": "Done.",
        }
        if claim_mode != "missing":
            if claim_mode == "exact":
                evidence_ids = action_ids
            elif claim_mode == "first_only":
                evidence_ids = action_ids[:1]
            else:
                evidence_ids = ["action_not_executed"]
            plan["completionClaim"] = {
                "satisfied": True,
                "evidenceActionIds": evidence_ids,
            }
        gated = loop.gate_terminal(plan)
        actual_next_step = str(gated.get("nextStep") or "")
        task_view = gated.get("task")
        if not isinstance(task_view, Mapping):
            task_view = loop.snapshot()
        actual_task_status = str(task_view.get("status") or "")
        passed = (
            actual_next_step == case["expectedNextStep"]
            and actual_task_status == case["expectedTaskStatus"]
        )
        loop_results.append(
            {
                "id": case["id"],
                "passed": passed,
                "nextStep": actual_next_step,
                "expectedNextStep": case["expectedNextStep"],
                "taskStatus": actual_task_status,
                "expectedTaskStatus": case["expectedTaskStatus"],
            }
        )

    chain_results: list[dict[str, Any]] = []
    for case in matrix["chainCases"]:
        loop = AgentTaskLoop(str(case["objective"]))
        for requirement in case.get("requirements", []):
            loop.require_action(
                kind=str(requirement["kind"]),
                tool=str(requirement["tool"]),
                arguments=requirement["arguments"],
                verification_profile=str(requirement.get("verificationProfile") or ""),
            )
        action_ids: dict[str, str] = {}
        action_statuses: dict[str, str] = {}
        action_error_codes: dict[str, str] = {}
        for action in case["actions"]:
            outcome = normalize_agent_tool_result(
                action["result"],
                fallback_summary="Harness chain action result.",
                write=bool(action.get("write")),
            )
            correction_for = str(action.get("correctionFor") or "")
            record = loop.record_action(
                kind=str(action["kind"]),
                tool=str(action["tool"]),
                arguments=action["arguments"],
                raw_result=action["result"],
                outcome=outcome,
                correction_for_action_id=action_ids.get(correction_for, ""),
            )
            action_key = str(action["key"])
            action_ids[action_key] = record["actionId"]
            action_statuses[action_key] = record["status"]
            error = record.get("outcome", {}).get("error")
            if isinstance(error, Mapping):
                action_error_codes[action_key] = str(error.get("code") or "")
            if correction_for:
                corrected = loop.planner_projection()["actions"]
                previous = next(
                    (item for item in corrected if item.get("actionId") == action_ids[correction_for]),
                    None,
                )
                if isinstance(previous, Mapping):
                    action_statuses[correction_for] = str(previous.get("status") or "")
        plan: dict[str, Any] = {"planner": "llm", "nextStep": "done", "reply": "Done."}
        if case["claim"] == "exact":
            plan["completionClaim"] = {
                "satisfied": True,
                "evidenceActionIds": loop.completed_action_ids(),
            }
        gated = loop.gate_terminal(plan)
        task = gated.get("task")
        task = task if isinstance(task, Mapping) else loop.snapshot()
        expected_statuses = {
            str(key): str(value)
            for key, value in dict(case.get("expectedActionStatuses") or {}).items()
        }
        expected_error_codes = {
            str(key): str(value)
            for key, value in dict(case.get("expectedErrorCodes") or {}).items()
        }
        passed = (
            str(gated.get("nextStep") or "") == case["expectedNextStep"]
            and str(task.get("status") or "") == case["expectedTaskStatus"]
            and all(action_statuses.get(key) == value for key, value in expected_statuses.items())
            and all(action_error_codes.get(key) == value for key, value in expected_error_codes.items())
        )
        chain_results.append(
            {
                "id": case["id"],
                "passed": passed,
                "nextStep": str(gated.get("nextStep") or ""),
                "expectedNextStep": case["expectedNextStep"],
                "taskStatus": str(task.get("status") or ""),
                "expectedTaskStatus": case["expectedTaskStatus"],
                "actionStatuses": action_statuses,
                "actionErrorCodes": action_error_codes,
            }
        )

    selection_passed = sum(1 for item in selection_results if item["passed"])
    completion_passed = sum(1 for item in completion_results if item["passed"])
    loop_passed = sum(1 for item in loop_results if item["passed"])
    chain_passed = sum(1 for item in chain_results if item["passed"])
    accepted = (
        selection_passed == len(selection_results)
        and completion_passed == len(completion_results)
        and loop_passed == len(loop_results)
        and chain_passed == len(chain_results)
    )
    provider_evidence_valid = bool(selection_results) and all(
        item["providerEvidenceValid"] for item in selection_results
    )
    selection_receipt_accepted = bool(
        accepted and trusted_selection_receipts and provider_evidence_valid
    )
    journey_results = [
        _evaluate_runtime_journey(
            item,
            verify_runtime_journey=verify_runtime_journey,
        )
        for item in list(runtime_journeys or [])
    ]
    runtime_journey_accepted = bool(
        trusted_runtime_journey_receipts
        and journey_results
        and all(item["accepted"] for item in journey_results)
    )
    tools_executed = bool(
        runtime_journey_accepted
        and any(item["toolExecutions"] > 0 for item in journey_results)
    )
    external_verification_accepted = bool(
        runtime_journey_accepted
        and any(item["externalVerificationAccepted"] for item in journey_results)
    )
    release_accepted = bool(
        selection_receipt_accepted
        and tools_executed
        and external_verification_accepted
    )
    return {
        "schema": AGENT_HARNESS_REPORT_SCHEMA,
        "accepted": accepted,
        "releaseAccepted": release_accepted,
        "selectionReceiptAccepted": selection_receipt_accepted,
        "runtimeJourneyAccepted": runtime_journey_accepted,
        "externalVerificationAccepted": external_verification_accepted,
        "selectionOnly": False,
        "toolsExecuted": tools_executed,
        "selectionSource": str(selection_source or "offline-runtime")[:200],
        "trustedSelectionReceipts": bool(trusted_selection_receipts),
        "providerEvidenceValid": provider_evidence_valid,
        "trustedRuntimeJourneyReceipts": bool(trusted_runtime_journey_receipts),
        "runtimeJourneys": {
            "passed": sum(1 for item in journey_results if item["accepted"]),
            "total": len(journey_results),
            "cases": journey_results,
        },
        "selection": {
            "passed": selection_passed,
            "total": len(selection_results),
            "cases": selection_results,
        },
        "completion": {
            "passed": completion_passed,
            "total": len(completion_results),
            "cases": completion_results,
        },
        "loop": {
            "passed": loop_passed,
            "total": len(loop_results),
            "cases": loop_results,
        },
        "chain": {
            "passed": chain_passed,
            "total": len(chain_results),
            "cases": chain_results,
        },
    }


def _evaluate_runtime_journey(
    value: Mapping[str, Any],
    *,
    verify_runtime_journey: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
) -> dict[str, Any]:
    authenticated: Mapping[str, Any] = {}
    if verify_runtime_journey is not None:
        try:
            verified = verify_runtime_journey(value)
            if isinstance(verified, Mapping):
                authenticated = verified
        except Exception:
            authenticated = {}
    receipt_valid = bool(authenticated)
    schema = str(authenticated.get("schema") or "")
    tool_executions = max(0, int(authenticated.get("toolExecutions") or 0))
    provider_requests = max(0, int(authenticated.get("providerRequestCount") or 0))
    result_refeeds = max(0, int(authenticated.get("resultRefeedCount") or 0))
    next_step = str(authenticated.get("nextStep") or "")
    task_status = str(authenticated.get("taskStatus") or "")
    completed_action_ids = _bounded_action_ids(authenticated.get("completedActionIds"))
    evidence_action_ids = _bounded_action_ids(authenticated.get("evidenceActionIds"))
    verification_profiles = _bounded_verification_values(
        authenticated.get("verificationProfiles")
    )
    verification_states = _bounded_verification_values(
        authenticated.get("verificationStates")
    )
    external_verification_accepted = bool(
        verification_profiles
        and len(verification_profiles) == len(verification_states)
        and all(state == "passed" for state in verification_states)
        and any(profile != "canonical_tool_result" for profile in verification_profiles)
    )
    accepted = bool(
        schema == AGENT_HARNESS_JOURNEY_SCHEMA
        and receipt_valid
        and tool_executions > 0
        and provider_requests >= 2
        and result_refeeds > 0
        and next_step == "done"
        and task_status == "completed"
        and completed_action_ids
        and evidence_action_ids == completed_action_ids
        and external_verification_accepted
    )
    return {
        "id": str(authenticated.get("id") or "")[:160],
        "accepted": accepted,
        "receiptValid": receipt_valid,
        "toolExecutions": tool_executions,
        "providerRequestCount": provider_requests,
        "resultRefeedCount": result_refeeds,
        "nextStep": next_step,
        "taskStatus": task_status,
        "completedActionIds": completed_action_ids,
        "evidenceActionIds": evidence_action_ids,
        "verificationProfiles": verification_profiles,
        "verificationStates": verification_states,
        "externalVerificationAccepted": external_verification_accepted,
    }


def _bounded_action_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:160] for item in value[:3] if str(item).startswith("action_")]


def _bounded_verification_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:80] for item in value[:6] if str(item).strip()]


def _case_id(case: Any, seen_ids: set[str]) -> str:
    if not isinstance(case, dict):
        raise AgentHarnessError("matrix cases must be objects")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise AgentHarnessError("matrix cases require non-empty ids")
    if case_id in seen_ids:
        raise AgentHarnessError(f"duplicate matrix case id: {case_id}")
    seen_ids.add(case_id)
    return case_id
