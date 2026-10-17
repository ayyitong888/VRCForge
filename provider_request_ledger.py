"""Durable, content-free ledger for one physical provider request.

The transport owns request timing and response extraction; this leaf module
owns only safe identity/usage projection and append-only persistence.  It does
not create provider requests, retry SDK calls, execute tools, or make approval
decisions.
"""
from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping


LEDGER_SCHEMA = "vrcforge.provider_request.v1"
PHASES = ("找工具", "绑定", "预览", "审批", "恢复")
_SAFE_METADATA = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")
_ACTIVE_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("vrcforge_provider_request_ledger", default=None)


def _safe_text(value: Any, limit: int = 240) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def safe_id(value: Any, limit: int = 180) -> str:
    return _safe_text(value, limit)


def current_scope() -> Mapping[str, Any]:
    return _ACTIVE_SCOPE.get() or {}


@contextmanager
def ledger_scope(ledger: "ProviderRequestLedger", context: Mapping[str, Any] | None = None,
                 request_ordinal: int | None = None):
    token = _ACTIVE_SCOPE.set({
        "ledger": ledger,
        "context": dict(context or {}),
        "requestOrdinal": request_ordinal,
    })
    try:
        yield ledger
    finally:
        _ACTIVE_SCOPE.reset(token)


def _safe_metadata(value: Any) -> str:
    """Keep registry/protocol labels while excluding prompt-like free text."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()[:80]
    return candidate if _SAFE_METADATA.fullmatch(candidate) else ""


def usage_row(usage: Mapping[str, Any] | None) -> dict[str, int | None]:
    """Normalize usage without turning absent provider fields into zero."""
    source = usage if isinstance(usage, Mapping) else {}

    def value(*keys: str) -> int | None:
        for key in keys:
            candidate = source.get(key)
            if type(candidate) is int and candidate >= 0:
                return candidate
        return None

    return {
        "inputTokens": value("input_tokens", "inputTokens"),
        "outputTokens": value("output_tokens", "outputTokens"),
        "totalTokens": value("total_tokens", "totalTokens"),
        "reasoningTokens": value("reasoning_tokens", "reasoningTokens"),
        "cacheReadTokens": value("cached_tokens", "cacheReadTokens"),
        "cacheWriteTokens": value("cache_write_tokens", "cacheWriteTokens"),
    }


def _safe_composition(value: Any) -> Any:
    numeric_keys = {
        "count", "chars", "bytes", "contentChars", "contentBytes", "itemCount",
        "definitionCount", "serializedChars", "serializedBytes", "fieldChars",
        "fieldBytes", "offset", "returnedChars", "totalChars", "returnedItems",
    }
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if isinstance(child, str):
                if name.lower().endswith("sha256") and len(child) == 64 and all(char in "0123456789abcdef" for char in child.casefold()):
                    projected[name] = child
            elif type(child) is bool or (type(child) is int and name in numeric_keys):
                projected[name] = child
            elif isinstance(child, (Mapping, list, tuple)):
                projected[name] = _safe_composition(child)
        return projected
    if isinstance(value, (list, tuple)):
        return [_safe_composition(item) for item in value]
    return None


def safe_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only joinable, content-free request context."""
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, Any] = {}
    aliases = {
        "sessionId": ("sessionId", "session_id"),
        "turnId": ("turnId", "turn_id", "parentTurnId"),
        "clientTurnId": ("clientTurnId", "client_turn_id", "taskId"),
        "taskId": ("taskId", "task_id"),
        "parentTurnId": ("parentTurnId", "parent_turn_id", "turnId", "turn_id"),
        "approvalId": ("approvalId", "approval_id"),
        "provider": ("provider",),
        "model": ("model",),
        "providerRequestId": ("providerRequestId", "provider_request_id"),
        "localDispatchId": ("localDispatchId", "local_dispatch_id"),
    }
    for target, keys in aliases.items():
        for key in keys:
            candidate = safe_id(source.get(key))
            if candidate:
                result[target] = candidate
                break
    ordinal = source.get("requestOrdinal", source.get("request_ordinal"))
    if type(ordinal) is int and ordinal > 0:
        result["requestOrdinal"] = ordinal
    phase = source.get("phase") or source.get("primaryPhase")
    if isinstance(phase, str) and phase in PHASES:
        result["primaryPhase"] = phase
    for target in ("permission", "effect", "actionKind", "intent", "stage"):
        candidate = _safe_metadata(source.get(target))
        if candidate:
            result[target] = candidate
    raw_tool_metadata = source.get("toolMetadata", source.get("tool_metadata"))
    if isinstance(raw_tool_metadata, Mapping):
        tool_metadata: dict[str, dict[str, str]] = {}
        for raw_name, raw_meta in raw_tool_metadata.items():
            name = safe_id(raw_name, 180)
            if not name or not isinstance(raw_meta, Mapping):
                continue
            projected: dict[str, str] = {}
            for key in ("permission", "effect", "actionKind", "intent", "stage"):
                candidate = _safe_metadata(raw_meta.get(key))
                if candidate:
                    projected[key] = candidate
            if projected:
                tool_metadata[name] = projected
        if tool_metadata:
            result["toolMetadata"] = tool_metadata
    for key in ("intendedPhase", "intentPhase", "phaseHint"):
        candidate = source.get(key)
        if isinstance(candidate, str) and candidate in PHASES:
            result["primaryPhase"] = candidate
            break
    phases = source.get("phases")
    if isinstance(phases, (list, tuple)):
        selected = [item for item in phases if isinstance(item, str) and item in PHASES]
        if selected:
            result["phases"] = list(dict.fromkeys(selected))
    approval_context = source.get("approvalTaskContext", source.get("approval_task_context"))
    if isinstance(approval_context, Mapping):
        approval_ordinal = approval_context.get("firstApprovalRequestOrdinal")
        if type(approval_ordinal) is int and approval_ordinal > 0:
            result["firstApprovalRequestOrdinal"] = approval_ordinal
        approval_ids = approval_context.get("firstApprovalRequestIds", approval_context.get("requestIds"))
        if isinstance(approval_ids, (list, tuple)):
            ids = [safe_id(item) for item in approval_ids if safe_id(item)]
            if ids:
                result["firstApprovalRequestIds"] = list(dict.fromkeys(ids))
    composition = source.get("nativeComposition")
    if isinstance(composition, Mapping):
        try:
            projected = _safe_composition(composition)
            encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) <= 256 * 1024:
                result["nativeComposition"] = projected
        except (TypeError, ValueError, OverflowError):
            pass
    return result


def safe_tool_calls(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    values = value if isinstance(value, list) else []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        name = safe_id(item.get("name") or item.get("tool") or item.get("toolName"), 180)
        call_id = safe_id(item.get("id") or item.get("call_id") or item.get("callId"), 180)
        if name or call_id:
            row = {"id": call_id, "name": name}
            action = safe_id(item.get("action"), 80)
            if action:
                row["action"] = action
            metadata = item.get("_meta") if isinstance(item.get("_meta"), Mapping) else {}
            for key in ("permission", "effect", "actionKind", "intent", "stage"):
                value = _safe_metadata(item.get(key) or metadata.get(key))
                if value:
                    row[key] = value
            rows.append(row)
    return rows


def classify_request_phases(tool_calls: Any, context: Mapping[str, Any] | None = None) -> tuple[list[str], str | None]:
    """Look up actual tool names only; caller/model phase hints have no authority."""
    from provider_tool_phases import PHASE_BY_TOOL

    phases = list(dict.fromkeys(
        PHASE_BY_TOOL.get(row["name"], "unknown") for row in safe_tool_calls(tool_calls)
    )) or ["unknown"]
    return phases, phases[0]


class ProviderRequestLedger:
    """Append-only physical-request rows with fsync and idempotent event IDs."""

    def __init__(self, path: str | Path, *, lock: Any | None = None) -> None:
        self.path = Path(path)
        self._lock = lock or threading.RLock()

    @staticmethod
    def event_id(row_id: str, phase: str) -> str:
        return f"{row_id}:{phase}"

    def append(self, row: Mapping[str, Any]) -> bool:
        payload = dict(row)
        row_id = safe_id(payload.get("rowId"), 180)
        event_id = safe_id(payload.get("eventId"), 220)
        if not row_id or not event_id:
            return False
        payload.setdefault("schema", LEDGER_SCHEMA)
        payload.setdefault("createdAt", time.time())
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    for line in self.path.read_text(encoding="utf-8").splitlines():
                        try:
                            existing = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(existing, Mapping) and existing.get("eventId") == event_id:
                            return False
                except OSError:
                    pass
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                try:
                    import os
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        return True

    def read_rows(self, *, limit: int = 0) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        by_row: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        seen_events: set[str] = set()
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            row_id = safe_id(value.get("rowId"), 180)
            event_id = safe_id(value.get("eventId"), 220)
            if not row_id or not event_id or event_id in seen_events:
                continue
            seen_events.add(event_id)
            if row_id not in by_row:
                order.append(row_id)
                by_row[row_id] = {}
            by_row[row_id] = {**by_row[row_id], **value}
        rows = [by_row[row_id] for row_id in order]
        return rows if limit <= 0 else rows[-max(1, min(int(limit), 2000)) :]
