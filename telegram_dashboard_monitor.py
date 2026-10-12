"""Dashboard-owned adapter from durable chat/run/Unity sources to monitor snapshots."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any, Callable

from channel_context_status import project_context_usage


class TelegramDashboardMonitorMapper:
    def __init__(self, load_chats: Callable[[], Any], list_runs: Callable[..., Any], unity_status: Callable[[str], Any]) -> None:
        self._load_chats = load_chats
        self._list_runs = list_runs
        self._unity_status = unity_status

    async def snapshot(self, project_root: str, chat_or_session: str) -> dict[str, Any]:
        project = str(project_root or "").strip()
        identity = str(chat_or_session or "").strip()
        if not identity:
            raise PermissionError("telegram monitor scope is invalid")
        chats = await _read(self._load_chats)
        chat = _resolve_chat(chats, identity, project)
        session_id = str(chat.get("sessionId") or "")
        if not session_id:
            raise PermissionError("telegram monitor session is missing")
        runs = await _read_call(self._list_runs, session_id=session_id, project_root=project)
        unity = await _read_call(self._unity_status, project) if project else None
        run = _latest_run(runs, session_id, project)
        result = _canonical_snapshot(run, unity, chat)
        if not project:
            result["unityStatus"] = "not_applicable"
        return result


async def _read(callable_value: Callable[[], Any]) -> Any:
    value = await asyncio.to_thread(callable_value)
    return await value if inspect.isawaitable(value) else value


async def _read_call(callable_value: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    value = await asyncio.to_thread(lambda: callable_value(*args, **kwargs))
    return await value if inspect.isawaitable(value) else value


def _resolve_chat(raw: Any, identity: str, project: str) -> Mapping[str, Any]:
    chats = raw.get("chats") if isinstance(raw, Mapping) else raw
    if not isinstance(chats, list):
        raise PermissionError("telegram monitor chat scope is invalid")
    matches = [item for item in chats if isinstance(item, Mapping)
               and (str(item.get("id") or "") == identity or str(item.get("sessionId") or "") == identity)]
    if len(matches) != 1 or str(matches[0].get("projectPath") or matches[0].get("projectRoot") or "") != project:
        raise PermissionError("telegram monitor chat scope is invalid")
    return matches[0]


def _latest_run(raw: Any, session_id: str, project_root: str) -> Mapping[str, Any] | None:
    runs = raw.get("runs") if isinstance(raw, Mapping) else raw
    if not isinstance(runs, list):
        return None
    return next((run for run in runs if isinstance(run, Mapping)
                 and str(run.get("sessionId") or run.get("session_id") or "") == session_id
                 and str(run.get("projectRoot") or run.get("projectPath") or "") == project_root), None)


def _canonical_snapshot(run: Mapping[str, Any] | None, unity: Any, chat: Mapping[str, Any] | None = None) -> dict[str, Any]:
    run = run or {}
    status = str(run.get("status") or "unknown").lower().replace("-", "_")
    if status in {"blocked", "pending_approval", "approval_required", "waiting_for_approval"} and run.get("approvalId"):
        task_status = "waiting_approval"
    elif status in {"completed", "success", "succeeded", "finished"}:
        task_status = "completed"
    elif status in {"failed", "error", "failure", "timeout", "timed_out"}:
        task_status = "failed"
    elif status in {"cancelled", "canceled"}:
        task_status = "cancelled"
    elif status in {"queued", "running", "active", "started"}:
        task_status = "running"
    else:
        task_status = "unknown"
    connected = unity.get("connected") if isinstance(unity, Mapping) else None
    unity_state = "connected" if connected is True else "disconnected" if connected is False else "unknown"
    result = {
        "runId": str(run.get("turnId") or ""),
        "clientTurnId": str(run.get("clientTurnId") or ""),
        "taskStatus": task_status,
        "unityStatus": unity_state,
        "progressSummary": str(run.get("progressSummary") or "")[:200],
    }
    context_usage = project_context_usage(chat or {}, run)
    if context_usage is not None:
        result["contextUsage"] = context_usage
    return result
