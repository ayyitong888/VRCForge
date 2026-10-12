"""Channel-neutral session adapter over injected VRCForge runtime services.

The adapter deliberately knows neither FastAPI nor the transcript filesystem.
Its store callbacks must implement the existing full-snapshot/CAS contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from channel_turn_coordinator import ChannelRuntimePort, ChannelTurnCoordinator, ChannelTurnMetadata
from channel_session_controls import ChannelSessionControls, SessionControlError


class ChannelAdapterError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class ChannelChatStorePort(Protocol):
    def load(self) -> Any: ...
    def save(self, chats: list[dict[str, Any]], source_revisions: list[dict[str, Any]]) -> Any: ...


class ChannelNativeRuntimePort(Protocol):
    async def start_turn(self, session_id: str, client_turn_id: str, message: str, context: dict[str, Any]) -> Any: ...
    async def steer(self, session_id: str, target_client_turn_id: str, client_turn_id: str, message: str, context: dict[str, Any]) -> Any: ...
    async def cancel(self, session_id: str, target_client_turn_id: str, context: dict[str, Any]) -> Any: ...


class _RuntimeBridge:
    def __init__(self, runtime: ChannelNativeRuntimePort) -> None:
        self.runtime = runtime
        self.contexts: dict[tuple[str, str], dict[str, Any]] = {}

    async def start_turn(self, session_id: str, client_turn_id: str, message: str) -> Any:
        return await self.runtime.start_turn(session_id, client_turn_id, message, self.contexts.get((session_id, client_turn_id), {}))

    async def steer(self, session_id: str, target_client_turn_id: str, client_turn_id: str, message: str) -> Any:
        return await self.runtime.steer(session_id, target_client_turn_id, client_turn_id, message, self.contexts.get((session_id, target_client_turn_id), {}))

    async def cancel(self, session_id: str, target_client_turn_id: str) -> Any:
        return await self.runtime.cancel(session_id, target_client_turn_id, self.contexts.get((session_id, target_client_turn_id), {}))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelRuntimeAdapter:
    """Map channel chat identities to existing durable chats and runtime turns."""

    def __init__(
        self,
        runtime: ChannelRuntimePort,
        store: ChannelChatStorePort,
        emit: Callable[[dict[str, Any]], Any],
        *,
        max_active_sessions: int = 16,
        recovery: Any = None,
        model_catalog: Callable[[str], Any] | None = None,
        compactor: Callable[[dict[str, Any], str, str], Any] | None = None,
        unresolved: Callable[[dict[str, Any], str], Any] | None = None,
    ) -> None:
        self._store = store
        self._recovery = recovery
        self._unresolved = unresolved
        self._emit_callback = emit
        self._runtime_bridge = _RuntimeBridge(runtime)
        self._coordinator = ChannelTurnCoordinator(self._runtime_bridge, self._on_coordinator_event, max_active_sessions=max_active_sessions)
        self._event_tasks: set[asyncio.Task[Any]] = set()
        self._pending_submissions: dict[tuple[str, str], set[asyncio.Event]] = {}
        self._control_active: set[tuple[str, str]] = set()
        self._compact_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._session_controls = ChannelSessionControls(
            load_snapshot=self._load_snapshot,
            save_snapshot=self._save_snapshot,
            find_chat=lambda rows, chat_id, scope: self._find_chat(rows, chat_id, project_root=scope),
            busy=self._ensure_control_available,
            model_catalog=model_catalog,
            compactor=compactor,
        )

    async def model_options(self, chat_id: str, project_root: str) -> list[dict[str, Any]]:
        scope = self._require_project(project_root)
        key = (chat_id, scope)
        if key in self._control_active:
            raise SessionControlError("control_busy")
        self._control_active.add(key)
        try:
            return await self._session_controls.model_options(chat_id, scope)
        finally:
            self._control_active.discard(key)

    async def set_model(self, chat_id: str, project_root: str, provider: str, model: str) -> dict[str, Any]:
        scope = self._require_project(project_root)
        key = (chat_id, scope)
        if key in self._control_active:
            raise SessionControlError("control_busy")
        self._control_active.add(key)
        try:
            return await self._session_controls.set_model(chat_id, scope, provider, model)
        finally:
            self._control_active.discard(key)

    async def compact(self, chat_id: str, project_root: str, instructions: str = "") -> dict[str, Any]:
        scope = self._require_project(project_root)
        key = (chat_id, scope)
        if key in self._control_active:
            raise SessionControlError("control_busy")
        self._control_active.add(key)
        task = asyncio.create_task(self._session_controls.compact(chat_id, scope, instructions),
                                   name=f"channel-compact-{chat_id}")
        self._compact_tasks[key] = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return {"status": "cancelled", "chatId": chat_id, "projectRoot": scope}
        finally:
            self._compact_tasks.pop(key, None)
            self._control_active.discard(key)

    async def list_sessions(self, project_root: str) -> list[dict[str, Any]]:
        project_root = self._require_project(project_root)
        chats, _revisions = await self._load_snapshot()
        return [self._summary(chat) for chat in chats if str(chat.get("projectPath") or "") == project_root]

    async def list_available_sessions(self, project_roots: list[str]) -> list[dict[str, Any]]:
        """List summaries only for the caller's already-authorized scopes."""
        allowed = {self._require_project(value) for value in project_roots}
        chats, _revisions = await self._load_snapshot()
        return [self._summary(chat) for chat in chats if str(chat.get("projectPath") or "") in allowed]

    async def create_session(self, project_root: str, title: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        project_root = self._require_project(project_root)
        chats, revisions = await self._load_snapshot()
        request_id = str(request_id or "").strip()
        if request_id:
            stable = hashlib.sha256(f"{project_root}\0{request_id}".encode("utf-8")).hexdigest()[:24]
            chat_id, session_id = self._stable_identity(project_root, request_id, stable)
            existing = [chat for chat in chats if chat.get("id") == chat_id]
            if existing:
                if str(existing[0].get("projectPath") or "") != project_root:
                    raise ChannelAdapterError("session_scope_mismatch")
                return self._summary(existing[0])
        else:
            chat_id, session_id = self._random_identity()
        chat = {
            "id": chat_id,
            "title": str(title or self._default_title()).strip()[:180] or self._default_title(),
            "sessionId": session_id,
            "projectPath": project_root,
            "projectType": self._project_type(project_root),
            "items": [],
        }
        await self._save_snapshot([*chats, chat], revisions)
        self._emit({"event": "session_created", "session": self._summary(chat)})
        return self._summary(chat)

    async def bind_project(self, chat_id: str, project_root: str) -> dict[str, Any]:
        """Bind a general chat to a project while preserving its identity/history."""
        target = str(project_root or "").strip()
        if not target:
            raise ChannelAdapterError("invalid_project")
        chats, revisions = await self._load_snapshot()
        chat = self._find_chat(chats, chat_id, project_root="")
        session_id = str(chat.get("sessionId") or "")
        if self._coordinator.has_active_session(session_id):
            raise ChannelAdapterError("active_run")
        if self._recovery is not None:
            decision = await self._recovery.check(session_id, "bind-project", "")
            if not getattr(decision, "allowed", False) or getattr(decision, "status", "") != "absent":
                raise ChannelAdapterError("active_run" if getattr(decision, "status", "") == "active" else "recovery_unavailable")
        updated = {**chat, "projectPath": target, "projectType": self._project_type(target)}
        await self._save_snapshot(
            [updated if row.get("id") == chat.get("id") else row for row in chats], revisions
        )
        summary = self._summary(updated)
        self._emit({"event": "session_project_bound", "session": summary})
        return summary

    async def get_session(self, chat_id: str, project_root: str) -> dict[str, Any]:
        project_root = self._require_project(project_root)
        chats, _revisions = await self._load_snapshot()
        chat = self._find_chat(chats, chat_id, project_root=project_root)
        self._emit({"event": "session_selected", "session": self._summary(chat)})
        return self._summary(chat)

    async def history(self, chat_id: str, project_root: str) -> list[dict[str, str]]:
        chats, _ = await self._load_snapshot()
        chat = self._find_chat(chats, chat_id, project_root=self._require_project(project_root))
        return [
            {"role": "user" if row["role"] == "user" else "assistant", "content": row["text"]}
            for row in self._runtime_history(chat.get("items") or [])
        ]

    async def submit(
        self, chat_id: str, client_turn_id: str, message: str, project_root: str,
        *, metadata: ChannelTurnMetadata | dict[str, Any] | None = None, steer_only: bool = False,
    ) -> dict[str, Any]:
        # Only in-flight submissions hold handles; stop can cancel them while
        # storage/ledger awaits are pending, before the coordinator owns a turn.
        key = (chat_id, self._require_project(project_root))
        cancelled = asyncio.Event()
        pending = self._pending_submissions.setdefault(key, set())
        pending.add(cancelled)
        try:
            return await self._submit(chat_id, client_turn_id, message, project_root,
                metadata=metadata, steer_only=steer_only, cancelled=cancelled)
        finally:
            pending.discard(cancelled)
            if not pending:
                self._pending_submissions.pop(key, None)

    async def _submit(
        self,
        chat_id: str,
        client_turn_id: str,
        message: str,
        project_root: str,
        *,
        metadata: ChannelTurnMetadata | dict[str, Any] | None = None,
        steer_only: bool = False,
        cancelled: asyncio.Event,
    ) -> dict[str, Any]:
        project_root = self._require_project(project_root)
        if not str(message or "").strip():
            raise ChannelAdapterError("invalid_message")
        chats, revisions = await self._load_snapshot()
        chat = self._find_chat(chats, chat_id, project_root=project_root)
        session_id = str(chat.get("sessionId") or "").strip()
        if not session_id:
            session_id = f"sess_{secrets.token_urlsafe(18)}"
            chat = {**chat, "sessionId": session_id}
        if (chat_id, project_root) in self._control_active:
            return {"status": "notaccepted", "reason": "control_busy", "chatId": chat["id"]}
        if any(str(item.get("id") or "") == client_turn_id for item in chat.get("items") or [] if isinstance(item, dict)):
            return {"status": "duplicate", "chatId": chat["id"], "sessionId": session_id, "clientTurnId": client_turn_id}
        external_control: Any = None
        owned_active = self._coordinator.has_active_session(session_id)
        if self._recovery is not None and not owned_active:
            decision = await self._recovery.check(session_id, client_turn_id, project_root)
            if not decision.allowed:
                if decision.status == "active" and hasattr(self._recovery, "control"):
                    external_control = await self._recovery.control(session_id, "", project_root)
                    if not external_control.allowed:
                        return {"status": "notaccepted", "reason": external_control.reason, "recoveryStatus": external_control.status}
                else:
                    return {"status": "notaccepted", "reason": decision.reason, "recoveryStatus": decision.status}
        if steer_only and not owned_active and external_control is None:
            return {"status": "notaccepted", "reason": "no_active_turn", "chatId": chat["id"]}
        if cancelled.is_set():
            return {"status": "notaccepted", "reason": "stopped_before_submission", "chatId": chat["id"]}
        item = {"id": client_turn_id, "type": "user", "text": message, "createdAt": _now()}
        updated = {**chat, "items": [*list(chat.get("items") or []), item]}
        await self._save_snapshot([updated if item_chat.get("id") == chat.get("id") else item_chat for item_chat in chats], revisions)
        if cancelled.is_set():
            return {"status": "notaccepted", "reason": "stopped_before_submission", "chatId": chat["id"]}
        if isinstance(metadata, ChannelTurnMetadata):
            route = metadata.route
            provider = metadata.provider
            model = metadata.model
            context = {"chatId": chat["id"], "projectRoot": project_root, "provider": provider, "model": model,
                       "history": self._runtime_history(chat.get("items") or []),
                       "compactionRecovery": self._compaction_recovery(chat.get("items") or []),
                       "projectType": self._project_type(project_root),
                       "sessionConfig": self._session_runtime_config(chat)}
        else:
            raw = dict(metadata or {})
            route = str(raw.get("route") or "")
            provider = str(raw.get("provider") or "")
            model = str(raw.get("model") or "")
            context = {**raw, "chatId": chat["id"], "projectRoot": project_root, "provider": provider, "model": model,
                       "history": self._runtime_history(chat.get("items") or []),
                       "compactionRecovery": self._compaction_recovery(chat.get("items") or []),
                       "projectType": self._project_type(project_root),
                       "sessionConfig": self._session_runtime_config(chat)}
        self._runtime_bridge.contexts[(session_id, client_turn_id)] = context
        if external_control is not None:
            self._runtime_bridge.contexts.pop((session_id, client_turn_id), None)
            target = str((external_control.run or {}).get("clientTurnId") or "").strip()
            if not target:
                self._runtime_bridge.contexts.pop((session_id, client_turn_id), None)
                raise ChannelAdapterError("recovery_target_missing")
            external_result = await self._dispatch_external_steer(
                chat, project_root, target, client_turn_id, message
            )
            return {**external_result, "chatId": chat["id"]}
        result = self._coordinator.submit(
            session_id,
            client_turn_id,
            message,
            metadata=ChannelTurnMetadata(route=route, project_root=project_root, provider=provider, model=model,
                                         chat_id=str(chat["id"]), session_title=str(chat.get("title") or "")),
        )
        if result.get("status") == "notaccepted":
            raise ChannelAdapterError("runtime_notaccepted", str(result.get("reason") or "runtime_notaccepted"))
        return {**result, "chatId": chat["id"]}

    async def project_continuation(self, payload: dict[str, Any], project_root: str, route: str) -> dict[str, Any]:
        """Persist an already-computed approval/question continuation.

        This is deliberately a projection-only lane: it never submits a new
        turn or calls the injected runtime.  Each CAS retry reloads the full
        snapshot so a concurrent desktop write is not overwritten blindly.
        """
        if not isinstance(payload, dict):
            raise ChannelAdapterError("continuation_invalid")
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "").strip()
        turn_id = str(payload.get("turnId") or payload.get("turn_id") or "").strip()
        client_turn_id = str(payload.get("clientTurnId") or payload.get("client_turn_id") or "").strip()
        project_root = self._require_project(project_root)
        route = str(route or "").strip()
        if not session_id or not turn_id or not client_turn_id or not route:
            raise ChannelAdapterError("continuation_invalid")
        item_id = turn_id
        notification = {"event": "turn_returned", "sessionId": session_id,
            "turnId": turn_id, "clientTurnId": client_turn_id,
            "metadata": {"route": route, "projectRoot": project_root}, "result": payload}
        for _attempt in range(3):
            chats, revisions = await self._load_snapshot()
            matches = [chat for chat in chats if str(chat.get("sessionId") or "") == session_id]
            if len(matches) != 1:
                raise ChannelAdapterError("session_not_found")
            chat = matches[0]
            if str(chat.get("projectPath") or "") != project_root:
                raise ChannelAdapterError("session_scope_mismatch")
            notification["metadata"].update(chatId=str(chat["id"]), sessionTitle=str(chat.get("title") or ""))
            existing = next((item for item in chat.get("items") or [] if isinstance(item, dict) and str(item.get("id") or "") == item_id), None)
            if existing is not None:
                if existing.get("response") != payload:
                    raise ChannelAdapterError("continuation_content_conflict")
                # A crash may occur after saving the transcript and before
                # enqueueing its notification. The durable outbox deduplicates
                # this same turn identity, including sent/unknown outcomes.
                self._emit(notification)
                return {"status": "duplicate", "chatId": chat["id"], "sessionId": session_id, "turnId": turn_id}
            item = {"id": item_id, "type": "agent", "response": payload, "createdAt": _now()}
            updated = {**chat, "items": [*list(chat.get("items") or []), item]}
            try:
                await self._save_continuation_snapshot(
                    [updated if row.get("id") == chat.get("id") else row for row in chats], revisions
                )
            except ChannelAdapterError as exc:
                if exc.code == "store_conflict" and _attempt < 2:
                    continue
                raise
            self._emit(notification)
            return {"status": "projected", "chatId": chat["id"], "sessionId": session_id, "turnId": turn_id}
        raise ChannelAdapterError("store_conflict")

    async def start_turn(self, session_id: str, message: str, client_turn_id: str, project_root: str, *, provider: str = "", model: str = "") -> dict[str, Any]:
        # Compatibility entry point for callers that still hold the runtime
        # session id.  The public Channel contract uses chat_id; resolve the
        # legacy id explicitly so list()->get() remains chat-id based.
        project_root = self._require_project(project_root)
        chats, _ = await self._load_snapshot()
        chat = self._find(chats, session_id, project_root=project_root)
        return await self.submit(chat["id"], client_turn_id, message, project_root, metadata={"provider": provider, "model": model, "route": session_id})

    async def steer(self, chat_id: str, message: str, client_turn_id: str, project_root: str) -> dict[str, Any]:
        project_root = self._require_project(project_root)
        chats, _ = await self._load_snapshot()
        chat = self._find_chat(chats, chat_id, project_root=project_root)
        session_id = str(chat["sessionId"])
        if not self._coordinator.has_active_session(session_id) and self._recovery is not None:
            # The Channel client turn is a new control request; recovery must
            # resolve the existing desktop target independently.
            decision = await self._recovery.control(session_id, "", project_root)
            if not decision.allowed:
                return {"status": "notaccepted", "reason": decision.reason, "recoveryStatus": decision.status, "chatId": chat["id"]}
            target = str((decision.run or {}).get("clientTurnId") or client_turn_id)
            return {**await self._dispatch_external_steer(chat, project_root, target, client_turn_id, message), "chatId": chat["id"]}
        result = self._coordinator.submit(str(chat["sessionId"]), client_turn_id, message)
        return {**result, "chatId": chat["id"]}

    async def stop(self, chat_id: str, project_root: str) -> dict[str, Any]:
        project_root = self._require_project(project_root)
        pending = self._pending_submissions.get((chat_id, project_root), set())
        for cancelled in pending:
            cancelled.set()
        chats, _ = await self._load_snapshot()
        chat = self._find_chat(chats, chat_id, project_root=project_root)
        compact_task = self._compact_tasks.get((chat_id, project_root))
        if compact_task is not None and not compact_task.done():
            compact_task.cancel()
            return {"status": "cancelled", "control": "compact", "chatId": chat["id"]}
        session_id = str(chat["sessionId"])
        if pending and not self._coordinator.has_active_session(session_id):
            return {"status": "requested", "pendingOnly": True, "chatId": chat["id"]}
        if not self._coordinator.has_active_session(session_id) and self._recovery is not None:
            decision = await self._recovery.control(session_id, "", project_root)
            if not decision.allowed:
                # A recovery lookup without a client id cannot safely select a
                # target; callers receive an explicit bounded rejection.
                return {"status": "notaccepted", "reason": decision.reason, "recoveryStatus": decision.status, "chatId": chat["id"]}
            target = str((decision.run or {}).get("clientTurnId") or "")
            if not target:
                return {"status": "notaccepted", "reason": "client_turn_missing", "chatId": chat["id"]}
            self._runtime_bridge.contexts[(session_id, target)] = self._control_context(chat, project_root)
            try:
                try:
                    result = await asyncio.wait_for(self._runtime_bridge.cancel(session_id, target), timeout=1.0)
                except asyncio.TimeoutError:
                    return {"status": "notaccepted", "reason": "runtime_cancel_timeout", "chatId": chat["id"]}
                except Exception as exc:
                    return {"status": "notaccepted", "reason": "runtime_cancel_failed", "errorCode": type(exc).__name__, "chatId": chat["id"]}
                if isinstance(result, dict) and result.get("accepted") is False:
                    return {"status": "notaccepted", "reason": str(result.get("reason") or "runtime_rejected"), "chatId": chat["id"]}
                return {"status": "requested", "external": True, "sessionId": session_id, "targetClientTurnId": target, "chatId": chat["id"]}
            finally:
                self._runtime_bridge.contexts.pop((session_id, target), None)
        return {**self._coordinator.stop(session_id), "chatId": chat["id"]}

    async def cancel(self, chat_id: str, project_root: str) -> dict[str, Any]:
        return await self.stop(chat_id, project_root)

    async def shutdown(self) -> None:
        for pending in self._pending_submissions.values():
            for cancelled in pending:
                cancelled.set()
        for task in list(self._compact_tasks.values()):
            if not task.done():
                task.cancel()
        if self._compact_tasks:
            await asyncio.gather(*self._compact_tasks.values(), return_exceptions=True)
        await self._coordinator.shutdown()
        tasks = list(self._event_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._event_tasks.clear()

    async def _ensure_control_available(self, chat: dict[str, Any], project_root: str, operation: str) -> None:
        if self._pending_submissions.get((str(chat.get("id") or ""), project_root)):
            raise SessionControlError("active_run")
        session_id = str(chat.get("sessionId") or "").strip()
        if self._coordinator.has_active_session(session_id):
            raise SessionControlError("active_run")
        if self._unresolved is not None:
            pending = self._unresolved(chat, project_root)
            pending = await pending if inspect.isawaitable(pending) else pending
            if pending:
                raise SessionControlError("active_run")
        if self._recovery is None:
            return
        decision = await self._recovery.check(session_id, f"session-control-{operation}", project_root)
        if not getattr(decision, "allowed", False) or getattr(decision, "status", "") != "absent":
            raise SessionControlError("recovery_unavailable")

    async def _load_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        value = self._store.load()
        value = await value if inspect.isawaitable(value) else value
        if not isinstance(value, dict) or not isinstance(value.get("chats"), list):
            raise ChannelAdapterError("chat_store_invalid")
        revisions = value.get("sourceRevisions")
        if not isinstance(revisions, list):
            raise ChannelAdapterError("chat_store_invalid")
        return [dict(item) for item in value["chats"] if isinstance(item, dict)], list(revisions)

    async def _save_snapshot(self, chats: list[dict[str, Any]], revisions: list[dict[str, Any]]) -> Any:
        try:
            value = self._store.save(chats, revisions)
            return await value if inspect.isawaitable(value) else value
        except ChannelAdapterError:
            raise
        except Exception as exc:
            raise ChannelAdapterError("store_conflict", type(exc).__name__) from exc

    async def _save_continuation_snapshot(self, chats: list[dict[str, Any]], revisions: list[dict[str, Any]]) -> Any:
        try:
            value = self._store.save(chats, revisions)
            return await value if inspect.isawaitable(value) else value
        except ChannelAdapterError:
            raise
        except Exception as exc:
            raise ChannelAdapterError("store_write_failed", type(exc).__name__) from exc

    def _on_coordinator_event(self, event: dict[str, Any]) -> None:
        task = asyncio.create_task(self._handle_runtime_event(event))
        self._event_tasks.add(task)
        task.add_done_callback(self._consume_event_task)

    def _consume_event_task(self, task: asyncio.Task[Any]) -> None:
        self._event_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._emit({"event": "adapter_event_failed", "errorCode": "runtime_event_handler_failed", "error": type(exc).__name__})

    async def _handle_runtime_event(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("sessionId") or "")
        client_turn_id = str(event.get("clientTurnId") or "")
        try:
            if event.get("event") not in {"turn_returned", "turn_failed"}:
                self._emit(event)
                return
            # The coordinator emits before its own finally block clears the
            # active turn. Yield once so that cleanup runs before persistence
            # and delivery; no polling loop can span a later turn.
            await asyncio.sleep(0)
            chats, revisions = await self._load_snapshot()
            matches = [chat for chat in chats if str(chat.get("sessionId") or "") == session_id]
            if len(matches) != 1:
                self._emit({"event": "transcript_update_failed", "errorCode": "session_not_found", "sessionId": session_id})
                return
            chat = matches[0]
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            item_id = str(result.get("turnId") or result.get("turn_id") or "").strip()
            item_id = item_id or (f"agent:{client_turn_id}" if client_turn_id else secrets.token_urlsafe(8))
            existing = next((item for item in chat.get("items") or [] if isinstance(item, dict) and (
                str(item.get("id") or "") == item_id
                or (item.get("type") == "agent" and isinstance(item.get("response"), dict)
                    and str(item["response"].get("turnId") or item["response"].get("turn_id") or "") == item_id)
            )), None)
            if existing is not None:
                self._emit(event)
                return
            if event.get("event") == "turn_failed":
                item = {"id": item_id, "type": "error", "text": str(event.get("errorCode") or "runtime_failed"), "createdAt": _now()}
            else:
                item = {"id": item_id, "type": "agent", "response": event.get("result"), "createdAt": _now()}
            updated = {**chat, "items": [*list(chat.get("items") or []), item]}
            try:
                await self._save_snapshot([updated if item_chat.get("id") == chat.get("id") else item_chat for item_chat in chats], revisions)
            except ChannelAdapterError as exc:
                self._emit({"event": "transcript_update_failed", "errorCode": exc.code, "sessionId": session_id})
                return
            self._emit(event)
        finally:
            if client_turn_id and event.get("event") in {"turn_returned", "turn_failed"}:
                self._runtime_bridge.contexts.pop((session_id, client_turn_id), None)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self._emit_callback(dict(event))
        except Exception:
            return

    @staticmethod
    def _require_project(project_root: str) -> str:
        if project_root is None:
            raise ChannelAdapterError("invalid_project")
        return str(project_root or "").strip()

    @staticmethod
    def _project_type(project_root: str) -> str:
        return "unity" if str(project_root or "").strip() else "general"

    @staticmethod
    def _session_runtime_config(chat: dict[str, Any]) -> dict[str, Any]:
        """Read desktop-persisted non-secret provider/session settings."""
        config: dict[str, Any] = {}
        keys = ("provider", "providerLabel", "model", "contextLimit", "planMode")
        for key in keys:
            if chat.get(key) not in (None, ""):
                config[key] = chat[key]
        settings = chat.get("settings")
        if isinstance(settings, dict):
            for key in keys:
                if settings.get(key) not in (None, ""):
                    config[key] = settings[key]
        for item in reversed(chat.get("items") or []):
            if not isinstance(item, dict):
                continue
            candidates = [item]
            envelope = item.get("queueEnvelope")
            if isinstance(envelope, dict):
                candidates.append(envelope)
            for candidate in candidates:
                for key in keys:
                    if candidate.get(key) not in (None, ""):
                        config.setdefault(key, candidate[key])
        return config

    @staticmethod
    def _runtime_history(items: list[Any]) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "user" and isinstance(item.get("text"), str):
                text = ChannelRuntimeAdapter._user_history_text(item)
                if text:
                    history.append({"role": "user", "text": text})
            elif item_type == "agent":
                response = item.get("response") if isinstance(item.get("response"), dict) else {}
                plan = response.get("plan") if isinstance(response.get("plan"), dict) else {}
                text = str(plan.get("reply") or plan.get("summary") or "").strip()
                if text:
                    history.append({"role": "agent", "text": text})
            elif item_type == "compact":
                text = str(item.get("detail") or item.get("text") or "").strip()
                if text:
                    history.append({"role": "agent", "text": text})
            elif item_type == "subagent":
                task = item.get("task") if isinstance(item.get("task"), dict) else {}
                if str(task.get("mergeDecision") or "") == "adopted":
                    identity = str(task.get("displayName") or task.get("role") or task.get("id") or "")
                    result = task.get("result")
                    summary = str(task.get("summary") or "").strip()
                    if isinstance(result, dict):
                        summary = str(result.get("summaryText") or summary).strip()
                    text = f"[Adopted sub-agent result: {identity}]"
                    if task.get("task"):
                        text += f"\n\nDelegated task:\n{task['task']}"
                    if summary:
                        text += f"\n\nReviewed summary:\n{summary}"
                    if isinstance(result, dict):
                        text += f"\n\nResult payload:\n{result}"
                    history.append({"role": "agent", "text": text[:8000]})
            elif item_type == "error" and isinstance(item.get("text"), str):
                history.append({"role": "agent", "text": item["text"]})
        return history

    @staticmethod
    def _user_history_text(item: dict[str, Any]) -> str:
        text = str(item.get("text") or "").strip()
        attachments = item.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            return text
        rows: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            name = str(attachment.get("name") or "attachment")
            kind = str(attachment.get("type") or "file")
            size = attachment.get("size")
            marker = {
                "data_url": "payload attached",
                "text": "text attached",
                "vault_file": "stored locally, not sent to model",
            }.get(str(attachment.get("payloadKind") or ""),
                  "metadata only, too large" if attachment.get("truncated") else "metadata only")
            rows.append(f"- {name} ({kind}, {size} bytes, {marker})")
        return "\n\n".join(part for part in (text, "Attachments:\n" + "\n".join(rows)) if part)

    @staticmethod
    def _compaction_recovery(items: list[Any]) -> list[dict[str, Any]]:
        recovery: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in {"agent", "compact"}:
                continue
            values = item.get("compactionRecovery")
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                key = repr(sorted(value.items()))
                if key not in seen:
                    seen.add(key)
                    recovery.append(dict(value))
        return recovery

    @classmethod
    def _control_context(cls, chat: dict[str, Any], project_root: str) -> dict[str, Any]:
        items = chat.get("items") or []
        return {"chatId": chat["id"], "projectRoot": project_root,
                "history": cls._runtime_history(items),
                "compactionRecovery": cls._compaction_recovery(items),
                "projectType": cls._project_type(project_root),
                "sessionConfig": cls._session_runtime_config(chat)}

    async def _dispatch_external_steer(
        self, chat: dict[str, Any], project_root: str, target: str, client_turn_id: str, message: str
    ) -> dict[str, Any]:
        session_id = str(chat["sessionId"])
        target = str(target or "").strip()
        if not target:
            return {"status": "notaccepted", "reason": "recovery_target_missing", "sessionId": session_id}
        self._runtime_bridge.contexts[(session_id, target)] = self._control_context(chat, project_root)
        try:
            try:
                result = await asyncio.wait_for(
                    self._runtime_bridge.steer(session_id, target, client_turn_id, message), timeout=1.0
                )
            except asyncio.TimeoutError:
                return {"status": "notaccepted", "reason": "runtime_steer_timeout", "sessionId": session_id, "targetClientTurnId": target}
            except Exception as exc:
                return {"status": "notaccepted", "reason": "runtime_steer_failed", "errorCode": type(exc).__name__, "sessionId": session_id, "targetClientTurnId": target}
            if not (isinstance(result, dict) and result.get("accepted") is True):
                reason = result.get("reason") if isinstance(result, dict) else "runtime_rejected"
                return {"status": "notaccepted", "reason": str(reason or "runtime_rejected"), "sessionId": session_id, "targetClientTurnId": target}
            return {"status": "steer_submitted", "external": True, "sessionId": session_id, "targetClientTurnId": target, "clientTurnId": client_turn_id}
        finally:
            self._runtime_bridge.contexts.pop((session_id, target), None)

    @staticmethod
    def _find(chats: list[dict[str, Any]], session_id: str, *, project_root: str) -> dict[str, Any]:
        matches = [chat for chat in chats if str(chat.get("sessionId") or "") == session_id]
        if len(matches) != 1:
            raise ChannelAdapterError("session_not_found")
        chat = matches[0]
        if str(chat.get("projectPath") or "") != project_root:
            raise ChannelAdapterError("session_scope_mismatch")
        return chat

    @staticmethod
    def _find_chat(chats: list[dict[str, Any]], chat_id: str, *, project_root: str) -> dict[str, Any]:
        matches = [chat for chat in chats if str(chat.get("id") or "") == str(chat_id)]
        if len(matches) != 1 or str(matches[0].get("projectPath") or "") != project_root:
            raise ChannelAdapterError("session_scope_mismatch" if matches else "session_not_found")
        return matches[0]

    @staticmethod
    def _summary(chat: dict[str, Any]) -> dict[str, Any]:
        project_root = str(chat.get("projectPath") or "").strip()
        return {**{key: chat.get(key) for key in ("id", "title", "sessionId", "projectPath")},
                "projectType": str(chat.get("projectType") or ChannelRuntimeAdapter._project_type(project_root))}

    @staticmethod
    def _stable_identity(project_root: str, request_id: str, stable: str) -> tuple[str, str]:
        return f"chat_{stable}", f"sess_{stable}"

    @staticmethod
    def _random_identity() -> tuple[str, str]:
        return f"chat_{secrets.token_urlsafe(12)}", f"sess_{secrets.token_urlsafe(12)}"

    @staticmethod
    def _default_title() -> str:
        return "channel chat"
