"""Composition of remote channels with existing dashboard chat/runtime services.

No HTTP listener or agent executor is created here. All runtime execution and
Unity approval policy stay with the injected application services.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable



class DashboardChannelApprovals:
    def __init__(self, *, list_pending: Callable, approve: Callable, reject: Callable) -> None:
        self._list, self._approve, self._reject = list_pending, approve, reject

    async def list_pending(self, project_root: str, session_id: str) -> list[dict]:
        if not isinstance(session_id, str) or not session_id.strip():
            return []
        rows = await asyncio.to_thread(self._list, project_root)
        def matches(row: dict) -> bool:
            arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
            context = row.get("taskContext") if isinstance(row.get("taskContext"), dict) else {}
            candidate = next((arguments.get(key) or row.get(key) or context.get(key)
                              for key in ("projectRoot", "project_root", "projectPath", "project_path")
                              if arguments.get(key) or row.get(key) or context.get(key)), "")
            same_project = (candidate == project_root if not candidate or not project_root else
                            os.path.normcase(os.path.abspath(str(candidate))) == os.path.normcase(os.path.abspath(project_root)))
            return same_project and row.get("sessionId") == session_id
        return [row for row in rows if matches(row)]

    async def decide(self, approval_id: str, project_root: str, approve: bool) -> dict:
        return await (self._approve if approve else self._reject)(approval_id, project_root)


class DashboardChannelPermissions:
    """Use the same global permission owner and UI events as the desktop."""
    def __init__(self, *, read: Callable, update: Callable) -> None:
        self._read, self._update = read, update

    async def read(self) -> dict:
        return await asyncio.to_thread(self._read)

    async def update(self, mode: str) -> dict:
        return await self._update(mode)


class DashboardChannelChatStore:
    def __init__(self, load: Callable, save: Callable) -> None:
        self._load, self._save = load, save

    async def load(self) -> dict:
        return await asyncio.to_thread(self._load)

    async def save(self, chats: list, revisions: list) -> Any:
        return await asyncio.to_thread(self._save, chats, revisions)


class DashboardChannelRuntime:
    def __init__(self, *, message: Callable, steer: Callable, cancel: Callable,
                 current_config: Callable, agent_name: str, stop_reason: str) -> None:
        self._message, self._steer, self._cancel = message, steer, cancel
        self._current_config = current_config
        self._agent_name, self._stop_reason = agent_name, stop_reason

    @staticmethod
    def _setting(context: dict, key: str, fallback: Any = "") -> Any:
        saved = context.get("sessionConfig")
        if isinstance(saved, dict) and saved.get(key) not in (None, ""):
            return saved[key]
        return fallback

    def _config_value(self, context: dict, key: str, config: Any) -> Any:
        value = context.get(key)
        if value not in (None, ""):
            return value
        saved = self._setting(context, key)
        if saved not in (None, ""):
            return saved
        if isinstance(config, dict):
            return config.get(key, "")
        return getattr(config, key, "")

    async def start_turn(self, session_id: str, client_turn_id: str, message: str, context: dict) -> dict:
        config = self._current_config()
        project_root = context.get("projectRoot") or ""
        params = {
            "session_id": session_id, "clientTurnId": client_turn_id,
            "chatId": context["chatId"], "message": message,
            "projectPath": project_root or None, "projectRoot": project_root or None,
            "projectType": context.get("projectType") or ("unity" if project_root else "general"),
            "agent_name": self._agent_name,
            "provider": self._config_value(context, "provider", config),
            "providerLabel": self._config_value(context, "providerLabel", config),
            "model": self._config_value(context, "model", config),
            "history": context.get("history", []), "maxAgenticTurns": 64,
            "compactionRecovery": context.get("compactionRecovery", []),
            "planMode": self._config_value(context, "planMode", config) is True,
        }
        context_limit = self._config_value(context, "contextLimit", config)
        if context_limit not in (None, ""):
            params["contextLimit"] = context_limit
        return await self._message(params)

    async def steer(self, session_id: str, target_client_turn_id: str, client_turn_id: str,
                    message: str, context: dict) -> dict:
        # Calls the existing mailbox directly, not the ordinary follow-up queue.
        return self._steer({"sessionId": session_id, "targetClientTurnId": target_client_turn_id,
                            "clientTurnId": client_turn_id, "message": message,
                            "projectType": context.get("projectType") or ("unity" if context.get("projectRoot") else "general"),
                            "projectPath": context.get("projectRoot") or None, "attachments": []})

    async def cancel(self, session_id: str, target_client_turn_id: str, context: dict) -> dict:
        return self._cancel({"session_id": session_id, "clientTurnId": target_client_turn_id,
                             "reason": self._stop_reason})


