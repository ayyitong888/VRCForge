"""Small task-loop contract for evidence-bound agent completion.

The planner may choose the next action and may propose that a task is done.
This owner records the actual actions and decides whether that terminal claim
is supported.  It deliberately owns no tools, Provider, process, approval,
filesystem, network, or persistence capability.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from agent_tool_result_contract import completion_gate_plan, normalize_agent_tool_result


TASK_LOOP_SCHEMA = "vrcforge.agent_task_loop.v2"
TASK_APPROVAL_CONTEXT_SCHEMA = "vrcforge.agent_task_approval.v1"
_RUNNING_STATUSES = frozenset(
    {"accepted", "in_progress", "queued", "running", "started", "starting"}
)
_TERMINAL_BYPASS_STEPS = frozenset(
    {
        "cancelled",
        "context_compaction_required",
        "loop_suppressed",
        "needs_user_action",
        "paused",
        "planner_failed",
        "tool_failed",
        "waiting_for_tool",
    }
)
_VERIFICATION_PROFILES: dict[str, tuple[tuple[str, Any], ...]] = {
    # The tool-result contract itself is the registered baseline verifier for
    # read actions and host Shell commands that have no stronger postcondition.
    "canonical_tool_result": (),
    "persisted_scene_write": (
        ("persistedReadback", True),
        ("sceneSaved", True),
    ),
    "persisted_scene_write_console": (
        ("persistedReadback", True),
        ("sceneSaved", True),
        ("consoleVerified", True),
    ),
    "multi_angle_visual": (
        ("visualVerified", True),
        ("coverageComplete", True),
        ("captureEvidenceVerified", True),
    ),
    "shell_exit_zero": (("exitCode", 0),),
}
_TOOL_DEFAULT_VERIFICATION_PROFILE = {
    "vrcforge_create_gameobject": "persisted_scene_write_console",
    "vrcforge_capture_multi_screenshot": "canonical_tool_result",
    "vrcforge_vision_audit_multi": "multi_angle_visual",
    "shell": "shell_exit_zero",
}


def _status(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    bounded: list[dict[str, str]] = []
    remaining = 12_000
    for item in reversed(value[-20:]):
        if not isinstance(item, Mapping) or remaining <= 0:
            continue
        role = _bounded_text(item.get("role"), 32)
        text = _bounded_text(item.get("text") or item.get("content"), min(2_000, remaining))
        if not role or not text:
            continue
        bounded.append({"role": role, "text": text})
        remaining -= len(text)
    bounded.reverse()
    return bounded


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_action_id(kind: str, tool: str, arguments: Any) -> str:
    payload = [_bounded_text(kind, 32), _bounded_text(tool, 160), arguments]
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"action_{digest[:24]}"


def canonical_task_id(session_id: str, client_turn_id: str, objective: str) -> str:
    payload = [
        _bounded_text(session_id, 180),
        _bounded_text(client_turn_id, 180),
        _bounded_text(objective, 600),
    ]
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"task_{digest[:24]}"


def _mapping_views(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    result: list[Mapping[str, Any]] = []
    pending: list[tuple[Mapping[str, Any], int]] = [(value, 0)]
    seen: set[int] = set()
    while pending and len(result) < 16:
        current, depth = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if depth >= 4:
            continue
        for key in ("structuredContent", "result", "data", "entrypoint", "toolResult"):
            nested = current.get(key)
            if isinstance(nested, Mapping):
                pending.append((nested, depth + 1))
    return result


def _running_state(value: Any) -> str:
    for view in _mapping_views(value):
        state = _status(view.get("status"))
        if state in _RUNNING_STATUSES:
            return state
        session = view.get("session")
        if isinstance(session, Mapping):
            state = _status(session.get("status"))
            if state in _RUNNING_STATUSES:
                return state
    return ""


_FIELD_MISSING = object()


def _field_value(value: Any, field_name: str) -> Any:
    for view in _mapping_views(value):
        if field_name in view:
            return view.get(field_name)
    return _FIELD_MISSING


def _bounded_outcome(value: Mapping[str, Any]) -> dict[str, Any]:
    verification = value.get("verification")
    error = value.get("error")
    evidence = value.get("evidence")
    bounded_error: dict[str, Any] | None = None
    if isinstance(error, Mapping):
        bounded_error = {
            "type": _bounded_text(error.get("type"), 80),
            "code": _bounded_text(error.get("code"), 120),
            "likelyCauses": [
                _bounded_text(item, 240)
                for item in (
                    list(error.get("likelyCauses") or [])[:6]
                    if isinstance(error.get("likelyCauses"), (list, tuple))
                    else []
                )
                if _bounded_text(item, 240)
            ],
            "nextActions": [
                _bounded_text(item, 240)
                for item in (
                    list(error.get("nextActions") or [])[:6]
                    if isinstance(error.get("nextActions"), (list, tuple))
                    else []
                )
                if _bounded_text(item, 240)
            ],
            "retryable": error.get("retryable") is True,
        }
    return {
        "status": _bounded_text(value.get("status"), 40),
        "summary": _bounded_text(value.get("summary"), 600),
        "error": bounded_error,
        "evidence": [
            {
                key: _bounded_text(item.get(key), 160)
                for key in ("ref", "kind", "sha256")
                if _bounded_text(item.get(key), 160)
            }
            for item in (list(evidence)[:12] if isinstance(evidence, (list, tuple)) else [])
            if isinstance(item, Mapping) and _bounded_text(item.get("ref"), 160)
        ],
        "verification": (
            {
                "state": _bounded_text(verification.get("state"), 40),
                "checks": [
                    {
                        "kind": _bounded_text(item.get("kind"), 80),
                        "state": _bounded_text(item.get("state"), 80),
                    }
                    for item in verification.get("checks", [])[:12]
                    if isinstance(item, Mapping)
                ],
            }
            if isinstance(verification, Mapping)
            else {"state": "not_required", "checks": []}
        ),
    }


def _bounded_action(value: Mapping[str, Any]) -> dict[str, Any] | None:
    action_id = _bounded_text(value.get("actionId"), 80)
    tool = _bounded_text(value.get("tool"), 160)
    if not action_id or not tool:
        return None
    status = _status(value.get("status"))
    if status not in {
        "cancelled",
        "completed",
        "failed",
        "needs_user_action",
        "running",
        "superseded",
    }:
        return None
    outcome = value.get("outcome")
    result = {
        "actionId": action_id,
        "kind": _bounded_text(value.get("kind"), 32),
        "tool": tool,
        "status": status,
        "attempts": max(1, min(int(value.get("attempts") or 1), 3)),
        "outcome": _bounded_outcome(outcome if isinstance(outcome, Mapping) else {}),
    }
    superseded_by = _bounded_text(value.get("supersededBy"), 80)
    if status == "superseded" and superseded_by:
        result["supersededBy"] = superseded_by
    if value.get("preProvider") is True:
        result["preProvider"] = True
    return result


def _bounded_requirement(value: Mapping[str, Any]) -> dict[str, Any] | None:
    kind = _bounded_text(value.get("kind"), 32)
    tool = _bounded_text(value.get("tool"), 160)
    if not kind or not tool:
        return None
    action_id = _bounded_text(value.get("actionId"), 80)
    profile = _bounded_text(value.get("verificationProfile"), 80)
    requirement_id = _bounded_text(value.get("requirementId"), 80)
    if not requirement_id:
        digest = hashlib.sha256(
            _canonical_json([kind, tool, action_id, profile]).encode("utf-8")
        ).hexdigest()
        requirement_id = f"requirement_{digest[:24]}"
    return {
        "requirementId": requirement_id,
        "kind": kind,
        "tool": tool,
        "actionId": action_id,
        "verificationProfile": profile,
    }


def _bounded_skill_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    name = _bounded_text(value.get("name"), 160)
    allowed = [
        bounded
        for item in list(value.get("allowedTools") or [])[:32]
        if (bounded := _bounded_text(item, 160))
    ]
    disallowed = [
        bounded
        for item in list(value.get("disallowedTools") or [])[:32]
        if (bounded := _bounded_text(item, 160))
    ]
    if not (name or allowed or disallowed):
        return {}
    return {
        "name": name,
        "allowedTools": list(dict.fromkeys(allowed)),
        "disallowedTools": list(dict.fromkeys(disallowed)),
    }


def _bounded_skill_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    policy = _bounded_skill_policy(value)
    instructions = _bounded_text(value.get("instructions"), 6_000)
    if not (policy or instructions):
        return {}
    return {
        "name": _bounded_text(policy.get("name"), 160),
        "instructions": instructions,
        "allowedTools": list(policy.get("allowedTools") or []),
        "disallowedTools": list(policy.get("disallowedTools") or []),
    }


def apply_declared_verification(
    tool: str,
    raw_result: Any,
    outcome: Mapping[str, Any],
    *,
    verification_profile: str = "",
) -> dict[str, Any]:
    """Apply a Runtime-owned postcondition profile, if one was declared."""

    bounded = _bounded_outcome(outcome)
    declared_profile = _bounded_text(verification_profile, 80)
    profile = declared_profile or _TOOL_DEFAULT_VERIFICATION_PROFILE.get(
        str(tool or "").strip(),
        "",
    )
    if _status(bounded.get("status")) != "ok":
        return bounded
    if _running_state(raw_result):
        return bounded
    if declared_profile and profile not in _VERIFICATION_PROFILES:
        return {
            "status": "needs_user_action",
            "summary": f"The declared verification profile '{profile}' is not registered.",
            "error": {
                "type": "verification",
                "code": "verification_profile_unknown",
                "likelyCauses": ["The action declared a verifier that this runtime cannot execute."],
                "nextActions": ["Use a registered verification profile before claiming completion."],
                "retryable": False,
            },
            "verification": {
                "state": "needs_user_action",
                "checks": [{"kind": profile, "state": "unknown"}],
            },
        }
    requirements = _VERIFICATION_PROFILES.get(profile, ())
    if not requirements:
        return bounded

    checks: list[dict[str, str]] = []
    failed: list[str] = []
    for field_name, expected in requirements:
        actual = _field_value(raw_result, field_name)
        state = (
            "passed"
            if actual is not _FIELD_MISSING
            and type(actual) is type(expected)
            and actual == expected
            else "failed"
        )
        checks.append({"kind": field_name, "state": state})
        if state == "failed":
            failed.append(field_name)
    if failed:
        return {
            "status": "needs_user_action",
            "summary": (
                "The Unity write returned, but its required persisted readback did not pass: "
                + ", ".join(failed)
                + "."
            ),
            "verification": {"state": "needs_user_action", "checks": checks},
        }
    bounded["verification"] = {"state": "passed", "checks": checks}
    return bounded


def approval_task_context(
    seed: Mapping[str, Any] | None,
    *,
    tool: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(seed, Mapping) or seed.get("schema") != TASK_LOOP_SCHEMA:
        return None
    objective = _bounded_text(seed.get("objective"), 600)
    if not objective:
        return None
    seeded_tool = _bounded_text(seed.get("requestedTool"), 160)
    requested_kind = _bounded_text(seed.get("requestedKind"), 32) or "write"
    requested_tool = seeded_tool or tool
    requested_arguments = (
        dict(seed.get("requestedArguments"))
        if seeded_tool and isinstance(seed.get("requestedArguments"), Mapping)
        else dict(arguments)
    )
    computed_action_id = canonical_action_id(
        requested_kind,
        requested_tool,
        requested_arguments,
    )
    seeded_action_id = _bounded_text(seed.get("requestedActionId"), 80)
    prior_requirements = [
        bounded
        for item in list(seed.get("requirements") or [])[:3]
        if isinstance(item, Mapping)
        if (bounded := _bounded_requirement(item)) is not None
    ]
    seeded_action_matches_requirement = bool(
        seeded_action_id
        and any(
            requirement.get("kind") == requested_kind
            and requirement.get("tool") == requested_tool
            and requirement.get("actionId") == seeded_action_id
            for requirement in prior_requirements
        )
    )
    requested_action_id = (
        seeded_action_id
        if seeded_action_matches_requirement or seeded_action_id == computed_action_id
        else computed_action_id
    )
    return {
        "schema": TASK_APPROVAL_CONTEXT_SCHEMA,
        "taskId": _bounded_text(seed.get("taskId"), 80),
        "objective": objective,
        "sessionId": _bounded_text(seed.get("sessionId"), 180),
        "turnId": _bounded_text(seed.get("turnId"), 180),
        "clientTurnId": _bounded_text(seed.get("clientTurnId"), 180),
        "projectRoot": _bounded_text(seed.get("projectRoot"), 600),
        "agentName": _bounded_text(seed.get("agentName"), 160),
        "provider": _bounded_text(seed.get("provider"), 80),
        "providerLabel": _bounded_text(seed.get("providerLabel"), 160),
        "model": _bounded_text(seed.get("model"), 160),
        "contextLimit": (
            max(1, min(int(seed.get("contextLimit")), 10_000_000))
            if isinstance(seed.get("contextLimit"), int)
            else None
        ),
        "toolCallsUsed": max(0, min(int(seed.get("toolCallsUsed") or 0), 3)),
        "providerRequestCount": max(
            0,
            min(int(seed.get("providerRequestCount") or 0), 100),
        ),
        "continueAfterApproval": seed.get("continueAfterApproval") is True,
        "exposureLayer": (
            "execution"
            if _status(seed.get("exposureLayer")) == "execution"
            else "planning"
        ),
        "priorActions": [
            bounded
            for item in list(seed.get("actions") or [])[:3]
            if isinstance(item, Mapping)
            if (bounded := _bounded_action(item)) is not None
        ],
        "priorRequirements": prior_requirements,
        "managedVisualCaptureActionIds": [
            bounded
            for item in list(seed.get("managedVisualCaptureActionIds") or [])[:2]
            if (bounded := _bounded_text(item, 80))
        ],
        "skillPolicy": _bounded_skill_policy(seed.get("skillPolicy")),
        "skillContext": _bounded_skill_context(seed.get("skillContext")),
        "history": _bounded_history(seed.get("history")),
        "requestedArguments": requested_arguments,
        "requestedActionId": requested_action_id,
        "actionId": requested_action_id,
        "kind": requested_kind,
        "tool": requested_tool,
        "verificationProfile": next(
            (
                _bounded_text(item.get("verificationProfile"), 80)
                for item in list(seed.get("requirements") or [])[:3]
                if isinstance(item, Mapping)
                and _bounded_text(item.get("kind"), 32) == requested_kind
                and _bounded_text(item.get("tool"), 160) == requested_tool
                and (
                    not _bounded_text(item.get("actionId"), 80)
                    or _bounded_text(item.get("actionId"), 80) == requested_action_id
                )
                and _bounded_text(item.get("verificationProfile"), 80)
            ),
            _TOOL_DEFAULT_VERIFICATION_PROFILE.get(tool, "canonical_tool_result"),
        ),
    }


def approval_completion(
    context: Mapping[str, Any] | None,
    *,
    raw_result: Any,
    outcome: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(context, Mapping) or context.get("schema") != TASK_APPROVAL_CONTEXT_SCHEMA:
        return None
    tool = _bounded_text(context.get("tool"), 160)
    verified = apply_declared_verification(
        tool,
        raw_result,
        outcome,
        verification_profile=_bounded_text(context.get("verificationProfile"), 80),
    )
    status = _status(verified.get("status"))
    if status == "ok":
        task_status = "completed"
    elif status == "failed":
        task_status = "failed"
    else:
        task_status = "needs_user_action"
    return {
        "schema": TASK_LOOP_SCHEMA,
        "taskId": _bounded_text(context.get("taskId"), 80),
        "status": task_status,
        "objective": _bounded_text(context.get("objective"), 600),
        "actionId": _bounded_text(context.get("actionId"), 80),
        "tool": tool,
        "outcome": verified,
    }


def rejected_approval_completion(
    context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(context, Mapping) or context.get("schema") != TASK_APPROVAL_CONTEXT_SCHEMA:
        return None
    return {
        "schema": TASK_LOOP_SCHEMA,
        "taskId": _bounded_text(context.get("taskId"), 80),
        "status": "needs_user_action",
        "objective": _bounded_text(context.get("objective"), 600),
        "actionId": _bounded_text(context.get("actionId"), 80),
        "tool": _bounded_text(context.get("tool"), 160),
        "outcome": {
            "status": "needs_user_action",
            "summary": "The requested project change was rejected by the user.",
            "verification": {"state": "not_run", "checks": []},
        },
    }


def prepare_approval_task_continuation(
    approval: Mapping[str, Any] | None,
    execution: Mapping[str, Any] | None = None,
    *,
    rejected: bool = False,
) -> dict[str, Any] | None:
    """Build the inert inputs needed to return an approval to its task loop."""

    approval = approval if isinstance(approval, Mapping) else {}
    execution = execution if isinstance(execution, Mapping) else {}
    context = approval.get("taskContext")
    if not isinstance(context, Mapping) or not context:
        return None
    if rejected:
        completion = rejected_approval_completion(context)
    else:
        if _status(execution.get("status")) not in {"applied", "failed", "needs_user_action"}:
            return None
        completion = execution.get("taskCompletion")
        if not isinstance(completion, Mapping) or not completion:
            completion = approval.get("taskCompletion")
    if not isinstance(completion, Mapping) or not completion:
        return None

    approval_id = _bounded_text(approval.get("id"), 100)
    original_client_turn_id = _bounded_text(context.get("clientTurnId"), 180)
    continuation_client_turn_id = (
        f"{original_client_turn_id}:approval:{approval_id}"
        if original_client_turn_id
        else f"approval:{approval_id}"
    )[:240]
    params: dict[str, Any] = {
        "message": _bounded_text(context.get("objective"), 600),
        "session_id": _bounded_text(context.get("sessionId"), 180),
        "clientTurnId": continuation_client_turn_id,
        "projectRoot": _bounded_text(context.get("projectRoot"), 600),
        "provider": _bounded_text(context.get("provider"), 80),
        "providerLabel": _bounded_text(context.get("providerLabel"), 160),
        "model": _bounded_text(context.get("model"), 160),
        "_requestedContextLimit": context.get("contextLimit"),
        "history": _bounded_history(context.get("history")),
    }
    task_status = _status(completion.get("status"))
    terminal_plan: dict[str, Any] | None = None
    outcome = completion.get("outcome")
    outcome = outcome if isinstance(outcome, Mapping) else {}
    if task_status == "completed" and context.get("continueAfterApproval") is False:
        summary = _bounded_text(outcome.get("summary"), 600) or "The approved task completed."
        terminal_plan = {
            "summary": summary,
            "reply": summary,
            "planner": "runtime",
            "continueLoop": False,
            "nextStep": "done",
        }
    elif task_status != "completed":
        summary = _bounded_text(outcome.get("summary"), 600) or "The approved task needs user action."
        terminal_plan = {
            "summary": summary,
            "reply": summary,
            "planner": "runtime",
            "continueLoop": False,
            "nextStep": "tool_failed" if task_status == "failed" else "needs_user_action",
            "completionGate": {
                "status": "failed" if task_status == "failed" else "needs_user_action",
                "reason": "approval_rejected" if rejected else "approved_write_not_verified",
            },
        }
    requested_arguments = context.get("requestedArguments")
    approval_arguments = approval.get("arguments")
    task_continuation: dict[str, Any] = {
        "source": "approval_finished",
        "context": dict(context),
        "completion": dict(completion),
        "approvalId": approval_id,
        "arguments": dict(
            requested_arguments
            if isinstance(requested_arguments, Mapping) and requested_arguments
            else approval_arguments
            if isinstance(approval_arguments, Mapping)
            else {}
        ),
        "execution": dict(execution),
        "terminalPlan": terminal_plan,
    }
    execution_result = execution.get("result")
    execution_result = execution_result if isinstance(execution_result, Mapping) else {}
    capture_data = execution_result.get("data")
    capture_data = capture_data if isinstance(capture_data, Mapping) else execution_result
    capture_receipt = _bounded_text(capture_data.get("captureReceipt"), 256)
    if (
        _bounded_text(context.get("tool"), 160) == "vrcforge_capture_multi_screenshot"
        and capture_receipt
    ):
        task_continuation["plannerObservation"] = {
            "tool": "vrcforge_capture_multi_screenshot",
            "kind": "write",
            "status": _status(execution.get("status")),
            "result": {
                "captureReceipt": capture_receipt,
                "captureEvidenceId": _bounded_text(
                    capture_data.get("captureEvidenceId"), 160
                ),
                "angles": [
                    _bounded_text(item, 32)
                    for item in (
                        list(capture_data.get("angles") or [])[:4]
                        if isinstance(capture_data.get("angles"), (list, tuple))
                        else []
                    )
                    if _bounded_text(item, 32)
                ],
            },
            "outcome": _bounded_outcome(outcome),
        }
    return {
        "params": params,
        "agentName": (
            _bounded_text(context.get("agentName"), 160)
            or _bounded_text(approval.get("agentName"), 160)
            or "desktop-agent"
        ),
        "taskContinuation": task_continuation,
    }


def prepare_shell_task_continuation(
    seed: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Turn one terminal background Shell event into an inert task continuation."""

    if not isinstance(seed, Mapping) or not isinstance(event, Mapping):
        return None
    requested_arguments = seed.get("requestedArguments")
    if not isinstance(requested_arguments, Mapping):
        return None
    context = approval_task_context(
        seed,
        tool="shell",
        arguments=dict(requested_arguments),
    )
    if context is None:
        return None
    success = bool(
        _status(event.get("status")) == "finished"
        and event.get("exitCode") == 0
        and event.get("timedOut") is not True
        and event.get("cancelled") is not True
        and event.get("terminationFailed") is not True
    )
    raw_result = {
        "ok": success,
        "status": "executed" if success else "failed",
        "exitCode": event.get("exitCode"),
        "timedOut": event.get("timedOut") is True,
        "cancelled": event.get("cancelled") is True,
        "terminationFailed": event.get("terminationFailed") is True,
    }
    outcome = normalize_agent_tool_result(
        raw_result,
        fallback_summary=(
            "The background Shell action finished successfully."
            if success
            else "The background Shell action did not finish successfully."
        ),
        write=False,
    )
    completion = approval_completion(context, raw_result=raw_result, outcome=outcome)
    if completion is None:
        return None
    shell_session_id = _bounded_text(event.get("shellSessionId"), 100)
    execution_status = "applied" if success else "failed"
    prepared = prepare_approval_task_continuation(
        {
            "id": shell_session_id,
            "agentName": seed.get("agentName"),
            "arguments": dict(requested_arguments),
            "taskContext": context,
        },
        {
            "status": execution_status,
            "result": raw_result,
            "taskCompletion": completion,
        },
    )
    if prepared is None:
        return None
    original_client_turn_id = _bounded_text(context.get("clientTurnId"), 180)
    prepared["params"]["clientTurnId"] = (
        f"{original_client_turn_id}:shell:{shell_session_id}"
        if original_client_turn_id
        else f"shell:{shell_session_id}"
    )[:240]
    prepared["taskContinuation"]["source"] = "shell_process_finished"
    return prepared


def prepare_sub_agent_task_continuation(
    seed: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return one durable sub-agent terminal result to its original task."""

    if not isinstance(seed, Mapping) or not isinstance(event, Mapping):
        return None
    requested_arguments = seed.get("requestedArguments")
    if not isinstance(requested_arguments, Mapping):
        return None
    context = approval_task_context(
        seed,
        tool="vrcforge_delegate_subagent",
        arguments=dict(requested_arguments),
    )
    if context is None:
        return None
    parent_session_id = _bounded_text(event.get("parentSessionId"), 180)
    context_session_id = _bounded_text(context.get("sessionId"), 180)
    if parent_session_id and parent_session_id != context_session_id:
        return None
    task_status = _status(event.get("status"))
    result_payload = event.get("result")
    result_payload = result_payload if isinstance(result_payload, Mapping) else {}
    cancelled = task_status == "cancelled"
    success = task_status == "completed" and result_payload.get("ok") is not False
    raw_result = {
        "ok": success,
        "status": "executed" if success else "cancelled" if cancelled else "failed",
        "subAgentTaskId": _bounded_text(event.get("subAgentTaskId"), 100),
        "subAgentStatus": task_status,
        "summary": _bounded_text(event.get("summary") or event.get("error"), 600),
    }
    if not success:
        raw_result["error"] = result_payload.get("error") or event.get("error") or (
            "The delegated sub-agent task did not complete successfully."
        )
    outcome = normalize_agent_tool_result(
        raw_result,
        fallback_summary=(
            "The delegated sub-agent task completed."
            if success
            else "The delegated sub-agent task did not complete successfully."
        ),
        write=False,
    )
    completion = approval_completion(context, raw_result=raw_result, outcome=outcome)
    if completion is None:
        return None
    if cancelled:
        completion = {
            **completion,
            "status": "cancelled",
            "outcome": {
                **dict(outcome),
                "status": "needs_user_action",
                "summary": (
                    _bounded_text(event.get("error") or event.get("summary"), 600)
                    or "The delegated sub-agent task was cancelled."
                ),
            },
        }
    sub_agent_task_id = _bounded_text(event.get("subAgentTaskId"), 100)
    prepared = prepare_approval_task_continuation(
        {
            "id": sub_agent_task_id,
            "agentName": seed.get("agentName"),
            "arguments": dict(requested_arguments),
            "taskContext": context,
        },
        {
            "status": "applied" if success else "needs_user_action" if cancelled else "failed",
            "result": raw_result,
            "taskCompletion": completion,
        },
    )
    if prepared is None:
        return None
    original_client_turn_id = _bounded_text(context.get("clientTurnId"), 180)
    prepared["params"]["clientTurnId"] = (
        f"{original_client_turn_id}:subagent:{sub_agent_task_id}"
        if original_client_turn_id
        else f"subagent:{sub_agent_task_id}"
    )[:240]
    prepared["taskContinuation"]["source"] = "sub_agent_finished"
    if cancelled:
        summary = _bounded_text(event.get("error") or event.get("summary"), 600) or (
            "The delegated sub-agent task was cancelled."
        )
        prepared["taskContinuation"]["terminalPlan"] = {
            "summary": summary,
            "reply": summary,
            "planner": "runtime",
            "continueLoop": False,
            "nextStep": "cancelled",
            "completionGate": {"status": "cancelled", "reason": "sub_agent_cancelled"},
        }
    elif not success:
        # A worker failure is a correlated tool result, not the end of the
        # parent task.  Return it to the model so it can correct parameters,
        # choose another tool, or explain the actionable failure.
        prepared["taskContinuation"]["terminalPlan"] = None
    return prepared


@dataclass
class AgentTaskLoop:
    objective: str
    session_id: str = ""
    turn_id: str = ""
    client_turn_id: str = ""
    project_root: str = ""
    task_id: str = ""
    agent_name: str = ""
    provider: str = ""
    provider_label: str = ""
    model: str = ""
    context_limit: int | None = None
    tool_calls_used: int = 0
    provider_request_count: int = 0
    exposure_layer: str = "planning"
    history: list[dict[str, Any]] = field(default_factory=list)
    _actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    _requirements: dict[str, dict[str, Any]] = field(default_factory=dict)
    _skill_policy: dict[str, Any] = field(default_factory=dict)
    _skill_context: dict[str, Any] = field(default_factory=dict)
    _managed_visual_capture_action_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.objective = _bounded_text(self.objective, 600)
        self.task_id = _bounded_text(self.task_id, 80) or canonical_task_id(
            self.session_id,
            self.client_turn_id,
            self.objective,
        )
        self.tool_calls_used = max(0, min(int(self.tool_calls_used or 0), 3))
        self.provider_request_count = max(
            0,
            min(int(self.provider_request_count or 0), 100),
        )
        self.exposure_layer = (
            "execution" if _status(self.exposure_layer) == "execution" else "planning"
        )

    @classmethod
    def from_approval_context(
        cls,
        context: Mapping[str, Any],
        completion: Mapping[str, Any],
    ) -> AgentTaskLoop:
        loop = cls(
            _bounded_text(context.get("objective"), 600),
            session_id=_bounded_text(context.get("sessionId"), 180),
            turn_id=_bounded_text(context.get("turnId"), 180),
            client_turn_id=_bounded_text(context.get("clientTurnId"), 180),
            project_root=_bounded_text(context.get("projectRoot"), 600),
            task_id=_bounded_text(context.get("taskId"), 80),
            agent_name=_bounded_text(context.get("agentName"), 160),
            provider=_bounded_text(context.get("provider"), 80),
            provider_label=_bounded_text(context.get("providerLabel"), 160),
            model=_bounded_text(context.get("model"), 160),
            context_limit=(
                int(context.get("contextLimit"))
                if isinstance(context.get("contextLimit"), int)
                else None
            ),
            tool_calls_used=max(0, min(int(context.get("toolCallsUsed") or 0), 3)),
            provider_request_count=max(
                0,
                min(int(context.get("providerRequestCount") or 0), 100),
            ),
            exposure_layer=str(context.get("exposureLayer") or "execution"),
            history=_bounded_history(context.get("history")),
        )
        for item in list(context.get("priorActions") or [])[:3]:
            if isinstance(item, Mapping) and (bounded := _bounded_action(item)) is not None:
                loop._actions[bounded["actionId"]] = bounded
        for item in list(context.get("priorRequirements") or [])[:3]:
            if isinstance(item, Mapping) and (bounded := _bounded_requirement(item)) is not None:
                loop._requirements[bounded["requirementId"]] = bounded
        loop._managed_visual_capture_action_ids = list(
            dict.fromkeys(
                bounded
                for item in list(context.get("managedVisualCaptureActionIds") or [])[:2]
                if (bounded := _bounded_text(item, 80))
            )
        )
        loop._skill_context = _bounded_skill_context(context.get("skillContext"))
        loop._skill_policy = _bounded_skill_policy(
            context.get("skillPolicy") or loop._skill_context
        )
        completion_outcome = completion.get("outcome")
        current = _bounded_action(
            {
                "actionId": completion.get("actionId") or context.get("actionId"),
                "kind": _bounded_text(context.get("kind"), 32) or "write",
                "tool": completion.get("tool") or context.get("tool"),
                "status": completion.get("status"),
                "attempts": 1,
                "outcome": (
                    completion_outcome if isinstance(completion_outcome, Mapping) else {}
                ),
            }
        )
        if current is not None:
            loop._actions[current["actionId"]] = current
            if (
                current["tool"] == "vrcforge_capture_multi_screenshot"
                and current["status"] == "completed"
            ):
                loop._remember_managed_visual_capture(current["actionId"])
        return loop

    def _remember_managed_visual_capture(self, action_id: Any) -> None:
        bounded = _bounded_text(action_id, 80)
        if not bounded:
            return
        self._managed_visual_capture_action_ids = [
            item
            for item in self._managed_visual_capture_action_ids
            if item != bounded
        ]
        self._managed_visual_capture_action_ids.append(bounded)
        self._managed_visual_capture_action_ids = self._managed_visual_capture_action_ids[-2:]

    def approval_seed(
        self,
        *,
        tool_calls_used: int | None = None,
        exposure_layer: str | None = None,
        requested_tool: str = "",
        requested_kind: str = "write",
        requested_arguments: Mapping[str, Any] | None = None,
        continue_after_approval: bool = True,
        provider_request_count: int | None = None,
    ) -> dict[str, Any]:
        effective_kind = _bounded_text(requested_kind, 32) or "write"
        effective_tool = _bounded_text(requested_tool, 160)
        effective_arguments = dict(requested_arguments or {})
        return {
            "schema": TASK_LOOP_SCHEMA,
            "taskId": self.task_id,
            "objective": _bounded_text(self.objective, 600),
            "sessionId": _bounded_text(self.session_id, 180),
            "turnId": _bounded_text(self.turn_id, 180),
            "clientTurnId": _bounded_text(self.client_turn_id, 180),
            "projectRoot": _bounded_text(self.project_root, 600),
            "agentName": _bounded_text(self.agent_name, 160),
            "provider": _bounded_text(self.provider, 80),
            "providerLabel": _bounded_text(self.provider_label, 160),
            "model": _bounded_text(self.model, 160),
            "contextLimit": self.context_limit,
            "toolCallsUsed": max(
                0,
                min(
                    int(self.tool_calls_used if tool_calls_used is None else tool_calls_used),
                    3,
                ),
            ),
            "providerRequestCount": max(
                0,
                min(
                    int(
                        self.provider_request_count
                        if provider_request_count is None
                        else provider_request_count
                    ),
                    100,
                ),
            ),
            "exposureLayer": exposure_layer or self.exposure_layer,
            "actions": [dict(item) for item in self._actions.values()][-3:],
            "requirements": [dict(item) for item in self._requirements.values()][-3:],
            "managedVisualCaptureActionIds": list(
                self._managed_visual_capture_action_ids
            ),
            "skillPolicy": dict(self._skill_policy),
            "skillContext": dict(self._skill_context),
            "history": _bounded_history(self.history),
            "requestedTool": effective_tool,
            "requestedKind": effective_kind,
            "requestedArguments": effective_arguments,
            "requestedActionId": (
                canonical_action_id(effective_kind, effective_tool, effective_arguments)
                if effective_tool
                else ""
            ),
            "continueAfterApproval": bool(continue_after_approval),
        }

    def planner_observations(self) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        if self._skill_context:
            observations.append(
                {
                    "kind": "skill_context",
                    "status": "loaded",
                    "synthetic": True,
                    "skillContext": dict(self._skill_context),
                }
            )
        observations.extend(
            [
            {
                "tool": item["tool"],
                "kind": item["kind"],
                "status": item["status"],
                "result": {"summary": item["outcome"].get("summary", "")},
                "outcome": dict(item["outcome"]),
                "actionId": item["actionId"],
            }
            for item in self._actions.values()
            ]
        )
        return observations

    def record_action(
        self,
        *,
        kind: str,
        tool: str,
        arguments: Any,
        raw_result: Any,
        outcome: Mapping[str, Any],
        action_id: str = "",
        correction_for_action_id: str = "",
        pre_provider: bool = False,
    ) -> dict[str, Any]:
        action_id = _bounded_text(action_id, 80) or canonical_action_id(kind, tool, arguments)
        correction_id = _bounded_text(correction_for_action_id, 80)
        accepted_correction_id = ""
        if correction_id and correction_id != action_id:
            previous_branch = self._actions.get(correction_id)
            if (
                previous_branch is not None
                and _status(previous_branch.get("status"))
                in {"failed", "needs_user_action"}
                and _bounded_text(previous_branch.get("kind"), 32)
                == _bounded_text(kind, 32)
                and _bounded_text(previous_branch.get("tool"), 160)
                == _bounded_text(tool, 160)
            ):
                accepted_correction_id = correction_id
                previous_branch["status"] = "superseded"
                previous_branch["supersededBy"] = action_id
                for requirement in self._requirements.values():
                    if requirement.get("actionId") == correction_id:
                        requirement["actionId"] = action_id
        requirement_profile = next(
            (
                _bounded_text(requirement.get("verificationProfile"), 80)
                for requirement in self._requirements.values()
                if (
                    (
                        not _bounded_text(requirement.get("actionId"), 80)
                        or _bounded_text(requirement.get("actionId"), 80) == action_id
                    )
                    and _bounded_text(requirement.get("kind"), 32) == _bounded_text(kind, 32)
                    and _bounded_text(requirement.get("tool"), 160) == _bounded_text(tool, 160)
                    and _bounded_text(requirement.get("verificationProfile"), 80)
                )
            ),
            "",
        )
        effective = apply_declared_verification(
            tool,
            raw_result,
            outcome,
            verification_profile=requirement_profile,
        )
        outcome_status = _status(effective.get("status"))
        running_state = _running_state(raw_result)
        if outcome_status == "failed":
            lifecycle = "failed"
        elif outcome_status == "needs_user_action":
            lifecycle = "needs_user_action"
        elif running_state:
            lifecycle = "running"
        else:
            lifecycle = "completed"
        previous = self._actions.get(action_id)
        attempts = int((previous or {}).get("attempts") or 0) + 1
        record = {
            "actionId": action_id,
            "kind": _bounded_text(kind, 32),
            "tool": _bounded_text(tool, 160),
            "status": lifecycle,
            "attempts": attempts,
            "outcome": effective,
        }
        if pre_provider:
            record["preProvider"] = True
        if running_state:
            record["runtimeStatus"] = running_state
        self._actions[action_id] = record
        if tool == "vrcforge_capture_multi_screenshot" and lifecycle == "completed":
            self._remember_managed_visual_capture(action_id)
        result = dict(record)
        if accepted_correction_id:
            result["correctedActionId"] = accepted_correction_id
        return result

    def require_action(
        self,
        *,
        kind: str,
        tool: str,
        arguments: Any | None = None,
        verification_profile: str = "",
    ) -> dict[str, Any]:
        effective_profile = _bounded_text(verification_profile, 80) or _TOOL_DEFAULT_VERIFICATION_PROFILE.get(
            str(tool or "").strip(),
            "canonical_tool_result" if _status(kind) == "skill" else "",
        )
        requirement = _bounded_requirement(
            {
                "kind": kind,
                "tool": tool,
                "actionId": (
                    canonical_action_id(kind, tool, arguments)
                    if arguments is not None
                    else ""
                ),
                "verificationProfile": effective_profile,
            }
        )
        if requirement is None:
            raise ValueError("task requirement requires kind and tool")
        self._requirements[requirement["requirementId"]] = requirement
        return dict(requirement)

    def activate_skill_policy(
        self,
        *,
        name: str,
        instructions: Any = "",
        allowed_tools: Any,
        disallowed_tools: Any,
    ) -> dict[str, Any]:
        self._skill_context = _bounded_skill_context(
            {
                "name": name,
                "instructions": instructions,
                "allowedTools": allowed_tools,
                "disallowedTools": disallowed_tools,
            }
        )
        self._skill_policy = _bounded_skill_policy(self._skill_context)
        return dict(self._skill_policy)

    def skill_policy_block_reason(self, tool: str) -> str:
        policy = self._skill_policy
        if not policy:
            return ""
        tool = _bounded_text(tool, 160)
        disallowed = set(policy.get("disallowedTools") or [])
        if tool in disallowed:
            return "skill_tool_disallowed"
        allowed = set(policy.get("allowedTools") or [])
        if allowed and tool not in allowed:
            return "skill_tool_not_allowed"
        return ""

    def planner_projection(self) -> dict[str, Any]:
        return {
            "schema": TASK_LOOP_SCHEMA,
            "taskId": self.task_id,
            "objective": _bounded_text(self.objective, 600),
            "actions": [dict(item) for item in self._actions.values()],
            "requirements": [dict(item) for item in self._requirements.values()],
            "skillPolicy": dict(self._skill_policy),
        }

    def completed_action_ids(self) -> list[str]:
        return [
            str(action.get("actionId") or "")
            for action in self._actions.values()
            if _status(action.get("status")) == "completed"
        ]

    def historical_steps(self) -> list[dict[str, Any]]:
        """Project only bounded action identity for a resumed Runtime turn."""

        steps: list[dict[str, Any]] = []
        for action in self._actions.values():
            lifecycle = _status(action.get("status"))
            outcome = action.get("outcome")
            outcome_status = (
                _status(outcome.get("status"))
                if isinstance(outcome, Mapping)
                else ""
            )
            status = outcome_status if lifecycle == "superseded" else lifecycle
            step = {
                "index": len(steps),
                "kind": _bounded_text(action.get("kind"), 32),
                "tool": _bounded_text(action.get("tool"), 160),
                "status": status,
                "actionId": _bounded_text(action.get("actionId"), 80),
                "historical": True,
            }
            if action.get("preProvider") is True:
                step["preProvider"] = True
            steps.append(step)
        return steps

    def snapshot(self, status_override: str = "") -> dict[str, Any]:
        statuses = {
            str(item.get("status") or "")
            for item in self._actions.values()
            if _status(item.get("status")) != "superseded"
        }
        if "cancelled" in statuses:
            status = "cancelled"
        elif "failed" in statuses:
            status = "failed"
        elif "needs_user_action" in statuses:
            status = "needs_user_action"
        elif "running" in statuses:
            status = "running"
        elif self._actions and statuses == {"completed"}:
            status = "completed"
        else:
            status = "planning"
        return {
            **self.planner_projection(),
            "status": _bounded_text(status_override, 40) or status,
        }

    def gate_terminal(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        gated = dict(plan)
        next_step = _status(gated.get("nextStep"))
        if next_step in _TERMINAL_BYPASS_STEPS:
            gated["task"] = self.snapshot(next_step)
            return gated

        for action in self._actions.values():
            status = _status(action.get("status"))
            if status == "superseded":
                continue
            outcome = action.get("outcome")
            if status in {"failed", "needs_user_action"} and isinstance(outcome, Mapping):
                replacement = completion_gate_plan(gated, outcome)
                if replacement is not None:
                    replacement["task"] = self.snapshot()
                    return replacement

        running = [
            action for action in self._actions.values() if _status(action.get("status")) == "running"
        ]
        if running:
            label = _bounded_text(running[0].get("tool"), 160) or "background action"
            gated.update(
                {
                    "summary": f"{label} is still running.",
                    "reply": (
                        f"{label} is still running, so this task is not complete yet. "
                        "Poll or inspect the active process before claiming completion."
                    ),
                    "continueLoop": False,
                    "nextStep": "waiting_for_tool",
                    "completionGate": {
                        "status": "running",
                        "reason": "action_not_terminal",
                    },
                }
            )
            gated["task"] = self.snapshot()
            return gated

        completed_ids = self.completed_action_ids()
        missing_requirements: list[str] = []
        for requirement in self._requirements.values():
            required_action_id = str(requirement.get("actionId") or "")
            matched = any(
                _status(action.get("status")) == "completed"
                and (
                    (
                        str(requirement.get("kind") or "") == "action"
                        and str(requirement.get("tool") or "") == "*"
                    )
                    or (
                        str(action.get("kind") or "") == str(requirement.get("kind") or "")
                        and str(action.get("tool") or "") == str(requirement.get("tool") or "")
                    )
                )
                and (not required_action_id or str(action.get("actionId") or "") == required_action_id)
                for action in self._actions.values()
            )
            if not matched:
                missing_requirements.append(str(requirement.get("requirementId") or ""))
        if missing_requirements:
            gated.update(
                {
                    "summary": "The required task action has not completed.",
                    "reply": (
                        "A prerequisite or partial tool action returned, but the required task action "
                        "has not completed, so I cannot mark the task done."
                    ),
                    "continueLoop": False,
                    "nextStep": "completion_unverified",
                    "completionGate": {
                        "status": "needs_user_action",
                        "reason": "required_action_missing",
                        "requirementIds": missing_requirements,
                    },
                }
            )
            gated["task"] = self.snapshot("completion_unverified")
            return gated

        if not completed_ids:
            gated["task"] = self.snapshot()
            return gated

        planner = _status(gated.get("planner"))
        if planner == "llm":
            claim = gated.get("completionClaim")
            evidence_ids = []
            satisfied = False
            if isinstance(claim, Mapping):
                satisfied = claim.get("satisfied") is True
                raw_ids = claim.get("evidenceActionIds") or claim.get("evidence_action_ids")
                if isinstance(raw_ids, list):
                    evidence_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
            if not satisfied or set(evidence_ids) != set(completed_ids):
                gated.update(
                    {
                        "summary": "The final completion claim was not bound to the executed actions.",
                        "reply": (
                            "The tool actions returned, but I cannot honestly mark the task complete "
                            "because the final claim did not cite the exact completed action evidence."
                        ),
                        "continueLoop": False,
                        "nextStep": "completion_unverified",
                        "completionGate": {
                            "status": "needs_user_action",
                            "reason": "completion_claim_unbound",
                        },
                    }
                )
                gated["task"] = self.snapshot("completion_unverified")
                return gated
        else:
            raw_ids = gated.get("completionActionIds") or gated.get("completion_action_ids")
            evidence_ids = (
                [str(item).strip() for item in raw_ids if str(item).strip()]
                if isinstance(raw_ids, list)
                else []
            )
            if gated.get("completionSatisfied") is not True or set(evidence_ids) != set(completed_ids):
                gated.update(
                    {
                        "summary": "The deterministic completion decision was not bound to the executed actions.",
                        "reply": (
                            "The tool actions returned, but the runtime did not bind an exact completion "
                            "decision to those actions, so this task is not marked done."
                        ),
                        "continueLoop": False,
                        "nextStep": "completion_unverified",
                        "completionGate": {
                            "status": "needs_user_action",
                            "reason": "runtime_completion_unbound",
                        },
                    }
                )
                gated["task"] = self.snapshot("completion_unverified")
                return gated

        gated["nextStep"] = "done"
        gated["taskCompletion"] = {
            "schema": TASK_LOOP_SCHEMA,
            "status": "completed",
            "evidenceActionIds": completed_ids,
        }
        gated["task"] = self.snapshot("completed")
        return gated
