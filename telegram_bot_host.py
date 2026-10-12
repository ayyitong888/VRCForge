"""Bounded Telegram receiver/dispatcher/sender coordination.

This module owns no routing or provider policy.  A backend service owns the host
and supplies its transport, durable store, and async dispatcher callback.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from telegram_bot_store import PendingUpdate, TelegramBotStore
from telegram_bot_transport import TelegramBotError, TelegramBotTransport, split_telegram_text


Dispatch = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any] | None]]


class TelegramBotHost:
    def __init__(
        self,
        transport: TelegramBotTransport,
        store: TelegramBotStore,
        dispatch: Dispatch,
        owner_user_id: int,
        *,
        max_inflight: int = 8,
        poll_timeout: int = 25,
    ) -> None:
        if not isinstance(owner_user_id, int) or isinstance(owner_user_id, bool):
            raise ValueError("owner_user_id must be numeric")
        if max_inflight <= 0:
            raise ValueError("max_inflight must be positive")
        if not 0 <= poll_timeout <= 25:
            raise ValueError("poll_timeout must be between 0 and 25")
        self.transport = transport
        self.store = store
        self.dispatch = dispatch
        self.owner_user_id = owner_user_id
        self.max_inflight = max_inflight
        self.poll_timeout = poll_timeout
        self._queue: asyncio.PriorityQueue[tuple[int, int, PendingUpdate]] = asyncio.PriorityQueue(
            maxsize=max_inflight * 2
        )
        self._control_queue: asyncio.Queue[PendingUpdate] = asyncio.Queue(maxsize=max_inflight)
        self._approval_queue: asyncio.Queue[PendingUpdate] = asyncio.Queue(maxsize=max_inflight)
        self._queued_ids: set[int] = set()
        self._active_ids: set[int] = set()
        self._route_locks: dict[str, asyncio.Lock] = {}
        self._sequence = 0
        self._stop_event = asyncio.Event()
        self._started = False
        self._closed = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._callback_acks: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self._accepted = 0
        self._dispatched = 0
        self._channel_error: str | None = None
        self._retry_counts: dict[str, int] = {}
        self._poll_confirmed = False
        self._last_receive: float | None = None

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("telegram host is closed")
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        for update_id, result in self.store.pending_dispatches(limit=self.max_inflight * 4):
            await self._enqueue_journal_result(update_id, result)
        self._spawn(self._receiver_loop())
        self._spawn(self._pending_pump())
        for _ in range(self.max_inflight):
            self._spawn(self._dispatcher_worker())
        self._spawn(self._control_worker())
        self._spawn(self._approval_worker())
        self._spawn(self._sender_loop())
        self._spawn(self._callback_ack_worker())

    def _spawn(self, coroutine: Awaitable[Any]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _receiver_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                updates = await self.transport.get_updates(
                    offset=self.store.get_offset(),
                    timeout=self.poll_timeout,
                    allowed_updates=["message", "callback_query"],
                )
            except TelegramBotError as error:
                if error.code == "unauthorized":
                    self._channel_error = "unauthorized"
                    self._stop_event.set()
                    self._spawn(self.stop("unauthorized"))
                    return
                if error.code == "closed":
                    return
                self._poll_confirmed = False
                self._channel_error = error.code if error.code in {"network_error", "protocol_error", "rate_limited"} else "receiver_error"
                await self._pause()
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self._channel_error = "receiver_error"
                await self._pause()
                continue
            if not isinstance(updates, list):
                self._channel_error = "protocol_error"
                await self._pause()
                continue
            self._poll_confirmed = True
            self._last_receive = time.time()
            if self._channel_error in {"network_error", "protocol_error", "rate_limited", "receiver_error"}:
                self._channel_error = None
            for update in updates:
                try:
                    self._admit_update(update)
                except Exception:
                    self._channel_error = "store_error"
                    self._stop_event.set()
                    self._spawn(self.stop("store_error"))
                    return

    async def _pending_pump(self) -> None:
        while not self._stop_event.is_set():
            try:
                for pending in self.store.next_pending(limit=self.max_inflight * 4):
                    self._queue_pending(pending)
                for update_id, result in self.store.pending_dispatches(limit=self.max_inflight * 4):
                    await self._enqueue_journal_result(update_id, result)
            except Exception:
                self._channel_error = "store_error"
                self._stop_event.set()
                self._spawn(self.stop("store_error"))
                return
            await self._pause()

    async def _pause(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=0.25)
        except asyncio.TimeoutError:
            pass

    def _admit_update(self, update: Any) -> None:
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            return
        if not self._is_owner_private(update):
            self.store.skip_update(update["update_id"])
            return
        result = self.store.admit_update(update["update_id"], update)
        if result.admitted:
            self._accepted += 1
            callback = update.get("callback_query")
            if self._callback_message_id(update) is not None and isinstance(callback.get("id"), str) and callback["id"]:
                try:
                    self._callback_acks.put_nowait(callback["id"])
                except asyncio.QueueFull:
                    pass
            pending = PendingUpdate(update["update_id"], update)
            self._queue_pending(pending)

    def _queue_pending(self, pending: PendingUpdate) -> bool:
        if pending.update_id in self._queued_ids or pending.update_id in self._active_ids:
            return True
        callback = pending.payload.get("callback_query")
        if isinstance(callback, Mapping) and not str(callback.get("data") or "").startswith(("setup:", "model:")):
            try:
                self._approval_queue.put_nowait(pending)
            except asyncio.QueueFull:
                return False
            self._queued_ids.add(pending.update_id)
            return True
        if _is_priority_control(pending.payload):
            try:
                self._control_queue.put_nowait(pending)
            except asyncio.QueueFull:
                return False
            self._queued_ids.add(pending.update_id)
            return True
        priority = 1
        try:
            self._queue.put_nowait((priority, self._sequence, pending))
        except asyncio.QueueFull:
            return False
        self._queued_ids.add(pending.update_id)
        self._sequence += 1
        return True

    async def _dispatcher_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                _priority, _sequence, pending = await asyncio.wait_for(self._queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            self._queued_ids.discard(pending.update_id)
            await self._run_pending(pending)
            self._queue.task_done()

    async def _control_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                pending = await asyncio.wait_for(self._control_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            self._queued_ids.discard(pending.update_id)
            await self._run_control_pending(pending)
            self._control_queue.task_done()

    async def _approval_worker(self) -> None:
        while not self._stop_event.is_set():
            pending = await self._approval_queue.get()
            self._queued_ids.discard(pending.update_id)
            try:
                await self._run_control_pending(pending)
            finally:
                self._approval_queue.task_done()

    async def _run_pending(self, pending: PendingUpdate) -> None:
        self._active_ids.add(pending.update_id)
        route_key = str(_chat_id(pending.payload) or "unknown")
        try:
            lock = self._route_locks.setdefault(route_key, asyncio.Lock())
            async with lock:
                await self._run_dispatch_body(pending, control=False)
        finally:
            self._active_ids.discard(pending.update_id)
            if len(self._route_locks) > self.max_inflight * 4:
                self._route_locks = {key: value for key, value in self._route_locks.items() if value.locked()}

    async def _run_control_pending(self, pending: PendingUpdate) -> None:
        self._active_ids.add(pending.update_id)
        try:
            await self._run_dispatch_body(pending, control=True)
        finally:
            self._active_ids.discard(pending.update_id)

    async def _run_dispatch_body(self, pending: PendingUpdate, *, control: bool) -> None:
        if not self._is_owner_private(pending.payload):
            return
        try:
            result = await self.dispatch(pending.payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._channel_error = "dispatch_error"
            self.store.mark_dispatched(pending.update_id)
            return
        try:
            if isinstance(result, dict):
                enriched = dict(result)
                messages = enriched.get("messages", [enriched])
                original_id = self._callback_message_id(pending.payload)
                normalized = []
                for index, message in enumerate(messages):
                    if not isinstance(message, Mapping):
                        continue
                    safe = dict(message)
                    requested_id = safe.pop("edit_message_id", None)
                    markup = safe.get("reply_markup")
                    if (index == 0 and original_id is not None and type(requested_id) is int
                            and requested_id == original_id and isinstance(markup, Mapping)
                            and isinstance(markup.get("inline_keyboard"), list)):
                        safe["edit_message_id"] = original_id
                    if not safe.get("reply_markup") and not safe.get("photo"):
                        safe["reply_markup"] = {"remove_keyboard": True}
                    normalized.append(safe)
                enriched["messages"] = normalized
                enriched.pop("edit_message_id", None)
                chat_id = _chat_id(pending.payload)
                if isinstance(chat_id, int) and not isinstance(chat_id, bool):
                    enriched["chat_id"] = chat_id
                self.store.record_dispatch(pending.update_id, enriched)
                await self._enqueue_journal_result(pending.update_id, enriched)
            else:
                self.store.record_dispatch(pending.update_id, None)
            self._dispatched += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            # Business dispatch is journaled before notification storage. A
            # failed notification stays repairable without re-running dispatch.
            self._channel_error = "notification_store_error"

    async def _enqueue_journal_result(self, update_id: int, result: Any) -> None:
        if not isinstance(result, Mapping):
            return
        # Journal results may carry their target directly; normal dispatch
        # results are augmented from the original update before journaling.
        chat_id = result.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        messages = result.get("messages", [result])
        for message_index, message in enumerate(messages):
            text = message.get("text")
            if not isinstance(text, str) or not text:
                continue
            if message.get("photo"):
                self.store.enqueue_outbox(f"{update_id}:{message_index}:photo", {
                    "chat_id": chat_id, "text": text, "photo": message["photo"], "sha256": message["sha256"]})
                continue
            chunks = split_telegram_text(text)
            for index, chunk in enumerate(chunks):
                payload = {"chat_id": chat_id, "text": chunk, "route": result.get("route")}
                if len(chunks) == 1 and type(message.get("edit_message_id")) is int:
                    payload["edit_message_id"] = message["edit_message_id"]
                if index == len(chunks) - 1 and message.get("reply_markup"):
                    payload["reply_markup"] = message["reply_markup"]
                self.store.enqueue_outbox(f"{update_id}:{message_index}:{index}", payload)

    async def _enqueue_result(self, pending: PendingUpdate, result: Mapping[str, Any]) -> None:
        chat_id = _chat_id(pending.payload)
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        enriched = dict(result)
        enriched["chat_id"] = chat_id
        await self._enqueue_journal_result(pending.update_id, enriched)

    async def _sender_loop(self) -> None:
        while not self._stop_event.is_set():
            items = self.store.claim_outbox(limit=8)
            if not items:
                await self._pause()
                continue
            for item in items:
                try:
                    options = {"reply_markup": item.payload["reply_markup"]} if item.payload.get("reply_markup") else {}
                    if item.payload.get("photo"):
                        data = base64.b64decode(item.payload["photo"], validate=True)
                        if hashlib.sha256(data).hexdigest() != item.payload.get("sha256"):
                            raise ValueError("preview_digest_mismatch")
                        await self.transport.send_photo(item.payload["chat_id"], data, item.payload["text"])
                    elif type(item.payload.get("edit_message_id")) is int:
                        await self.transport.edit_message_text(item.payload["chat_id"], item.payload["edit_message_id"], item.payload["text"], **options)
                    else:
                        await self.transport.send_message(item.payload["chat_id"], item.payload["text"], **options)
                except asyncio.CancelledError:
                    raise
                except TelegramBotError as error:
                    if error.code == "unauthorized":
                        self._channel_error = "unauthorized"
                        self.store.mark_outbox_unknown(item.key)
                        self._stop_event.set()
                        self._spawn(self.stop("unauthorized"))
                        return
                    if error.code == "rate_limited" and error.retry_after is not None:
                        attempts = self._retry_counts.get(item.key, 0)
                        if attempts < 2:
                            self._retry_counts[item.key] = attempts + 1
                            self.store.mark_outbox_failed(item.key, retryable=True)
                            await asyncio.sleep(min(error.retry_after, 60))
                            continue
                    if error.code == "network_error":
                        self.store.mark_outbox_unknown(item.key)
                    else:
                        self.store.mark_outbox_failed(item.key, retryable=False)
                    continue
                except Exception:
                    self.store.mark_outbox_unknown(item.key)
                    continue
                self.store.mark_outbox_sent(item.key)

    async def _callback_ack_worker(self) -> None:
        # One host-owned worker and eight queued IDs: ACK I/O never occupies
        # the dispatch/control lanes and cannot create unbounded tasks.
        while not self._stop_event.is_set():
            callback_id = await self._callback_acks.get()
            try:
                await asyncio.wait_for(self.transport.answer_callback_query(callback_id), timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                self._callback_acks.task_done()

    def _callback_message_id(self, update: Mapping[str, Any]) -> int | None:
        callback = update.get("callback_query")
        original = callback.get("message") if isinstance(callback, Mapping) else None
        if not isinstance(original, Mapping) or not self._is_owner_private(update):
            return None
        message_id = original.get("message_id")
        return message_id if (_chat_id(update) == self.owner_user_id
                              and type(message_id) is int and message_id > 0) else None

    async def stop(self, reason: str = "stopped") -> None:
        if self._closed:
            return
        self._channel_error = self._channel_error or reason
        self._stop_event.set()
        current = asyncio.current_task()
        tasks = [task for task in tuple(self._tasks) if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)
        await self.transport.close()
        # The injected store belongs to the backend service; its owner closes it.
        self._closed = True
        self._started = False

    async def close(self) -> None:
        await self.stop("closed")

    def status(self) -> dict[str, Any]:
        return {
            "running": self._started and not self._closed and not self._stop_event.is_set() and self._poll_confirmed,
            "state": "running" if self._poll_confirmed else "starting",
            "pollConfirmed": self._poll_confirmed,
            "lastReceive": self._last_receive,
            "channelError": self._channel_error,
            "accepted": self._accepted,
            "dispatched": self._dispatched,
            "pendingQueue": self._queue.qsize(),
        }

    def _is_owner_private(self, update: Mapping[str, Any]) -> bool:
        actor, chat = _actor_and_chat(update)
        chat_id = chat.get("id") if chat is not None else None
        return (
            actor == self.owner_user_id
            and chat is not None
            and chat.get("type") == "private"
            and isinstance(chat_id, int)
            and not isinstance(chat_id, bool)
        )

    def emit(self, event: Mapping[str, Any]) -> bool:
        """Project a safe runtime event into durable outbox state; never sends network traffic."""
        if not isinstance(event, Mapping):
            return False
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("route") != str(self.owner_user_id):
            return False
        kind = event.get("event")
        if kind in {"telegram_task_status_changed", "telegram_unity_status_changed"}:
            result = event.get("result") or {}
            text, version = result.get("reply"), event.get("version")
            if not isinstance(text, str) or not isinstance(version, str):
                return False
            self.store.enqueue_outbox(f"monitor:{event.get('sessionId')}:{kind}:{version}",
                {"chat_id": self.owner_user_id, "text": text})
            return True
        if kind == "waiting_approval":
            return False
        if kind == "turn_returned":
            result = event.get("result")
            text = None
            if isinstance(result, Mapping):
                plan = result.get("plan")
                # Native turns carry user-facing text in plan; simple ports
                # also return top-level reply/message.
                candidates = ([plan.get("reply"), plan.get("summary")] if isinstance(plan, Mapping) else [])
                candidates.extend((result.get("reply"), result.get("message")))
                text = next((value for value in candidates if isinstance(value, str) and value.strip()), None)
        elif kind == "steer_accepted":
            text = "Steer accepted."
        elif kind == "turn_failed":
            text = "Turn failed (turn_failed)."
        else:
            return False
        if not isinstance(text, str) or not text:
            return False
        route = metadata.get("route")
        # The binding can change while this turn runs. Keep its original label
        # on results so an old task cannot appear to answer in the new session.
        binding = self.store.get_binding(str(route))
        source_chat = metadata.get("chatId")
        if source_chat and binding and binding.get("session_id") != source_chat:
            title = str(metadata.get("sessionTitle") or source_chat)[:120]
            text = f"来自其他对话：{title}\n{text}"
        payload = {"chat_id": self.owner_user_id, "text": text, "route": route, "control": kind}
        session_id = event.get("sessionId")
        client_turn_id = event.get("clientTurnId")
        if not isinstance(session_id, str) or not session_id or not isinstance(client_turn_id, str) or not client_turn_id:
            return False
        event_id = ":".join((session_id, client_turn_id, str(kind)))
        result = event.get("result") if isinstance(event.get("result"), Mapping) else {}
        turn_id = event.get("turnId") or result.get("turnId") or result.get("turn_id")
        if turn_id:
            event_id += ":" + str(turn_id)
        for index, chunk in enumerate(split_telegram_text(text)):
            self.store.enqueue_outbox(f"{event_id}:{index}", {**payload, "text": chunk})
        return True


def _actor_and_chat(update: Mapping[str, Any]) -> tuple[int | None, Mapping[str, Any] | None]:
    message = update.get("message") or update.get("callback_query")
    if not isinstance(message, Mapping):
        return None, None
    actor = message.get("from")
    actor_id = actor.get("id") if isinstance(actor, Mapping) else None
    if "chat" in message:
        chat = message.get("chat")
    else:
        nested = message.get("message")
        chat = nested.get("chat") if isinstance(nested, Mapping) else None
    return actor_id if isinstance(actor_id, int) else None, chat if isinstance(chat, Mapping) else None


def _chat_id(update: Mapping[str, Any]) -> int | str | None:
    _actor, chat = _actor_and_chat(update)
    value = chat.get("id") if chat else None
    return value if (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, str) else None


def _is_priority_control(update: Any) -> bool:
    if not isinstance(update, Mapping):
        return False
    message = update.get("message")
    text = message.get("text") if isinstance(message, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        return False
    return text.strip() == "停止任务" or text.split(maxsplit=1)[0] in {"/stop", "/steer"}
