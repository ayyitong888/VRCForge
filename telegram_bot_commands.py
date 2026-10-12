"""Authorized private-chat commands; no network or independent agent executor.

The host owns this dispatcher, store and coordinator. Only the configured numeric
owner in their private chat may address it. Projects come from the local user's
explicit allowlist; Telegram text never supplies an arbitrary filesystem path.
"""
from __future__ import annotations

import asyncio
from typing import Any

from channel_turn_coordinator import ChannelTurnMetadata
from channel_runtime_adapter import ChannelAdapterError
from telegram_bot_approvals import TelegramApprovalError
from telegram_bot_monitor import format_monitor_status
from telegram_bot_questions import TelegramQuestionError
from telegram_bot_onboarding import TelegramOnboarding, bound_keyboard, selection_text
from telegram_bot_session_commands import HELP_TEXT, TelegramSessionCommands


class TelegramCommandDispatcher:
    def __init__(self, *, owner_id: int, projects: tuple[str, ...], store: Any,
                 runtime: Any, approvals: Any = None, previews: Any = None,
                 monitor_snapshot: Any = None, status_timeout: float = 1.0, questions: Any = None,
                 permissions: Any = None) -> None:
        if type(owner_id) is not int or owner_id <= 0:
            raise ValueError("A numeric Telegram owner is required")
        self.owner_id = owner_id
        self.projects = tuple(dict.fromkeys(projects))
        self.store = store
        self.runtime = runtime
        self.approvals = approvals
        self.previews = previews
        self.questions = questions
        self.permissions = permissions
        if status_timeout <= 0:
            raise ValueError("status_timeout must be positive")
        self.monitor_snapshot = monitor_snapshot
        self.status_timeout = status_timeout
        self.onboarding = TelegramOnboarding(projects=self.projects, store=store, runtime=runtime)
        self.session_commands = TelegramSessionCommands(runtime, store, projects=self.projects)
        self._stop_revision = 0  # One authenticated private route owns this dispatcher.

    def authorized_message(self, update: dict[str, Any]) -> dict[str, Any] | None:
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        if (type(sender.get("id")) is not int or sender["id"] != self.owner_id
                or sender.get("is_bot") is True or chat.get("type") != "private"
                or type(chat.get("id")) is not int or chat["id"] != self.owner_id):
            return None
        return message

    async def dispatch(self, update: dict[str, Any]) -> dict[str, Any] | None:
        stop_revision = self._stop_revision
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            original = callback.get("message")
            if not isinstance(original, dict):
                return None
            checked = self.authorized_message({"message": {"from": callback.get("from"), "chat": original.get("chat")}})
            if checked is None:
                return None
            route = str(checked["chat"]["id"])
            data = str(callback.get("data") or "")
            if data.startswith("perm:"):
                if self.permissions is None:
                    return self._reply(route, "权限服务暂不可用。")
                result = await self.permissions.consume(route, data)
                for row in result.get("messages", []):
                    if type(original.get("message_id")) is int:
                        row["edit_message_id"] = original["message_id"]
                return result
            if data.startswith("model:"):
                result = await self.session_commands.consume(route, data)
                for row in result.get("messages", []):
                    if row.get("reply_markup", {}).get("inline_keyboard") and type(original.get("message_id")) is int:
                        row["edit_message_id"] = original["message_id"]
                return result
            if data.startswith("setup:"):
                result = await self.onboarding.consume(route, data)
                if not result or not result.get("resume"):
                    if result:
                        for row in result.get("messages", []):
                            if row.get("reply_markup", {}).get("inline_keyboard") and type(original.get("message_id")) is int:
                                row["edit_message_id"] = original["message_id"]
                    return result
                resumed = await self.dispatch({"update_id": update.get("update_id") if result.get("project_selected") else result.get("update_id"),
                    "message": {"from": callback.get("from"), "chat": original.get("chat"),
                                "text": ("已选择工程，请继续刚才的需求：\n" if result.get("project_selected") else "") + result["text"]}})
                if result.get("project_selected") and isinstance(resumed, dict) and (resumed.get("control") or {}).get("status") in {"started", "duplicate"}:
                    self.store.clear_project_request(route)
                if isinstance(resumed, dict):
                    resumed["reply_markup"] = bound_keyboard()
                    resumed["text"] = selection_text(result["project"], result["session"]) + "\n" + resumed["text"]
                return resumed
            if self.approvals is None:
                return None
            try:
                decision = await self.approvals.consume_callback(data,
                    owner_id=self.owner_id, chat_id=self.owner_id)
            except TelegramApprovalError:
                return self._reply(route, "此审批按钮已失效或审批内容有变化，请重新发送 /approvals 核对。")
            if decision.get("status") != "decided":
                return self._reply(route, "审批操作未被接受，请重新发送 /approvals 检查状态。")
            outcome = "已批准，等待执行结果。" if data.endswith(":a") else "已拒绝此操作。"
            original_text = str(original.get("text") or "")[:2800]
            if original_text:
                outcome += "\n\n" + original_text
            return {"route": route, "messages": [{"text": outcome,
                "reply_markup": {"inline_keyboard": []},
                "edit_message_id": original.get("message_id")}]}
        message = self.authorized_message(update)
        if message is None:
            return None
        route = str(message["chat"]["id"])
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._reply(route, "请发送文字。附件支持尚未启用。")
        text = text.strip()
        if len(text) > 16000:
            return self._reply(route, "消息过长，请缩短后重发。")
        parts = text.split(maxsplit=1)
        command, argument = parts[0], parts[1].strip() if len(parts) > 1 else ""
        if command == "/permissions":
            if self.permissions is None:
                return self._reply(route, "权限服务暂不可用，请在桌面端查看。")
            return await self.permissions.show(route)
        binding = self.store.get_binding(route)
        if text == "新对话":
            command, argument, text = "/new", "", "/new"
        elif text == "停止任务":
            command, argument, text = "/stop", "", "/stop"
        elif text == "切换工程":
            request = self.store.project_request(route)
            return self.onboarding.choose_project(route, update.get("update_id"),
                chat_id=binding["session_id"] if binding and not binding.get("project_root") else "",
                text=request.get("requestText", "") if request and request.get("ready") else "")
        elif text == "切换对话":
            return await self.onboarding.choose_session(route, update.get("update_id"))
        if command == "/session":
            verb, _, rest = argument.partition(" ")
            if verb in {"new", "use"}:
                command, argument = "/" + verb, rest.strip()
            else:
                command = "/sessions"
                argument = rest.strip() if verb == "list" else argument
        if command == "/sessions":
            self.session_commands.cancel(route)
            result = await self.onboarding.choose_session(route, update.get("update_id"), argument[:120])
            if binding and binding.get("session_id") and (not binding.get("project_root") or binding["project_root"] in self.projects):
                try:
                    current = await self.runtime.get_session(binding["session_id"], binding["project_root"])
                    if current:
                        result["messages"][0]["text"] = selection_text(binding["project_root"], current) + "\n" + result["messages"][0]["text"]
                except ChannelAdapterError:
                    pass
            return result
        if command == "/cancel":
            if self.permissions is not None:
                self.permissions.cancel(route)
            self.onboarding.cancel(route)
            self.session_commands.cancel(route)
            self.store.clear_project_request(route)
            return self._reply(route, "已取消选择，继续当前对话。", markup=bound_keyboard())
        if command == "/start":
            binding, current = await self._ensure_session(route, update.get("update_id"))
            if not current:
                return self._reply(route, "当前会话不可用，请用 /sessions 选择或 /new 新建。")
            pending = self.store.project_request(route)
            if pending and pending.get("ready") and pending.get("chatId") == current["id"] and not binding["project_root"]:
                return self.onboarding.choose_project(route, update.get("update_id"), chat_id=current["id"], text=pending["requestText"])
            return self._reply(route, selection_text(binding["project_root"], current) + "\n直接发送消息即可；也可以新建或继续电脑上的对话。", markup=bound_keyboard())
        if command == "/help":
            return self._reply(route, HELP_TEXT, markup=bound_keyboard())
        if command == "/projects":
            return self._reply(route, "\n".join(f"{i + 1}. {path}" for i, path in enumerate(self.projects))
                               or "尚未授权工程，请在桌面端配置。")
        if command == "/project":
            self.session_commands.cancel(route)
            if not argument:
                request = self.store.project_request(route)
                return self.onboarding.choose_project(route, update.get("update_id"),
                    chat_id=binding["session_id"] if binding and not binding.get("project_root") else "",
                    text=request.get("requestText", "") if request and request.get("ready") else "")
            self.onboarding.cancel(route)
            self.store.clear_project_request(route)
            try:
                index = int(argument) - 1
                if index < 0 or index >= len(self.projects):
                    raise ValueError
            except ValueError:
                return self._reply(route, "请用 /projects 查看编号，再发送 /project 编号。")
            self.store.set_binding(route, self.projects[index], "")
            return self._reply(route, "已选择工程，请用 /sessions 或 /new 选择会话。")
        binding = self.store.get_binding(route)
        if command in {"/project", "/new", "/use"}:
            self.onboarding.cancel(route)
            self.session_commands.cancel(route)
            self.store.clear_project_request(route)
        if command == "/stop":
            self._stop_revision += 1
            self.session_commands.cancel(route)
            self.store.clear_project_request(route)
        if command == "/stop" and self.onboarding.has_pending(route):
            self.onboarding.cancel(route)
            note = "已取消当前工程/会话选择，原需求未执行。"
            if binding and binding.get("session_id") and (not binding.get("project_root") or binding.get("project_root") in self.projects):
                try:
                    stopped = await self.runtime.stop(binding["session_id"], binding["project_root"])
                    note += "\n" + self._stop_text(stopped)
                except Exception:
                    note += "\n旧会话的停止状态尚未确认，请用 /status 核对。"
            return self._reply(route, note, markup=bound_keyboard())
        if not text.startswith("/"):
            self.session_commands.cancel(route)
            if self.onboarding.is_session_browser(route):
                self.onboarding.cancel(route)
        if self.onboarding.has_pending(route) and not text.startswith("/"):
            return self._reply(route, "请先完成当前工程/会话选择；完成后再发送新的需求。")
        if binding and binding["project_root"] and binding["project_root"] not in self.projects:
            return self._reply(route, "当前工程已不在授权范围，请切换对话或在桌面设置中重新授权。")
        project = binding["project_root"] if binding else ""
        if command == "/new":
            self.onboarding.cancel(route)
            session = await self.runtime.create_session(project, argument[:120] or "Telegram 会话",
                request_id=f"telegram-{route}-{update.get('update_id')}")
            if not session or not session.get("id") or not session.get("sessionId"):
                return self._reply(route, "新会话尚未确认，请在桌面端检查。")
            if not self.store.watch_session(route, project, session["sessionId"]):
                return self._reply(route, "无法保存会话通知绑定，尚未切换到新会话。请在桌面端检查。")
            if self.store.set_binding(route, project, session["id"]) is False:
                return self._reply(route, "会话切换未保存，请稍后重试。")
            return self._reply(route, "已新建会话。\n" + selection_text(project, session)
                               + "\n旧对话保留；旧任务不会自动停止。", markup=bound_keyboard())
        if command == "/use":
            self.onboarding.cancel(route)
            session = None
            for scope in dict.fromkeys((project, "", *self.projects)):
                try:
                    session = await self.runtime.get_session(argument, scope)
                except ChannelAdapterError as error:
                    if error.code not in {"session_not_found", "session_scope_mismatch"}:
                        raise
                if session:
                    project = scope
                    break
            if not session or not session.get("id") or not session.get("sessionId"):
                return self._reply(route, "该工程下找不到这个会话。")
            if not self.store.watch_session(route, project, session["sessionId"]):
                return self._reply(route, "无法保存会话通知绑定，尚未切换会话。请在桌面端检查。")
            if self.store.set_binding(route, project, session["id"]) is False:
                return self._reply(route, "会话切换未保存，请稍后重试。")
            return self._reply(route, "已切换会话。\n" + selection_text(project, session)
                               + "\n切换不会自动停止旧会话任务。", markup=bound_keyboard())
        if not binding or not binding.get("session_id"):
            if text.startswith("/"):
                return self._reply(route, "尚未选择会话，直接发消息即可开始，或用 /sessions 选择已有对话。", markup=bound_keyboard())
            binding, current = await self._ensure_session(route, update.get("update_id"), text[:60])
            if not current:
                return self._reply(route, "无法保存当前会话，消息尚未交给任务，请重试。")
            project = binding["project_root"]
        session_id = binding["session_id"]
        if command == "/model":
            if argument:
                return self._reply(route, "发送 /model 打开模型选择，然后点选模型。", markup=bound_keyboard())
            return await self.session_commands.models(route, session_id, project)
        if command == "/compact":
            return await self.session_commands.compact(route, session_id, project, argument)
        if command == "/history":
            try:
                rows = await asyncio.wait_for(self.runtime.history(session_id, project), 2)
            except ChannelAdapterError as error:
                if error.code in {"session_not_found", "session_scope_mismatch"}:
                    return self._reply(route, "该工程下找不到当前会话，请重新用 /use 选择。")
                return self._reply(route, "会话历史暂不可用，请在桌面端检查。")
            except Exception:
                return self._reply(route, "会话历史暂不可用，请稍后重试。")
            # The runtime port returns redacted conversation text only. Never
            # stringify raw tool payloads, extra fields, or non-text content.
            visible = [row for row in rows if isinstance(row, dict)
                       and row.get("role") in {"user", "assistant"}
                       and isinstance(row.get("content"), str) and row["content"].strip()] if isinstance(rows, list) else []
            if not visible:
                return self._reply(route, "当前会话暂无可显示的文字历史。")
            messages = [{"text": ("你：" if row["role"] == "user" else "助手：") + row["content"][:1000]}
                        for row in visible[-10:]]
            messages.append({"text": "以上仅显示最近 10 条文字消息，每条最多 1000 字；更早历史仍保留在同一会话中，未删除。"})
            return {"route": route, "messages": messages}
        if command == "/preview":
            session = await self.runtime.get_session(session_id, project)
            preview = self.previews.message(project, session["sessionId"]) if self.previews else None
            return {"route": route, "messages": [preview]} if preview else self._reply(route,
                "此会话暂无近期已批准的截图。请让任务拍摄 Unity 预览并确认截图审批后再试。")
        if command in {"/questions", "/answer"}:
            if self.questions is None:
                return self._reply(route, "问答服务暂不可用。")
            session = await self.runtime.get_session(session_id, project)
            try:
                if command == "/questions":
                    rows = await asyncio.wait_for(self.questions.list(project, session["sessionId"]), 2)
                    messages = [{"text": f"{row['header']}\n{row['question']}\n" +
                        "\n".join(option['label'] for option in row.get('options', [])) +
                        f"\n回复：/answer {row['questionId']} 你的答案"} for row in rows]
                    return {"route": route, "messages": messages} if messages else self._reply(route, "当前会话没有待回答问题。")
                question_id, _, answer = argument.partition(" ")
                if not question_id or not answer.strip():
                    return self._reply(route, "请发送 /answer 问题ID 答案；用 /questions 查看待回答问题。")
                result = await asyncio.wait_for(self.questions.answer(question_id, answer, project, session["sessionId"]), 3)
                return self._reply(route, {"queued": "答案已保存，等待任务继续。", "delivered": "答案已交给任务，请查看后续结果。",
                    "interrupted": "答案已保存，但续跑已中断，请查看任务状态。", "answered": "答案已保存。"}.get(result["status"],
                    "答案已保存，续跑状态待核对；请用 /status 查询。"))
            except (TelegramQuestionError, asyncio.TimeoutError):
                return self._reply(route, "问答状态尚未确认，请用 /questions 重新查看；不要重复启动改模任务。")
        if command == "/approvals":
            if self.approvals is None:
                return self._reply(route, "审批服务暂不可用，请在桌面端检查。")
            session = await self.runtime.get_session(session_id, project)
            approvals = await self.approvals.pending_messages(owner_id=self.owner_id,
                chat_id=self.owner_id, project_root=project, session_id=session["sessionId"])
            if not approvals:
                return self._reply(route, "当前会话没有待审批操作。")
            return {"route": route, "messages": [{"text": row["text"],
                "reply_markup": {"inline_keyboard": [row["buttons"]]}} for row in approvals]}
        if command == "/status":
            current = await self.runtime.get_session(session_id, project)
            heading = selection_text(project, current)
            model = " · ".join(str(current.get(key) or "") for key in ("provider", "model") if current.get(key))
            heading += "\n模型：" + (model or "沿用桌面默认配置（/model 选择）")
            if self.permissions is not None:
                heading += "\n" + await self.permissions.status_text()
            pending = self.store.project_request(route)
            if pending and pending.get("ready") and pending.get("chatId") == session_id and not project:
                return self.onboarding.choose_project(route, update.get("update_id"), chat_id=session_id, text=pending["requestText"])
            if self.monitor_snapshot is None:
                return self._reply(route, heading + "\n任务/Unity 状态服务暂不可用。")
            try:
                snapshot = await asyncio.wait_for(self.monitor_snapshot(project, session_id),
                                                  timeout=self.status_timeout)
                details = format_monitor_status(snapshot)
            except asyncio.TimeoutError:
                details = "任务/Unity 状态读取超时，请稍后重试。"
            except Exception:
                details = "任务/Unity 状态暂不可用，请在桌面端检查。"
            return self._reply(route, heading + "\n" + details)
        if command == "/stop":
            result = await self.runtime.stop(session_id, project)
            return self._reply(route, self._stop_text(result), result)
        if text.startswith("/") and command != "/steer":
            return self._reply(route, "未知命令，请用 /help 查看。")
        prompt = argument if command == "/steer" else text
        if not prompt:
            return self._reply(route, "请在 /steer 后填写追加要求。")
        update_id = update.get("update_id")
        if type(update_id) is not int:
            return self._reply(route, "消息标识无效，未执行。")
        # Deterministic IDs let the existing runtime reject delivery replay.
        selected = await self.runtime.get_session(session_id, project)
        if stop_revision != self._stop_revision:
            return self._reply(route, "这条待提交消息已被停止，未启动任务。", markup=bound_keyboard())
        if not selected or not self.store.watch_session(route, project, selected["sessionId"]):
            return self._reply(route, "无法保存任务通知绑定，任务尚未启动。请在桌面端检查会话。")
        result = await self.runtime.submit(session_id, f"telegram-{route}-{update_id}", prompt, project,
                                         metadata=ChannelTurnMetadata(route=route, project_root=project),
                                         steer_only=command == "/steer")
        status = result["status"]
        response = {"started": "任务已接收，等待执行结果。",
                    "steer_submitted": "追加要求已收到，正在交给当前任务。"}.get(status)
        return self._reply(route, response or "当前未接纳这条消息，请稍后查看状态再重试。", result, markup=bound_keyboard())

    async def _ensure_session(self, route: str, update_id: int, title: str = "Telegram 会话") -> tuple:
        binding = self.store.get_binding(route)
        if binding and binding.get("project_root") and binding["project_root"] not in self.projects:
            return binding, None
        project = binding["project_root"] if binding else ""
        if binding and binding.get("session_id"):
            try:
                return binding, await self.runtime.get_session(binding["session_id"], project)
            except ChannelAdapterError as error:
                if error.code not in {"session_not_found", "session_scope_mismatch"}:
                    raise
                return binding, None
        if type(update_id) is not int:
            return binding, None
        session = await self.runtime.create_session(project, title, request_id=f"telegram-default-{route}-{update_id}")
        if not session or not session.get("sessionId") or not session.get("id"):
            return binding, None
        if not self.store.watch_session(route, project, session["sessionId"]) or self.store.set_binding(route, project, session["id"]) is False:
            return binding, None
        return self.store.get_binding(route), session

    @staticmethod
    def _stop_text(result: dict[str, Any]) -> str:
        if result.get("control") == "compact" and result.get("status") == "cancelled":
            return "已取消上下文压缩，原会话保留。"
        return ("停止已请求；当前操作可能需要安全结束。" if result["status"] == "requested"
                else "无法确认可停止的任务，请用 /status 核对当前状态。" if result["status"] == "notaccepted"
                else "当前没有可停止的任务。")

    @staticmethod
    def _reply(route: str, text: str, result: dict[str, Any] | None = None, *, markup: dict[str, Any] | None = None) -> dict[str, Any]:
        value = {"route": route, "text": text, "control": result}
        if markup is not None:
            value["reply_markup"] = markup
        return value
