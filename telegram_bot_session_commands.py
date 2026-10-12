"""Owner-route session controls and short-lived model picker cards.

The dispatcher authenticates every call. One picker belongs to its originating
chat/project, expires after ten minutes, and contains no provider credentials.
All mutations go through the shared session adapter, never global settings.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from channel_runtime_adapter import ChannelAdapterError
from channel_session_controls import SessionControlError


HELP_TEXT = """直接发文字聊天；任务运行时，新消息会追加给当前任务。

会话与工程
/session [关键词] 查看、搜索和切换会话
/new [标题] 新建对话，保留旧对话
/project 选择工程
/history 查看最近消息

模型与上下文
/model 选择当前会话模型
/compact [保留重点] 压缩当前上下文

任务控制
/status 查看当前会话、上下文用量、权限和 Unity 状态
/steer 内容 向正在运行的任务追加要求
/stop 请求停止当前任务

审批、问答与预览
/permissions 查看和切换此电脑权限（与桌面共享）
/approvals 查看并处理当前会话审批
/questions 查看待回答问题
/answer 问题ID 答案 回答指定问题
/preview 查看已批准的 Unity 预览

/cancel 取消当前选择（不停止任务）
/help 查看全部操作
兼容入口：/start、/sessions [关键词]、/use 会话ID、/projects、/project 编号。
也可用 /session new [标题] 新建、/session use 会话ID 切换。
切换会话不会停止旧任务；需要审批的操作会直接显示确认卡片。"""


@dataclass
class _ModelPicker:
    nonce: str
    chat_id: str
    project: str
    options: tuple[dict[str, str], ...]
    expires: float
    current: str


class TelegramSessionCommands:
    def __init__(self, runtime: Any, store: Any, *, projects: tuple[str, ...],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.runtime, self.store = runtime, store
        self.projects, self.clock = projects, clock
        self._pickers: dict[str, _ModelPicker] = {}

    def cancel(self, route: str) -> None:
        self._pickers.pop(route, None)

    async def models(self, route: str, chat_id: str, project: str) -> dict:
        self.cancel(route)
        try:
            current = await asyncio.wait_for(self.runtime.get_session(chat_id, project), 2)
            options = await asyncio.wait_for(self.runtime.model_options(chat_id, project), 20)
        except (ChannelAdapterError, SessionControlError, asyncio.TimeoutError) as exc:
            return self._failure(route, exc)
        except Exception:
            return self._reply(route, "模型列表暂不可用，请在桌面端检查供应商配置后重试。")
        # Project/session identity may have changed while the catalog was loading.
        if not self._bound(route, chat_id, project):
            return self._reply(route, "会话已切换，请在当前会话重新发送 /model。")
        safe = []
        seen = set()
        for row in options if isinstance(options, list) else []:
            if not isinstance(row, dict):
                continue
            provider, model = str(row.get("provider") or ""), str(row.get("model") or row.get("id") or "")
            if not provider or not model or (provider, model) in seen:
                continue
            safe.append({"provider": provider, "model": model,
                         "label": str(row.get("label") or row.get("name") or model)[:75]})
            seen.add((provider, model))
            if len(safe) == 500:
                break
        if not safe:
            return self._reply(route, "暂无可选模型，请先在桌面端配置供应商并加载模型。")
        current = current or {}
        selected = " · ".join(str(current.get(key) or "") for key in ("provider", "model") if current.get(key))
        picker = _ModelPicker(secrets.token_urlsafe(6), chat_id, project, tuple(safe), self.clock() + 600,
                              selected or "沿用桌面默认模型")
        self._pickers[route] = picker
        return self._page(route, picker, 0)

    async def consume(self, route: str, data: str) -> dict:
        picker = self._pickers.get(route)
        parts = data.split(":")
        if (picker is None or picker.expires <= self.clock() or len(parts) != 4
                or parts[1] != picker.nonce or not self._bound(route, picker.chat_id, picker.project)):
            self.cancel(route)
            return self._reply(route, "模型选择已过期或会话已切换，请重新发送 /model。")
        action, raw = parts[2:]
        if action == "cancel":
            self.cancel(route)
            return self._reply(route, "已取消模型选择，继续当前对话。")
        try:
            index = int(raw)
            if index < 0:
                raise ValueError
            if action == "page" and index * 8 < len(picker.options):
                return self._page(route, picker, index)
            if action != "select" or index >= len(picker.options):
                raise ValueError
        except ValueError:
            return self._reply(route, "模型选项无效，请重新发送 /model。")
        option = picker.options[index]
        # Consume before awaiting storage so repeated clicks cannot apply twice.
        self.cancel(route)
        try:
            selected = await asyncio.wait_for(self.runtime.set_model(
                picker.chat_id, picker.project, option["provider"], option["model"]), 20)
        except (ChannelAdapterError, SessionControlError, asyncio.TimeoutError) as exc:
            return self._failure(route, exc)
        except Exception:
            return self._reply(route, "模型设置尚未确认，请重新发送 /model 核对。")
        return self._reply(route, f"已为此会话选择 {selected['provider']} · {selected['model']}。\n下一条消息开始使用，其他会话和桌面默认配置不变。")

    async def compact(self, route: str, chat_id: str, project: str, instructions: str) -> dict:
        try:
            result = await asyncio.wait_for(self.runtime.compact(chat_id, project, instructions), 90)
        except (ChannelAdapterError, SessionControlError, asyncio.TimeoutError) as exc:
            return self._failure(route, exc)
        except Exception:
            return self._reply(route, "上下文压缩未完成，原会话保留。请稍后重试。")
        status = result.get("status") if isinstance(result, dict) else ""
        if status == "cancelled":
            return self._reply(route, "已取消上下文压缩，原会话保留。")
        if status in {"no_content", "not_needed"}:
            return self._reply(route, "当前上下文无需压缩，可以继续聊天。")
        if status != "applied":
            return self._reply(route, "上下文压缩未完成，原会话保留。请稍后重试。")
        session_id = result.get("sessionId")
        if session_id and not self.store.watch_session(route, project, session_id):
            return self._reply(route, "上下文已压缩，但会话通知绑定未保存。请在桌面端检查后再继续。")
        return self._reply(route, "当前会话上下文已压缩，保留摘要与恢复信息，可以继续聊天。")

    def _bound(self, route: str, chat_id: str, project: str) -> bool:
        binding = self.store.get_binding(route)
        return (bool(binding) and binding.get("session_id") == chat_id
                and binding.get("project_root") == project and (not project or project in self.projects))

    @staticmethod
    def _page(route: str, picker: _ModelPicker, page: int) -> dict:
        buttons = [[{"text": f"{row['provider']} · {row['label']}"[:100],
                     "callback_data": f"model:{picker.nonce}:select:{i}"}]
                   for i, row in enumerate(picker.options[page * 8:(page + 1) * 8], page * 8)]
        navigation = []
        if page:
            navigation.append({"text": "上一页", "callback_data": f"model:{picker.nonce}:page:{page - 1}"})
        if (page + 1) * 8 < len(picker.options):
            navigation.append({"text": "下一页", "callback_data": f"model:{picker.nonce}:page:{page + 1}"})
        if navigation:
            buttons.append(navigation)
        buttons.append([{"text": "取消", "callback_data": f"model:{picker.nonce}:cancel:0"}])
        text = f"当前：{picker.current}\n选择此会话的模型 · 第 {page + 1}/{(len(picker.options) + 7) // 8} 页\n只使用桌面端已配置的供应商。直接发消息可继续聊天。"
        return {"route": route, "text": text,
                "messages": [{"text": text, "reply_markup": {"inline_keyboard": buttons}}]}

    @staticmethod
    def _reply(route: str, text: str) -> dict:
        return {"route": route, "text": text, "reply_markup": {"remove_keyboard": True}}

    @classmethod
    def _failure(cls, route: str, error: Exception) -> dict:
        code = getattr(error, "code", "")
        if code in {"active_run", "recovery_unavailable", "control_busy", "pending_submission"}:
            text = "当前会话仍有任务、待处理操作或状态未确认，请先用 /status、/approvals、/questions 核对；需要停止时发送 /stop。"
        elif code in {"store_conflict", "compact_scope_mismatch"}:
            text = "会话在操作期间发生变化，未覆盖新内容。请重试。"
        elif code in {"no_content", "not_needed", "compact_no_reduction"}:
            text = "当前上下文无需压缩，可以继续聊天。"
        elif code == "attachments_unsupported":
            text = "此会话包含附件，请在桌面端压缩上下文；原会话与附件均保留。"
        elif isinstance(error, asyncio.TimeoutError):
            text = "操作超时，结果尚未确认，请查看 /status 后重试。"
        else:
            text = "操作未完成，请在桌面端检查当前会话与供应商配置后重试。"
        return cls._reply(route, text)
