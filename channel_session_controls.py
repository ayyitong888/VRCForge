"""Small, channel-neutral controls for a persisted chat session.

The callbacks are deliberately injected: this module never reads global provider
configuration and never starts a runtime turn.
"""
from __future__ import annotations

import inspect
import secrets
from typing import Any, Awaitable, Callable


class SessionControlError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


async def resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class ChannelSessionControls:
    def __init__(
        self,
        *,
        load_snapshot: Callable[[], Any],
        save_snapshot: Callable[[list[dict[str, Any]], list[dict[str, Any]]], Any],
        find_chat: Callable[[list[dict[str, Any]], str, str], dict[str, Any]],
        busy: Callable[[dict[str, Any], str, str], Awaitable[None]],
        model_catalog: Callable[[str], Any] | None = None,
        compactor: Callable[[dict[str, Any], str, str], Any] | None = None,
    ) -> None:
        self._load = load_snapshot
        self._save = save_snapshot
        self._find = find_chat
        self._busy = busy
        self._catalog = model_catalog
        self._compactor = compactor

    async def model_options(self, chat_id: str, project_root: str) -> list[dict[str, Any]]:
        chats, _ = await resolve(self._load())
        chat = self._find(chats, chat_id, project_root)
        await self._busy(chat, project_root, "model-options")
        if self._catalog is None:
            raise SessionControlError("model_catalog_unavailable")
        raw = await resolve(self._catalog(project_root))
        if not isinstance(raw, list):
            raise SessionControlError("model_catalog_invalid")
        options: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                options.append({"provider": "", "model": item, "id": item, "label": item})
            elif isinstance(item, dict):
                model = str(item.get("model") or item.get("id") or "").strip()
                if model:
                    options.append({"provider": str(item.get("provider") or "").strip(), "model": model,
                                    "id": str(item.get("id") or model),
                                    "label": str(item.get("label") or item.get("displayName") or model)})
        return options

    async def set_model(self, chat_id: str, project_root: str, provider: str, model: str) -> dict[str, Any]:
        provider, model = str(provider or "").strip(), str(model or "").strip()
        if not provider or not model:
            raise SessionControlError("invalid_model")
        chats, revisions = await resolve(self._load())
        chat = self._find(chats, chat_id, project_root)
        await self._busy(chat, project_root, "set-model")
        if self._catalog is None:
            raise SessionControlError("model_catalog_unavailable")
        raw = await resolve(self._catalog(project_root))
        options = raw if isinstance(raw, list) else []
        valid = any(
            (isinstance(item, str) and item == model and provider == "")
            or (isinstance(item, dict) and str(item.get("id") or item.get("model") or "") == model
                and str(item.get("provider") or "") == provider)
            for item in options
        )
        if not valid:
            raise SessionControlError("model_unavailable")
        settings = dict(chat.get("settings") or {}) if isinstance(chat.get("settings"), dict) else {}
        settings.update({"provider": provider, "model": model})
        await self._busy(chat, project_root, "set-model-commit")
        updated = {**chat, "settings": settings}
        rows = [updated if row.get("id") == chat.get("id") else row for row in chats]
        await resolve(self._save(rows, revisions))
        return {"chatId": chat_id, "projectRoot": project_root, "provider": provider, "model": model}

    async def compact(self, chat_id: str, project_root: str, instructions: str = "") -> dict[str, Any]:
        if self._compactor is None:
            raise SessionControlError("compactor_unavailable")
        chats, revisions = await resolve(self._load())
        chat = self._find(chats, chat_id, project_root)
        await self._busy(chat, project_root, "compact")
        result = await resolve(self._compactor(chat, project_root, str(instructions or "")))
        if not isinstance(result, dict) or result.get("ok") is False:
            reason = str(result.get("reason") or "compact_failed") if isinstance(result, dict) else "compact_failed"
            if reason not in {"no_content", "compact_no_reduction", "attachments_unsupported", "provider_not_configured"}:
                reason = "compact_failed"
            raise SessionControlError(reason)
        latest_chats, latest_revisions = await resolve(self._load())
        latest_chat = self._find(latest_chats, chat_id, project_root)
        await self._busy(latest_chat, project_root, "compact-commit")
        if latest_revisions != revisions:
            raise SessionControlError("store_conflict")
        updated = result.get("chat")
        if not isinstance(updated, dict):
            raise SessionControlError("compact_uncommitted")
        if updated.get("id") != chat.get("id") or str(updated.get("projectPath") or "") != project_root:
            raise SessionControlError("compact_scope_mismatch")
        rows = [updated if row.get("id") == chat.get("id") else row for row in latest_chats]
        await resolve(self._save(rows, latest_revisions))
        return {**result, "status": "applied", "chatId": chat_id,
                "sessionId": str(updated.get("sessionId") or "")}


def compact_chat_items(chat: dict[str, Any], result: dict[str, Any], *, history_builder: Callable[[list[Any]], list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """Apply the established durable-window projection to a compact result."""
    summary = str(result.get("summary") or "").strip()
    recovery = result.get("recovery")
    if not summary or not isinstance(recovery, dict):
        return None
    items = [item for item in chat.get("items") or [] if isinstance(item, dict)
             and item.get("type") not in {"streaming"}]
    last_user = max((index for index, item in enumerate(items) if item.get("type") == "user"), default=-1)
    recent = items[last_user:] if last_user >= 0 else []
    retained_ids = {item.get("id") for item in recent if item.get("type") in {"user", "agent", "compact"}}
    retained_ids.update(item.get("id") for item in items if item.get("type") in {
        "result", "approval_revision", "subagent", "handoff_card", "error"
    })
    # Keep every persisted item.  The runtime projection understands the
    # marker below and filters only the source ids covered by this summary;
    # dropping rows here made a normal channel compact irreversibly erase the
    # visible transcript after a reload.
    covered_ids = {
        item.get("id") for item in items
        if isinstance(item.get("id"), str) and item.get("id") not in retained_ids
    }
    # Keep the durable reply/recovery payload, but invalidate provider usage
    # measured against the pre-compaction context window.
    retained = [
        ({**item, "response": {key: value for key, value in item["response"].items()
                                if key != "contextUsage"}}
         if item.get("type") == "agent" and isinstance(item.get("response"), dict)
         else item)
        for item in items
    ]
    prior_recovery: list[dict[str, Any]] = []
    for item in items:
        values = item.get("compactionRecovery")
        if isinstance(values, list):
            prior_recovery.extend(value for value in values if isinstance(value, dict))
    compact_item = {
        "id": f"compact:{result.get('summaryDigest') or 'session'}",
        "type": "compact", "text": "Context compacted", "detail": summary,
        "summarizedItemIds": sorted(covered_ids),
        "status": "completed", "compactionRecovery": [*prior_recovery, recovery],
        "entryCount": result.get("entryCount"),
        "retainedEntryCount": result.get("retainedEntryCount"),
    }
    updated = {key: value for key, value in chat.items() if key != "contextUsageCache"}
    updated = {**updated, "sessionId": f"sess_compact_{secrets.token_urlsafe(18)}", "items": [*retained, compact_item],
            "compaction": {"status": "applied", "summaryDigest": result.get("summaryDigest"),
                           "sourceDigest": result.get("sourceDigest"), "provider": result.get("provider"),
                           "model": result.get("model")}}
    if history_builder is not None:
        before = sum(len(str(row.get("text") or "")) for row in history_builder(items))
        after = sum(len(str(row.get("text") or "")) for row in history_builder(updated["items"]))
        if after >= before:
            return None
    return updated
