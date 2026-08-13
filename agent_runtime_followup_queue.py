from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class FollowupQueuePorts:
    path: Path
    lock: Any
    now: Callable[[], float] = time.time


class AgentRuntimeFollowupQueue:
    """Durable FIFO follow-up queue owned by one AgentGateway instance.

    The file inherits the private VRCForge user-data boundary and is written
    with user-only permissions where the platform supports them. Dashboard
    session authentication is enforced before this module is called. Pending
    input survives process restarts; terminal entries become compact idempotency
    tombstones instead of retaining user content indefinitely.
    """
    MAX_MESSAGE = 4000
    MAX_ATTACHMENTS = 16
    MAX_ATTACHMENT_BYTES = 16_384
    MAX_STORE_BYTES = 16 * 1024 * 1024
    LEASE_SECONDS = 300.0

    def __init__(self, ports: FollowupQueuePorts) -> None:
        self._ports = ports
        self._entries: list[dict[str, Any]] = []
        self._sequence = 0
        self._load_error = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._ports.path.read_text(encoding="utf-8")) if self._ports.path.exists() else []
            if not isinstance(raw, list):
                raise ValueError("follow-up queue root must be a list")
            self._entries = [x for x in raw if isinstance(x, dict)]
            self._entries.sort(key=lambda item: int(item.get("sequence", 0)))
        except (OSError, ValueError, TypeError):
            self._entries = []
            self._load_error = True
        self._sequence = max((int(x.get("sequence", 0)) for x in self._entries), default=0)

    def _persist(self) -> None:
        self._ports.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self._ports.path.name + ".", dir=str(self._ports.path.parent))
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._entries, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._ports.path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _safe(self, value: Any) -> Any:
        if isinstance(value, dict):
            blocked = {"apiKey", "api_key", "authorization", "token", "password", "secret", "dataUrl", "content"}
            return {str(k): "<redacted>" if str(k) in blocked else self._safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._safe(v) for v in value[: self.MAX_ATTACHMENTS]]
        return value

    def enqueue(self, *, session_id: str, client_turn_id: str, message: str,
                target_client_turn_id: str = "", attachments: list[dict[str, Any]] | None = None,
                envelope: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id, client_turn_id = str(session_id or "").strip()[:180], str(client_turn_id or "").strip()[:180]
        message = str(message or "").strip()
        if not session_id or not client_turn_id or (not message and not attachments):
            return {"accepted": False, "mode": "followup", "reason": "invalid_request"}
        if self._load_error:
            return {"accepted": False, "mode": "followup", "reason": "durable_store_unavailable", "status": "backpressure"}
        if len(message) > self.MAX_MESSAGE:
            return {"accepted": False, "mode": "followup", "reason": "message_oversize"}
        safe_attachments = self._safe(copy.deepcopy(attachments or []))
        if not isinstance(safe_attachments, list) or len(safe_attachments) > self.MAX_ATTACHMENTS:
            return {"accepted": False, "mode": "followup", "reason": "attachments_oversize"}
        # Persist only bounded metadata/reference fields; never inline bytes or secrets.
        compact: list[dict[str, Any]] = []
        for item in safe_attachments:
            if not isinstance(item, dict):
                return {"accepted": False, "mode": "followup", "reason": "invalid_attachment"}
            ref = {k: item[k] for k in ("id", "attachmentId", "name", "type", "size", "payloadHash", "vaultRef", "payloadKind") if k in item}
            if len(json.dumps(ref, ensure_ascii=False)) > self.MAX_ATTACHMENT_BYTES:
                return {"accepted": False, "mode": "followup", "reason": "attachment_oversize"}
            compact.append(ref)
        with self._ports.lock:
            for old in self._entries:
                if old.get("sessionId") == session_id and old.get("clientTurnId") == client_turn_id:
                    return {"accepted": True, "mode": "followup", "status": old.get("status"), "deduped": True, "queueId": old["queueId"], "sequence": old["sequence"]}
            self._sequence += 1
            item = {"queueId": f"followup_{self._sequence:08d}", "sequence": self._sequence, "sessionId": session_id,
                    "targetClientTurnId": str(target_client_turn_id or "")[:180], "clientTurnId": client_turn_id,
                    "message": message, "attachments": compact, "status": "pending"}
            for k, v in (envelope or {}).items():
                if k not in {"message", "attachments", "sessionId", "clientTurnId"} and v not in (None, ""):
                    item[k] = self._safe(v)
            projected_bytes = len(json.dumps([*self._entries, item], ensure_ascii=False, sort_keys=True).encode("utf-8"))
            if projected_bytes > self.MAX_STORE_BYTES:
                self._sequence -= 1
                return {
                    "accepted": False,
                    "mode": "followup",
                    "reason": "durable_store_capacity",
                    "status": "backpressure",
                }
            self._entries.append(item)
            try: self._persist()
            except OSError:
                self._entries.pop()
                self._sequence -= 1
                return {"accepted": False, "mode": "followup", "reason": "durable_store_unavailable", "status": "backpressure"}
            return {"accepted": True, "mode": "followup", "status": "pending", "reason": "queued_followup", "queueId": item["queueId"], "sequence": item["sequence"]}

    def list(self, *, session_id: str = "", include_terminal: bool = True) -> list[dict[str, Any]]:
        with self._ports.lock:
            return copy.deepcopy([x for x in self._entries if (not session_id or x.get("sessionId") == session_id) and (include_terminal or x.get("status") not in {"acked", "cancelled"})])

    def claim(self, *, session_id: str, owner_id: str, limit: int = 8, queue_id: str = "") -> list[dict[str, Any]]:
        _ = limit  # retained for wire compatibility; a session lane claims one item at a time
        now = self._ports.now()
        with self._ports.lock:
            lane = [x for x in self._entries if x.get("sessionId") == session_id and x.get("status") not in {"acked", "cancelled"}]
            if not lane:
                return []
            head = lane[0]
            if queue_id and head.get("queueId") != queue_id:
                return []
            if head.get("status") == "claimed" and float(head.get("leaseUntil", 0)) > now:
                return []
            # A session is a serialized lane. Claim exactly its oldest item;
            # worker concurrency exists across sessions, never within one.
            chosen = [head]
            previous = copy.deepcopy(head)
            for x in chosen:
                x.update(status="claimed", claimToken=secrets.token_urlsafe(18), claimOwner=str(owner_id)[:180], leaseUntil=now + self.LEASE_SECONDS)
            try:
                self._persist()
            except OSError:
                head.clear()
                head.update(previous)
                raise
            return copy.deepcopy(chosen)

    def _transition(self, queue_id: str, session_id: str, token: str, status: str) -> bool:
        with self._ports.lock:
            for x in self._entries:
                if x.get("queueId") == queue_id and x.get("sessionId") == session_id and x.get("status") == "claimed" and secrets.compare_digest(str(x.get("claimToken", "")), token):
                    previous = copy.deepcopy(x)
                    self._make_terminal_tombstone(x, status)
                    try:
                        self._persist()
                    except OSError:
                        x.clear()
                        x.update(previous)
                        raise
                    return True
            return False

    def ack(self, *, queue_id: str, session_id: str, claim_token: str) -> bool:
        return self._transition(queue_id, session_id, claim_token, "acked")

    def cancel(self, *, queue_id: str, session_id: str, claim_token: str = "") -> bool:
        with self._ports.lock:
            for x in self._entries:
                if x.get("queueId") == queue_id and x.get("sessionId") == session_id and (x.get("status") == "pending" or (x.get("status") == "claimed" and claim_token and secrets.compare_digest(str(x.get("claimToken", "")), claim_token))):
                    previous = copy.deepcopy(x)
                    self._make_terminal_tombstone(x, "cancelled")
                    try:
                        self._persist()
                    except OSError:
                        x.clear()
                        x.update(previous)
                        raise
                    return True
            return False

    @staticmethod
    def _make_terminal_tombstone(item: dict[str, Any], status: str) -> None:
        retained = {
            key: item.get(key)
            for key in ("queueId", "sequence", "sessionId", "clientTurnId")
        }
        item.clear()
        item.update(retained, status=status)
