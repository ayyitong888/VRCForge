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

from agent_tool_result_contract import completion_gate_plan


TASK_LOOP_SCHEMA = "vrcforge.agent_task_loop.v1"
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
_VERIFICATION_PROFILES: dict[str, tuple[tuple[str, bool], ...]] = {
    "vrcforge_create_gameobject": (
        ("persistedReadback", True),
        ("sceneSaved", True),
    ),
}


def _status(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


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


def _field_state(value: Any, field_name: str) -> bool | None:
    for view in _mapping_views(value):
        if field_name in view:
            field_value = view.get(field_name)
            if isinstance(field_value, bool):
                return field_value
            return None
    return None


def _bounded_outcome(value: Mapping[str, Any]) -> dict[str, Any]:
    verification = value.get("verification")
    return {
        "status": _bounded_text(value.get("status"), 40),
        "summary": _bounded_text(value.get("summary"), 600),
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
    if status not in {"completed", "failed", "needs_user_action", "running"}:
        return None
    outcome = value.get("outcome")
    return {
        "actionId": action_id,
        "kind": _bounded_text(value.get("kind"), 32),
        "tool": tool,
        "status": status,
        "attempts": max(1, min(int(value.get("attempts") or 1), 3)),
        "outcome": _bounded_outcome(outcome if isinstance(outcome, Mapping) else {}),
    }


def apply_declared_verification(
    tool: str,
    raw_result: Any,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the one declared VRCForge postcondition profile, if present."""

    bounded = _bounded_outcome(outcome)
    requirements = _VERIFICATION_PROFILES.get(str(tool or "").strip(), ())
    if not requirements or _status(bounded.get("status")) != "ok":
        return bounded

    checks: list[dict[str, str]] = []
    failed: list[str] = []
    for field_name, expected in requirements:
        actual = _field_state(raw_result, field_name)
        state = "passed" if actual is expected else "failed"
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
    requested_action_id = canonical_action_id(
        requested_kind,
        requested_tool,
        requested_arguments,
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
        "requestedArguments": requested_arguments,
        "requestedActionId": requested_action_id,
        "actionId": requested_action_id,
        "kind": requested_kind,
        "tool": requested_tool,
        "verificationProfile": (
            "persisted_scene_write"
            if tool in _VERIFICATION_PROFILES
            else "canonical_tool_result"
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
    verified = apply_declared_verification(tool, raw_result, outcome)
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
        "history": [],
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
    return {
        "params": params,
        "agentName": (
            _bounded_text(context.get("agentName"), 160)
            or _bounded_text(approval.get("agentName"), 160)
            or "desktop-agent"
        ),
        "taskContinuation": {
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
        },
    }


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
    exposure_layer: str = "planning"
    _actions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.objective = _bounded_text(self.objective, 600)
        self.task_id = _bounded_text(self.task_id, 80) or canonical_task_id(
            self.session_id,
            self.client_turn_id,
            self.objective,
        )
        self.tool_calls_used = max(0, min(int(self.tool_calls_used or 0), 3))
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
            exposure_layer=str(context.get("exposureLayer") or "execution"),
        )
        for item in list(context.get("priorActions") or [])[:3]:
            if isinstance(item, Mapping) and (bounded := _bounded_action(item)) is not None:
                loop._actions[bounded["actionId"]] = bounded
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
        return loop

    def approval_seed(
        self,
        *,
        tool_calls_used: int | None = None,
        exposure_layer: str | None = None,
        requested_tool: str = "",
        requested_kind: str = "write",
        requested_arguments: Mapping[str, Any] | None = None,
        continue_after_approval: bool = True,
    ) -> dict[str, Any]:
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
            "exposureLayer": exposure_layer or self.exposure_layer,
            "actions": [dict(item) for item in self._actions.values()][-3:],
            "requestedTool": _bounded_text(requested_tool, 160),
            "requestedKind": _bounded_text(requested_kind, 32) or "write",
            "requestedArguments": dict(requested_arguments or {}),
            "continueAfterApproval": bool(continue_after_approval),
        }

    def planner_observations(self) -> list[dict[str, Any]]:
        return [
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

    def record_action(
        self,
        *,
        kind: str,
        tool: str,
        arguments: Any,
        raw_result: Any,
        outcome: Mapping[str, Any],
        action_id: str = "",
    ) -> dict[str, Any]:
        action_id = _bounded_text(action_id, 80) or canonical_action_id(kind, tool, arguments)
        effective = apply_declared_verification(tool, raw_result, outcome)
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
        if running_state:
            record["runtimeStatus"] = running_state
        self._actions[action_id] = record
        return dict(record)

    def planner_projection(self) -> dict[str, Any]:
        return {
            "schema": TASK_LOOP_SCHEMA,
            "taskId": self.task_id,
            "objective": _bounded_text(self.objective, 600),
            "actions": [dict(item) for item in self._actions.values()],
        }

    def snapshot(self, status_override: str = "") -> dict[str, Any]:
        statuses = {str(item.get("status") or "") for item in self._actions.values()}
        if "failed" in statuses:
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

        completed_ids = [
            str(action.get("actionId") or "")
            for action in self._actions.values()
            if _status(action.get("status")) == "completed"
        ]
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

        gated["nextStep"] = "done"
        gated["taskCompletion"] = {
            "schema": TASK_LOOP_SCHEMA,
            "status": "completed",
            "evidenceActionIds": completed_ids,
        }
        gated["task"] = self.snapshot("completed")
        return gated
