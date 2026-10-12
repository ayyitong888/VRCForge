"""Bounded, change-only Telegram projections for runtime and Unity status."""

from __future__ import annotations

import asyncio
import uuid
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


Snapshot = Callable[[str, str], Awaitable[dict[str, Any]]]
Enqueue = Callable[[dict[str, Any]], Any]
Bindings = Callable[[], Any]


class TelegramBotMonitor:
    def __init__(
        self,
        snapshot: Snapshot,
        enqueue: Enqueue,
        bindings: Bindings,
        *,
        interval: float = 5.0,
        max_tasks: int = 8,
        sample_timeout: float = 10.0,
    ) -> None:
        if interval <= 0 or max_tasks <= 0 or sample_timeout <= 0:
            raise ValueError("monitor bounds must be positive")
        self._snapshot = snapshot
        self._enqueue = enqueue
        self._bindings = bindings
        self._interval = interval
        self._max_tasks = max_tasks
        self._sample_timeout = sample_timeout
        self._stop = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None
        self._owned: set[asyncio.Task[Any]] = set()
        self._previous: dict[tuple[str, str], dict[str, str]] = {}
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("telegram monitor is closed")
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            entries = self._read_bindings()
            semaphore = asyncio.Semaphore(self._max_tasks)
            jobs = [self._sample_one(entry, semaphore) for entry in entries]
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def _read_bindings(self) -> list[dict[str, str]]:
        try:
            raw = self._bindings()
        except Exception:
            return []
        if isinstance(raw, Mapping):
            raw = [dict(value, route=str(key)) if isinstance(value, Mapping) else {} for key, value in raw.items()]
        if not isinstance(raw, (list, tuple)):
            return []
        result = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            route = str(item.get("route") or "")
            project = str(item.get("project_root") or item.get("projectRoot") or "")
            session = str(item.get("session_id") or item.get("sessionId") or "")
            if route and session:
                result.append({"route": route, "project": project, "session": session})
        return result

    async def _sample_one(self, entry: Mapping[str, str], semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            key = (entry["route"], entry["session"])
            try:
                raw = await asyncio.wait_for(
                    self._snapshot(entry["project"], entry["session"]), timeout=self._sample_timeout
                )
                projection = _project_snapshot(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                projection = {"task": "monitor_error", "unity": "unknown"}
            previous = self._previous.get(key)
            self._previous[key] = projection
            if previous is None or previous == projection:
                return
            for event in _projection_events(entry, projection, previous):
                try:
                    self._enqueue(event)
                except Exception:
                    # One outbound sink failure must not stop sampling others.
                    return

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        tasks = [task for task in (self._task, *tuple(self._owned)) if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)
        self._task = None
        self._owned.clear()


def _project_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"runId": "", "clientTurnId": "", "task": "unknown", "unity": "unknown", "progress": ""}
    result: dict[str, Any] = {
        "runId": str(raw.get("runId") or ""),
        "clientTurnId": str(raw.get("clientTurnId") or ""),
        "task": _task_state(str(raw.get("taskStatus") or "unknown")),
        "unity": _connection_state(str(raw.get("unityStatus") or "unknown")),
        "progress": str(raw.get("progressSummary") or "")[:200],
    }
    if isinstance(raw.get("contextUsage"), Mapping):
        result["contextUsage"] = dict(raw["contextUsage"])
    return result


def _task_state(value: str) -> str:
    value = value.lower()
    if value in {"completed", "complete", "done", "succeeded"}:
        return "completed"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    if value in {"waiting_approval", "waiting_for_approval"}:
        return "waiting_approval"
    if value in {"running", "started", "active", "pending", "queued"}:
        return "running"
    return "unknown"


def format_monitor_status(raw: Any) -> str:
    projection = _project_snapshot(raw)
    task = {"completed": "运行已结束，请核对结果", "failed": "失败", "cancelled": "已取消",
            "waiting_approval": "等待审批", "running": "运行中"}.get(projection["task"], "未知")
    unity = {"connected": "已连接", "disconnected": "未连接", "not_applicable": "未选择工程（不适用）"}.get(projection["unity"], "未知")
    progress = projection["progress"]
    context = _format_context_status(projection.get("contextUsage"))
    return f"任务：{task}\nUnity/Core：{unity}" + (f"\n进度：{progress}" if progress else "") + context


def _format_context_status(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "\n上下文：暂无最近回合精确用量"
    if value.get("status") != "available":
        reason = {"model_changed": "模型已切换", "compaction_applied": "会话已压缩",
                  "provider_usage_missing": "provider 未返回精确用量"}.get(
                      str(value.get("unavailableReason") or ""), "暂无精确用量")
        return f"\n上下文：{reason}"
    used = value.get("usedTokens")
    if not isinstance(used, int) or used <= 0:
        return "\n上下文：暂无最近回合精确用量"
    limit = value.get("limitTokens")
    source = "最近回合峰值"
    if isinstance(limit, int) and limit > 0:
        percent = round(used / limit * 100)
        return f"\n上下文：{used:,} / {limit:,} tokens（{percent}% · {source}）"
    return f"\n上下文：{used:,} tokens（{source}；上限未知）"


def _connection_state(value: str) -> str:
    lowered = value.lower()
    if lowered == "not_applicable":
        return "not_applicable"
    if lowered in {"connected", "ready", "online", "running"}:
        return "connected"
    if lowered in {"disconnected", "offline", "error", "failed"}:
        return "disconnected"
    return "unknown"


def _projection_events(entry: Mapping[str, str], projection: Mapping[str, Any], previous: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if projection.get("task") != previous.get("task") or projection.get("runId") != previous.get("runId"):
        status = projection.get("task", "unknown")
        event = "telegram_task_status_changed"
        reply = {"completed": "本轮运行已结束，请查看结果和验证状态。", "failed": "任务失败。", "cancelled": "任务已取消。",
                 "waiting_approval": "等待审批。", "running": "任务运行中。"}.get(status, "任务状态未知。")
        events.append(_event(entry, projection, event, status, reply))
    elif (projection.get("task") == "running" and projection.get("progress")
          and projection.get("progress") != previous.get("progress")):
        events.append(_event(entry, projection, "telegram_task_status_changed", "running",
                             "任务进度：" + projection["progress"]))
    if projection.get("unity") != previous.get("unity"):
        status = projection.get("unity", "unknown")
        reply = {"connected": "Unity/Core 已连接。", "not_applicable": "Unity/Core：未选择工程（不适用）。"}.get(status, "Unity/Core 连接状态已变化。")
        events.append(_event(entry, projection, "telegram_unity_status_changed", status, reply))
    return events


def _event(entry: Mapping[str, str], projection: Mapping[str, str], kind: str, status: str, reply: str) -> dict[str, Any]:
    material = json.dumps({"kind": kind, "runId": projection.get("runId", ""), "status": status}, sort_keys=True)
    version = uuid.uuid4().hex
    return {"event": kind, "sessionId": entry["session"], "runId": projection.get("runId", ""), "clientTurnId": projection.get("clientTurnId", ""),
            "version": version, "metadata": {"route": entry["route"]},
            "result": {"reply": reply, "status": status}}
