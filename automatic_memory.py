"""Local, deterministic automatic Memory admission; no provider or network calls."""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from memory_consolidation_sources import (
    MemoryScope,
    SourceProjection,
    admit_memory_sources,
    project_scope_key,
    redact_memory_text,
)
from memory_review_inputs import collect_user_chat_records
from memory_safety import memory_text_is_instruction_sensitive


AUTOMATIC_MEMORY_POLICY_VERSION = "automatic-direct-v1"
_SCHEMA = "vrcforge.automatic_memory_policy.v1"
_REJECT = re.compile(
    r"\b(?:api[ _-]?key|secret|token|password|credential|https?://|permission|approval|"
    r"authorize|allow|grant|execute|run|delete|write|action|system|developer)\b|"
    r"(?:密钥|密鑰|令牌|權杖|密码|密碼|凭证|憑證|秘密|权限|權限|审批|審批|批准|授权|授權|"
    r"执行|執行|运行|運行|删除|刪除|写入|寫入|工具|命令|终端|終端)|"
    r"(?:キー|トークン|パスワード|認証情報|秘密|権限|承認|許可|実行|削除|書き込み|ツール|コマンド)",
    re.I,
)
_REMEMBER_PREFIX = re.compile(
    r"^\s*(?:(?:请|請|please)\s*)?(?:记住|記住|记下|記下|remember(?:\s+(?:that|this))?|覚えて)\s*[：:,，]?\s*",
    re.IGNORECASE,
)


def _turn_has_verified_memory_receipt(
    items: Sequence[Any], index: int, scope: MemoryScope, accepted: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Only a same-turn tool receipt plus accepted-store readback suppresses capture."""
    user_text = str(items[index].get("text") or "")
    exact_texts = {" ".join(user_text.split()), " ".join(_REMEMBER_PREFIX.sub("", user_text, count=1).split())}
    for later in items[index + 1:]:
        if not isinstance(later, Mapping):
            continue
        if later.get("type") == "user":
            break
        if later.get("type") != "agent" or later.get("status", "completed") != "completed":
            continue
        response = later.get("response")
        if not isinstance(response, Mapping) or response.get("ok") is not True or not isinstance(response.get("plan"), Mapping):
            continue
        steps = response.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping) or step.get("kind") != "write" or step.get("tool") != "vrcforge_remember_memory" or step.get("status") != "executed":
                continue
            receipt = step.get("result")
            if not isinstance(receipt, Mapping) or receipt.get("ok") is not True or receipt.get("status") != "saved" or receipt.get("committed") is not True or receipt.get("completionKnown") is not True:
                continue
            verification = receipt.get("verification")
            if not isinstance(verification, Mapping) or verification.get("state") != "passed":
                continue
            checks = verification.get("checks")
            if not isinstance(checks, list) or not any(isinstance(check, Mapping) and check.get("kind") == "accepted_memory_readback" and check.get("state") == "passed" for check in checks):
                continue
            memory = accepted.get(str(receipt.get("memoryId") or ""))
            if memory is None or receipt.get("scope") != scope.kind or memory.get("scope") != scope.kind:
                continue
            if scope.kind == "project":
                try:
                    if project_scope_key(str(memory.get("projectRoot") or ""), require_existing=False) != scope.scope_key:
                        continue
                except (OSError, RuntimeError, ValueError):
                    continue
            elif memory.get("projectRoot"):
                continue
            stored_text = " ".join(str(memory.get("text") or "").split())
            if stored_text and stored_text in exact_texts:
                return True
    return False


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class AutomaticMemoryPolicyError(RuntimeError):
    """The independent watermark cannot safely be read or written."""


class AutomaticMemoryPolicy:
    """App-owned atomic watermark file; each file handle ends inside its call."""

    def __init__(self, path: Path | Callable[[], Path], lock: Any) -> None:
        self._path_source = path
        self._lock = lock

    @property
    def path(self) -> Path:
        return self._path_source() if callable(self._path_source) else self._path_source

    def _read(self) -> str | None:
        path = self.path
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AutomaticMemoryPolicyError("Automatic Memory policy is unreadable.") from exc
        if not os.path.isfile(path) or os.path.islink(path) or status.st_size > 4096:
            raise AutomaticMemoryPolicyError("Automatic Memory policy is unsafe.")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AutomaticMemoryPolicyError("Automatic Memory policy is invalid.") from exc
        if not isinstance(value, dict) or set(value) != {"schema", "enabledAt"}:
            raise AutomaticMemoryPolicyError("Automatic Memory policy is invalid.")
        if value.get("schema") != _SCHEMA or _parse(value.get("enabledAt")) is None:
            raise AutomaticMemoryPolicyError("Automatic Memory policy is invalid.")
        return str(value["enabledAt"])

    def ensure(self) -> str:
        with self._lock:
            current = self._read()
            if current is not None:
                return current
            path = self.path
            value = {"schema": _SCHEMA, "enabledAt": datetime.now(timezone.utc).isoformat()}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    json.dump(value, handle, ensure_ascii=False, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except OSError as exc:
                raise AutomaticMemoryPolicyError("Automatic Memory watermark could not be saved.") from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return str(value["enabledAt"])

    def reenable(self) -> str:
        with self._lock:
            path = self.path
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise AutomaticMemoryPolicyError("Automatic Memory watermark could not be reset.") from exc
            return self.ensure()


def collect_automatic_chat_sources(
    chats: Iterable[Mapping[str, Any]],
    *,
    scope: MemoryScope,
    project_root: str,
    enabled_at: str,
    accepted_memories: Iterable[Mapping[str, Any]] = (),
) -> list[SourceProjection]:
    """Keep only new, short, unmodified direct preference/fact sources."""

    watermark = _parse(enabled_at)
    if watermark is None:
        return []
    latest_allowed = datetime.now(timezone.utc) + timedelta(minutes=5)
    accepted = {str(item.get("memoryId") or ""): item for item in accepted_memories if isinstance(item, Mapping)}
    scoped_chats: list[Mapping[str, Any]] = []
    for chat in chats:
        if not isinstance(chat, Mapping):
            continue
        if scope.kind == "project":
            embedded_project = str(chat.get("projectPath") or chat.get("projectRoot") or "").strip()
            try:
                if (
                    not embedded_project
                    or project_scope_key(embedded_project) != scope.scope_key
                    or project_scope_key(project_root) != scope.scope_key
                ):
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
        items = chat.get("items")
        if accepted and isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            # Preserve user turn boundaries, including skipped turns; never edit the transcript.
            projected_items = [
                {**item, "text": ""}
                if isinstance(item, Mapping) and item.get("type") == "user"
                and _turn_has_verified_memory_receipt(items, index, scope, accepted)
                else item
                for index, item in enumerate(items)
            ]
            scoped_chats.append({**chat, "items": projected_items})
        else:
            scoped_chats.append(chat)

    kept: list[dict[str, Any]] = []
    for record in collect_user_chat_records(scoped_chats, scope=scope.kind, project_root=project_root):
        text = str(record.get("text") or "").strip()
        created = _parse(record.get("createdAt"))
        redacted, report = redact_memory_text(text, limit=320)
        if (
            created is None
            or created <= watermark
            or created > latest_allowed
            or record.get("signalKind") not in {"preference", "fact"}
            or record.get("hasAttachments")
            or not text
            or len(text) > 320
            or "?" in text
            or "？" in text
            or redacted != text
            or report.get("total")
            or _REJECT.search(text)
            or memory_text_is_instruction_sensitive(text)
        ):
            continue
        kept.append(record)
    return admit_memory_sources(kept, scope=scope)[0]
