"""Bounded, in-memory Telegram project/session selection for first-use text."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any, Callable

from channel_runtime_adapter import ChannelAdapterError


def bound_keyboard() -> dict[str, Any]:
    # Telegram keeps a reply keyboard until it receives this explicit removal.
    # Navigation now lives in the native command menu and temporary inline cards.
    return {"remove_keyboard": True}


def selection_text(project: str, session: dict[str, Any]) -> str:
    title = str(session.get("title") or session["id"])[:120]
    return f"当前：{PureWindowsPath(project).name if project else '普通对话'} · {title}"


@dataclass
class _Pending:
    text: str
    update_id: int | None
    nonce: str
    expires: float
    stage: str
    project: str = ""
    sessions: tuple[dict[str, Any], ...] = ()
    mode: str = "legacy"
    chat_id: str = ""


class TelegramOnboarding:
    def __init__(self, *, projects: tuple[str, ...], store: Any, runtime: Any,
                 ttl_seconds: float = 600.0, page_size: int = 5,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if ttl_seconds <= 0 or not 1 <= page_size <= 5:
            raise ValueError("onboarding bounds must be positive")
        self.projects = tuple(dict.fromkeys(projects))
        self.store, self.runtime = store, runtime
        self.ttl_seconds, self.page_size, self._clock = ttl_seconds, page_size, clock
        self._pending: dict[str, _Pending] = {}

    def cancel(self, route: str) -> None:
        self._pending.pop(str(route), None)

    def has_pending(self, route: str) -> bool:
        self._expire()
        return str(route) in self._pending

    def is_session_browser(self, route: str) -> bool:
        self._expire()
        pending = self._pending.get(str(route))
        return pending is not None and pending.mode == "all_sessions"

    def choose_project(self, route: str, update_id: int, *, chat_id: str = "", text: str = "") -> dict:
        """Present an explicit allowlist choice; never infer paths from text."""
        self.cancel(route)
        if not self.projects:
            return self._reply(route, "还没有授权工程。请在桌面设置中添加要操作的工程，普通聊天仍可继续。")
        pending = _Pending(text, update_id, secrets.token_urlsafe(6), self._clock() + self.ttl_seconds,
                           "project", mode="attach" if chat_id else "new_project", chat_id=chat_id)
        self._pending[route] = pending
        return self._project_page(route, pending, 0)

    async def choose_session(self, route: str, update_id: int, query: str = "") -> dict:
        self.cancel(route)
        pending = _Pending("", update_id, secrets.token_urlsafe(6), self._clock() + self.ttl_seconds,
                           "other", mode="all_sessions")
        self._pending[route] = pending
        sessions = []
        for project in ("", *self.projects):
            rows = await self.runtime.list_sessions(project)
            if not self._current(route, pending):
                return self._reply(route, "会话选择已取消。")
            sessions.extend({"id": row["id"], "title": row.get("title"), "updatedAt": row.get("updatedAt", ""),
                             "projectPath": project} for row in rows if isinstance(row, dict) and row.get("id")
                            and (not query or query.casefold() in str(row.get("title") or "").casefold()))
        sessions.reverse()
        sessions.sort(key=lambda row: str(row.get("updatedAt") or ""), reverse=True)
        pending.sessions = tuple(sessions)
        return self._session_page(route, pending, 0)

    def begin(self, route: str, text: str, update_id: int | None) -> dict[str, Any]:
        self._expire()
        route = str(route)
        if route in self._pending:
            return self._reply(route, "请先完成当前工程/会话选择；完成后再发送新的需求。")
        if type(update_id) is not int:
            return self._reply(route, "消息标识无效，未执行。")
        if not self.projects:
            return self._reply(route, "尚未授权工程，请在桌面端设置中添加工程。")
        pending = _Pending(str(text), update_id if type(update_id) is int else None,
                           secrets.token_urlsafe(6), self._clock() + self.ttl_seconds, "project")
        self._pending[route] = pending
        return self._project_prompt(route, pending)

    async def begin_for_project(self, route: str, text: str, update_id: int | None, project: str) -> dict[str, Any]:
        self._expire()
        if project not in self.projects or type(update_id) is not int:
            return self._reply(str(route), "工程或消息标识无效，未执行。")
        if str(route) in self._pending:
            return self._reply(str(route), "请先完成当前工程/会话选择；完成后再发送新的需求。")
        pending = _Pending(str(text), update_id if type(update_id) is int else None,
                           secrets.token_urlsafe(6), self._clock() + self.ttl_seconds, "session", str(project))
        self._pending[str(route)] = pending
        result = await self._session_prompt(str(route), pending)
        return result if self._current(str(route), pending) else self._reply(str(route), "当前引导已取消或过期，请重新发送需求。")

    def _current(self, route: str, pending: _Pending) -> bool:
        self._expire()
        return self._pending.get(route) is pending

    async def consume(self, route: str, data: str) -> dict[str, Any] | None:
        """Consume one valid button and return either a prompt or resume payload."""
        self._expire()
        route = str(route)
        pending = self._pending.get(route)
        parts = str(data or "").split(":")
        if pending is None or len(parts) != 3 or parts[0] != "setup" or parts[1] != pending.nonce:
            return self._reply(route, "这个引导按钮已失效，请重新发送需求。")
        action, value = parts[2].split("@", 1) if "@" in parts[2] else (parts[2], "")
        if action == "x":
            self.cancel(route)
            self.store.clear_project_request(route)
            return self._reply(route, "已取消选择，继续当前对话。")
        if pending.stage == "project" and action == "q":
            try:
                page = int(value)
                if page < 0 or page * self.page_size >= len(self.projects):
                    raise ValueError
            except ValueError:
                return self._reply(route, "工程页码无效，请重新发送需求。")
            return self._project_page(route, pending, page)
        if pending.stage == "project" and action == "p":
            try:
                index = int(value)
                if index < 0:
                    raise ValueError
                project = self.projects[index]
            except (ValueError, IndexError):
                return self._reply(route, "工程选择无效，请重新发送需求。")
            if pending.mode in {"attach", "new_project"}:
                return await self._select_project(route, pending, project)
            pending.project, pending.stage = project, "session"
            result = await self._session_prompt(route, pending)
            if not self._current(route, pending):
                return self._reply(route, "当前引导已取消，请重新发送需求。")
            return result
        if pending.stage == "session" and action in {"n", "r", "o"} and value == "0":
            if action == "o":
                pending.stage = "other"
                return self._session_page(route, pending, 0)
            if action == "r":
                value = "0"
            if action == "n":
                session = await self.runtime.create_session(pending.project, pending.text[:60] or "Telegram 会话",
                    request_id=f"telegram-{route}-{pending.update_id}")
                if not self._current(route, pending):
                    return self._reply(route, "当前引导已取消，请重新发送需求。")
            else:
                try:
                    if self._pending.get(route) is not pending:
                        return self._reply(route, "当前引导已取消，请重新发送需求。")
                    index = int(value)
                    if index < 0 or index >= len(pending.sessions):
                        raise ValueError
                    selected = pending.sessions[index]
                    session = await self.runtime.get_session(selected["id"], pending.project)
                except ChannelAdapterError as error:
                    if error.code not in {"session_not_found", "session_scope_mismatch"}:
                        raise
                    self.cancel(route)
                    return self._reply(route, "会话已不可用，请重新选择；原需求未执行。")
                except (ValueError, IndexError, KeyError):
                    return self._reply(route, "会话选择无效，请重新发送需求。")
            if not session or not session.get("id") or not session.get("sessionId"):
                return self._reply(route, "会话尚未确认，请稍后重试。")
            if not self._current(route, pending):
                return self._reply(route, "当前引导已取消，请重新发送需求。")
            if self.store.watch_session(route, pending.project, session["sessionId"]) is False:
                return self._reply(route, "通知绑定未保存，尚未切换会话。")
            if self.store.set_binding(route, pending.project, session["id"]) is False:
                return self._reply(route, "会话切换未保存，原需求未执行。")
            self._pending.pop(route, None)
            return self._resume_or_ready(route, pending, session)
        if pending.stage == "other" and action == "h" and value == "0":
            pending.stage = "session"
            return self._session_home(route, pending)
        if pending.stage == "other" and action in {"s", "g"}:
            if action == "s":
                try:
                    index = int(value)
                    if index < 0 or index >= len(pending.sessions):
                        raise ValueError
                    target = pending.sessions[index]
                    target_project = target.get("projectPath", pending.project)
                    if target_project and target_project not in self.projects:
                        raise ValueError
                    session = await self.runtime.get_session(target["id"], target_project)
                except ChannelAdapterError as error:
                    if error.code not in {"session_not_found", "session_scope_mismatch"}:
                        raise
                    self.cancel(route)
                    return self._reply(route, "会话已不可用，请重新选择；原需求未执行。")
                except (ValueError, IndexError, KeyError):
                    return self._reply(route, "会话选择无效，请重新发送需求。")
                if not session or not session.get("id") or not session.get("sessionId"):
                    return self._reply(route, "会话尚未确认，请稍后重试。")
                if not self._current(route, pending):
                    return self._reply(route, "当前引导已取消，请重新发送需求。")
                pending.project = target_project
                if self.store.watch_session(route, pending.project, session["sessionId"]) is False or self.store.set_binding(route, pending.project, session["id"]) is False:
                    return self._reply(route, "会话切换未保存，原需求未执行。")
                self._pending.pop(route, None)
                self.store.clear_project_request(route)
                return self._resume_or_ready(route, pending, session)
            try:
                page = int(value)
                if page < 0 or page * self.page_size >= len(pending.sessions):
                    raise ValueError
            except ValueError:
                return self._reply(route, "会话页码无效，请重新发送需求。")
            return self._session_page(route, pending, page)
        return self._reply(route, "这个引导按钮已失效，请重新发送需求。")

    async def _select_project(self, route: str, pending: _Pending, project: str) -> dict:
        binding = self.store.get_binding(route)
        if pending.mode == "attach":
            if not binding or binding.get("session_id") != pending.chat_id or binding.get("project_root") not in {"", project}:
                self.cancel(route)
                return self._reply(route, "当前对话已切换，未改变工程。请在需要操作的对话中重新选择。")
            try:
                session = await self.runtime.bind_project(pending.chat_id, project)
            except ChannelAdapterError as error:
                if error.code == "session_scope_mismatch":
                    # A prior attempt may have saved the chat but not the route.
                    session = await self.runtime.get_session(pending.chat_id, project)
                elif error.code in {"active_run", "store_conflict", "recovery_unavailable"}:
                    return self._reply(route, "当前对话还在处理消息或保存状态，请稍后再选；尚未开始工程操作。")
                else:
                    raise
            if not self.store.bind_selected_project(route, pending.chat_id, session["sessionId"], project):
                return self._reply(route, "会话工程已保存，入口绑定未确认，请重试本次选择；任务尚未启动。")
            if not self._current(route, pending):
                return self._reply(route, "工程已选择；自动继续已取消，未启动工程操作。")
        else:
            session = await self.runtime.create_session(project, "Telegram 会话",
                request_id=f"telegram-project-{route}-{pending.update_id}")
            if not self._current(route, pending):
                return self._reply(route, "选择已取消，未启动工程操作。")
            if not self.store.watch_session(route, project, session["sessionId"]) or self.store.set_binding(route, project, session["id"]) is False:
                return self._reply(route, "会话绑定未保存，请稍后重试。")
        pending.project = project
        self._pending.pop(route, None)
        # Preserve the intent until dispatch has recorded the continuation.
        result = self._resume_or_ready(route, pending, session)
        result["project_selected"] = True
        return result

    def _project_prompt(self, route: str, pending: _Pending) -> dict[str, Any]:
        return self._project_page(route, pending, 0)

    def _project_page(self, route: str, pending: _Pending, page: int) -> dict[str, Any]:
        start = page * self.page_size
        buttons = [[{"text": f"{i + 1}. {PureWindowsPath(project).name}"[:100], "callback_data": f"setup:{pending.nonce}:p@{i}"}]
                   for i, project in enumerate(self.projects[start:start + self.page_size], start)]
        if page > 0:
            buttons.append([{"text": "上一页", "callback_data": f"setup:{pending.nonce}:q@{page - 1}"}])
        if start + self.page_size < len(self.projects):
            buttons.append([{"text": "下一页", "callback_data": f"setup:{pending.nonce}:q@{page + 1}"}])
        buttons.append([{"text": "取消", "callback_data": f"setup:{pending.nonce}:x@0"}])
        text = "请选择工程：\n" + "\n".join(f"{i+1}. {path}" for i, path in enumerate(self.projects[start:start+self.page_size], start))
        return {"route": route, "text": text, "messages": [{"text": text, "reply_markup": {"inline_keyboard": buttons}}]}

    @staticmethod
    def _resume_or_ready(route: str, pending: _Pending, session: dict[str, Any]) -> dict[str, Any]:
        if not pending.text:
            return {"route": route, "text": selection_text(pending.project, session)
                    + "\n现在可以直接发送需求。切换不会自动停止旧会话任务。", "reply_markup": bound_keyboard()}
        return {"resume": True, "route": route, "text": pending.text,
                "update_id": pending.update_id, "project": pending.project, "session": session}

    async def _session_prompt(self, route: str, pending: _Pending) -> dict[str, Any]:
        sessions = await self.runtime.list_sessions(pending.project)
        usable = [{key: row.get(key) for key in ("id", "title", "updatedAt", "updated_at")}
                  for row in sessions if isinstance(row, dict) and row.get("id")]
        usable.reverse()
        usable.sort(key=lambda row: str(row.get("updatedAt") or row.get("updated_at") or ""), reverse=True)
        pending.sessions = tuple(usable)
        return self._session_home(route, pending)

    def _session_home(self, route: str, pending: _Pending) -> dict[str, Any]:
        buttons = [[{"text": "新建对话", "callback_data": f"setup:{pending.nonce}:n@0"}]]
        text = f"工程：{PureWindowsPath(pending.project).name}\n选择新建或继续对话："
        if pending.sessions:
            text += "\n最近：" + str(pending.sessions[0].get("title") or "未命名")[:100]
            buttons.extend([[{"text": "继续最近对话", "callback_data": f"setup:{pending.nonce}:r@0"}],
                            [{"text": "选择其他对话", "callback_data": f"setup:{pending.nonce}:o@0"}]])
        return {"route": route, "text": text, "messages": [{"text": text, "reply_markup": {"inline_keyboard": buttons}}]}

    def _session_page(self, route: str, pending: _Pending, page: int) -> dict[str, Any]:
        buttons = [[{"text": f"{i+1}. {PureWindowsPath(row.get('projectPath') or pending.project).name if row.get('projectPath') or pending.project else '普通'} · {row.get('title') or '未命名'}"[:100], "callback_data": f"setup:{pending.nonce}:s@{i}"}]
                   for i, row in enumerate(pending.sessions[page * self.page_size:(page + 1) * self.page_size], page * self.page_size)]
        if page > 0:
            buttons.append([{"text": "上一页", "callback_data": f"setup:{pending.nonce}:g@{page - 1}"}])
        if (page + 1) * self.page_size < len(pending.sessions):
            buttons.append([{"text": "下一页", "callback_data": f"setup:{pending.nonce}:g@{page + 1}"}])
        buttons.append([{"text": "返回", "callback_data": f"setup:{pending.nonce}:{'x' if pending.mode == 'all_sessions' else 'h'}@0"}])
        text = ("请选择会话（每页 5 个；/session 关键词 搜索，/new 新建）：" if pending.sessions
                else "没有匹配会话。发送 /new 新建，或直接发消息继续当前对话。")
        return {"route": route, "text": text, "messages": [{"text": text, "reply_markup": {"inline_keyboard": buttons}}]}

    def _expire(self) -> None:
        now = self._clock()
        for route, pending in tuple(self._pending.items()):
            if pending.expires <= now:
                self._pending.pop(route, None)

    @staticmethod
    def _reply(route: str, text: str) -> dict[str, Any]:
        return {"route": route, "text": text, "control": None}
