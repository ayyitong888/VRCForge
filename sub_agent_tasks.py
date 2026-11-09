from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field

from runtime_error_schema import runtime_error_from_exception
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable

from agent_command_safety import normalize_filesystem_path
from background_goal_runtime import RuntimeLaneBudget
from runtime_planner_service import public_runtime_payload


SUB_AGENT_SCHEMA = "vrcforge.sub_agent_task.v2"
SUB_AGENT_LIST_SCHEMA = "vrcforge.sub_agent_tasks.v2"
SUB_AGENT_LOG_SCHEMA = "vrcforge.sub_agent_lifecycle.v2"
SUB_AGENT_RESULT_SCHEMA = "vrcforge.sub_agent_result.v1"
_RECOVERY_CHECKPOINT_SCHEMA = "vrcforge.sub_agent_recovery.v1"
_RECOVERY_BOUNDARY_BYTES = 64
SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT = 5

_RUNNING_STATUSES = {"queued", "running", "cancelling"}
_RETRYABLE_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
_PARENT_CONTINUATION_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
# Handler watchdog: a hung sub-agent must eventually produce a terminal event
# so the parent's waiting_for_tool turn is never parked forever.
# Established CLI agents bound sub-agent waits at a hard 1h max; VRCForge
# sub-agent turns are multi-step so the default matches the shell supervisor.
DEFAULT_SUB_AGENT_TIMEOUT_SECONDS = 1800.0
MAX_SUB_AGENT_TIMEOUT_SECONDS = 3600.0
_MERGE_DECISIONS = {"adopted", "dismissed"}

SubAgentHandler = Callable[[dict[str, Any], threading.Event], dict[str, Any]]


@dataclass
class SubAgentRole:
    id: str
    title: str
    description: str
    tool_profile: str = "read-only"
    read_only: bool = True
    handler: str = "skill"
    model: str = ""
    system_prompt: str = ""
    max_steps: int = 4
    tools: tuple[str, ...] | None = None


@dataclass
class SubAgentTask:
    id: str
    role: str
    display_name: str
    task: str
    parent_chat_id: str = ""
    parent_session_id: str = ""
    project_path: str = ""
    tool_profile: str = "read-only"
    model: str = ""
    system_prompt: str = ""
    max_steps: int = 4
    tools: tuple[str, ...] | None = None
    handler: str = "skill"
    provider: str = ""
    timeout_seconds: float = DEFAULT_SUB_AGENT_TIMEOUT_SECONDS
    resumable: bool = False
    child_session_id: str = ""
    status: str = "queued"
    created_at: str = field(default_factory=lambda: utc_now())
    started_at: str = ""
    stopped_at: str = ""
    updated_at: str = field(default_factory=lambda: utc_now())
    params: dict[str, Any] = field(default_factory=dict)
    general_read_roots: tuple[str, ...] = ()
    result: dict[str, Any] | None = None
    result_available: bool = False
    result_unavailable: bool = False
    summary: str = ""
    error: str = ""
    error_code: str = ""
    cancel_requested: bool = False
    event_count: int = 0
    revision: int = 0
    retry_of: str = ""
    resume_of: str = ""
    handoff_status: str = ""
    handoff_at: str = ""
    merged_at: str = ""
    merged_chat_id: str = ""
    merge_decision: str = ""
    parent_continuation_status: str = ""


class SubAgentTaskRegistry:
    """Durable sub-agent lifecycle registry.

    Lifecycle transitions are appended as full v2 task projections before the
    in-memory projection changes. Worker results live in atomic sidecars so a
    large result never has to be duplicated in every JSONL event.
    """

    def __init__(
        self,
        artifact_dir: str | Path,
        roles: list[SubAgentRole],
        handlers: dict[str, SubAgentHandler],
        max_concurrent: int = 3,
        reconcile_on_init: bool = True,
        lane_budget: RuntimeLaneBudget | None = None,
        task_finished: Callable[[dict[str, Any]], None] | None = None,
        wait_parent_idle: Callable[..., bool] | None = None,
        defer_projection_load: bool = False,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.roles = {role.id: role for role in roles}
        self.handlers = dict(handlers)
        self.max_concurrent = max(1, min(int(max_concurrent), SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT))
        self._lane_budget = lane_budget
        self._task_finished = task_finished or (lambda _event: None)
        self._wait_parent_idle = wait_parent_idle
        self._tasks: dict[str, SubAgentTask] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._active_child_sessions: dict[str, str] = {}
        self._parent_continuations_inflight: set[str] = set()
        self._lock = threading.RLock()
        self._parent_continuation_condition = threading.Condition(self._lock)
        self._startup_reconciled = False
        self._shutdown = False
        self._accepting = True
        if not defer_projection_load:
            with self._lock:
                self._load_projection_locked()
        if reconcile_on_init:
            self.reconcile_startup(refresh_from_disk=defer_projection_load)

    def reconcile_startup(
        self,
        *,
        refresh_from_disk: bool = False,
        replay_parent_continuations: bool = True,
    ) -> bool:
        """Reconcile work owned by a prior process, at most once per instance."""

        with self._lock:
            if self._shutdown:
                return False
            if self._startup_reconciled:
                return False
            if refresh_from_disk:
                alive_workers = {
                    task_id: worker for task_id, worker in self._threads.items() if worker.is_alive()
                }
                if alive_workers:
                    raise RuntimeError("Cannot refresh sub-agent startup state while local workers are alive.")
                refreshed_tasks, refreshed_cancel_events = self._read_projection_state_locked(strict_io=True)
                self._tasks = refreshed_tasks
                self._cancel_events = refreshed_cancel_events
                self._threads.clear()
                self._parent_continuations_inflight.clear()
            self._recover_orphaned_result_sidecars_locked()
            self._reconcile_interrupted_tasks_locked()
            self._interrupt_dispatched_parent_continuations_locked()
            self._stage_existing_parent_continuations_locked()
            pending_continuations = [
                task.id
                for task in self._tasks.values()
                if task.parent_continuation_status == "pending"
            ]
            self._startup_reconciled = True
            self._accepting = True
        if replay_parent_continuations:
            self.replay_parent_continuations(pending_continuations)
        return True

    def replay_parent_continuations(self, task_ids: list[str] | None = None) -> int:
        """Replay staged parent continuations outside startup's critical path."""

        with self._lock:
            if self._shutdown or not self._accepting:
                return 0
            if task_ids is None:
                task_ids = [
                    task.id
                    for task in self._tasks.values()
                    if task.parent_continuation_status == "pending"
                ]
        attempted = 0
        for task_id in task_ids:
            with self._lock:
                if self._shutdown or not self._accepting:
                    break
            attempted += 1
            self._notify_task_finished(task_id)
        return attempted

    def interrupted_parent_continuations(self) -> list[dict[str, Any]]:
        """Return bounded private identities for UI-only ambiguous terminal notices."""

        with self._lock:
            if self._shutdown or not self._accepting:
                return []
            events: list[dict[str, Any]] = []
            for task in self._tasks.values():
                seed = task.params.get("_taskSeed")
                if (
                    task.parent_continuation_status != "interrupted"
                    or not isinstance(seed, dict)
                ):
                    continue
                events.append(
                    {
                        "subAgentTaskId": task.id,
                        "parentSessionId": task.parent_session_id,
                        "taskSeed": copy.deepcopy(seed),
                        "summary": task.summary,
                        "error": task.error,
                        "errorCode": task.error_code,
            "timeoutSeconds": task.timeout_seconds,
                    }
                )
            return events[-200:]

    def start(self) -> None:
        """Open admission for one app lifecycle after the prior owner stopped."""

        with self._lock:
            if any(worker.is_alive() for worker in self._threads.values()):
                raise RuntimeError("Cannot restart sub-agent admission while workers are active.")
            if self._parent_continuations_inflight:
                raise RuntimeError("Cannot restart sub-agent admission while continuations are active.")
            self._shutdown = False
            self._accepting = False
            self._startup_reconciled = False

    def shutdown(self, timeout_seconds: float = 5.0) -> dict[str, Any]:
        """Stop admission, request cancellation, and boundedly collect local workers."""

        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("shutdown timeout must be a non-negative number") from exc
        if timeout != timeout or timeout < 0:
            raise ValueError("shutdown timeout must be a non-negative number")
        timeout = min(timeout, 30.0)
        cancellation_errors: list[dict[str, str]] = []
        cancel_requested: list[str] = []
        with self._lock:
            self._shutdown = True
            self._accepting = False
            workers = {
                task_id: worker
                for task_id, worker in self._threads.items()
                if worker.is_alive()
            }
            for task_id in workers:
                task = self._tasks.get(task_id)
                cancel_event = self._cancel_events.get(task_id)
                if task is not None and task.status in _RUNNING_STATUSES:
                    cancel_requested.append(task_id)
                    if not task.cancel_requested or task.status != "cancelling":
                        next_task = copy.deepcopy(task)
                        next_task.cancel_requested = True
                        next_task.status = "cancelling"
                        try:
                            self._commit_task_event_locked(
                                task,
                                next_task,
                                "cancel_requested",
                                {"reason": "registry_shutdown"},
                            )
                        except Exception as exc:  # noqa: BLE001 - cancellation signal still owns process shutdown.
                            cancellation_errors.append(
                                {"taskId": task_id, "error": str(exc)[:500]}
                            )
                if cancel_event is not None:
                    cancel_event.set()

        deadline = time.monotonic() + timeout
        current_thread = threading.current_thread()
        for worker in workers.values():
            if worker is current_thread:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(remaining)

        with self._lock:
            while self._parent_continuations_inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._parent_continuation_condition.wait(remaining)
            timed_out = sorted(
                task_id for task_id, worker in workers.items() if worker.is_alive()
            )
            continuation_timeouts = sorted(self._parent_continuations_inflight)
            joined = sorted(task_id for task_id in workers if task_id not in timed_out)
            for task_id in joined:
                if self._threads.get(task_id) is workers[task_id]:
                    self._threads.pop(task_id, None)
            return {
                "ok": not timed_out and not continuation_timeouts and not cancellation_errors,
                "shutdown": True,
                "cancelRequestedTaskIds": sorted(cancel_requested),
                "joinedTaskIds": joined,
                "timedOutTaskIds": timed_out,
                "timedOutContinuationTaskIds": continuation_timeouts,
                "cancellationErrors": cancellation_errors,
            }

    def list_roles(self) -> list[dict[str, Any]]:
        return [self._serialize_role(role) for role in self.roles.values()]

    def list_tasks(self, include_events: bool = False, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
            tasks = tasks[: max(1, min(int(limit), 200))]
            return {
                "ok": True,
                "schema": SUB_AGENT_LIST_SCHEMA,
                "tasks": [self._serialize_task(task, include_events=include_events) for task in tasks],
                "count": len(tasks),
                "roles": self.list_roles(),
                "maxConcurrent": self.max_concurrent,
                "runningCount": self._running_count_locked(),
            }

    def active_parent_task_ids(
        self,
        parent_session_id: str,
        *,
        parent_turn_id: str = "",
        parent_client_turn_id: str = "",
    ) -> list[str]:
        if not parent_session_id:
            return []
        with self._lock:
            active_ids = []
            for task in self._tasks.values():
                if task.parent_session_id != parent_session_id or (
                    task.status not in _RUNNING_STATUSES and task.parent_continuation_status != "pending"
                ):
                    continue
                seed = task.params.get("_taskSeed")
                seed = seed if isinstance(seed, dict) else {}
                if parent_turn_id and str(seed.get("turnId") or "") != parent_turn_id:
                    continue
                if parent_client_turn_id and str(seed.get("clientTurnId") or "") != parent_client_turn_id:
                    continue
                active_ids.append(task.id)
            return active_ids

    def get_task(self, task_id: str, include_events: bool = True) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            return {"ok": True, "task": self._serialize_task(task, include_events=include_events)}

    def create_task(
        self,
        *,
        role: str,
        task: str,
        display_name: str,
        parent_chat_id: str = "",
        parent_session_id: str = "",
        project_path: str = "",
        params: dict[str, Any] | None = None,
        general_read_roots: tuple[str, ...] = (),
        retry_of: str = "",
        _child_session_id: str = "",
        _resume_of: str = "",
    ) -> dict[str, Any]:
        role_id = str(role or "").strip() or "project_index_review"
        if role_id not in self.roles:
            raise ValueError(f"Unknown sub-agent role: {role_id}")
        if role_id not in self.handlers:
            raise ValueError(f"Sub-agent role is not executable: {role_id}")
        task_text = str(task or "").strip() or self.roles[role_id].title
        role_spec = self.roles[role_id]
        task_params = copy.deepcopy(params or {})
        task_params.pop("_generalReadRoots", None)
        model_value = task_params.get("model")
        model = str(model_value if model_value is not None else role_spec.model or "").strip()
        system_prompt_value = task_params.get(
            "system_prompt",
            task_params.get("systemPrompt", role_spec.system_prompt),
        )
        system_prompt = str(system_prompt_value or "").strip()
        max_steps_value = task_params.get("max_steps", task_params.get("maxSteps", role_spec.max_steps))
        if max_steps_value is None:
            max_steps_value = role_spec.max_steps or 4
        max_steps = max(1, min(int(max_steps_value), 12))
        raw_timeout = task_params.get("timeoutSeconds", task_params.get("timeout_seconds"))
        timeout_seconds = DEFAULT_SUB_AGENT_TIMEOUT_SECONDS
        if raw_timeout is not None:
            try:
                timeout_seconds = max(1.0, min(float(raw_timeout), MAX_SUB_AGENT_TIMEOUT_SECONDS))
            except (TypeError, ValueError):
                timeout_seconds = DEFAULT_SUB_AGENT_TIMEOUT_SECONDS

        role_tools = self._normalize_tool_allowlist(role_spec.tools)
        requested_tools = self._normalize_tool_allowlist(task_params.get("tools")) \
            if "tools" in task_params and task_params.get("tools") is not None else None
        if role_tools is None:
            tools = requested_tools
        elif requested_tools is None:
            tools = role_tools
        else:
            requested_set = set(requested_tools)
            tools = tuple(tool for tool in role_tools if tool in requested_set)

        with self._lock:
            self._ensure_accepting_locked()
            if self._running_count_locked() >= self.max_concurrent:
                raise RuntimeError(f"Sub-agent concurrency limit reached ({self.max_concurrent}).")
        task_id = f"sub_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        lane_acquired = False
        if self._lane_budget is not None:
            lane_acquired = self._lane_budget.acquire("interactive", task_id)
            if not lane_acquired:
                raise RuntimeError("Shared runtime concurrency limit reached.")
        with self._lock:
            if self._shutdown:
                if lane_acquired and self._lane_budget is not None:
                    self._lane_budget.release(task_id)
                self._ensure_accepting_locked()
            if self._running_count_locked() >= self.max_concurrent:
                if lane_acquired and self._lane_budget is not None:
                    self._lane_budget.release(task_id)
                raise RuntimeError(f"Sub-agent concurrency limit reached ({self.max_concurrent}).")
            sub_task = SubAgentTask(
                id=task_id,
                role=role_id,
                display_name=str(display_name or "").strip() or "Manuka",
                task=task_text,
                parent_chat_id=str(parent_chat_id or "").strip(),
                parent_session_id=str(parent_session_id or "").strip(),
                project_path=str(project_path or "").strip(),
                tool_profile=role_spec.tool_profile,
                model=model,
                system_prompt=system_prompt,
                max_steps=max_steps,
                tools=tools,
                handler=str(role_spec.handler or "skill").strip() or "skill",
                provider=str(task_params.get("provider") or "").strip(),
                child_session_id=(
                    str(_child_session_id or "").strip()
                    or (f"sub-session-{task_id}" if role_spec.handler == "llm_readonly" else "")
                ),
                params=task_params,
                general_read_roots=general_read_roots,
                timeout_seconds=timeout_seconds,
                retry_of=str(retry_of or "").strip(),
                resume_of=str(_resume_of or "").strip(),
            )
            child_session_id = sub_task.child_session_id
            if child_session_id in self._active_child_sessions:
                if lane_acquired and self._lane_budget is not None:
                    self._lane_budget.release(task_id)
                raise RuntimeError("Child session already has an active runtime owner.")
            if child_session_id:
                self._active_child_sessions[child_session_id] = task_id
            worker_started = False
            try:
                self._cancel_events[task_id] = threading.Event()
                worker = threading.Thread(
                    target=self._run_task,
                    args=(task_id, child_session_id),
                    daemon=True,
                    name=f"vrcforge-sub-agent-{task_id}",
                )
                self._threads[task_id] = worker
                # The worker acquires this lock before execution. A launch
                # failure therefore cannot leave a durable task stuck queued.
                worker.start()
                worker_started = True
                sub_task = self._commit_task_event_locked(
                    None,
                    sub_task,
                    "created",
                    {"role": role_id, "task": task_text, "retryOf": sub_task.retry_of,
                     "resumeOf": sub_task.resume_of},
                )
            except Exception:
                self._cancel_events.pop(task_id, None)
                self._threads.pop(task_id, None)
                if child_session_id:
                    self._active_child_sessions.pop(child_session_id, None)
                if lane_acquired and not worker_started and self._lane_budget is not None:
                    self._lane_budget.release(task_id)
                raise
            return {"ok": True, "task": self._serialize_task(sub_task)}

    @staticmethod
    def _normalize_tool_allowlist(value: Any) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError("sub-agent tools must be a list of tool names or null.")
        names: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("sub-agent tool names must be non-empty strings.")
            name = item.strip()
            if name not in names:
                names.append(name)
        return tuple(names)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            if task.status not in _RUNNING_STATUSES:
                if task.parent_continuation_status == "pending":
                    next_task = copy.deepcopy(task)
                    next_task.parent_continuation_status = "interrupted"
                    task = self._commit_task_event_locked(task, next_task, "parent_continuation_interrupted", {"reason": "user_stop"})
                    event = self._cancel_events.get(task_id)
                    if event:
                        event.set()
                return {"ok": True, "task": self._serialize_task(task), "message": "task already stopped"}
            next_task = copy.deepcopy(task)
            next_task.cancel_requested = True
            next_task.status = "cancelling"
            next_task = self._commit_task_event_locked(task, next_task, "cancel_requested", {})
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            return {"ok": True, "task": self._serialize_task(next_task)}

    def retry_task(self, task_id: str, display_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            if task.status not in _RETRYABLE_STATUSES:
                return {"ok": False, "error": "only stopped sub-agent tasks can be retried."}
            # A manual retry starts a new task, without replaying a settled parent call.
            params = {key: value for key, value in task.params.items() if key not in {"_taskSeed", "taskSeed"}}
            return self.create_task(
                role=task.role,
                task=task.task,
                display_name=display_name or task.display_name,
                parent_chat_id=task.parent_chat_id,
                parent_session_id=task.parent_session_id,
                project_path=task.project_path,
                params=params,
                general_read_roots=task.general_read_roots,
                retry_of=task.id,
            )

    def resume_task(
        self,
        task_id: str,
        *,
        task: str,
        display_name: str | None = None,
        task_seed: dict[str, Any] | None = None,
        general_read_roots: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Start a new turn in a stopped LLM child, preserving its native session."""

        new_task_text = str(task or "").strip()
        if not new_task_text:
            return {"ok": False, "error": "resume requires a new task message."}
        if task_seed is not None and not isinstance(task_seed, dict):
            return {"ok": False, "error": "resume task seed must be a trusted object."}
        with self._lock:
            source = self._tasks.get(str(task_id or "").strip())
            if source is None:
                return {"ok": False, "error": "sub-agent task was not found."}
            if source.status not in _RETRYABLE_STATUSES:
                return {"ok": False, "error": "only stopped sub-agent tasks can be resumed."}
            if source.handler != "llm_readonly" or not source.child_session_id:
                return {"ok": False, "error": "this sub-agent role does not support history resume."}
            if not source.resumable:
                return {"ok": False, "error": "this child has no resumable native history."}
            if source.child_session_id in self._active_child_sessions:
                return {"ok": False, "error": "this child session already has an active turn."}
            seed_session_id = str((task_seed or {}).get("sessionId") or "").strip()
            if seed_session_id and seed_session_id != source.parent_session_id:
                return {"ok": False, "error": "resume task seed belongs to a different parent session."}
            if task_seed is not None and "projectRoot" in task_seed:
                seed_project = str(task_seed["projectRoot"] or "")
                if (bool(seed_project) != bool(source.project_path)
                    or (seed_project and normalize_filesystem_path(seed_project) != normalize_filesystem_path(source.project_path))
                ):
                    return {"ok": False, "error": "This sub-agent belongs to a different parent project."}
            role = self.roles.get(source.role)
            if role is None or role.handler != "llm_readonly" or source.role not in self.handlers:
                return {"ok": False, "error": "the original read-only child role is unavailable."}

            # A resumed turn must never dispatch the original task's automatic
            # parent continuation a second time. The explicit resume request is
            # the owner of this turn and its result stays on the child lineage.
            params = copy.deepcopy(source.params)
            params.pop("_taskSeed", None)
            params.pop("taskSeed", None)
            if task_seed is not None:
                params["_taskSeed"] = copy.deepcopy(task_seed)
            resumed = self.create_task(
                role=source.role,
                task=new_task_text,
                display_name=display_name or source.display_name,
                parent_chat_id=source.parent_chat_id,
                parent_session_id=source.parent_session_id,
                project_path=source.project_path,
                params=params,
                general_read_roots=source.general_read_roots if general_read_roots is None else general_read_roots,
                _child_session_id=source.child_session_id,
                _resume_of=source.id,
            )
            return resumed

    def acknowledge_handoff(self, task_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        """Acknowledge that the stable result card was saved in the parent chat."""

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            if task.handoff_status == "materialized":
                return {"ok": True, "task": self._serialize_task(task), "message": "handoff already materialized"}
            if expected_revision is not None and expected_revision != task.revision:
                return self._revision_conflict(task)
            if task.handoff_status != "handoff_pending":
                return {"ok": False, "error": "sub-agent handoff is not pending."}
            next_task = copy.deepcopy(task)
            next_task.handoff_status = "materialized"
            next_task.handoff_at = utc_now()
            next_task = self._commit_task_event_locked(task, next_task, "handoff_materialized", {})
            return {"ok": True, "task": self._serialize_task(next_task)}

    def merge_task(
        self,
        task_id: str,
        *,
        decision: str = "adopted",
        chat_id: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        decision_id = str(decision or "adopted").strip().lower()
        if decision_id not in _MERGE_DECISIONS:
            return {"ok": False, "error": "merge decision must be adopted or dismissed."}
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            requested_chat_id = str(chat_id or "").strip()
            if task.merge_decision:
                if not task.parent_chat_id:
                    return {"ok": False, "error": "sub-agent parent chat owner is missing."}
                if requested_chat_id and requested_chat_id != task.parent_chat_id:
                    return {"ok": False, "error": "sub-agent parent chat does not match the merge target."}
                if task.merge_decision != decision_id:
                    return {"ok": False, "error": f"task was already {task.merge_decision}."}
                return {"ok": True, "task": self._serialize_task(task), "message": "task already merged"}
            if expected_revision is not None and expected_revision != task.revision:
                return self._revision_conflict(task)
            if task.status not in {"completed", "failed"}:
                return {"ok": False, "error": "only completed or failed sub-agent tasks can be merged."}
            if decision_id == "adopted" and task.status != "completed":
                return {"ok": False, "error": "only completed sub-agent tasks can be adopted."}
            if not task.parent_chat_id:
                return {"ok": False, "error": "sub-agent parent chat owner is missing."}
            if requested_chat_id and requested_chat_id != task.parent_chat_id:
                return {"ok": False, "error": "sub-agent parent chat does not match the merge target."}
            merged_at = utc_now()
            next_task = copy.deepcopy(task)
            next_task.merge_decision = decision_id
            next_task.merged_chat_id = task.parent_chat_id
            next_task.merged_at = merged_at
            next_task.handoff_status = decision_id
            next_task.handoff_at = merged_at
            next_task = self._commit_task_event_locked(
                task,
                next_task,
                "merged",
                {"decision": decision_id, "chatId": task.parent_chat_id},
            )
            return {"ok": True, "task": self._serialize_task(next_task)}

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        path = self._event_log_path()
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 1000)) :]
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                public_payload = dict(payload)
                public_payload.pop("task", None)
                events.append(public_payload)
        return events

    def _run_task(self, task_id: str, child_session_id: str) -> None:
        handler_done = threading.Event()
        handler_done.set()
        lifecycle_done = threading.Event()

        def release_child_session() -> None:
            if not child_session_id or not (lifecycle_done.is_set() and handler_done.is_set()):
                return
            with self._lock:
                if self._active_child_sessions.get(child_session_id) == task_id:
                    self._active_child_sessions.pop(child_session_id)

        def finish_lifecycle() -> None:
            lifecycle_done.set()
            release_child_session()

        try:
            self._run_task_body(task_id, handler_done, release_child_session, finish_lifecycle)
        finally:
            finish_lifecycle()
            if self._lane_budget is not None:
                self._lane_budget.release(task_id)
        self._notify_task_finished(task_id)

    def _notify_task_finished(self, task_id: str) -> None:
        """Notify the app after terminal state is durable and its lane is free."""

        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.parent_continuation_status != "pending":
                return
            session_id = task.parent_session_id
            cancel_event = self._cancel_events.get(task_id)
        if self._wait_parent_idle is not None and session_id:
            if not self._wait_parent_idle(session_id, cancelled=lambda: self._shutdown or bool(cancel_event and cancel_event.is_set())):
                return
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                self._shutdown
                or not self._accepting
                or task is None
                or task.status not in _PARENT_CONTINUATION_TERMINAL_STATUSES
                or task.parent_continuation_status != "pending"
                or task_id in self._parent_continuations_inflight
            ):
                return
            self._parent_continuations_inflight.add(task_id)
            dispatching = copy.deepcopy(task)
            dispatching.parent_continuation_status = "dispatching"
            try:
                task = self._commit_task_event_locked(
                    task,
                    dispatching,
                    "parent_continuation_dispatching",
                    {"terminalStatus": task.status},
                )
            except Exception:
                self._clear_parent_continuation_inflight_locked(task_id)
                raise
            seed = task.params.get("_taskSeed")
            event = {
                "schema": "vrcforge.sub_agent_terminal.v1",
                "subAgentTaskId": task.id,
                "status": task.status,
                "parentSessionId": task.parent_session_id,
                "taskSeed": copy.deepcopy(seed) if isinstance(seed, dict) else None,
                "summary": task.summary,
                "error": task.error,
                "result": copy.deepcopy(task.result) if isinstance(task.result, dict) else None,
            }
        try:
            self._task_finished(event)
        except Exception:
            with self._lock:
                self._clear_parent_continuation_inflight_locked(task_id)
                current = self._tasks.get(task_id)
                if current is not None and current.parent_continuation_status == "dispatching":
                    interrupted = copy.deepcopy(current)
                    interrupted.parent_continuation_status = "interrupted"
                    try:
                        self._commit_task_event_locked(
                            current,
                            interrupted,
                            "parent_continuation_interrupted",
                            {
                                "terminalStatus": current.status,
                                "reason": "callback_failed_after_dispatch",
                            },
                        )
                    except Exception:
                        pass
            return
        with self._lock:
            self._clear_parent_continuation_inflight_locked(task_id)
            current = self._tasks.get(task_id)
            if current is None or current.parent_continuation_status != "dispatching":
                return
            delivered = copy.deepcopy(current)
            delivered.parent_continuation_status = "delivered"
            try:
                self._commit_task_event_locked(
                    current,
                    delivered,
                    "parent_continuation_delivered",
                    {"terminalStatus": current.status},
                )
            except Exception:
                # The durable dispatching marker deliberately survives. On
                # restart it becomes interrupted instead of replaying a
                # continuation that may already have produced side effects.
                return

    def _clear_parent_continuation_inflight_locked(self, task_id: str) -> None:
        self._parent_continuations_inflight.discard(task_id)
        self._parent_continuation_condition.notify_all()

    def _run_task_body(
        self, task_id: str, handler_done: threading.Event,
        release_child_session: Callable[[], None], finish_lifecycle: Callable[[], None],
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            cancel_event = self._cancel_events.get(task_id)
            if not task or not cancel_event:
                return
            if task.cancel_requested or cancel_event.is_set():
                finish_lifecycle()
                self._commit_cancelled_locked(task, "Sub-agent task was cancelled before execution.")
                return
            next_task = copy.deepcopy(task)
            next_task.status = "running"
            next_task.started_at = utc_now()
            task = self._commit_task_event_locked(
                task,
                next_task,
                "started",
                {"role": task.role, "displayName": task.display_name},
            )
        try:
            if cancel_event.is_set():
                raise CancelledError("Sub-agent task was cancelled before execution.")
            handler = self.handlers[task.role]
            timeout_seconds = max(1.0, min(float(task.timeout_seconds or DEFAULT_SUB_AGENT_TIMEOUT_SECONDS), MAX_SUB_AGENT_TIMEOUT_SECONDS))
            result_box: list[dict[str, Any]] = []
            handler_error: list[BaseException] = []

            def _run_handler() -> None:
                try:
                    result_box.append(handler(self._task_payload(task), cancel_event))
                except BaseException as exc:  # noqa: BLE001 - the watchdog needs the error.
                    handler_error.append(exc)
                finally:
                    handler_done.set()
                    release_child_session()

            worker = threading.Thread(target=_run_handler, daemon=True, name=f"subagent-{task_id[:12]}")
            handler_done.clear()
            try:
                worker.start()
            except Exception:
                handler_done.set()
                raise
            timed_out = not handler_done.wait(timeout=timeout_seconds)
            if timed_out:
                cancel_event.set()
                raise TimeoutError(
                    f"Sub-agent handler exceeded its {int(timeout_seconds)}s watchdog and was signalled to cancel."
                )
            if handler_error:
                raise handler_error[0]
            result = result_box[0] if result_box else {}
            summary = summarize_worker_result(result)
        except CancelledError as exc:
            with self._lock:
                finish_lifecycle()
                current = self._tasks.get(task_id)
                if current and current.status in _RUNNING_STATUSES:
                    self._commit_cancelled_locked(current, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - task failures should be visible to users.
            with self._lock:
                finish_lifecycle()
                current = self._tasks.get(task_id)
                if not current or current.status not in _RUNNING_STATUSES:
                    return
                is_watchdog_timeout = isinstance(exc, TimeoutError)
                if not is_watchdog_timeout and (
                    current.cancel_requested or cancel_event.is_set() or current.status == "cancelling"
                ):
                    self._commit_cancelled_locked(current, "Sub-agent task was cancelled.")
                    return
                next_task = copy.deepcopy(current)
                next_task.status = "failed"
                is_timeout = isinstance(exc, TimeoutError)
                failure = runtime_error_from_exception(
                    "sub_agent_task_timeout" if is_timeout else "sub_agent_task_failed",
                    exc, phase="run",
                    message=str(exc) if is_timeout else "",  # watchdog text is our own, safe to surface
                )
                next_task.error = str(failure.get("message") or "The sub-agent task failed.")
                next_task.error_code = str(failure.get("code") or "")
                next_task.stopped_at = utc_now()
                next_task.handoff_status = "handoff_pending"
                self._commit_task_event_locked(current, next_task, "failed", {"failure": failure})
            return

        with self._lock:
            finish_lifecycle()
            current = self._tasks.get(task_id)
            if not current or current.status not in _RUNNING_STATUSES:
                return
            if current.cancel_requested or cancel_event.is_set() or current.status == "cancelling":
                self._commit_cancelled_locked(current, "Sub-agent task was cancelled.")
                return
            try:
                next_task = self._terminal_task_from_result(current, result, summary)
                self._commit_task_event_locked(
                    current,
                    next_task,
                    next_task.status,
                    {"summary": summary, "error": next_task.error},
                    result=result,
                )
            except Exception as exc:  # noqa: BLE001 - preserve a durable sidecar before classifying failure.
                self._recover_completion_persistence_error_locked(current, exc)

    def _commit_cancelled_locked(self, task: SubAgentTask, error: str) -> SubAgentTask:
        error = error or "The sub-agent task was cancelled."
        next_task = copy.deepcopy(task)
        next_task.status = "cancelled"
        next_task.cancel_requested = True
        next_task.error = error
        next_task.error_code = "sub_agent_task_cancelled"
        next_task.stopped_at = utc_now()
        return self._commit_task_event_locked(task, next_task, "cancelled", {"error": error, "errorCode": next_task.error_code})

    @staticmethod
    def _completed_task(task: SubAgentTask, result: dict[str, Any], summary: str) -> SubAgentTask:
        next_task = copy.deepcopy(task)
        next_task.status = "completed"
        next_task.result = copy.deepcopy(result)
        next_task.result_available = True
        next_task.result_unavailable = False
        next_task.summary = summary
        next_task.error = ""
        next_task.cancel_requested = False
        next_task.stopped_at = utc_now()
        next_task.handoff_status = "handoff_pending"
        return next_task

    @classmethod
    def _terminal_task_from_result(
        cls,
        task: SubAgentTask,
        result: dict[str, Any],
        summary: str,
    ) -> SubAgentTask:
        if result.get("ok") is not False:
            return cls._freeze_runtime_metadata(cls._completed_task(task, result, summary), result)
        next_task = copy.deepcopy(task)
        next_task.status = "failed"
        next_task.result = copy.deepcopy(result)
        next_task.result_available = True
        next_task.result_unavailable = False
        next_task.summary = summary
        next_task.error = str(result.get("error") or summary or "Sub-agent task reported failure.")[:1000]
        next_task.cancel_requested = False
        next_task.stopped_at = utc_now()
        next_task.handoff_status = "handoff_pending"
        return cls._freeze_runtime_metadata(next_task, result)

    @staticmethod
    def _freeze_runtime_metadata(task: SubAgentTask, result: dict[str, Any]) -> SubAgentTask:
        task.resumable = result.get("resumable") is True
        route = result.get("providerRoute")
        if not isinstance(route, dict):
            return task
        provider = route.get("provider")
        model = route.get("model")
        if isinstance(provider, str) and provider.strip():
            task.provider = provider.strip()[:160]
            task.params["provider"] = task.provider
        if isinstance(model, str) and model.strip():
            task.model = model.strip()[:300]
            task.params["model"] = task.model
        return task

    def _recover_completion_persistence_error_locked(self, task: SubAgentTask, error: Exception) -> None:
        """Prefer a durable result sidecar over a transient event-append error."""

        try:
            result, summary = self._load_result_sidecar_locked(task.id)
        except FileNotFoundError:
            self._commit_result_persistence_failure_locked(task, error)
            return
        except (ValueError, json.JSONDecodeError, UnicodeError):
            self._commit_result_persistence_failure_locked(task, error)
            return
        except OSError:
            # The sidecar may already be durable but temporarily unreadable.
            # Keep the in-memory projection active for a later startup retry.
            return
        next_task = self._terminal_task_from_result(task, result, summary)
        try:
            self._commit_task_event_locked(
                task,
                next_task,
                "recovered",
                {
                    "previousStatus": task.status,
                    "terminalStatus": next_task.status,
                    "summary": summary,
                    "source": "result_sidecar_after_append_error",
                },
            )
        except Exception:  # noqa: BLE001 - a persistent append failure must leave the task retryable on restart.
            return

    def _commit_result_persistence_failure_locked(self, task: SubAgentTask, error: Exception) -> None:
        next_task = copy.deepcopy(task)
        next_task.status = "failed"
        next_task.error = f"Unable to persist the completed sub-agent result: {error}"
        next_task.stopped_at = utc_now()
        next_task.handoff_status = "handoff_pending"
        try:
            self._commit_task_event_locked(task, next_task, "failed", {"error": next_task.error})
        except Exception:  # noqa: BLE001 - leave the active projection intact if even failure cannot be recorded.
            return

    def _task_payload(self, task: SubAgentTask) -> dict[str, Any]:
        payload = copy.deepcopy(task.params)
        payload.setdefault("taskId", task.id)
        payload.setdefault("task", task.task)
        for alias in ("projectRoot", "project_path", "project_root"):
            payload.pop(alias, None)
        payload.update(
            projectPath=task.project_path,
            parentChatId=task.parent_chat_id,
            parentSessionId=task.parent_session_id,
            _generalReadRoots=task.general_read_roots,
        )
        seed = task.params.get("_taskSeed")
        if isinstance(seed, dict):
            payload.setdefault("parentTurnId", str(seed.get("turnId") or "")[:180])
            payload.setdefault("parentClientTurnId", str(seed.get("clientTurnId") or "")[:240])
        payload.setdefault("displayName", task.display_name)
        if task.handler == "llm_readonly":
            # The registry owns child identity and the persisted execution
            # choices; arbitrary task params cannot redirect it into a parent.
            payload.update(
                taskId=task.id,
                task=task.task,
                childSessionId=task.child_session_id,
                model=task.model,
                systemPrompt=task.system_prompt,
                maxSteps=task.max_steps,
                tools=list(task.tools) if task.tools is not None else None,
                provider=task.provider,
                handler=task.handler,
                clientTurnId=task.id,
                resumeOf=task.resume_of,
                isResume=bool(task.resume_of),
            )
        else:
            # Preserve the seven fixed handler contracts; their arguments can
            # still supply explicit per-task values as before.
            payload.setdefault("model", task.model)
            payload.setdefault("systemPrompt", task.system_prompt)
            payload.setdefault("maxSteps", task.max_steps)
            payload.setdefault("tools", list(task.tools) if task.tools is not None else None)
            payload.setdefault("handler", task.handler)
        payload.setdefault("retryOf", task.retry_of)
        return payload

    def _serialize_role(self, role: SubAgentRole) -> dict[str, Any]:
        return {
            "id": role.id,
            "title": role.title,
            "description": role.description,
            "toolProfile": role.tool_profile,
            "readOnly": role.read_only,
            "handler": role.handler,
            "model": role.model,
            "systemPrompt": role.system_prompt,
            "maxSteps": role.max_steps,
            "tools": list(role.tools) if role.tools is not None else None,
        }

    def _serialize_task(self, task: SubAgentTask, include_events: bool = False) -> dict[str, Any]:
        payload = self._task_snapshot(task)
        payload["schema"] = SUB_AGENT_SCHEMA
        payload["result"] = copy.deepcopy(task.result)
        payload["paramsSummary"] = summarize_params(public_runtime_payload(task.params))
        payload.pop("params", None)
        payload.pop("generalReadRoots", None)
        with self._lock:
            payload["canResume"] = bool(
                task.handler == "llm_readonly"
                and task.child_session_id
                and task.resumable
                and task.status in _RETRYABLE_STATUSES
                and task.child_session_id not in self._active_child_sessions
            )
        if include_events:
            payload["events"] = [event for event in self.recent_events(limit=500) if event.get("taskId") == task.id]
        return payload

    def _task_snapshot(self, task: SubAgentTask) -> dict[str, Any]:
        seed = task.params.get("_taskSeed")
        parent_turn_id = str(seed.get("turnId") or "")[:180] if isinstance(seed, dict) else ""
        parent_client_turn_id = str(seed.get("clientTurnId") or "")[:240] if isinstance(seed, dict) else ""
        return {
            "id": task.id,
            "role": task.role,
            "displayName": task.display_name,
            "task": task.task,
            "parentChatId": task.parent_chat_id,
            "parentSessionId": task.parent_session_id,
            "parentTurnId": parent_turn_id,
            "parentClientTurnId": parent_client_turn_id,
            "projectPath": task.project_path,
            "toolProfile": task.tool_profile,
            "model": task.model,
            "provider": task.provider,
            "resumable": task.resumable,
            "systemPrompt": task.system_prompt,
            "maxSteps": task.max_steps,
            "tools": list(task.tools) if task.tools is not None else None,
            "handler": task.handler,
            "childSessionId": task.child_session_id,
            "status": task.status,
            "createdAt": task.created_at,
            "startedAt": task.started_at,
            "stoppedAt": task.stopped_at,
            "updatedAt": task.updated_at,
            "cancelRequested": task.cancel_requested,
            "summary": task.summary,
            "error": task.error,
            "errorCode": task.error_code,
            "timeoutSeconds": task.timeout_seconds,
            "eventCount": task.event_count,
            "revision": task.revision,
            "retryOf": task.retry_of,
            "resumeOf": task.resume_of,
            "handoffStatus": task.handoff_status,
            "handoffAt": task.handoff_at,
            "mergedAt": task.merged_at,
            "mergedChatId": task.merged_chat_id,
            "mergeDecision": task.merge_decision,
            "parentContinuationStatus": task.parent_continuation_status,
            "resultAvailable": task.result_available,
            "resultUnavailable": task.result_unavailable,
            "params": redact_for_storage(task.params),
            "generalReadRoots": list(task.general_read_roots),
        }

    def _task_from_snapshot(self, snapshot: dict[str, Any]) -> SubAgentTask | None:
        task_id = str(snapshot.get("id") or "").strip()
        if not task_id:
            return None
        params = snapshot.get("params")
        read_roots = snapshot.get("generalReadRoots")
        role_id = str(snapshot.get("role") or "project_index_review")
        role_spec = self.roles.get(role_id)
        handler = str(snapshot.get("handler") or (role_spec.handler if role_spec else "skill"))
        return SubAgentTask(
            id=task_id,
            role=role_id,
            display_name=str(snapshot.get("displayName") or "Manuka"),
            task=str(snapshot.get("task") or ""),
            parent_chat_id=str(snapshot.get("parentChatId") or ""),
            parent_session_id=str(snapshot.get("parentSessionId") or ""),
            project_path=str(snapshot.get("projectPath") or ""),
            tool_profile=str(snapshot.get("toolProfile") or "read-only"),
            model=str(snapshot.get("model") or ""),
            provider=str(snapshot.get("provider") or ((params or {}).get("provider") if isinstance(params, dict) else "") or ""),
            resumable=bool(snapshot.get("resumable")),
            system_prompt=str(snapshot.get("systemPrompt") or ""),
            max_steps=int(snapshot.get("maxSteps") or 4),
            tools=self._normalize_tool_allowlist(snapshot.get("tools", role_spec.tools if role_spec else None)),
            handler=handler,
            child_session_id=str(snapshot.get("childSessionId") or ""),
            status=str(snapshot.get("status") or "queued"),
            created_at=str(snapshot.get("createdAt") or utc_now()),
            started_at=str(snapshot.get("startedAt") or ""),
            stopped_at=str(snapshot.get("stoppedAt") or ""),
            updated_at=str(snapshot.get("updatedAt") or snapshot.get("createdAt") or utc_now()),
            params=copy.deepcopy(params if isinstance(params, dict) else {}),
            general_read_roots=tuple(root for root in read_roots if isinstance(root, str)) if isinstance(read_roots, list) else (),
            result_available=bool(snapshot.get("resultAvailable")),
            result_unavailable=bool(snapshot.get("resultUnavailable")),
            summary=str(snapshot.get("summary") or ""),
            error=str(snapshot.get("error") or ""),
            error_code=str(snapshot.get("errorCode") or ""),
            timeout_seconds=max(1.0, min(float(snapshot.get("timeoutSeconds") or DEFAULT_SUB_AGENT_TIMEOUT_SECONDS), MAX_SUB_AGENT_TIMEOUT_SECONDS)),
            cancel_requested=bool(snapshot.get("cancelRequested")),
            event_count=int(snapshot.get("eventCount") or 0),
            revision=int(snapshot.get("revision") or 0),
            retry_of=str(snapshot.get("retryOf") or ""),
            resume_of=str(snapshot.get("resumeOf") or ""),
            handoff_status=str(snapshot.get("handoffStatus") or ""),
            handoff_at=str(snapshot.get("handoffAt") or ""),
            merged_at=str(snapshot.get("mergedAt") or ""),
            merged_chat_id=str(snapshot.get("mergedChatId") or ""),
            merge_decision=str(snapshot.get("mergeDecision") or ""),
            parent_continuation_status=str(snapshot.get("parentContinuationStatus") or ""),
        )

    def _running_count_locked(self) -> int:
        return sum(1 for task in self._tasks.values() if task.status in _RUNNING_STATUSES)

    def _ensure_accepting_locked(self) -> None:
        if self._shutdown:
            raise RuntimeError("Sub-agent task registry is shut down.")
        if not self._accepting:
            raise RuntimeError("Sub-agent task registry has not completed startup recovery.")

    def _commit_task_event_locked(
        self,
        current: SubAgentTask | None,
        next_task: SubAgentTask,
        event: str,
        data: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
    ) -> SubAgentTask:
        timestamp = utc_now()
        next_task = copy.deepcopy(next_task)
        if (
            next_task.status in _PARENT_CONTINUATION_TERMINAL_STATUSES
            and isinstance(next_task.params.get("_taskSeed"), dict)
            and not next_task.parent_continuation_status
        ):
            next_task.parent_continuation_status = "pending"
        next_task.revision = (current.revision if current else 0) + 1
        next_task.event_count = (current.event_count if current else 0) + 1
        next_task.updated_at = timestamp
        if result is not None:
            self._write_result_sidecar_locked(next_task.id, result, next_task.summary)
            next_task.result_available = True
            next_task.result_unavailable = False
        entry = {
            "schema": SUB_AGENT_LOG_SCHEMA,
            "timestamp": timestamp,
            "taskId": next_task.id,
            "event": event,
            "revision": next_task.revision,
            "data": summarize_params(data),
            "task": self._task_snapshot(next_task),
        }
        self._append_event_locked(entry)
        self._tasks[next_task.id] = next_task
        return next_task

    def _append_event_locked(self, entry: dict[str, Any]) -> None:
        path = self._event_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        needs_separator = False
        try:
            if path.exists() and path.stat().st_size:
                with path.open("rb") as existing:
                    existing.seek(-1, os.SEEK_END)
                    needs_separator = existing.read(1) != b"\n"
        except OSError:
            needs_separator = False
        with path.open("a", encoding="utf-8") as log_file:
            if needs_separator:
                # Keep a crash-truncated tail isolated so the next valid event
                # remains independently replayable JSONL.
                log_file.write("\n")
            log_file.write(encoded)
            log_file.flush()
            os.fsync(log_file.fileno())

    def _write_result_sidecar_locked(self, task_id: str, result: dict[str, Any], summary: str) -> None:
        path = self._result_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic_json(path, {
            "schema": SUB_AGENT_RESULT_SCHEMA,
            "taskId": task_id,
            "summary": summary,
            "result": result,
        })

    def _read_result_sidecar_locked(self, task: SubAgentTask, *, strict_io: bool = False) -> None:
        if not task.result_available:
            return
        try:
            result, _summary = self._load_result_sidecar_locked(task.id)
            task.result = result
            task.result_unavailable = False
        except FileNotFoundError:
            task.result = None
            task.result_available = False
            task.result_unavailable = True
        except (ValueError, json.JSONDecodeError, UnicodeError):
            task.result = None
            task.result_available = False
            task.result_unavailable = True
        except OSError:
            if strict_io:
                raise
            task.result = None
            task.result_available = False
            task.result_unavailable = True

    def _load_result_sidecar_locked(self, task_id: str) -> tuple[dict[str, Any], str]:
        payload = json.loads(self._result_path(task_id).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("result sidecar did not contain an object")
        if payload.get("schema") != SUB_AGENT_RESULT_SCHEMA:
            raise ValueError("result sidecar schema did not match")
        sidecar_task_id = payload.get("taskId")
        if not isinstance(sidecar_task_id, str) or sidecar_task_id != task_id:
            raise ValueError("result sidecar task id did not match")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("result sidecar result did not contain an object")
        summary = payload.get("summary")
        if not isinstance(summary, str):
            summary = summarize_worker_result(result)
        return copy.deepcopy(result), summary[:1000]

    def _recover_orphaned_result_sidecars_locked(self) -> None:
        """Finish the terminal projection when a crash followed sidecar replace."""

        for task in list(self._tasks.values()):
            if task.status not in _RUNNING_STATUSES:
                continue
            try:
                result, summary = self._load_result_sidecar_locked(task.id)
            except FileNotFoundError:
                continue
            except (ValueError, json.JSONDecodeError, UnicodeError):
                # A missing, corrupt, or foreign sidecar is not completion
                # evidence. The ordinary restart reconciliation below owns it.
                continue
            next_task = self._terminal_task_from_result(task, result, summary)
            self._commit_task_event_locked(
                task,
                next_task,
                "recovered",
                {
                    "previousStatus": task.status,
                    "terminalStatus": next_task.status,
                    "summary": summary,
                    "source": "result_sidecar",
                },
            )

    def _load_projection_locked(self) -> None:
        tasks, cancel_events = self._read_projection_state_locked(strict_io=False)
        self._tasks = tasks
        self._cancel_events = cancel_events

    def _read_projection_state_locked(
        self,
        *,
        strict_io: bool,
    ) -> tuple[dict[str, SubAgentTask], dict[str, threading.Event]]:
        path = self._event_log_path()
        tasks: dict[str, SubAgentTask] = {}
        legacy_tasks: dict[str, SubAgentTask] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        try:
            with path.open("rb") as source:
                log_state = _log_state(os.fstat(source.fileno()))
                checkpoint = self._read_recovery_checkpoint(source, log_state)
                start = 0
                if checkpoint is not None:
                    start, snapshots, tasks, legacy_tasks = checkpoint
                source.seek(start)
                offset = start
                unterminated = b""
                for line in source:
                    if not line.endswith(b"\n"):
                        unterminated = line
                        break
                    self._project_event_line(line, snapshots, tasks, legacy_tasks)
                    offset += len(line)
                if strict_io and offset > start:
                    self._write_recovery_checkpoint(source, log_state, offset, snapshots, legacy_tasks)
                if unterminated:
                    # A complete JSON object at EOF was readable before we had
                    # checkpoints. Preserve it without advancing the cursor.
                    self._project_event_line(unterminated, snapshots, tasks, legacy_tasks)
        except FileNotFoundError:
            return {}, {}
        except OSError:
            if strict_io:
                raise
            return {}, {}
        for task_id, task in legacy_tasks.items():
            tasks.setdefault(task_id, task)
        cancel_events: dict[str, threading.Event] = {}
        for task in tasks.values():
            self._read_result_sidecar_locked(task, strict_io=strict_io)
            cancel_events[task.id] = threading.Event()
            if task.cancel_requested:
                cancel_events[task.id].set()
        return tasks, cancel_events

    def _project_event_line(
        self, line: bytes, snapshots: dict[str, dict[str, Any]], tasks: dict[str, SubAgentTask],
        legacy_tasks: dict[str, SubAgentTask],
    ) -> None:
        try:
            entry = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(entry, dict):
            return
        snapshot = entry.get("task")
        if isinstance(snapshot, dict):
            task = self._task_from_snapshot(snapshot)
            if task:
                snapshots[task.id] = snapshot
                tasks[task.id] = task
        else:
            self._project_legacy_event(entry, legacy_tasks)

    def _read_recovery_checkpoint(
        self, source: BinaryIO, log_state: tuple[int, int, int, int],
    ) -> tuple[int, dict[str, dict[str, Any]], dict[str, SubAgentTask], dict[str, SubAgentTask]] | None:
        try:
            checkpoint = json.loads(self._recovery_checkpoint_path().read_text(encoding="utf-8"))
            if checkpoint["schema"] != _RECOVERY_CHECKPOINT_SCHEMA:
                return None
            device, inode, size, modified = checkpoint["log"]
            offset = checkpoint["offset"]
            if (not inode or (device, inode) != log_state[:2]
                    or type(offset) is not int or not 0 < offset <= size <= log_state[2]
                    or (size == log_state[2] and modified != log_state[3])):
                return None
            boundary = bytes.fromhex(checkpoint["boundary"])
            if len(boundary) != min(offset, _RECOVERY_BOUNDARY_BYTES) or not boundary.endswith(b"\n"):
                return None
            source.seek(offset - len(boundary))
            if source.read(len(boundary)) != boundary:
                return None
            snapshots: dict[str, dict[str, Any]] = {}
            tasks: dict[str, SubAgentTask] = {}
            legacy_tasks: dict[str, SubAgentTask] = {}
            for key, target in (("tasks", tasks), ("legacyTasks", legacy_tasks)):
                records = checkpoint[key]
                if not isinstance(records, list):
                    return None
                for snapshot in records:
                    task = self._task_from_snapshot(snapshot)
                    if task is None or task.id in target:
                        return None
                    target[task.id] = task
                    if key == "tasks":
                        snapshots[task.id] = snapshot
            return offset, snapshots, tasks, legacy_tasks
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            return None  # The original event log remains authoritative.

    def _write_recovery_checkpoint(
        self, source: BinaryIO, log_state: tuple[int, int, int, int], offset: int,
        snapshots: dict[str, dict[str, Any]], legacy_tasks: dict[str, SubAgentTask],
    ) -> None:
        try:
            if not log_state[1] or log_state != _log_state(self._event_log_path().stat()):
                return
            boundary_size = min(offset, _RECOVERY_BOUNDARY_BYTES)
            source.seek(offset - boundary_size)
            boundary = source.read(boundary_size)
            _write_atomic_json(self._recovery_checkpoint_path(), {
                "schema": _RECOVERY_CHECKPOINT_SCHEMA,
                "log": log_state,
                "offset": offset,
                "boundary": boundary.hex(),
                # Keep first-seen order for interruption and replay processing.
                "tasks": list(snapshots.values()),
                # Full lifecycle snapshots take precedence over legacy rows,
                # including when the two are on opposite sides of the cursor.
                "legacyTasks": [self._task_snapshot(task) for task_id, task in legacy_tasks.items()
                                if task_id not in snapshots],
            })
        except (OSError, ValueError, RecursionError):
            pass  # Cache publication failure must not block recovery.

    def _project_legacy_event(self, entry: dict[str, Any], tasks: dict[str, SubAgentTask]) -> None:
        task_id = str(entry.get("taskId") or "").strip()
        event = str(entry.get("event") or "").strip()
        if not task_id or not event:
            return
        timestamp = str(entry.get("timestamp") or utc_now())
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        task = tasks.get(task_id)
        if task is None:
            task = SubAgentTask(
                id=task_id,
                role=str(data.get("role") or "project_index_review"),
                display_name=str(data.get("displayName") or "Manuka"),
                task=str(data.get("task") or ""),
                created_at=timestamp,
                updated_at=timestamp,
            )
            tasks[task_id] = task
        task.event_count += 1
        task.revision += 1
        task.updated_at = timestamp
        if event == "started":
            task.status = "running"
            task.started_at = timestamp
        elif event == "cancel_requested":
            task.status = "cancelling"
            task.cancel_requested = True
        elif event in {"completed", "failed", "cancelled"}:
            task.status = event
            task.stopped_at = timestamp
            task.summary = str(data.get("summary") or task.summary)
            task.error = str(data.get("error") or task.error)
            if event in {"completed", "failed"}:
                task.handoff_status = "handoff_pending"
                task.result_unavailable = True
        elif event == "merged":
            task.merge_decision = str(data.get("decision") or "")
            task.merged_chat_id = str(data.get("chatId") or "")
            task.merged_at = timestamp
            task.handoff_status = task.merge_decision
            task.handoff_at = timestamp

    def _reconcile_interrupted_tasks_locked(self) -> None:
        for task in list(self._tasks.values()):
            if task.status in {"queued", "running"}:
                next_task = copy.deepcopy(task)
                next_task.status = "interrupted"
                next_task.error = "Sub-agent task was interrupted by a process restart. Retry to run a new attempt."
                next_task.stopped_at = utc_now()
                self._commit_task_event_locked(task, next_task, "interrupted", {"previousStatus": task.status})
            elif task.status == "cancelling":
                next_task = copy.deepcopy(task)
                next_task.status = "cancelled"
                next_task.cancel_requested = True
                next_task.error = task.error or "Sub-agent cancellation completed during process restart."
                next_task.stopped_at = utc_now()
                self._commit_task_event_locked(task, next_task, "cancelled", {"reason": "process_restart"})

    def _stage_existing_parent_continuations_locked(self) -> None:
        """Persist pending delivery for terminal seeded tasks from older projections."""

        for task in list(self._tasks.values()):
            if (
                task.status not in _PARENT_CONTINUATION_TERMINAL_STATUSES
                or task.parent_continuation_status
                or not isinstance(task.params.get("_taskSeed"), dict)
            ):
                continue
            pending = copy.deepcopy(task)
            pending.parent_continuation_status = "pending"
            self._commit_task_event_locked(
                task,
                pending,
                "parent_continuation_pending",
                {"terminalStatus": task.status},
            )

    def _interrupt_dispatched_parent_continuations_locked(self) -> None:
        """Fail closed after a crash in the continuation side-effect window."""

        for task in list(self._tasks.values()):
            if task.parent_continuation_status != "dispatching":
                continue
            interrupted = copy.deepcopy(task)
            interrupted.parent_continuation_status = "interrupted"
            self._commit_task_event_locked(
                task,
                interrupted,
                "parent_continuation_interrupted",
                {
                    "terminalStatus": task.status,
                    "reason": "process_restart_after_dispatch",
                },
            )

    def _revision_conflict(self, task: SubAgentTask) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "sub-agent task revision changed.",
            "currentRevision": task.revision,
            "task": self._serialize_task(task),
        }

    def _event_log_path(self) -> Path:
        return self.artifact_dir / "sub-agent-events.jsonl"

    def _recovery_checkpoint_path(self) -> Path:
        return self.artifact_dir / "sub-agent-recovery.json"

    def _result_path(self, task_id: str) -> Path:
        safe_task_id = "".join(char for char in task_id if char.isalnum() or char in {"-", "_"})
        if safe_task_id != task_id or not safe_task_id:
            raise ValueError("invalid sub-agent task id")
        return self.artifact_dir / "results" / f"{safe_task_id}.json"


class CancelledError(RuntimeError):
    pass


def _log_state(stat: os.stat_result) -> tuple[int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_worker_result(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return "Worker finished."
    if result.get("summaryText"):
        return str(result.get("summaryText"))[:1000]
    summary = result.get("summary")
    if isinstance(summary, dict):
        parts: list[str] = []
        for key in (
            "status",
            "changed",
            "findingCount",
            "addedFiles",
            "modifiedFiles",
            "deletedFiles",
            "prefabCandidateCount",
            "unityPackageCount",
        ):
            if key in summary:
                parts.append(f"{key}={summary.get(key)}")
        if parts:
            return "Worker finished: " + ", ".join(parts)
    if result.get("ok") is False:
        return "Worker failed: " + str(result.get("error") or "unknown error")[:500]
    return "Worker finished."


def redact_for_storage(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered == "_nativeconversation":
                result[key_text] = copy.deepcopy(item)
                continue
            if any(secret in lowered for secret in ("token", "secret", "api_key", "apikey", "authorization")):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = redact_for_storage(item)
        return result
    if isinstance(value, list):
        return [redact_for_storage(item) for item in value]
    return value


def summarize_params(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            key_text = str(key)
            lowered = key_text.lower()
            if any(secret in lowered for secret in ("token", "secret", "api_key", "apikey", "authorization")):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = summarize_params(item)
        return result
    if isinstance(value, list):
        return {"type": "list", "count": len(value), "items": [summarize_params(item) for item in value[:5]]}
    if isinstance(value, str):
        return value[:500] + ("..." if len(value) > 500 else "")
    return value
