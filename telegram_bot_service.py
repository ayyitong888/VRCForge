"""Single backend-owner lifecycle for the optional Telegram integration."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

from telegram_bot_commands import TelegramCommandDispatcher
from telegram_bot_host import TelegramBotHost
from telegram_bot_settings import TelegramSettings
from telegram_bot_store import TelegramBotStore
from telegram_bot_transport import TelegramBotTransport
from telegram_bot_approvals import TelegramApprovalController
from telegram_bot_permissions import TelegramPermissionController
from telegram_bot_monitor import TelegramBotMonitor
from telegram_bot_menu import TelegramBotMenu
from telegram_bot_previews import TelegramPreviews


class TelegramBotService:
    def __init__(self, *, settings: TelegramSettings, database_path: Path,
                 runtime_factory: Callable[..., Any], owns_backend: Callable[[], bool],
                 approval_port: Any = None,
                 permission_port: Any = None,
                 questions: Any = None,
                 recovery: Any = None,
                 monitor_snapshot: Callable[..., Any] | None = None,
                 transport_factory: Callable[..., Any] = TelegramBotTransport) -> None:
        self.settings = settings
        self.database_path = database_path
        self.runtime_factory = runtime_factory
        self.owns_backend = owns_backend
        self.transport_factory = transport_factory
        self.approval_port = approval_port
        self.permission_port = permission_port
        self.questions = questions
        self.recovery = recovery
        self.monitor_snapshot = monitor_snapshot
        self._monitor = None
        self._menu = None
        self._watched: dict[str, dict] = {}
        self.previews = TelegramPreviews()
        self._host = None
        self._runtime = None
        self._store = None
        self._error = None
        self._lock = asyncio.Lock()
        self._loop = None
        self._projections: set[asyncio.Task] = set()
        self._approval_tasks: set[asyncio.Task] = set()
        self._approval_controller: TelegramApprovalController | None = None
        self._approval_lifecycle = secrets.token_urlsafe(9)
        self._phases: dict[str, tuple[str, float]] = {}
        self._dispatcher = None

    def status(self) -> dict:
        try:
            projection = self.settings.projection()
        except Exception:
            projection = {"configured": False, "ownerId": None, "projects": []}
            self._error = "config_unavailable"
        host_status = self._host.status() if self._host is not None else {}
        running = host_status.get("running") is True
        return {**projection, "enabled": self._host is not None,
                "state": "running" if running else "starting" if self._host is not None and not host_status.get("channelError")
                else "configured" if projection["configured"] else "disabled",
                "errorCode": host_status.get("channelError") or self._error,
                "menuErrorCode": self._menu.error_code if self._menu is not None else None}

    async def configure(self, **values: Any) -> None:
        async with self._lock:
            if self._host is not None:
                raise ValueError("Disconnect Telegram before changing its owner or scope")
            self.settings.save(**values)
            self._error = None

    def _make_transport(self, config: dict[str, Any]) -> Any:
        mode = config.get("proxyMode", "environment")
        proxy_url = config.get("proxyUrl", "")
        factory = self.transport_factory
        try:
            parameters = inspect.signature(factory).parameters.values()
            supports_options = any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                                   or parameter.name in {"proxy_mode", "proxy_url"}
                                   for parameter in parameters)
        except (TypeError, ValueError):
            supports_options = True
        if not supports_options:
            if mode != "environment" or proxy_url:
                raise ValueError("telegram_proxy_transport_unsupported")
            return factory(config["token"])
        return factory(config["token"], proxy_mode=mode, proxy_url=proxy_url)

    async def test_connection(self) -> dict[str, bool]:
        """Validate the saved bot and proxy without starting polling or runtime."""
        config = self.settings.load()
        transport = None
        try:
            transport = self._make_transport(config)
            identity = await asyncio.wait_for(transport.get_me(), timeout=15.0)
            if not isinstance(identity, dict) or type(identity.get("id")) is not int or identity.get("is_bot") is not True:
                raise ValueError("telegram_bot_identity_invalid")
            return {"ok": True}
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError("telegram_connection_failed") from None
        finally:
            if transport is not None:
                try:
                    await transport.close()
                except Exception:
                    pass

    async def start(self) -> None:
        async with self._lock:
            if not self.owns_backend():
                raise RuntimeError("telegram_backend_not_owner")
            if self._host is not None:
                return
            config = self.settings.load()
            transport = self._make_transport(config)
            store = None
            runtime = None
            host = None
            try:
                identity = await transport.get_me()
                if not isinstance(identity, dict) or type(identity.get("id")) is not int or identity.get("is_bot") is not True:
                    raise ValueError("telegram_bot_identity_invalid")
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                store = TelegramBotStore(self.database_path, str(identity["id"]))
                runtime = self.runtime_factory(self._emit)
                self._approval_lifecycle = secrets.token_urlsafe(9)
                self._approval_controller = TelegramApprovalController(self.approval_port) if self.approval_port else None
                dispatcher = TelegramCommandDispatcher(owner_id=config["ownerId"], projects=tuple(config["projects"]),
                    store=store, runtime=runtime,
                    previews=self.previews,
                    questions=self.questions,
                    monitor_snapshot=self.monitor_snapshot,
                    permissions=TelegramPermissionController(self.permission_port) if self.permission_port else None,
                    approvals=self._approval_controller)
                self._menu = TelegramBotMenu(transport, config["ownerId"])
                menu = self._menu
                async def dispatch_with_menu(update):
                    menu.observe_update(update)
                    return await dispatcher.dispatch(update)
                host = TelegramBotHost(transport, store, dispatch_with_menu, config["ownerId"])
                self._store, self._runtime, self._host, self._dispatcher = store, runtime, host, dispatcher
                self._loop = asyncio.get_running_loop()
                self._watched = {row["session_id"]: row for row in store.watches()
                    if row["route"] == str(config["ownerId"]) and (not row["project_root"] or row["project_root"] in config["projects"])}
                await host.start()
                menu.request_sync()
                if self.monitor_snapshot is not None:
                    self._monitor = TelegramBotMonitor(self.monitor_snapshot, self._emit, self._monitor_bindings)
                    await self._monitor.start()
                for session, binding in tuple(self._watched.items()):
                    self._schedule_approval_notification(session, binding)
                self._error = None
                if self.recovery is not None:
                    task = asyncio.create_task(self._recover_notifications())
                    self._projections.add(task)
                    task.add_done_callback(self._projection_done)
            except BaseException:
                # Preserve the start failure while independently releasing every
                # owned resource; a cleanup failure must not skip later owners.
                approval_tasks = tuple(self._approval_tasks)
                for task in approval_tasks:
                    task.cancel()
                if approval_tasks:
                    await asyncio.gather(*approval_tasks, return_exceptions=True)
                for resource, method in ((self._menu, "close"), (self._monitor, "close"), (host, "close"),
                                         (runtime, "shutdown")):
                    if resource is not None:
                        try:
                            await getattr(resource, method)()
                        except BaseException:
                            pass
                try:
                    if store is not None:
                        store.close()
                except BaseException:
                    pass
                try:
                    await transport.close()
                except BaseException:
                    pass
                self._host = self._runtime = self._store = self._monitor = None
                self._menu = None
                self._dispatcher = None
                self._approval_controller = None
                self._loop = None
                self._watched.clear()
                self._error = "connection_failed"
                raise

    def _emit(self, event: dict) -> None:
        # The host's event projection queues notifications; it never executes
        # a task again merely because its notification was not delivered.
        if event.get("event") == "turn_started":
            metadata = event.get("metadata") or {}
            if self._store is not None and self._store.watch_session(str(metadata.get("route") or ""),
                    str(metadata.get("projectRoot") or ""), event["sessionId"]):
                self._watched[event["sessionId"]] = {"route": metadata.get("route"),
                    "project_root": metadata.get("projectRoot"), "session_id": event["sessionId"]}
        if self._host is not None:
            self._host.emit(event)
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        waiting = (event.get("phase") == "waiting_approval"
                   or result.get("status") == "waiting_approval")
        if event.get("event") == "turn_returned" or (
                event.get("event") == "telegram_task_status_changed" and waiting):
            session = str(event.get("sessionId") or "")
            binding = self._watched.get(session)
            if binding is not None:
                self._schedule_approval_notification(session, binding)
        if event.get("event") == "turn_returned":
            self._offer_project_selection(event)

    def request_project_selection(self, scope: dict) -> dict:
        """Runtime worker -> service loop; only this bot's current general chat.

        The gateway authenticates the native task. This bridge grants no project
        authority. One durable pending request belongs to the authenticated bot
        route; the loop owns SQLite, and the worker wait is limited to 1 second.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return {"status": "failed", "errorCode": "channel_unavailable"}
        result = Future()
        def record() -> None:
            if result.cancelled():
                return
            try:
                result.set_result(self._record_project_selection(scope))
            except Exception:
                if not result.done():
                    result.set_result({"status": "failed", "errorCode": "project_selection_unavailable"})
        try:
            loop.call_soon_threadsafe(record)
            return result.result(timeout=1.0)
        except (FutureTimeout, RuntimeError):
            result.cancel()
            return {"status": "failed", "errorCode": "project_selection_unavailable"}

    def _record_project_selection(self, scope: dict) -> dict:
        if self._store is None or self._dispatcher is None:
            return {"status": "failed", "errorCode": "channel_unavailable"}
        route = str(self._dispatcher.owner_id)
        binding = self._store.get_binding(route)
        self._refresh_watches()
        watch = self._watched.get(str(scope.get("sessionId") or ""))
        if (not binding or binding.get("project_root") or scope.get("projectRoot")
                or binding.get("session_id") != scope.get("chatId") or not watch
                or watch["route"] != route or watch["project_root"] or not scope.get("clientTurnId")):
            return {"status": "failed", "errorCode": "project_selection_scope_mismatch"}
        text = scope.get("requestText")
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            return {"status": "failed", "errorCode": "project_selection_invalid_request"}
        self._store.save_project_request(route, {**scope, "ready": False})
        return {"ok": True, "status": "needs_user_action", "projectSelectionRequested": True,
                "summary": "请先选择要操作的工程；选择后会继续当前需求。工程选择不代表批准修改。"}

    def _offer_project_selection(self, event: dict) -> None:
        if self._store is None or self._dispatcher is None:
            return
        route = str(self._dispatcher.owner_id)
        pending = self._store.project_request(route)
        if not pending or pending.get("sessionId") != event.get("sessionId") or pending.get("clientTurnId") != event.get("clientTurnId"):
            return
        binding = self._store.get_binding(route)
        if not binding or binding.get("session_id") != pending.get("chatId") or binding.get("project_root"):
            return
        pending["ready"] = True
        self._store.save_project_request(route, pending)
        prompt = self._dispatcher.onboarding.choose_project(route, 0, chat_id=pending["chatId"], text=pending["requestText"])
        for index, message in enumerate(prompt.get("messages", [prompt])):
            self._store.enqueue_outbox(f"project-choice:{event['sessionId']}:{event['clientTurnId']}:{index}",
                {"chat_id": self._dispatcher.owner_id, "text": message["text"],
                 **({"reply_markup": message["reply_markup"]} if message.get("reply_markup") else {})})

    async def _recover_notifications(self) -> None:
        # Existing ledger only: never start or replay an agent execution.
        for session, binding in tuple(self._watched.items()):
            payloads = await self.recovery.completed_continuations(session, binding["project_root"])
            for payload in payloads:
                try:
                    await self._runtime.project_continuation(payload, binding["project_root"], binding["route"])
                except Exception:
                    self._error = "continuation_projection_failed"

    def _monitor_bindings(self) -> list[dict]:
        self._refresh_watches()
        rows = list(self._watched.values())
        if self._store is not None:
            owner = self.settings.projection()["ownerId"]
            current = self._store.get_binding(str(owner))
            if current and current.get("session_id") and not any(
                row["project_root"] == current["project_root"]
                and row["session_id"] == current["session_id"] for row in rows
            ):
                rows.append({**current, "route": str(owner)})
        return rows

    def _refresh_watches(self) -> None:
        if self._store is None:
            return
        config = self.settings.projection()
        self._watched = {row["session_id"]: row for row in self._store.watches()
            if row["route"] == str(config["ownerId"]) and (not row["project_root"] or row["project_root"] in config["projects"])}

    def record_capture(self, *, project: str, session: str, approval: str, image: bytes) -> bool:
        if self._host is None or project not in self.settings.projection()["projects"]:
            return False
        return self.previews.record(project=project, session=session, approval=approval, image=image)

    def observe_continuation(self, payload: dict) -> None:
        """Read-only projection of an app-owned result, scoped to a watched task.

        Called by the existing runtime completion hook, potentially on its worker
        thread. The service loop owns all store writes and bounded task lifetime.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._schedule_projection, dict(payload))
        except RuntimeError:
            return

    def notify_foreground_result(self, payload: dict) -> None:
        """Notify a watched session without competing with desktop transcript writes."""
        if self._host is None:
            return
        self._refresh_watches()
        session = str(payload.get("sessionId") or payload.get("session_id") or "")
        binding = self._watched.get(session)
        if binding is None:
            return
        turn = str(payload.get("turnId") or payload.get("turn_id") or "")
        client = str(payload.get("clientTurnId") or "")
        if not turn or not client:
            return
        self._host.emit({"event": "turn_returned", "sessionId": session, "turnId": turn,
            "clientTurnId": client, "metadata": {"route": binding["route"]}, "result": payload})

    def observe_runtime_status(self, payload: dict) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._notify_runtime_status, dict(payload))
            except RuntimeError:
                pass

    def _notify_runtime_status(self, payload: dict) -> None:
        labels = {"preparing": "正在准备任务。", "waiting_for_model": "正在等待模型回复。",
            "running_tool": "正在执行工具。", "waiting_for_approval": "需要你的审批，下面的卡片可直接处理。",
            "verifying": "正在验证修改结果。"}
        phase = payload.get("phase")
        if phase not in labels or self._host is None:
            return
        self._refresh_watches()
        session = str(payload.get("sessionId") or "")
        binding = self._watched.get(session)
        if binding is None or not payload.get("turnId") or not payload.get("clientTurnId"):
            return
        identity = str(payload["turnId"]) + ":" + phase
        previous, last = self._phases.get(session, ("", 0.0))
        now = time.monotonic()
        if previous == identity or (now - last < 2 and phase != "waiting_for_approval"):
            return
        self._phases[session] = (identity, now)
        if phase == "waiting_for_approval":
            self._schedule_approval_notification(session, binding)
            return
        self._host.emit({"event": "telegram_task_status_changed", "sessionId": session,
            "phase": phase, "turnId": payload["turnId"], "clientTurnId": payload["clientTurnId"],
            "version": identity + ":" + str(now), "metadata": {"route": binding["route"]},
            "result": {"reply": labels[phase], "status": phase}})

    def _schedule_approval_notification(self, session: str, binding: dict) -> None:
        controller = self._approval_controller
        if controller is None or self._store is None or self._host is None:
            return
        if sum(not task.done() for task in self._approval_tasks) >= 8:
            self._error = "approval_notification_busy"
            return
        if any(getattr(task, "_vrcforge_session", None) == session and not task.done()
               for task in self._approval_tasks):
            return
        owner = self.settings.projection().get("ownerId")
        if type(owner) is not int or not session:
            return
        task = asyncio.create_task(self._notify_pending_approvals(
            controller, owner, session, dict(binding)))
        task._vrcforge_session = session  # type: ignore[attr-defined]
        self._approval_tasks.add(task)
        task.add_done_callback(self._approval_task_done)

    async def _notify_pending_approvals(self, controller: TelegramApprovalController, owner: int,
                                        session: str, binding: dict) -> None:
        messages = await asyncio.wait_for(controller.pending_messages(owner_id=owner, chat_id=owner,
            project_root=str(binding.get("project_root") or ""), session_id=session,
            reuse_existing=True), 3.0)
        for message in messages:
            approval_id = str(message.get("approvalId") or "")
            if not approval_id:
                continue
            callback_data = json.dumps(message.get("buttons") or [], ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256((str(message.get("text") or "") + callback_data).encode("utf-8")).hexdigest()[:20]
            self._store.enqueue_outbox(f"approval-card:{self._approval_lifecycle}:{session}:{approval_id}:{digest}", {
                "chat_id": owner,
                "text": str(message.get("text") or "")[:2000],
                "reply_markup": {"inline_keyboard": [message.get("buttons", [])]},
            })

    def _approval_task_done(self, task: asyncio.Task) -> None:
        self._approval_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            self._error = "approval_notification_failed"

    def _schedule_projection(self, payload: dict) -> None:
        self._refresh_watches()
        session = str(payload.get("sessionId") or payload.get("session_id") or "")
        binding = self._watched.get(session)
        if self._runtime is None or self._host is None or binding is None:
            return
        if len(self._projections) >= 8:
            self._error = "continuation_projection_busy"
            return
        task = asyncio.create_task(self._runtime.project_continuation(payload,
            binding["project_root"], binding["route"]))
        self._projections.add(task)
        task.add_done_callback(self._projection_done)

    def _projection_done(self, task: asyncio.Task) -> None:
        self._projections.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            self._error = "continuation_projection_failed"

    async def stop(self) -> None:
        async with self._lock:
            self._loop = None
            projections = tuple(self._projections)
            for task in projections:
                task.cancel()
            if projections:
                await asyncio.gather(*projections, return_exceptions=True)
            approval_tasks = tuple(self._approval_tasks)
            for task in approval_tasks:
                task.cancel()
            if approval_tasks:
                await asyncio.gather(*approval_tasks, return_exceptions=True)
            runtime, host, store = self._runtime, self._host, self._store
            try:
                if self._menu is not None:
                    await self._menu.close()
                    self._menu = None
                if self._monitor is not None:
                    await self._monitor.close()
                    self._monitor = None
                if runtime is not None:
                    await runtime.shutdown()
            finally:
                try:
                    if host is not None:
                        await host.close()
                finally:
                    if store is not None:
                        store.close()
                    self._runtime = self._host = self._store = None
                    self._dispatcher = None
                    self._approval_controller = None
                    self._approval_tasks.clear()
                    self._watched.clear()
                    self._phases.clear()
                    self.previews.clear()
