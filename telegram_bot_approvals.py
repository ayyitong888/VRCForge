"""Bounded, owner-scoped Telegram presentation of existing approvals.

This module contains no approval policy or execution implementation.  It maps
an existing pending approval to a short-lived one-time callback nonce, then
rechecks the live approval list before calling the injected decision service.
"""

from __future__ import annotations

import inspect
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol


class TelegramApprovalPort(Protocol):
    async def list_pending(self, project_root: str, session_id: str) -> list[dict[str, Any]]: ...

    async def decide(self, approval_id: str, project_root: str, approve: bool) -> dict[str, Any]: ...


class TelegramApprovalError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Binding:
    approval_id: str
    owner_id: int
    chat_id: int
    session_id: str
    project_root: str
    expires_at: float
    fingerprint: str


class TelegramApprovalController:
    """Render pending approvals and consume callbacks exactly once."""

    def __init__(self, port: TelegramApprovalPort, *, max_pending_nonces: int = 256,
                 ttl_seconds: float = 600.0, clock=time.monotonic) -> None:
        if not inspect.iscoroutinefunction(port.list_pending) or not inspect.iscoroutinefunction(port.decide):
            raise TypeError("approval port methods must be async")
        if max_pending_nonces <= 0 or ttl_seconds <= 0:
            raise ValueError("approval nonce bounds must be positive")
        self._port = port
        self._max = max_pending_nonces
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._bindings: dict[str, _Binding] = {}

    async def pending_messages(self, *, owner_id: int, chat_id: int, project_root: str,
                               session_id: str, reuse_existing: bool = False) -> list[dict[str, Any]]:
        self._validate_identity(owner_id, chat_id, project_root, session_id)
        self._purge_expired()
        rows = await self._port.list_pending(project_root, session_id)
        result: list[dict[str, Any]] = []
        for approval in rows:
            if not self._is_pending_scope(approval, project_root, session_id):
                continue
            approval_id = str(approval.get("approvalId") or approval.get("id") or "").strip()
            if not approval_id:
                continue
            fingerprint = self._fingerprint(approval)
            nonce = self._find_binding(approval_id, owner_id, chat_id, session_id, project_root,
                                       fingerprint) if reuse_existing else None
            if nonce is None:
                if len(self._bindings) >= self._max:
                    raise TelegramApprovalError("approval_nonce_capacity")
                nonce = self._new_nonce()
                self._bindings[nonce] = _Binding(approval_id, owner_id, chat_id, session_id, project_root,
                                                  self._clock() + self._ttl, fingerprint)
            label = str(approval.get("targetTool") or approval.get("tool") or "write request")
            reason = self._bounded_text(approval.get("reason") or approval.get("description") or "Approval required", 500)
            result.append({
                "approvalId": approval_id,
                "text": self._card_text(label, reason, approval),
                "buttons": [
                    {"text": "批准", "callback_data": f"vrca:{nonce}:a"},
                    {"text": "拒绝", "callback_data": f"vrca:{nonce}:r"},
                ],
            })
        return result

    def _find_binding(self, approval_id: str, owner_id: int, chat_id: int, session_id: str,
                      project_root: str, fingerprint: str) -> str | None:
        now = self._clock()
        for nonce, binding in self._bindings.items():
            if (binding.expires_at > now and binding.approval_id == approval_id
                    and binding.owner_id == owner_id and binding.chat_id == chat_id
                    and binding.session_id == session_id and binding.project_root == project_root
                    and binding.fingerprint == fingerprint):
                return nonce
        return None

    async def consume_callback(self, callback_data: str, *, owner_id: int, chat_id: int) -> dict[str, Any]:
        parts = str(callback_data or "").split(":")
        if len(parts) != 3 or parts[0] != "vrca" or parts[2] not in {"a", "r"}:
            raise TelegramApprovalError("approval_callback_invalid")
        nonce = parts[1]
        binding = self._bindings.get(nonce)
        if binding is None or binding.expires_at <= self._clock():
            self._bindings.pop(nonce, None)
            raise TelegramApprovalError("approval_callback_expired")
        if binding.owner_id != owner_id or binding.chat_id != chat_id:
            raise TelegramApprovalError("approval_callback_scope")
        self._bindings.pop(nonce, None)  # atomic one-time consume before any await
        rows = await self._port.list_pending(binding.project_root, binding.session_id)
        approval = next((row for row in rows if str(row.get("approvalId") or row.get("id") or "") == binding.approval_id), None)
        if (approval is None or not self._is_pending_scope(approval, binding.project_root, binding.session_id)
                or self._fingerprint(approval) != binding.fingerprint):
            raise TelegramApprovalError("approval_state_changed")
        result = await self._port.decide(binding.approval_id, binding.project_root, parts[2] == "a")
        if not isinstance(result, dict) or result.get("ok") is not True:
            return {"status": "notaccepted", "approvalId": binding.approval_id, "result": result}
        return {"status": "decided", "approvalId": binding.approval_id, "result": result}

    def _new_nonce(self) -> str:
        for _ in range(8):
            nonce = secrets.token_urlsafe(12)
            if nonce not in self._bindings:
                return nonce
        raise TelegramApprovalError("approval_nonce_unavailable")

    def _purge_expired(self) -> None:
        now = self._clock()
        for nonce, binding in list(self._bindings.items()):
            if binding.expires_at <= now:
                self._bindings.pop(nonce, None)

    @staticmethod
    def _validate_identity(owner_id: int, chat_id: int, project_root: str, session_id: str) -> None:
        if type(owner_id) is not int or type(chat_id) is not int or not isinstance(project_root, str) or not isinstance(session_id, str) or not session_id.strip():
            raise TelegramApprovalError("approval_scope_invalid")

    @staticmethod
    def _is_pending_scope(approval: dict[str, Any], project_root: str, session_id: str) -> bool:
        if str(approval.get("status") or "").casefold() != "pending":
            return False
        arguments = approval.get("arguments") if isinstance(approval.get("arguments"), dict) else {}
        task_context = approval.get("taskContext") if isinstance(approval.get("taskContext"), dict) else {}
        candidate_project = next((arguments.get(key) or approval.get(key) or task_context.get(key)
                                  for key in ("projectRoot", "project_root", "projectPath", "project_path")
                                  if arguments.get(key) or approval.get(key) or task_context.get(key)), "")
        if not TelegramApprovalController._same_path(str(candidate_project), project_root):
            return False
        candidate_session = approval.get("sessionId") or approval.get("session_id")
        return bool(candidate_session) and str(candidate_session) == session_id

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        if not left or not right:
            return left == right
        return os.path.normcase(os.path.normpath(os.path.abspath(left))) == os.path.normcase(os.path.normpath(os.path.abspath(right)))

    @classmethod
    def _preview(cls, approval: dict[str, Any]) -> str:
        safe = {key: cls._safe_value(approval.get(key)) for key in ("targetTool", "operationId", "arguments", "taskContext") if key in approval}
        return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:1200]

    @classmethod
    def _card_text(cls, label: str, reason: str, approval: dict[str, Any]) -> str:
        arguments = approval.get("arguments") if isinstance(approval.get("arguments"), dict) else {}
        target = next((arguments.get(key) for key in ("objectPath", "propertyPath", "assetPath")
                       if arguments.get(key)), None)
        operation = str(approval.get("operationId") or "").strip()
        lines = [f"需要审批：{cls._bounded_text(label, 120)}"]
        if target:
            lines.append(f"对象：{cls._bounded_text(target, 240)}")
        if operation:
            lines.append(f"操作：{cls._bounded_text(operation, 160)}")
        details = []
        for key in ("propertyPath", "value", "newValue", "changes"):
            if key in arguments:
                value = cls._safe_value(arguments[key])
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                details.append(f"{key}={cls._bounded_text(rendered, 220)}")
        if details:
            lines.append("参数：" + "；".join(details))
        lines.append(f"原因：{reason}")
        return "\n".join(lines)[:1400]

    @staticmethod
    def _bounded_text(value: Any, limit: int) -> str:
        text = str(value or "").strip()
        return text if len(text) <= limit else text[:limit - 1] + "…"

    @classmethod
    def _fingerprint(cls, approval: dict[str, Any]) -> str:
        safe = {key: cls._safe_value(approval.get(key))
                for key in ("targetTool", "operationId", "arguments", "taskContext") if key in approval}
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _safe_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): ("[redacted]" if any(word in str(key).casefold() for word in ("token", "secret", "password", "key")) else cls._safe_value(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._safe_value(item) for item in value[:20]]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
