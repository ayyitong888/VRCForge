"""Private, gateway-owned native history; never an approval or execution queue.

Uses the gateway's local audit root and authenticated session boundary. No new
network interface or open handle is retained. Files live until session deletion;
process shutdown does not delete them. Callers serialize access with their lock.
"""
from __future__ import annotations

import hashlib
import json
import os
from atomic_file_replace import replace_with_retry
import tempfile
import time
from pathlib import Path
from typing import Any


class NativeHistoryPersistenceError(OSError):
    """A local durable-history failure, never a Provider transport failure."""


class NativeHistoryStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _paths(self, session_id: str) -> list[Path]:
        prefix = hashlib.sha256(session_id.encode("utf-8")).hexdigest() + "-"
        return [p for p in self.directory.glob(prefix + "*.json")
                if len(p.stem) == 129 and all(c in "0123456789abcdef" for c in p.stem[65:])]

    def _path(self, session_id: str, binding: str) -> Path:
        session = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        route = hashlib.sha256(binding.encode("utf-8")).hexdigest()
        return self.directory / (session + "-" + route + ".json")

    def load(self, session_id: str, binding: str | None = None) -> dict[str, Any] | None:
        if binding is None:
            paths = self._paths(session_id)
            if not paths:
                return None
            path = max(paths, key=lambda p: p.stat().st_mtime_ns)
        else:
            path = self._path(session_id, binding)
        try:
            with path.open("rb") as stream:
                raw = stream.read()
        except FileNotFoundError:
            return None
        # Parsing can be expensive; do not hold a Windows read handle that
        # prevents another process from atomically replacing this snapshot.
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("version") != 1 or value.get("sessionId") != session_id:
            raise ValueError("Saved native history identity is invalid")
        if binding is not None and value.get("native", {}).get("binding") != binding:
            raise ValueError("Saved native history binding is invalid")
        return value

    def save(self, session_id: str, snapshot: dict[str, Any], selections: dict[str, Any]) -> None:
        raw = json.dumps({"version": 1, "sessionId": session_id, "native": snapshot,
                          "selections": selections}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.directory.mkdir(parents=True, exist_ok=True)
        # mkstemp owns a single private handle; close before atomic replacement.
        fd, temporary = tempfile.mkstemp(prefix=".native-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            target = self._path(session_id, snapshot["binding"])
            try:
                # Retry only the same already-written private file, never
                # a Provider request or a tool whose side effects occurred.
                replace_with_retry(temporary, target)
            except OSError as exc:
                raise NativeHistoryPersistenceError("Could not persist local native history.") from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save_approval(self, approval_id: str, session_id: str, snapshot: dict[str, Any], selections: dict[str, Any]) -> dict[str, Any]:
        """Freeze one approval's native context under its approval identity."""
        approval_id = str(approval_id or "").strip()
        if not approval_id or not session_id or not isinstance(snapshot, dict):
            raise ValueError("approval native history identity is invalid")
        target = self.directory / ("approval-" + hashlib.sha256(approval_id.encode("utf-8")).hexdigest() + ".json")
        if target.exists():
            existing = json.loads(target.read_bytes())
            if existing.get("approvalId") != approval_id or existing.get("sessionId") != session_id:
                raise NativeHistoryPersistenceError("Approval native history identity is invalid.")
            return {"schema": "vrcforge.native-history-ref.v1", "approvalId": approval_id,
                    "sessionId": session_id, "binding": str(existing.get("native", {}).get("binding") or "")}
        raw = json.dumps({"version": 1, "approvalId": approval_id, "sessionId": session_id,
                          "native": snapshot, "selections": selections}, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".approval-native-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return {"schema": "vrcforge.native-history-ref.v1", "approvalId": approval_id,
                    "sessionId": session_id, "binding": str(snapshot.get("binding") or "")}
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load_approval(self, reference: dict[str, Any]) -> dict[str, Any] | None:
        approval_id = str(reference.get("approvalId") or "").strip() if isinstance(reference, dict) else ""
        if not approval_id:
            return None
        path = self.directory / ("approval-" + hashlib.sha256(approval_id.encode("utf-8")).hexdigest() + ".json")
        try:
            value = json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        if value.get("version") != 1 or value.get("approvalId") != approval_id:
            raise ValueError("Saved approval native history identity is invalid")
        if str(reference.get("sessionId") or "") != value.get("sessionId"):
            raise ValueError("Saved approval native history session is invalid")
        native = value.get("native")
        if not isinstance(native, dict) or str(reference.get("binding") or "") != native.get("binding"):
            raise ValueError("Saved approval native history binding is invalid")
        return value

    def delete(self, session_id: str) -> None:
        for path in self._paths(session_id):
            path.unlink(missing_ok=True)
        for path in self.directory.glob("approval-*.json"):
            try:
                value = json.loads(path.read_bytes())
            except (FileNotFoundError, OSError, UnicodeError, ValueError):
                continue
            if value.get("sessionId") == session_id:
                path.unlink(missing_ok=True)
