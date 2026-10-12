"""Owner-only presentation of the existing computer-wide permission setting.

The dispatcher authenticates the private owner before calling this controller.
One short-lived choice belongs to that route; this service creates no executor,
listener or policy override. Updates go through the desktop permission owner.
"""
from __future__ import annotations

import asyncio
import secrets
import time


PERMISSION_LABELS = {"approval": "逐项审批", "auto": "自动审批",
                     "roslyn_full_auto": "Full Access（完全权限）"}


class TelegramPermissionController:
    def __init__(self, port, *, clock=time.monotonic):
        self.port, self.clock = port, clock
        self._choice = None

    def cancel(self, route: str) -> None:
        if self._choice and self._choice[0] == route:
            self._choice = None

    async def status_text(self) -> str:
        try:
            state = await asyncio.wait_for(self.port.read(), 3)
            label = PERMISSION_LABELS[state["executionMode"]]
            return f"权限：{label}（此电脑全局）"
        except Exception:
            return "权限：暂时无法读取，请稍后用 /permissions 查看。"

    async def show(self, route: str) -> dict:
        self.cancel(route)
        try:
            state = await asyncio.wait_for(self.port.read(), 3)
            current = state["executionMode"]
            label = PERMISSION_LABELS[current]
        except Exception:
            return self._reply(route, "权限暂不可用，请稍后重试。")
        nonce = secrets.token_urlsafe(9)
        self._choice = (route, nonce, current, self.clock() + 600)
        text = (f"当前权限：{label}\n适用于此电脑所有会话，与桌面共享。点选后立即生效。\n\n"
                "逐项审批：写操作由你确认。\n"
                "自动审批：普通操作自动通过，需要确认的操作仍发卡片。\n"
                "Full Access：按完全权限执行，普通写操作不再打断；恢复备份仍需确认。")
        rows = [[{"text": ("✓ " if mode == current else "") + name,
                  "callback_data": f"perm:{nonce}:{mode}"}]
                for mode, name in PERMISSION_LABELS.items()]
        rows.append([{"text": "取消", "callback_data": f"perm:{nonce}:cancel"}])
        return {"route": route, "messages": [{"text": text,
                "reply_markup": {"inline_keyboard": rows}}]}

    async def consume(self, route: str, data: str) -> dict:
        parts = data.split(":")
        choice = self._choice
        if (len(parts) != 3 or not choice or choice[0] != route
                or parts[1] != choice[1] or choice[3] <= self.clock()):
            return self._reply(route, "权限选择已失效，请重新发送 /permissions。")
        self._choice = None  # One-time choice before any I/O.
        mode = parts[2]
        if mode == "cancel":
            return self._card_result(route, "已取消，权限未更改。")
        if mode not in PERMISSION_LABELS:
            return self._reply(route, "权限选项无效，请重新发送 /permissions。")
        try:
            current = await asyncio.wait_for(self.port.read(), 3)
            if current.get("executionMode") != choice[2]:
                return self._reply(route, "权限已变化，请重新发送 /permissions 核对后选择。")
            result = await asyncio.wait_for(self.port.update(mode), 10)
            if result.get("ok") is not True or result.get("permission", {}).get("executionMode") != mode:
                raise ValueError("permission_update_unconfirmed")
        except Exception:
            return self._reply(route, "权限变更尚未确认，请用 /permissions 查看当前状态。")
        return self._card_result(route, f"权限已设为：{PERMISSION_LABELS[mode]}\n此电脑所有会话与桌面同步生效。")

    @staticmethod
    def _reply(route, text):
        return {"route": route, "text": text}

    @staticmethod
    def _card_result(route, text):
        return {"route": route, "messages": [{"text": text, "reply_markup": {"inline_keyboard": []}}]}
