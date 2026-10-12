"""Telegram policy adapter over the channel-neutral runtime adapter."""
from __future__ import annotations

import secrets
from typing import Any

from channel_runtime_adapter import (
    ChannelRuntimeAdapter,
)


class TelegramRuntimeAdapter(ChannelRuntimeAdapter):
    """Apply Telegram identity, title, and summary policy to shared runtime logic."""

    @staticmethod
    def _stable_identity(project_root: str, request_id: str, stable: str) -> tuple[str, str]:
        return f"tg_{stable}", f"sess_tg_{stable}"

    @staticmethod
    def _random_identity() -> tuple[str, str]:
        return f"tg_{secrets.token_urlsafe(12)}", f"sess_tg_{secrets.token_urlsafe(12)}"

    @staticmethod
    def _default_title() -> str:
        return "Telegram chat"

    @staticmethod
    def _summary(chat: dict[str, Any]) -> dict[str, Any]:
        project_root = str(chat.get("projectPath") or "").strip()
        summary = {key: chat.get(key) for key in ("id", "title", "sessionId", "projectPath", "telegramChatId")}
        summary["projectType"] = str(chat.get("projectType") or ("unity" if project_root else "general"))
        settings = chat.get("settings") if isinstance(chat.get("settings"), dict) else {}
        summary["provider"] = str(settings.get("provider") or "")
        summary["providerLabel"] = str(settings.get("providerLabel") or "")
        summary["model"] = str(settings.get("model") or "")
        times = [chat.get("updatedAt"), chat.get("createdAt")]
        times.extend(item.get("createdAt") for item in chat.get("items") or [] if isinstance(item, dict))
        summary["updatedAt"] = max((value for value in times if isinstance(value, str)), default="")
        return summary
