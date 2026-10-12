"""Durable, single-owner state for a Telegram bot service.

The service owns one store instance and its synchronous sqlite connection.  The
store has no worker, executor, token, network call, retry loop, or exactly-once
claim: callers decide when to dispatch and how to handle unknown outcomes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class TelegramStoreError(Exception):
    """Base class for safe, classifiable store errors."""


class TelegramStoreFullError(TelegramStoreError):
    """The requested bounded queue has no capacity."""

    def __init__(self, queue: str) -> None:
        self.queue = queue
        super().__init__(f"telegram {queue} capacity is full")


@dataclass(frozen=True)
class AdmitResult:
    admitted: bool
    duplicate: bool
    offset: int | None


@dataclass(frozen=True)
class PendingUpdate:
    update_id: int
    payload: Any


@dataclass(frozen=True)
class OutboxItem:
    key: str
    payload: Any
    status: str


class TelegramBotStore:
    """Short-transaction sqlite state owned by one backend service."""

    def __init__(
        self,
        path: str | Path,
        bot_identity: str,
        *,
        max_pending: int = 1000,
        max_outbox: int = 1000,
        max_completed: int = 1000,
    ) -> None:
        if not bot_identity:
            raise ValueError("bot_identity is required")
        if max_pending <= 0 or max_outbox <= 0 or max_completed <= 0:
            raise ValueError("queue capacities must be positive")
        self.bot_identity = bot_identity
        self.max_pending = max_pending
        self.max_outbox = max_outbox
        self.max_completed = max_completed
        self._closed = False
        self._connection = sqlite3.connect(str(path), isolation_level=None)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_updates (
                bot_identity TEXT NOT NULL,
                update_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'done')),
                PRIMARY KEY (bot_identity, update_id)
            );
            CREATE TABLE IF NOT EXISTS telegram_offsets (
                bot_identity TEXT PRIMARY KEY,
                offset INTEGER
            );
            CREATE TABLE IF NOT EXISTS telegram_routes (
                bot_identity TEXT NOT NULL,
                route TEXT NOT NULL,
                project_root TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (bot_identity, route)
            );
            CREATE TABLE IF NOT EXISTS telegram_outbox (
                bot_identity TEXT NOT NULL,
                item_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued', 'claimed', 'sent', 'unknown', 'failed', 'retry')),
                PRIMARY KEY (bot_identity, item_key)
            );
            CREATE TABLE IF NOT EXISTS telegram_watches (
                bot_identity TEXT NOT NULL,
                route TEXT NOT NULL,
                project_root TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (bot_identity, session_id)
            );
            CREATE TABLE IF NOT EXISTS telegram_dispatch_journal (
                bot_identity TEXT NOT NULL,
                update_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                PRIMARY KEY (bot_identity, update_id)
            );
            CREATE TABLE IF NOT EXISTS telegram_project_requests (
                bot_identity TEXT NOT NULL,
                route TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (bot_identity, route)
            );
            """
        )
        # A process crash after claim leaves the send outcome unknown. Never
        # silently turn that into a resend on the next owner lifecycle.
        with self._transaction():
            self._connection.execute(
                "UPDATE telegram_outbox SET status='unknown' WHERE bot_identity=? AND status='claimed'",
                (self.bot_identity,),
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise TelegramStoreError("telegram store is closed")

    class _Transaction:
        def __init__(self, store: "TelegramBotStore") -> None:
            self.store = store

        def __enter__(self):
            self.store._ensure_open()
            self.store._connection.execute("BEGIN IMMEDIATE")
            return self.store._connection

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is None:
                self.store._connection.execute("COMMIT")
            else:
                self.store._connection.execute("ROLLBACK")
            return False

    def _transaction(self) -> "TelegramBotStore._Transaction":
        return self._Transaction(self)

    def get_offset(self) -> int | None:
        self._ensure_open()
        row = self._connection.execute(
            "SELECT offset FROM telegram_offsets WHERE bot_identity=?", (self.bot_identity,)
        ).fetchone()
        return None if row is None else row[0]

    def get_binding(self, route: str) -> dict[str, str] | None:
        self._ensure_open()
        if not route:
            raise ValueError("route is required")
        row = self._connection.execute(
            "SELECT project_root, session_id FROM telegram_routes WHERE bot_identity=? AND route=?",
            (self.bot_identity, route),
        ).fetchone()
        return None if row is None else {"project_root": row[0], "session_id": row[1]}

    def watch_session(self, route: str, project_root: str, session_id: str) -> bool:
        """Persist a notification destination before task dispatch, owned by this bot."""
        if not route or not isinstance(project_root, str) or not session_id:
            raise ValueError("complete watch scope is required")
        with self._transaction():
            row = self._connection.execute("SELECT route, project_root FROM telegram_watches WHERE bot_identity=? AND session_id=?",
                (self.bot_identity, session_id)).fetchone()
            if row is not None:
                return row == (route, project_root)
            count = self._connection.execute("SELECT COUNT(*) FROM telegram_watches WHERE bot_identity=?", (self.bot_identity,)).fetchone()[0]
            if count >= 100:
                return False
            self._connection.execute("INSERT INTO telegram_watches VALUES (?, ?, ?, ?)",
                (self.bot_identity, route, project_root, session_id))
        return True

    def watches(self) -> list[dict[str, str]]:
        self._ensure_open()
        return [{"route": row[0], "project_root": row[1], "session_id": row[2]} for row in
            self._connection.execute("SELECT route, project_root, session_id FROM telegram_watches WHERE bot_identity=? LIMIT 100", (self.bot_identity,)).fetchall()]

    def set_binding(self, route: str, project_root: str, session_id: str) -> None:
        self._ensure_open()
        if not route or not isinstance(project_root, str) or not isinstance(session_id, str):
            raise ValueError("route and project_root are required; session_id must be text")
        with self._transaction():
            self._connection.execute(
                "INSERT INTO telegram_routes(bot_identity, route, project_root, session_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(bot_identity, route) DO UPDATE SET project_root=excluded.project_root, session_id=excluded.session_id",
                (self.bot_identity, route, project_root, session_id),
            )

    def project_request(self, route: str) -> dict | None:
        self._ensure_open()
        row = self._connection.execute(
            "SELECT payload FROM telegram_project_requests WHERE bot_identity=? AND route=?",
            (self.bot_identity, route),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_project_request(self, route: str, payload: dict) -> None:
        """One pending choice per owner route; the service owns and validates it."""
        if not route or not isinstance(payload, dict):
            raise ValueError("project request scope required")
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 24000:
            raise ValueError("project request too large")
        with self._transaction():
            self._connection.execute(
                "INSERT INTO telegram_project_requests VALUES (?, ?, ?) "
                "ON CONFLICT(bot_identity, route) DO UPDATE SET payload=excluded.payload",
                (self.bot_identity, route, encoded),
            )

    def clear_project_request(self, route: str) -> None:
        with self._transaction():
            self._connection.execute("DELETE FROM telegram_project_requests WHERE bot_identity=? AND route=?",
                                     (self.bot_identity, route))

    def bind_selected_project(self, route: str, chat_id: str, runtime_session: str, project: str) -> bool:
        """Finish an explicitly selected scope change, without retargeting another chat."""
        if not project or not runtime_session:
            return False
        with self._transaction():
            row = self._connection.execute(
                "SELECT project_root, session_id FROM telegram_routes WHERE bot_identity=? AND route=?",
                (self.bot_identity, route),
            ).fetchone()
            if row not in {( "", chat_id), (project, chat_id)}:
                return False
            watch = self._connection.execute(
                "SELECT route, project_root FROM telegram_watches WHERE bot_identity=? AND session_id=?",
                (self.bot_identity, runtime_session),
            ).fetchone()
            if watch not in {(route, ""), (route, project)}:
                return False
            self._connection.execute("UPDATE telegram_routes SET project_root=? WHERE bot_identity=? AND route=?",
                                     (project, self.bot_identity, route))
            self._connection.execute("UPDATE telegram_watches SET project_root=? WHERE bot_identity=? AND session_id=?",
                                     (project, self.bot_identity, runtime_session))
        return True

    def admit_update(self, update_id: int, payload: Any) -> AdmitResult:
        self._ensure_open()
        if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
            raise ValueError("update_id must be a non-negative integer")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._transaction():
            existing = self._connection.execute(
                "SELECT status FROM telegram_updates WHERE bot_identity=? AND update_id=?",
                (self.bot_identity, update_id),
            ).fetchone()
            if existing is not None:
                return AdmitResult(False, True, self._offset_in_transaction())
            current = self._offset_in_transaction()
            if current is not None and update_id < current:
                return AdmitResult(False, True, current)
            pending = self._connection.execute(
                "SELECT COUNT(*) FROM telegram_updates WHERE bot_identity=? AND status='pending'",
                (self.bot_identity,),
            ).fetchone()[0]
            if pending >= self.max_pending:
                raise TelegramStoreFullError("pending")
            self._connection.execute(
                "INSERT INTO telegram_updates(bot_identity, update_id, payload, status) VALUES (?, ?, ?, 'pending')",
                (self.bot_identity, update_id, encoded),
            )
            current = self._offset_in_transaction()
            next_offset = max(update_id + 1, current or 0)
            self._connection.execute(
                "INSERT INTO telegram_offsets(bot_identity, offset) VALUES (?, ?) "
                "ON CONFLICT(bot_identity) DO UPDATE SET offset=excluded.offset",
                (self.bot_identity, next_offset),
            )
            return AdmitResult(True, False, next_offset)

    def skip_update(self, update_id: int) -> AdmitResult:
        """Persist an ignored update without retaining its potentially sensitive payload."""
        self._ensure_open()
        if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
            raise ValueError("update_id must be a non-negative integer")
        with self._transaction():
            existing = self._connection.execute(
                "SELECT status FROM telegram_updates WHERE bot_identity=? AND update_id=?",
                (self.bot_identity, update_id),
            ).fetchone()
            if existing is not None:
                return AdmitResult(False, True, self._offset_in_transaction())
            current = self._offset_in_transaction()
            next_offset = max(update_id + 1, current or 0)
            self._connection.execute(
                "INSERT INTO telegram_updates(bot_identity, update_id, payload, status) VALUES (?, ?, '{}', 'done')",
                (self.bot_identity, update_id),
            )
            self._connection.execute(
                "INSERT INTO telegram_offsets(bot_identity, offset) VALUES (?, ?) "
                "ON CONFLICT(bot_identity) DO UPDATE SET offset=excluded.offset",
                (self.bot_identity, next_offset),
            )
            self._prune_completed_in_transaction()
            return AdmitResult(True, False, next_offset)

    def _offset_in_transaction(self) -> int | None:
        row = self._connection.execute(
            "SELECT offset FROM telegram_offsets WHERE bot_identity=?", (self.bot_identity,)
        ).fetchone()
        return None if row is None else row[0]

    def next_pending(self, *, limit: int = 100) -> list[PendingUpdate]:
        self._ensure_open()
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            "SELECT update_id, payload FROM telegram_updates "
            "WHERE bot_identity=? AND status='pending' ORDER BY update_id LIMIT ?",
            (self.bot_identity, limit),
        ).fetchall()
        return [PendingUpdate(row[0], json.loads(row[1])) for row in rows]

    def mark_dispatched(self, update_id: int) -> bool:
        self._ensure_open()
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE telegram_updates SET status='done' WHERE bot_identity=? AND update_id=? AND status='pending'",
                (self.bot_identity, update_id),
            )
            self._prune_completed_in_transaction()
            return cursor.rowcount > 0

    def record_dispatch(self, update_id: int, result: Any) -> bool:
        """Durably finish business dispatch before attempting notification delivery."""
        self._ensure_open()
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self._transaction():
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO telegram_dispatch_journal(bot_identity, update_id, result) VALUES (?, ?, ?)",
                (self.bot_identity, update_id, encoded),
            )
            self._connection.execute(
                "UPDATE telegram_updates SET status='done' WHERE bot_identity=? AND update_id=?",
                (self.bot_identity, update_id),
            )
            self._prune_completed_in_transaction()
            return cursor.rowcount > 0

    def pending_dispatches(self, *, limit: int = 100) -> list[tuple[int, Any]]:
        self._ensure_open()
        rows = self._connection.execute(
            "SELECT update_id, result FROM telegram_dispatch_journal WHERE bot_identity=? ORDER BY update_id LIMIT ?",
            (self.bot_identity, limit),
        ).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def _prune_completed_in_transaction(self) -> None:
        self._connection.execute(
            "DELETE FROM telegram_updates WHERE bot_identity=? AND status='done' AND update_id < "
            "COALESCE((SELECT MAX(update_id) FROM telegram_updates WHERE bot_identity=? AND status='done') - ?, -1)",
            (self.bot_identity, self.bot_identity, self.max_completed),
        )

    def enqueue_outbox(self, key: str, payload: Any) -> bool:
        self._ensure_open()
        if not key:
            raise ValueError("outbox key is required")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._transaction():
            exists = self._connection.execute(
                "SELECT 1 FROM telegram_outbox WHERE bot_identity=? AND item_key=?",
                (self.bot_identity, key),
            ).fetchone()
            if exists is not None:
                return False
            count = self._connection.execute(
                "SELECT COUNT(*) FROM telegram_outbox WHERE bot_identity=?", (self.bot_identity,)
            ).fetchone()[0]
            if count >= self.max_outbox:
                raise TelegramStoreFullError("outbox")
            self._connection.execute(
                "INSERT INTO telegram_outbox(bot_identity, item_key, payload, status) VALUES (?, ?, ?, 'queued')",
                (self.bot_identity, key, encoded),
            )
            return True

    def claim_outbox(self, *, limit: int = 100) -> list[OutboxItem]:
        self._ensure_open()
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._transaction():
            rows = self._connection.execute(
                "SELECT item_key, payload FROM telegram_outbox "
                "WHERE bot_identity=? AND status IN ('queued', 'retry') ORDER BY rowid LIMIT ?",
                (self.bot_identity, limit),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    "UPDATE telegram_outbox SET status='claimed' WHERE bot_identity=? AND item_key=?",
                    (self.bot_identity, row[0]),
                )
            return [OutboxItem(row[0], json.loads(row[1]), "claimed") for row in rows]

    def mark_outbox_sent(self, key: str) -> bool:
        return self._set_outbox_status(key, "sent")

    def mark_outbox_unknown(self, key: str) -> bool:
        return self._set_outbox_status(key, "unknown")

    def mark_outbox_failed(self, key: str, *, retryable: bool = False) -> bool:
        return self._set_outbox_status(key, "retry" if retryable else "failed")

    def _set_outbox_status(self, key: str, status: str) -> bool:
        self._ensure_open()
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE telegram_outbox SET status=? WHERE bot_identity=? AND item_key=? AND status='claimed'",
                (status, self.bot_identity, key),
            )
            if status in {"sent", "failed"}:
                self._connection.execute(
                    "DELETE FROM telegram_outbox WHERE bot_identity=? AND status IN ('sent', 'failed') "
                    "AND rowid NOT IN (SELECT rowid FROM telegram_outbox WHERE bot_identity=? "
                    "AND status IN ('sent', 'failed') ORDER BY rowid DESC LIMIT ?)",
                    (self.bot_identity, self.bot_identity, self.max_outbox),
                )
            return cursor.rowcount > 0

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "TelegramBotStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
