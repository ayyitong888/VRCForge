from __future__ import annotations

import copy
import json
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SUB_AGENT_SCHEMA = "vrcforge.sub_agent_task.v2"
SUB_AGENT_LIST_SCHEMA = "vrcforge.sub_agent_tasks.v2"
SUB_AGENT_LOG_SCHEMA = "vrcforge.sub_agent_lifecycle.v2"
SUB_AGENT_RESULT_SCHEMA = "vrcforge.sub_agent_result.v1"
SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT = 5

_RUNNING_STATUSES = {"queued", "running", "cancelling"}
_RETRYABLE_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
_MERGE_DECISIONS = {"adopted", "dismissed"}

SubAgentHandler = Callable[[dict[str, Any], threading.Event], dict[str, Any]]


@dataclass
class SubAgentRole:
    id: str
    title: str
    description: str
    tool_profile: str = "read-only"
    read_only: bool = True


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
    status: str = "queued"
    created_at: str = field(default_factory=lambda: utc_now())
    started_at: str = ""
    stopped_at: str = ""
    updated_at: str = field(default_factory=lambda: utc_now())
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    result_available: bool = False
    result_unavailable: bool = False
    summary: str = ""
    error: str = ""
    cancel_requested: bool = False
    event_count: int = 0
    revision: int = 0
    retry_of: str = ""
    handoff_status: str = ""
    handoff_at: str = ""
    merged_at: str = ""
    merged_chat_id: str = ""
    merge_decision: str = ""


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
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.roles = {role.id: role for role in roles}
        self.handlers = dict(handlers)
        self.max_concurrent = max(1, min(int(max_concurrent), SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT))
        self._tasks: dict[str, SubAgentTask] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        with self._lock:
            self._load_projection_locked()
            self._reconcile_interrupted_tasks_locked()

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
        retry_of: str = "",
    ) -> dict[str, Any]:
        role_id = str(role or "").strip() or "project_index_review"
        if role_id not in self.roles:
            raise ValueError(f"Unknown sub-agent role: {role_id}")
        if role_id not in self.handlers:
            raise ValueError(f"Sub-agent role is not executable: {role_id}")
        task_text = str(task or "").strip() or self.roles[role_id].title
        role_spec = self.roles[role_id]
        with self._lock:
            if self._running_count_locked() >= self.max_concurrent:
                raise RuntimeError(f"Sub-agent concurrency limit reached ({self.max_concurrent}).")
            task_id = f"sub_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
            sub_task = SubAgentTask(
                id=task_id,
                role=role_id,
                display_name=str(display_name or "").strip() or "Manuka",
                task=task_text,
                parent_chat_id=str(parent_chat_id or "").strip(),
                parent_session_id=str(parent_session_id or "").strip(),
                project_path=str(project_path or "").strip(),
                tool_profile=role_spec.tool_profile,
                params=copy.deepcopy(params or {}),
                retry_of=str(retry_of or "").strip(),
            )
            sub_task = self._commit_task_event_locked(
                None,
                sub_task,
                "created",
                {"role": role_id, "task": task_text, "retryOf": sub_task.retry_of},
            )
            event = threading.Event()
            self._cancel_events[task_id] = event
            worker = threading.Thread(
                target=self._run_task,
                args=(task_id,),
                daemon=True,
                name=f"vrcforge-sub-agent-{task_id}",
            )
            self._threads[task_id] = worker
            worker.start()
            return {"ok": True, "task": self._serialize_task(sub_task)}

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            if task.status not in _RUNNING_STATUSES:
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
            return self.create_task(
                role=task.role,
                task=task.task,
                display_name=display_name or task.display_name,
                parent_chat_id=task.parent_chat_id,
                parent_session_id=task.parent_session_id,
                project_path=task.project_path,
                params=copy.deepcopy(task.params),
                retry_of=task.id,
            )

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

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            cancel_event = self._cancel_events.get(task_id)
            if not task or not cancel_event:
                return
            if task.cancel_requested or cancel_event.is_set():
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
            result = handler(self._task_payload(task), cancel_event)
            summary = summarize_worker_result(result)
            with self._lock:
                current = self._tasks[task_id]
                if current.cancel_requested or cancel_event.is_set() or current.status == "cancelling":
                    self._commit_cancelled_locked(current, "Sub-agent task was cancelled.")
                    return
                next_task = copy.deepcopy(current)
                next_task.status = "completed"
                next_task.result = copy.deepcopy(result)
                next_task.result_available = True
                next_task.result_unavailable = False
                next_task.summary = summary
                next_task.stopped_at = utc_now()
                next_task.handoff_status = "handoff_pending"
                self._commit_task_event_locked(
                    current,
                    next_task,
                    "completed",
                    {"summary": summary},
                    result=result,
                )
        except CancelledError as exc:
            with self._lock:
                current = self._tasks.get(task_id)
                if current and current.status in _RUNNING_STATUSES:
                    self._commit_cancelled_locked(current, str(exc))
        except Exception as exc:  # noqa: BLE001 - task failures should be visible to users.
            with self._lock:
                current = self._tasks.get(task_id)
                if not current or current.status not in _RUNNING_STATUSES:
                    return
                if current.cancel_requested or cancel_event.is_set() or current.status == "cancelling":
                    self._commit_cancelled_locked(current, "Sub-agent task was cancelled.")
                    return
                next_task = copy.deepcopy(current)
                next_task.status = "failed"
                next_task.error = str(exc)
                next_task.stopped_at = utc_now()
                next_task.handoff_status = "handoff_pending"
                self._commit_task_event_locked(current, next_task, "failed", {"error": str(exc)})

    def _commit_cancelled_locked(self, task: SubAgentTask, error: str) -> SubAgentTask:
        next_task = copy.deepcopy(task)
        next_task.status = "cancelled"
        next_task.cancel_requested = True
        next_task.error = error
        next_task.stopped_at = utc_now()
        return self._commit_task_event_locked(task, next_task, "cancelled", {"error": error})

    def _task_payload(self, task: SubAgentTask) -> dict[str, Any]:
        payload = copy.deepcopy(task.params)
        payload.setdefault("taskId", task.id)
        payload.setdefault("task", task.task)
        payload.setdefault("projectPath", task.project_path)
        payload.setdefault("parentChatId", task.parent_chat_id)
        payload.setdefault("parentSessionId", task.parent_session_id)
        payload.setdefault("displayName", task.display_name)
        payload.setdefault("retryOf", task.retry_of)
        return payload

    def _serialize_role(self, role: SubAgentRole) -> dict[str, Any]:
        return {
            "id": role.id,
            "title": role.title,
            "description": role.description,
            "toolProfile": role.tool_profile,
            "readOnly": role.read_only,
        }

    def _serialize_task(self, task: SubAgentTask, include_events: bool = False) -> dict[str, Any]:
        payload = self._task_snapshot(task)
        payload["schema"] = SUB_AGENT_SCHEMA
        payload["result"] = copy.deepcopy(task.result)
        payload["paramsSummary"] = summarize_params(task.params)
        payload.pop("params", None)
        if include_events:
            payload["events"] = [event for event in self.recent_events(limit=500) if event.get("taskId") == task.id]
        return payload

    def _task_snapshot(self, task: SubAgentTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "role": task.role,
            "displayName": task.display_name,
            "task": task.task,
            "parentChatId": task.parent_chat_id,
            "parentSessionId": task.parent_session_id,
            "projectPath": task.project_path,
            "toolProfile": task.tool_profile,
            "status": task.status,
            "createdAt": task.created_at,
            "startedAt": task.started_at,
            "stoppedAt": task.stopped_at,
            "updatedAt": task.updated_at,
            "cancelRequested": task.cancel_requested,
            "summary": task.summary,
            "error": task.error,
            "eventCount": task.event_count,
            "revision": task.revision,
            "retryOf": task.retry_of,
            "handoffStatus": task.handoff_status,
            "handoffAt": task.handoff_at,
            "mergedAt": task.merged_at,
            "mergedChatId": task.merged_chat_id,
            "mergeDecision": task.merge_decision,
            "resultAvailable": task.result_available,
            "resultUnavailable": task.result_unavailable,
            "params": redact_for_storage(task.params),
        }

    def _task_from_snapshot(self, snapshot: dict[str, Any]) -> SubAgentTask | None:
        task_id = str(snapshot.get("id") or "").strip()
        if not task_id:
            return None
        params = snapshot.get("params")
        return SubAgentTask(
            id=task_id,
            role=str(snapshot.get("role") or "project_index_review"),
            display_name=str(snapshot.get("displayName") or "Manuka"),
            task=str(snapshot.get("task") or ""),
            parent_chat_id=str(snapshot.get("parentChatId") or ""),
            parent_session_id=str(snapshot.get("parentSessionId") or ""),
            project_path=str(snapshot.get("projectPath") or ""),
            tool_profile=str(snapshot.get("toolProfile") or "read-only"),
            status=str(snapshot.get("status") or "queued"),
            created_at=str(snapshot.get("createdAt") or utc_now()),
            started_at=str(snapshot.get("startedAt") or ""),
            stopped_at=str(snapshot.get("stoppedAt") or ""),
            updated_at=str(snapshot.get("updatedAt") or snapshot.get("createdAt") or utc_now()),
            params=copy.deepcopy(params if isinstance(params, dict) else {}),
            result_available=bool(snapshot.get("resultAvailable")),
            result_unavailable=bool(snapshot.get("resultUnavailable")),
            summary=str(snapshot.get("summary") or ""),
            error=str(snapshot.get("error") or ""),
            cancel_requested=bool(snapshot.get("cancelRequested")),
            event_count=int(snapshot.get("eventCount") or 0),
            revision=int(snapshot.get("revision") or 0),
            retry_of=str(snapshot.get("retryOf") or ""),
            handoff_status=str(snapshot.get("handoffStatus") or ""),
            handoff_at=str(snapshot.get("handoffAt") or ""),
            merged_at=str(snapshot.get("mergedAt") or ""),
            merged_chat_id=str(snapshot.get("mergedChatId") or ""),
            merge_decision=str(snapshot.get("mergeDecision") or ""),
        )

    def _running_count_locked(self) -> int:
        return sum(1 for task in self._tasks.values() if task.status in _RUNNING_STATUSES)

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
        payload = {
            "schema": SUB_AGENT_RESULT_SCHEMA,
            "taskId": task_id,
            "summary": summary,
            "result": result,
        }
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

    def _read_result_sidecar_locked(self, task: SubAgentTask) -> None:
        if not task.result_available:
            return
        try:
            payload = json.loads(self._result_path(task.id).read_text(encoding="utf-8"))
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise ValueError("result sidecar did not contain an object")
            task.result = result
            task.result_unavailable = False
        except (OSError, ValueError, json.JSONDecodeError):
            task.result = None
            task.result_available = False
            task.result_unavailable = True

    def _load_projection_locked(self) -> None:
        path = self._event_log_path()
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        legacy_tasks: dict[str, SubAgentTask] = {}
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            snapshot = entry.get("task")
            if isinstance(snapshot, dict):
                task = self._task_from_snapshot(snapshot)
                if task:
                    self._tasks[task.id] = task
                continue
            self._project_legacy_event(entry, legacy_tasks)
        for task_id, task in legacy_tasks.items():
            self._tasks.setdefault(task_id, task)
        for task in self._tasks.values():
            self._read_result_sidecar_locked(task)
            self._cancel_events[task.id] = threading.Event()
            if task.cancel_requested:
                self._cancel_events[task.id].set()

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

    def _revision_conflict(self, task: SubAgentTask) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "sub-agent task revision changed.",
            "currentRevision": task.revision,
            "task": self._serialize_task(task),
        }

    def _event_log_path(self) -> Path:
        return self.artifact_dir / "sub-agent-events.jsonl"

    def _result_path(self, task_id: str) -> Path:
        safe_task_id = "".join(char for char in task_id if char.isalnum() or char in {"-", "_"})
        if safe_task_id != task_id or not safe_task_id:
            raise ValueError("invalid sub-agent task id")
        return self.artifact_dir / "results" / f"{safe_task_id}.json"


class CancelledError(RuntimeError):
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
