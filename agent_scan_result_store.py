"""Private, owner-scoped scan snapshots. Connections exist only during an operation."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_tool_result_reader import result_reference

_PROCESS_BOOT_ID = uuid.uuid4().hex
MAX_STORE_BYTES = 128 * 1024 * 1024
MAX_ASSET_PATHS = 256
MAX_HASH_BYTES = 128 * 1024 * 1024


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("A nonempty authenticated owner identity is required.")
    return value


def _project(value: str | Path, *, global_allowed: bool = False) -> str:
    if not str(value).strip():
        if global_allowed:
            return ""
        raise ValueError("An authenticated project root is required.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Project roots must be absolute owner-supplied paths.")
    resolved = str(path.resolve())
    return resolved.casefold() if os.name == "nt" else resolved


def _asset_hint(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1024 or any(c in value for c in ("\\", ":", "\x00")):
        return None
    parts = value.split("/")
    return value if len(parts) > 1 and parts[0] == "Assets" and all(p not in ("", ".", "..") for p in parts) else None


def _asset_hints(value: Any, key: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for name, child in value.items():
            found.update(_asset_hints(child, str(name).replace("_", "").lower()))
    elif isinstance(value, list):
        for child in value:
            found.update(_asset_hints(child, key))
    elif key.endswith(("path", "paths")):
        hint = _asset_hint(value)
        if hint:
            found.add(hint)
    return found


def describe_scan_scope(step: dict, arguments: dict) -> dict:
    return {"tool": step.get("tool", ""), "arguments": arguments,
            "assetPaths": sorted(_asset_hints(arguments) | _asset_hints(step)),
            "dependencyCoverage": "unverified"}


def _file_fingerprint(path: Path, project: Path, budget: list[int]) -> dict | None:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(project / "Assets") or not resolved.is_file():
            return None
        before = resolved.stat()
        if before.st_size > budget[0]:
            return None
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            while chunk := stream.read(min(1024 * 1024, budget[0] + 1)):
                budget[0] -= len(chunk)
                if budget[0] < 0:
                    return None
                digest.update(chunk)
        after = resolved.stat()
        fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino", "st_dev")
        if any(getattr(before, k) != getattr(after, k) for k in fields):
            return None
        return {"sha256": digest.hexdigest(), "mtime_ns": after.st_mtime_ns, "size": after.st_size}
    except (OSError, ValueError, RuntimeError):
        return None


def _fingerprints(project: str, paths: list[str]) -> dict:
    root, budget = Path(project), [MAX_HASH_BYTES]
    return {p: {"asset": _file_fingerprint(root / p, root, budget),
                "meta": _file_fingerprint(root / (p + ".meta"), root, budget)} for p in paths}


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(part), dict)
        and isinstance(value[part].get("sha256"), str)
        and len(value[part]["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value[part]["sha256"])
        and type(value[part].get("mtime_ns")) is int
        and type(value[part].get("size")) is int
        for part in ("asset", "meta")
    )


class ScanResultStore:
    """Gateway supplies authenticated owners; this store has no public raw-resource route."""

    def __init__(self, root: Path, shared_lock: Any = None) -> None:
        self._path = Path(root) / "scan-results.sqlite3"
        self._lock = shared_lock if shared_lock is not None else threading.RLock()
        self._boot_id = _PROCESS_BOOT_ID

    @contextmanager
    def _connection(self, *, create: bool = False):
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("BEGIN IMMEDIATE")
            if create:
                connection.execute("CREATE TABLE IF NOT EXISTS snapshots (ref TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL, project TEXT NOT NULL, boot TEXT NOT NULL, captured_at TEXT NOT NULL, raw BLOB NOT NULL, scope TEXT NOT NULL, fingerprints TEXT NOT NULL, size INTEGER NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS validity (ref TEXT PRIMARY KEY REFERENCES snapshots(ref) ON DELETE CASCADE, reason TEXT NOT NULL DEFAULT '')")
                connection.execute("CREATE TABLE IF NOT EXISTS mutations (project TEXT PRIMARY KEY, pending INTEGER NOT NULL CHECK(pending >= 0), epoch INTEGER NOT NULL CHECK(epoch >= 0))")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _pending(connection, project: str) -> bool:
        return bool(connection.execute("SELECT 1 FROM mutations WHERE project IN (?, '') AND pending > 0 LIMIT 1", (project,)).fetchone())

    @staticmethod
    def _generation(connection, project: str) -> int:
        return connection.execute("SELECT COALESCE(SUM(epoch), 0) FROM mutations WHERE project IN (?, '')", (project,)).fetchone()[0]

    def generation(self, project_root: str | Path) -> int:
        project = _project(project_root, global_allowed=True)
        with self._lock:
            if not self._path.is_file():
                return 0
            with self._connection() as connection:
                return self._generation(connection, project)

    @staticmethod
    def _expire(connection, ref: str, reason: str) -> None:
        connection.execute("UPDATE validity SET reason=? WHERE ref=? AND reason=''", (reason, ref))

    @staticmethod
    def _expiry(row, reason: str) -> dict:
        return {"expired": True, "reason": reason, "rescanScope": json.loads(row["scope"]), "capturedAt": row["captured_at"]}

    @staticmethod
    def _metadata(row, reason: str = "") -> dict:
        result = {"resultRef": row["ref"], "capturedAt": row["captured_at"], "scanScope": json.loads(row["scope"]),
                  "scope": "current_turn", "freshness": "unverified", "persisted": True}
        if reason:
            result.update(expired=True, reason=reason)
        return result

    def capture(self, session_id: str, turn_id: str, project_root: str | Path, step: dict, arguments: dict,
                *, expected_generation: int | None = None) -> dict:
        session_id, turn_id, project = _owner(session_id), _owner(turn_id), _project(project_root)
        if not isinstance(step, dict) or not isinstance(arguments, dict):
            raise ValueError("Step and owner-supplied arguments must be JSON objects.")
        if expected_generation is not None and (type(expected_generation) is not int or expected_generation < 0):
            raise ValueError("Expected mutation generation must be a nonnegative integer.")
        ref = result_reference(session_id, turn_id, str(project_root), step)
        descriptor = step.get("resultRead")
        if descriptor is not None and (not isinstance(descriptor, dict) or descriptor.get("resultRef") != ref):
            raise ValueError("Owned step result reference does not match its canonical reader reference.")
        raw = _json({"step": step, "arguments": arguments}).encode("utf-8")
        declared_scope = describe_scan_scope(step, arguments)
        paths = declared_scope["assetPaths"]
        if len(raw) > MAX_STORE_BYTES or len(paths) > MAX_ASSET_PATHS:
            raise ValueError("Scan snapshot exceeds the private store size limit.")
        scope = _json(declared_scope)
        with self._lock, self._connection(create=True) as connection:
            reason = "mutation_in_progress" if self._pending(connection, project) else ""
            if not reason and expected_generation is not None and expected_generation != self._generation(connection, project):
                reason = "mutation_during_scan"
            old = connection.execute("SELECT * FROM snapshots WHERE ref=?", (ref,)).fetchone()
            if old is not None:
                if (old["raw"], old["scope"]) != (raw, scope):
                    raise ValueError("Scan result reference collision with a different immutable snapshot.")
                if reason:
                    self._expire(connection, ref, reason)
            else:
                observed = _fingerprints(project, paths)
                if not reason and any(not _valid_fingerprint(observed[p]) for p in paths):
                    reason = "fingerprint_unavailable"
                fingerprints = _json(observed)
                size = len(raw) + len(scope.encode()) + len(fingerprints.encode())
                total = connection.execute("SELECT COALESCE(SUM(size), 0) FROM snapshots").fetchone()[0]
                if total + size > MAX_STORE_BYTES:
                    raise ValueError("Private scan result store exceeds its 128 MiB payload limit.")
                connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ref, session_id, turn_id, project, self._boot_id, datetime.now(timezone.utc).isoformat(), raw, scope, fingerprints, size))
                connection.execute("INSERT INTO validity(ref, reason) VALUES (?, ?)",
                    (ref, reason))
            row = connection.execute("SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=?", (ref,)).fetchone()
            metadata = self._metadata(row, row["reason"])
        return metadata

    def lookup(self, session_id: str, turn_id: str, project_root: str | Path, result_ref: str) -> dict | None:
        session_id, turn_id, project = _owner(session_id), _owner(turn_id), _project(project_root)
        if not self._path.is_file():
            return None
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT s.*, v.reason FROM snapshots s JOIN validity v USING(ref) WHERE ref=? AND session=? AND project=?", (result_ref, session_id, project)).fetchone()
            if row is None:
                return None
            reason = row["reason"]
            if not reason and self._pending(connection, project):
                reason = "mutation_in_progress"
            if not reason and row["boot"] != self._boot_id:
                reason = "runtime_observation_gap"
            if not reason and row["turn"] != turn_id:
                reason = "core_scope_unavailable"
            if not reason:
                try:
                    paths = json.loads(row["scope"])["assetPaths"]
                    saved = json.loads(row["fingerprints"])
                    if not isinstance(paths, list) or any(_asset_hint(p) != p for p in paths) or not isinstance(saved, dict) or set(saved) != set(paths) or any(not _valid_fingerprint(saved[p]) for p in paths):
                        reason = "fingerprint_unavailable"
                    else:
                        current = _fingerprints(project, paths)
                        if any(not _valid_fingerprint(current[p]) for p in paths):
                            reason = "fingerprint_unavailable"
                        elif current != saved:
                            reason = "asset_fingerprint_changed"
                except (ValueError, KeyError, TypeError):
                    reason = "fingerprint_unavailable"
            if reason:
                self._expire(connection, result_ref, reason)
                result = self._expiry(row, reason)
            else:
                result = {"step": json.loads(row["raw"])["step"]}
        return result

    @staticmethod
    def _invalidate(connection, project: str, reason: str) -> None:
        # Dependency closure is unverified: even disjoint project writes expire Unity snapshots.
        condition, params = ("", ()) if not project else (" WHERE project=?", (project,))
        connection.execute("UPDATE validity SET reason=? WHERE reason='' AND ref IN (SELECT ref FROM snapshots" + condition + ")", (reason, *params))

    def invalidate(self, project_root: str | Path, paths: Iterable[str] | None, reason: str) -> None:
        project, reason = _project(project_root, global_allowed=True), _owner(reason)
        with self._lock, self._connection(create=True) as connection:
            connection.execute("INSERT INTO mutations VALUES (?, 0, 1) ON CONFLICT(project) DO UPDATE SET epoch=epoch+1", (project,))
            self._invalidate(connection, project, reason)

    def begin_mutation(self, project_root: str | Path, reason: str) -> None:
        project, reason = _project(project_root, global_allowed=True), _owner(reason)
        with self._lock, self._connection(create=True) as connection:
            connection.execute("INSERT INTO mutations VALUES (?, 1, 1) ON CONFLICT(project) DO UPDATE SET pending=pending+1, epoch=epoch+1", (project,))
            self._invalidate(connection, project, reason)

    def end_mutation(self, project_root: str | Path, reason: str) -> None:
        project, reason = _project(project_root, global_allowed=True), _owner(reason)
        with self._lock, self._connection(create=True) as connection:
            self._invalidate(connection, project, reason)
            connection.execute("INSERT INTO mutations VALUES (?, 0, 1) ON CONFLICT(project) DO UPDATE SET pending=MAX(0, pending-1), epoch=epoch+1", (project,))

    def delete(self, session_id: str) -> None:
        session_id = _owner(session_id)
        if self._path.is_file():
            with self._lock, self._connection() as connection:
                connection.execute("DELETE FROM snapshots WHERE session=?", (session_id,))
