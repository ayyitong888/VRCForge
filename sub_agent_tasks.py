from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SUB_AGENT_SCHEMA = "vrcforge.sub_agent_task.v1"
SUB_AGENT_LIST_SCHEMA = "vrcforge.sub_agent_tasks.v1"
SUB_AGENT_LOG_SCHEMA = "vrcforge.sub_agent_lifecycle.v1"
SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT = 5

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
    summary: str = ""
    error: str = ""
    cancel_requested: bool = False
    event_count: int = 0


class SubAgentTaskRegistry:
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
        parent_session_id: str = "",
        project_path: str = "",
        params: dict[str, Any] | None = None,
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
            event = threading.Event()
            sub_task = SubAgentTask(
                id=task_id,
                role=role_id,
                display_name=str(display_name or "").strip() or "Manuka",
                task=task_text,
                parent_session_id=str(parent_session_id or ""),
                project_path=str(project_path or ""),
                tool_profile=role_spec.tool_profile,
                params=params or {},
            )
            self._tasks[task_id] = sub_task
            self._cancel_events[task_id] = event
            self._record_event_locked(task_id, "created", {"role": role_id, "task": task_text})
            worker = threading.Thread(target=self._run_task, args=(task_id,), daemon=True, name=f"vrcforge-sub-agent-{task_id}")
            self._threads[task_id] = worker
            worker.start()
            return {"ok": True, "task": self._serialize_task(sub_task)}

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            if task.status in {"completed", "failed", "cancelled"}:
                return {"ok": True, "task": self._serialize_task(task), "message": "task already stopped"}
            task.cancel_requested = True
            task.status = "cancelling"
            task.updated_at = utc_now()
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            self._record_event_locked(task_id, "cancel_requested", {})
            return {"ok": True, "task": self._serialize_task(task)}

    def retry_task(self, task_id: str, display_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "sub-agent task was not found."}
            params = dict(task.params)
            return self.create_task(
                role=task.role,
                task=task.task,
                display_name=display_name or task.display_name,
                parent_session_id=task.parent_session_id,
                project_path=task.project_path,
                params=params,
            )

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
                events.append(payload)
        return events

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            cancel_event = self._cancel_events.get(task_id)
            if not task or not cancel_event:
                return
            task.status = "running"
            task.started_at = utc_now()
            task.updated_at = task.started_at
            self._record_event_locked(task_id, "started", {"role": task.role, "displayName": task.display_name})
        try:
            if cancel_event.is_set():
                raise CancelledError("Sub-agent task was cancelled before execution.")
            handler = self.handlers[task.role]
            result = handler(self._task_payload(task), cancel_event)
            if cancel_event.is_set():
                raise CancelledError("Sub-agent task was cancelled.")
            summary = summarize_worker_result(result)
            with self._lock:
                current = self._tasks[task_id]
                current.status = "completed"
                current.result = result
                current.summary = summary
                current.stopped_at = utc_now()
                current.updated_at = current.stopped_at
                self._record_event_locked(task_id, "completed", {"summary": summary})
        except CancelledError as exc:
            with self._lock:
                current = self._tasks[task_id]
                current.status = "cancelled"
                current.error = str(exc)
                current.stopped_at = utc_now()
                current.updated_at = current.stopped_at
                self._record_event_locked(task_id, "cancelled", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - task failures should be visible to users.
            with self._lock:
                current = self._tasks[task_id]
                current.status = "failed"
                current.error = str(exc)
                current.stopped_at = utc_now()
                current.updated_at = current.stopped_at
                self._record_event_locked(task_id, "failed", {"error": str(exc)})

    def _task_payload(self, task: SubAgentTask) -> dict[str, Any]:
        payload = dict(task.params)
        payload.setdefault("taskId", task.id)
        payload.setdefault("task", task.task)
        payload.setdefault("projectPath", task.project_path)
        payload.setdefault("parentSessionId", task.parent_session_id)
        payload.setdefault("displayName", task.display_name)
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
        payload = {
            "schema": SUB_AGENT_SCHEMA,
            "id": task.id,
            "role": task.role,
            "displayName": task.display_name,
            "task": task.task,
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
            "result": task.result,
            "paramsSummary": summarize_params(task.params),
        }
        if include_events:
            payload["events"] = [event for event in self.recent_events(limit=500) if event.get("taskId") == task.id]
        return payload

    def _running_count_locked(self) -> int:
        return sum(1 for task in self._tasks.values() if task.status in {"queued", "running", "cancelling"})

    def _record_event_locked(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.event_count += 1
            task.updated_at = utc_now()
        entry = {
            "schema": SUB_AGENT_LOG_SCHEMA,
            "timestamp": utc_now(),
            "taskId": task_id,
            "event": event,
            "data": summarize_params(data),
        }
        path = self._event_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _event_log_path(self) -> Path:
        return self.artifact_dir / "sub-agent-events.jsonl"


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
        for key in ("status", "changed", "findingCount", "addedFiles", "modifiedFiles", "deletedFiles", "prefabCandidateCount", "unityPackageCount"):
            if key in summary:
                parts.append(f"{key}={summary.get(key)}")
        if parts:
            return "Worker finished: " + ", ".join(parts)
    if result.get("ok") is False:
        return "Worker failed: " + str(result.get("error") or "unknown error")[:500]
    return "Worker finished."


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
