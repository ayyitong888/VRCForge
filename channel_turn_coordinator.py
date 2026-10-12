"""Small, transport-neutral coordinator for remote channel runtime turns.

This module owns only local asyncio task bookkeeping.  It does not create a
runtime executor, persist credentials, or perform network I/O.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class ChannelRuntimePort(Protocol):
    async def start_turn(self, session_id: str, client_turn_id: str, message: str) -> Any: ...

    async def steer(
        self,
        session_id: str,
        target_client_turn_id: str,
        client_turn_id: str,
        message: str,
    ) -> Any: ...

    async def cancel(self, session_id: str, target_client_turn_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ChannelTurnMetadata:
    """Immutable route snapshot retained for the turn's final event."""

    route: str = ""
    project_root: str = ""
    provider: str = ""
    model: str = ""
    chat_id: str = ""
    session_title: str = ""


@dataclass(slots=True)
class _OwnedTurn:
    session_id: str
    client_turn_id: str
    metadata: ChannelTurnMetadata
    task: asyncio.Task[Any]


class ChannelTurnCoordinator:
    """Dispatch turns without tying channel delivery to runtime completion."""

    def __init__(
        self,
        runtime: ChannelRuntimePort,
        emit: Callable[[dict[str, Any]], Any],
        *,
        max_active_sessions: int = 16,
        max_auxiliary_tasks: int | None = None,
        shutdown_timeout_seconds: float = 1.0,
        operation_timeout_seconds: float = 30.0,
        turn_timeout_seconds: float = 300.0,
    ) -> None:
        if max_active_sessions < 1:
            raise ValueError("max_active_sessions must be positive")
        for name in ("start_turn", "steer", "cancel"):
            if not inspect.iscoroutinefunction(getattr(runtime, name, None)):
                raise TypeError(f"runtime.{name} must be an async function")
        self._runtime = runtime
        self._emit_callback = emit
        self._max_active_sessions = max_active_sessions
        self._max_auxiliary_tasks = max_auxiliary_tasks or max_active_sessions * 4
        if self._max_auxiliary_tasks < 1 or shutdown_timeout_seconds <= 0 or operation_timeout_seconds <= 0 or turn_timeout_seconds <= 0:
            raise ValueError("capacities and timeouts must be positive")
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._operation_timeout_seconds = operation_timeout_seconds
        self._turn_timeout_seconds = turn_timeout_seconds
        self._active: dict[str, _OwnedTurn] = {}
        self._auxiliary: set[asyncio.Task[Any]] = set()
        self._runtime_tasks: set[asyncio.Task[Any]] = set()
        self._cancel_requested: set[str] = set()
        self._closing = False

    @property
    def active_sessions(self) -> int:
        return len(self._active)

    def has_active_session(self, session_id: str) -> bool:
        return session_id in self._active

    def submit(
        self,
        session_id: str,
        client_turn_id: str,
        message: str,
        *,
        metadata: ChannelTurnMetadata | None = None,
    ) -> dict[str, Any]:
        """Start or steer and return before the runtime turn completes."""
        session_id = str(session_id or "").strip()
        client_turn_id = str(client_turn_id or "").strip()
        message = str(message or "")
        metadata = metadata or ChannelTurnMetadata()
        if self._closing:
            return self._rejected(session_id, client_turn_id, "coordinator_closing")
        if not session_id or not client_turn_id or not message.strip():
            return self._rejected(session_id, client_turn_id, "invalid_turn")

        current = self._active.get(session_id)
        if current is not None:
            # A steer is submitted independently; do not wait for the old turn
            # or bind outbound channel delivery to its completion.
            if len(self._auxiliary) >= self._max_auxiliary_tasks:
                return self._rejected(session_id, client_turn_id, "auxiliary_capacity")
            task = asyncio.create_task(
                self._submit_steer(current, client_turn_id, message),
                name=f"channel-steer-{session_id}-{client_turn_id}",
            )
            self._track_auxiliary(task)
            return {
                "status": "steer_submitted",
                "sessionId": session_id,
                "targetClientTurnId": current.client_turn_id,
                "clientTurnId": client_turn_id,
            }
        if len(self._active) >= self._max_active_sessions:
            return self._rejected(session_id, client_turn_id, "max_active_sessions")

        task = asyncio.create_task(
            self._run_turn(session_id, client_turn_id, message, metadata),
            name=f"channel-turn-{session_id}-{client_turn_id}",
        )
        self._active[session_id] = _OwnedTurn(session_id, client_turn_id, metadata, task)
        self._emit({
            "event": "turn_started",
            "sessionId": session_id,
            "clientTurnId": client_turn_id,
            "metadata": self._metadata_payload(metadata),
        })
        return {"status": "started", "sessionId": session_id, "clientTurnId": client_turn_id}

    def stop(self, session_id: str) -> dict[str, Any]:
        """Request cancellation; completion remains owned by the runtime."""
        session_id = str(session_id or "").strip()
        current = self._active.get(session_id)
        if self._closing:
            return self._rejected(session_id, "", "coordinator_closing")
        if current is None:
            result = {"status": "notaccepted", "reason": "no_active_turn", "sessionId": session_id}
            self._emit({"event": "turn_cancel_notaccepted", **result})
            return result
        if session_id in self._cancel_requested:
            return {
                "status": "requested",
                "sessionId": session_id,
                "targetClientTurnId": current.client_turn_id,
                "merged": True,
            }
        self._cancel_requested.add(session_id)
        task = asyncio.create_task(
            self._submit_cancel(current),
            name=f"channel-cancel-{session_id}-{current.client_turn_id}",
        )
        self._track_auxiliary(task)
        result = {
            "status": "requested",
            "sessionId": session_id,
            "targetClientTurnId": current.client_turn_id,
        }
        self._emit({"event": "turn_cancel_requested", **result})
        return result

    async def shutdown(self) -> None:
        """Stop admission, request runtime cancellation, then join local tasks."""
        if self._closing:
            return
        self._closing = True
        owned = list(self._active.values())
        cancellation_tasks: list[asyncio.Task[Any]] = []
        for current in owned:
            task = asyncio.create_task(
                self._submit_cancel(current),
                name=f"channel-shutdown-cancel-{current.session_id}",
            )
            cancellation_tasks.append(task)
            self._track_auxiliary(task)
        if cancellation_tasks:
            # Let the injected port receive the request before local task
            # cancellation.  This does not wait for the runtime turn itself.
            try:
                await asyncio.wait_for(
                    asyncio.gather(*cancellation_tasks, return_exceptions=True),
                    timeout=self._shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._emit({"event": "shutdown_timeout", "errorCode": "runtime_cancel_timeout"})
        tasks = [item.task for item in owned]
        tasks.extend(task for task in self._auxiliary if task not in cancellation_tasks)
        if tasks:
            # Cancellation only terminates coordinator tasks.  The injected
            # runtime owns any underlying Unity/process cancellation semantics.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._runtime_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.gather(*self._runtime_tasks, return_exceptions=True)),
                    timeout=self._shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._emit({"event": "runtime_owner_handoff", "errorCode": "runtime_task_still_running"})
        self._active.clear()
        self._auxiliary.clear()

    async def _run_turn(
        self,
        session_id: str,
        client_turn_id: str,
        message: str,
        metadata: ChannelTurnMetadata,
    ) -> None:
        runtime_task: asyncio.Task[Any] | None = None
        timed_out = False
        try:
            runtime_task = asyncio.create_task(
                self._runtime.start_turn(session_id, client_turn_id, message),
                name=f"channel-runtime-turn-{session_id}-{client_turn_id}",
            )
            self._runtime_tasks.add(runtime_task)
            runtime_task.add_done_callback(
                lambda task: self._runtime_finished(session_id, client_turn_id, task)
            )
            result = await asyncio.wait_for(
                asyncio.shield(runtime_task),
                timeout=self._turn_timeout_seconds,
            )
        except asyncio.CancelledError:
            if runtime_task is not None and not runtime_task.done():
                timed_out = True
            raise
        except asyncio.TimeoutError:
            # Shield keeps the injected runtime task alive; the finite
            # coordinator deadline requests cancellation instead of dropping
            # a possibly long-running Unity operation.
            timed_out = True
            current = self._active.get(session_id)
            if current is not None:
                cancel_task = asyncio.create_task(self._submit_cancel(current))
                self._track_auxiliary(cancel_task)
            self._emit({
                "event": "turn_failed",
                "sessionId": session_id,
                "clientTurnId": client_turn_id,
                "errorCode": "runtime_start_timeout",
                "metadata": self._metadata_payload(metadata),
            })
        except Exception as exc:  # isolate one failed turn from all sessions
            self._emit({
                "event": "turn_failed",
                "sessionId": session_id,
                "clientTurnId": client_turn_id,
                "errorCode": "runtime_start_failed",
                "metadata": self._metadata_payload(metadata),
                "error": type(exc).__name__,
            })
        else:
            self._emit({
                "event": "turn_returned",
                "sessionId": session_id,
                "clientTurnId": client_turn_id,
                "metadata": self._metadata_payload(metadata),
                "result": result,
            })
        finally:
            current = self._active.get(session_id)
            if not timed_out and current is not None and current.client_turn_id == client_turn_id:
                self._active.pop(session_id, None)
            self._cancel_requested.discard(session_id)

    async def _submit_steer(self, current: _OwnedTurn, client_turn_id: str, message: str) -> None:
        try:
            result = await asyncio.wait_for(
                self._maybe_await(self._runtime.steer(
                    current.session_id, current.client_turn_id, client_turn_id, message
                )),
                timeout=self._operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._emit({
                "event": "steer_failed",
                "sessionId": current.session_id,
                "targetClientTurnId": current.client_turn_id,
                "clientTurnId": client_turn_id,
                "errorCode": "runtime_steer_timeout",
            })
            return
        except Exception as exc:
            self._emit({
                "event": "steer_failed",
                "sessionId": current.session_id,
                "targetClientTurnId": current.client_turn_id,
                "clientTurnId": client_turn_id,
                "errorCode": "runtime_steer_failed",
                "error": type(exc).__name__,
            })
            return
        if not (isinstance(result, dict) and result.get("accepted") is True):
            reason = result.get("reason") if isinstance(result, dict) else "runtime_rejected"
            self._emit({
                "event": "steer_notaccepted",
                "sessionId": current.session_id,
                "targetClientTurnId": current.client_turn_id,
                "clientTurnId": client_turn_id,
                "reason": str(reason or "runtime_rejected"),
            })
            return
        self._emit({
            "event": "steer_accepted",
            "sessionId": current.session_id,
            "targetClientTurnId": current.client_turn_id,
            "clientTurnId": client_turn_id,
        })

    async def _submit_cancel(self, current: _OwnedTurn) -> None:
        try:
            await asyncio.wait_for(
                self._maybe_await(self._runtime.cancel(current.session_id, current.client_turn_id)),
                timeout=self._operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._emit({
                "event": "turn_cancel_failed",
                "sessionId": current.session_id,
                "targetClientTurnId": current.client_turn_id,
                "errorCode": "runtime_cancel_timeout",
            })
        except Exception as exc:
            self._emit({
                "event": "turn_cancel_failed",
                "sessionId": current.session_id,
                "targetClientTurnId": current.client_turn_id,
                "errorCode": "runtime_cancel_failed",
                "error": type(exc).__name__,
            })
        finally:
            self._cancel_requested.discard(current.session_id)

    def _track_auxiliary(self, task: asyncio.Task[Any]) -> None:
        self._auxiliary.add(task)
        task.add_done_callback(self._auxiliary.discard)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(self._safe_emit, dict(event))
        except RuntimeError:
            return

    def _safe_emit(self, event: dict[str, Any]) -> None:
        try:
            result = self._emit_callback(event)
            if inspect.isawaitable(result):
                # The callback contract is intentionally non-blocking; an
                # async callback is ignored instead of becoming a hidden task.
                close = getattr(result, "close", None)
                if close:
                    close()
        except Exception:
            return

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if not inspect.isawaitable(value):
            raise TypeError("runtime port method must return an awaitable")
        return await value

    def _runtime_finished(self, session_id: str, client_turn_id: str, task: asyncio.Task[Any]) -> None:
        self._runtime_tasks.discard(task)
        current = self._active.get(session_id)
        if current is not None and current.client_turn_id == client_turn_id:
            self._active.pop(session_id, None)
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            return

    @staticmethod
    def _metadata_payload(metadata: ChannelTurnMetadata) -> dict[str, str]:
        return {
            "route": metadata.route,
            "projectRoot": metadata.project_root,
            "provider": metadata.provider,
            "model": metadata.model,
            "chatId": metadata.chat_id,
            "sessionTitle": metadata.session_title,
        }

    @staticmethod
    def _rejected(session_id: str, client_turn_id: str, reason: str) -> dict[str, Any]:
        return {
            "status": "notaccepted",
            "sessionId": session_id,
            "clientTurnId": client_turn_id,
            "reason": reason,
        }
