from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
from typing import Any

from agent_tool_result_contract import completion_gate_plan, normalize_agent_tool_result
from agent_task_loop import AgentTaskLoop


AGENT_HARNESS_MATRIX_SCHEMA = "vrcforge.agent_harness_matrix.v2"
AGENT_HARNESS_REPORT_SCHEMA = "vrcforge.agent_harness_report.v2"
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
    if not isinstance(selection_cases, list) or not selection_cases:
        raise AgentHarnessError("selectionCases must be a non-empty list")
    if not isinstance(completion_cases, list) or not completion_cases:
        raise AgentHarnessError("completionCases must be a non-empty list")
    if not isinstance(loop_cases, list) or not loop_cases:
        raise AgentHarnessError("loopCases must be a non-empty list")

    seen_ids: set[str] = set()
    for case in selection_cases:
        case_id = _case_id(case, seen_ids)
        prompt = case.get("prompt")
        expected_tool = case.get("expectedTool")
        forbidden_tools = case.get("forbiddenTools", [])
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentHarnessError(f"selection case {case_id} requires a prompt")
        if not isinstance(expected_tool, str) or not expected_tool.strip():
            raise AgentHarnessError(f"selection case {case_id} requires expectedTool")
        if not isinstance(forbidden_tools, list) or any(
            not isinstance(item, str) or not item.strip() for item in forbidden_tools
        ):
            raise AgentHarnessError(f"selection case {case_id} has invalid forbiddenTools")
        if expected_tool in forbidden_tools:
            raise AgentHarnessError(f"selection case {case_id} forbids its expected tool")

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
    return value


def evaluate_agent_harness(
    matrix: Mapping[str, Any],
    *,
    select_tool: Callable[[str], str],
) -> dict[str, Any]:
    selection_results: list[dict[str, Any]] = []
    for case in matrix["selectionCases"]:
        selected_tool = str(select_tool(str(case["prompt"])) or "").strip()
        forbidden_tools = tuple(str(item) for item in case.get("forbiddenTools", []))
        passed = selected_tool == case["expectedTool"] and selected_tool not in forbidden_tools
        selection_results.append(
            {
                "id": case["id"],
                "passed": passed,
                "selectedTool": selected_tool,
                "expectedTool": case["expectedTool"],
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

    selection_passed = sum(1 for item in selection_results if item["passed"])
    completion_passed = sum(1 for item in completion_results if item["passed"])
    loop_passed = sum(1 for item in loop_results if item["passed"])
    accepted = (
        selection_passed == len(selection_results)
        and completion_passed == len(completion_results)
        and loop_passed == len(loop_results)
    )
    return {
        "schema": AGENT_HARNESS_REPORT_SCHEMA,
        "accepted": accepted,
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
    }


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
