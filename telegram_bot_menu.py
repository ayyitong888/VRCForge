"""Owner-private command menu; one service-owned bounded registration task."""
from __future__ import annotations

import asyncio
import time


MENU_COMMANDS = tuple({"command": command, "description": description} for command, description in (
    ("status", "查看当前状态"), ("session", "新建或切换会话"), ("new", "新建会话"),
    ("project", "选择工程"), ("model", "选择当前会话模型"), ("compact", "整理会话上下文"),
    ("history", "查看最近历史"), ("stop", "停止当前任务"), ("steer", "追加任务要求"),
    ("permissions", "切换此电脑权限"), ("approvals", "查看待审批操作"), ("questions", "查看待回答问题"), ("answer", "回答任务问题"),
    ("preview", "查看已批准的预览"), ("cancel", "取消当前选择"), ("help", "查看全部操作"),
))


class TelegramBotMenu:
    def __init__(self, transport, owner_id: int, *, timeout: float = 5.0, clock=time.monotonic):
        if type(owner_id) is not int or owner_id <= 0 or not 0 < timeout <= 5:
            raise ValueError("invalid menu scope or timeout")
        self.transport, self.owner_id = transport, owner_id
        self._timeout, self._clock = timeout, clock
        self._task = None
        self._closed = self._synced = self._seen_owner = self._owner_retry = False
        self._last_attempt = float("-inf")
        self.error_code = None

    def observe_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        message = callback.get("message") if isinstance(callback, dict) else update.get("message")
        sender = callback.get("from") if isinstance(callback, dict) else message.get("from") if isinstance(message, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        if (not isinstance(sender, dict) or not isinstance(chat, dict)
                or type(sender.get("id")) is not int or sender["id"] != self.owner_id
                or sender.get("is_bot") is True
                or type(chat.get("id")) is not int or chat["id"] != self.owner_id
                or chat.get("type") != "private"):
            return
        self.request_sync(authenticated=True)

    def request_sync(self, *, authenticated: bool = False) -> None:
        if self._closed or self._synced:
            return
        first_owner = authenticated and not self._seen_owner
        if authenticated:
            self._seen_owner = True
        if self._task is not None and not self._task.done():
            self._owner_retry |= first_owner
            return
        if not first_owner and self._clock() - self._last_attempt < 30:
            return
        self._task = asyncio.create_task(self._run())

    async def _register(self) -> None:
        await self.transport.set_my_commands(list(MENU_COMMANDS), owner_id=self.owner_id)
        await self.transport.set_chat_menu_button(self.owner_id)

    async def _run(self) -> None:
        # A first owner message during the startup attempt gets one immediate
        # retry after failure. Both attempts share this single owned task.
        while not self._closed:
            self._last_attempt = self._clock()
            try:
                await asyncio.wait_for(self._register(), self._timeout)
                self._synced, self.error_code = True, None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error_code = "menu_registration_failed"
            if self._synced or not self._owner_retry:
                return
            self._owner_retry = False

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
