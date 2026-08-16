from __future__ import annotations

import hashlib
import json
import os
import threading
import tempfile
from pathlib import Path
from typing import Any, Mapping, Protocol

from session_handoff import SessionHandoffStore
from session_handoff_chat_port import ChatPortConflict


class ChatPort(Protocol):
    def get_snapshot(self, *, owner_id: str, chat_id: str, session_id: str, scope: str) -> Mapping[str, Any]: ...
    def append_inbox_card(self, *, card_id: str, payload_digest: str, card: Mapping[str, Any], expected_revision: int) -> Mapping[str, Any]: ...
    def get_inbox_card(self, *, card_id: str, chat_id: str, scope: str = "") -> Mapping[str, Any] | None: ...
    def enqueue_next_turn_context(self, *, chat_id: str, context: Mapping[str, Any]) -> None: ...


class SessionHandoffService:
    """Application-lifetime orchestration; deliberately has no provider/tool/UI dependencies."""

    def __init__(self, store: SessionHandoffStore, chat: ChatPort, *, state_path: str | Path | None = None) -> None:
        self.store, self.chat = store, chat
        self.state_path = Path(state_path) if state_path else None
        if self.state_path is not None and not self.state_path.is_absolute():
            raise ValueError("state_path must be absolute")
        self._lock = threading.RLock()
        self._paused: set[str] = set()
        self._enqueued: set[str] = set()
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("paused", []), list) and isinstance(data.get("enqueued", []), list):
                self._paused = {str(x) for x in data.get("paused", [])}
                self._enqueued = {str(x) for x in data.get("enqueued", [])}
            else:
                self._paused, self._enqueued = set(), set()
        except (OSError, ValueError):
            self._paused, self._enqueued = set(), set()

    def _save_state(self) -> None:
        if not self.state_path: return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"paused": sorted(self._paused), "enqueued": sorted(self._enqueued)}, separators=(",", ":")).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(prefix=self.state_path.name + ".", suffix=".tmp", dir=str(self.state_path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(tmp_name, self.state_path)
            try:
                dir_fd = os.open(self.state_path.parent, os.O_RDONLY)
                try: os.fsync(dir_fd)
                finally: os.close(dir_fd)
            except OSError: pass
        finally:
            try: os.unlink(tmp_name)
            except OSError: pass

    @staticmethod
    def _snapshot(snapshot: Mapping[str, Any], *, owner_id: str, chat_id: str, session_id: str, scope: str) -> tuple[int, Mapping[str, Any]]:
        required = ("owner_id", "chat_id", "session_id", "scope", "revision")
        if any(key not in snapshot for key in required):
            raise PermissionError("chat snapshot is incomplete")
        if any(str(snapshot[key]) != expected for key, expected in (("owner_id", owner_id), ("chat_id", chat_id), ("session_id", session_id), ("scope", scope))):
            raise PermissionError("chat snapshot binding mismatch")
        try: revision = int(snapshot["revision"])
        except (TypeError, ValueError) as exc: raise PermissionError("chat snapshot revision is invalid") from exc
        if revision < 1: raise PermissionError("chat snapshot revision is invalid")
        return revision, snapshot

    @staticmethod
    def _safe(snapshot: Mapping[str, Any]) -> bool:
        if snapshot.get("safeForHandoff") is False or str(snapshot.get("safe_state", "")).casefold() in {"active", "unsafe", "busy"}:
            return False
        for key in ("active_stream", "activeStream", "pending_approval", "pendingApproval", "pending_question", "pendingQuestion", "chat_mutation", "chatMutation"):
            if snapshot.get(key): return False
        return True

    @staticmethod
    def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {"goal": row["goal"], "completed": bool(row["completed"]), "decisions": list(row["decisions"]), "blockers": list(row["blockers"]), "nextAction": row["nextAction"], "question": row["question"]}

    def _exact_existing_card(self, *, card_id: str, chat_id: str, scope: str, digest: str) -> Mapping[str, Any] | None:
        lookup = getattr(self.chat, "get_inbox_card", lambda **_: None)
        try:
            existing = lookup(card_id=card_id, chat_id=chat_id, scope=scope)
        except TypeError:
            existing = lookup(card_id=card_id, chat_id=chat_id)
        if isinstance(existing, Mapping) and existing.get("cardId") == card_id and existing.get("payloadDigest") == digest:
            return existing
        return None

    def _authorize_target_action(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        target_session_id: str,
        scope: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        row = self.store.authorize_target_action(
            handoff_id=handoff_id,
            owner_id=owner_id,
            target_session_id=target_session_id,
            scope=scope,
            expected_revision=expected_revision,
        )
        target_chat_id = str(row["target_chat_id"])
        snapshot = self.chat.get_snapshot(
            owner_id=owner_id,
            chat_id=target_chat_id,
            session_id=target_session_id,
            scope=scope,
        )
        self._snapshot(
            snapshot,
            owner_id=owner_id,
            chat_id=target_chat_id,
            session_id=target_session_id,
            scope=scope,
        )
        return self.store.authorize_target_action(
            handoff_id=handoff_id,
            owner_id=owner_id,
            target_session_id=target_session_id,
            scope=scope,
            expected_revision=expected_revision,
        )

    def send(self, *, owner_id: str, source_session_id: str, source_chat_id: str, target_session_id: str, target_chat_id: str, scope: str, payload: Mapping[str, Any], kind: str = "handoff", reply_to: str | None = None) -> dict[str, Any]:
        source = self.chat.get_snapshot(owner_id=owner_id, chat_id=source_chat_id, session_id=source_session_id, scope=scope)
        target = self.chat.get_snapshot(owner_id=owner_id, chat_id=target_chat_id, session_id=target_session_id, scope=scope)
        source_revision, source = self._snapshot(source, owner_id=owner_id, chat_id=source_chat_id, session_id=source_session_id, scope=scope)
        target_revision, target = self._snapshot(target, owner_id=owner_id, chat_id=target_chat_id, session_id=target_session_id, scope=scope)
        row = self.store.create(owner_id=owner_id, source_session_id=source_session_id, target_session_id=target_session_id, source_chat_id=source_chat_id, target_chat_id=target_chat_id, source_revision=source_revision, target_revision=target_revision, source_scope=scope, target_scope=scope, payload=payload, kind=kind, reply_to=reply_to)
        return self._public(row)

    def deliver(self, *, handoff_id: str, owner_id: str, target_session_id: str, scope: str) -> dict[str, Any]:
        with self._lock:
            row = self.store.get(handoff_id=handoff_id, owner_id=owner_id, session_id=target_session_id, scope=scope)
            if handoff_id in self._paused: return {"status": "paused", "handoff": self._public(row)}
            target_chat = str(row["target_chat_id"])
            snapshot = self.chat.get_snapshot(owner_id=owner_id, chat_id=target_chat, session_id=target_session_id, scope=scope)
            target_revision, snapshot = self._snapshot(snapshot, owner_id=owner_id, chat_id=target_chat, session_id=target_session_id, scope=scope)
            if not self._safe(snapshot): return {"status": "queued", "handoff": self._public(row)}
            digest = str(row["payloadDigest"])
            card_id = "handoff-card-" + hashlib.sha256((handoff_id + ":" + digest).encode()).hexdigest()[:32]
            card = {"schema": "vrcforge.session_handoff.card.v1", "cardId": card_id, "handoffId": handoff_id, "kind": row["kind"], "payloadDigest": digest, "payload": self._payload(row), "sourceChatId": row["source_chat_id"], "targetChatId": row["target_chat_id"], "sourceRevision": row["source_revision"], "targetRevision": row["target_revision"], "scope": row["target_scope"]}
            expected_target_revision = int(row["target_revision"])
            if target_revision != expected_target_revision:
                existing = self._exact_existing_card(card_id=card_id, chat_id=target_chat, scope=scope, digest=digest)
                if existing is None:
                    raise RuntimeError("target chat revision changed")
            else:
                append_error: Exception | None = None
                try:
                    self.chat.append_inbox_card(card_id=card_id, payload_digest=digest, card=card, expected_revision=expected_target_revision)
                except (ChatPortConflict, RuntimeError) as exc:
                    # A durable card may already have committed before the
                    # adapter's post-read/receipt step failed. Continue only after
                    # exact card/digest readback; every other conflict remains
                    # fail-closed and retryable.
                    append_error = exc
                # The adapter return value is not commit authority. Always read
                # the durable chat back and bind continuation to exact identity.
                result = self._exact_existing_card(card_id=card_id, chat_id=target_chat, scope=scope, digest=digest)
                if result is None and append_error is not None:
                    raise append_error
                if not isinstance(result, Mapping) or result.get("cardId") != card_id or result.get("payloadDigest") != digest:
                    raise RuntimeError("inbox card identity mismatch")
                try:
                    result_revision = int(result["revision"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("inbox card revision missing") from exc
                if result_revision < expected_target_revision:
                    raise RuntimeError("inbox card revision invalid")
            # Card is durable/idempotently present before changing the handoff state.
            if row["status"] == "pending_review":
                row = self.store.claim(handoff_id=handoff_id, owner_id=owner_id, session_id=target_session_id, scope=scope, expected_revision=int(row["revision"]))
            if row["status"] == "claimed":
                row = self.store.materialize(handoff_id=handoff_id, owner_id=owner_id, session_id=target_session_id, scope=scope, expected_revision=int(row["revision"]), claim_token=str(row["claim_token"]))
            if row["status"] == "materialized" and handoff_id not in self._enqueued:
                self.chat.enqueue_next_turn_context(chat_id=target_chat, context={"contextId": card_id, "handoffId": handoff_id, "cardId": card_id, "payloadDigest": digest, "payload": self._payload(row)})
                self._enqueued.add(handoff_id)
                self._save_state()
            return {"status": "materialized", "cardId": card_id, "handoff": self._public(row)}

    def accept(self, **kwargs: Any) -> dict[str, Any]: return self.deliver(**kwargs)
    def dismiss(self, *, handoff_id: str, owner_id: str, session_id: str, scope: str, expected_revision: int) -> dict[str, Any]: return self._public(self.store.dismiss(handoff_id=handoff_id, owner_id=owner_id, session_id=session_id, scope=scope, expected_revision=expected_revision))
    def cancel(self, *, handoff_id: str, owner_id: str, session_id: str, scope: str, expected_revision: int) -> dict[str, Any]: return self._public(self.store.cancel(handoff_id=handoff_id, owner_id=owner_id, session_id=session_id, scope=scope, expected_revision=expected_revision))
    def pause(self, *, handoff_id: str, owner_id: str, target_session_id: str, scope: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            row = self._authorize_target_action(handoff_id=handoff_id, owner_id=owner_id, target_session_id=target_session_id, scope=scope, expected_revision=expected_revision)
            self._paused.add(handoff_id)
            self._save_state()
            return {"status": "paused", "handoff": self._public(row)}
    def resume(self, *, handoff_id: str, owner_id: str, target_session_id: str, scope: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            row = self._authorize_target_action(handoff_id=handoff_id, owner_id=owner_id, target_session_id=target_session_id, scope=scope, expected_revision=expected_revision)
            self._paused.discard(handoff_id)
            self._save_state()
            return {"status": "resumed", "handoff": self._public(row)}

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k not in {"claim_token", "claim_owner", "materializeReceipt"}}
