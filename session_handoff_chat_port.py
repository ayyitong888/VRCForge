"""Server-owned ChatPort adapter for the B6 handoff service.

The adapter deliberately receives storage/runtime callbacks from the dashboard;
it never trusts a browser supplied owner or safety flag.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable, Mapping


class ChatPortConflict(RuntimeError):
    pass


def normalize_scope(scope: str | None) -> str:
    value = str(scope or "").strip()
    if not value:
        return ""
    return value.casefold()


class SessionHandoffChatPort:
    def __init__(
        self,
        *,
        principal_digest: str,
        lock: threading.RLock,
        load_chat: Callable[[str, str], Mapping[str, Any] | None],
        save_chat: Callable[[str, str, Mapping[str, Any], int], Mapping[str, Any]],
        runtime_snapshot: Callable[[str, str, str], Mapping[str, Any]],
        enqueue_context: Callable[[str, Mapping[str, Any]], None],
    ) -> None:
        if not principal_digest or len(principal_digest) > 256:
            raise ValueError("principal_digest is required")
        self._principal = principal_digest
        self._lock = lock
        self._load = load_chat
        self._save = save_chat
        self._runtime = runtime_snapshot
        self._enqueue = enqueue_context

    @staticmethod
    def _scope(value: str) -> str:
        return normalize_scope(value)

    def _snapshot(self, *, owner_id: str, chat_id: str, session_id: str, scope: str) -> dict[str, Any]:
        if owner_id != self._principal:
            raise PermissionError("owner is server-bound")
        normalized_scope = self._scope(scope)
        chat = self._load(chat_id, normalized_scope)
        if not isinstance(chat, Mapping) or str(chat.get("id") or "") != chat_id:
            raise PermissionError("chat is not bound to scope")
        stored_scope = self._scope(chat.get("handoffScope") or chat.get("projectPath") or "")
        if stored_scope != normalized_scope:
            raise PermissionError("chat scope mismatch")
        stored_session = str(chat.get("sessionId") or "")
        if not stored_session or stored_session != session_id:
            raise PermissionError("chat session mismatch")
        revision = chat.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise ChatPortConflict("chat revision missing; migration required")
        runtime = dict(self._runtime(chat_id, session_id, normalized_scope) or {})
        return {
            "owner_id": owner_id,
            "chat_id": chat_id,
            "session_id": session_id,
            "scope": normalized_scope,
            "revision": revision,
            **runtime,
        }

    def get_snapshot(self, *, owner_id: str, chat_id: str, session_id: str, scope: str) -> Mapping[str, Any]:
        with self._lock:
            return self._snapshot(owner_id=owner_id, chat_id=chat_id, session_id=session_id, scope=scope)

    def append_inbox_card(self, *, card_id: str, payload_digest: str, card: Mapping[str, Any], expected_revision: int) -> Mapping[str, Any]:
        chat_id = str(card.get("targetChatId") or card.get("target_chat_id") or "")
        scope = self._scope(str(card.get("scope") or ""))
        if not chat_id:
            raise ValueError("target chat is required")
        with self._lock:
            chat = self._load(chat_id, scope)
            if not isinstance(chat, Mapping):
                raise PermissionError("target chat is unavailable")
            revision = chat.get("revision")
            if not isinstance(revision, int) or revision < 1:
                raise ChatPortConflict("chat revision missing; migration required")
            items = list(chat.get("items") or []) if isinstance(chat.get("items"), list) else []
            for item in items:
                if isinstance(item, Mapping) and item.get("type") == "handoff_card" and item.get("cardId") == card_id:
                    if item.get("payloadDigest") != payload_digest:
                        raise ChatPortConflict("handoff card digest conflict")
                    return {"cardId": card_id, "payloadDigest": payload_digest, "revision": revision}
            if revision != expected_revision:
                raise ChatPortConflict("chat revision changed")
            safe_card = {
                "type": "handoff_card",
                "id": card_id,
                "cardId": card_id,
                "handoffId": str(card.get("handoffId") or ""),
                "sourceChatId": str(card.get("sourceChatId") or ""),
                "targetChatId": str(card.get("targetChatId") or ""),
                "sourceRevision": card.get("sourceRevision"),
                "targetRevision": card.get("targetRevision"),
                "kind": str(card.get("kind") or "handoff"),
                "status": "pending_review",
                "payloadDigest": payload_digest,
                "summary": dict(card.get("payload") or {}),
            }
            updated = {**chat, "items": [*items, safe_card], "revision": revision + 1}
            saved = self._save(chat_id, scope, updated, expected_revision)
            saved_revision = int(saved.get("revision", revision + 1)) if isinstance(saved, Mapping) else revision + 1
            return {"cardId": card_id, "payloadDigest": payload_digest, "revision": saved_revision}

    def get_inbox_card(self, *, card_id: str, chat_id: str, scope: str = "") -> Mapping[str, Any] | None:
        with self._lock:
            chat = self._load(chat_id, self._scope(scope))
            for item in (chat.get("items") or []) if isinstance(chat, Mapping) else []:
                if isinstance(item, Mapping) and item.get("type") == "handoff_card" and item.get("cardId") == card_id:
                    return {"cardId": card_id, "payloadDigest": item.get("payloadDigest"), "revision": chat.get("revision", 0)}
        return None

    def enqueue_next_turn_context(self, *, chat_id: str, context: Mapping[str, Any]) -> None:
        self._enqueue(chat_id, dict(context))
