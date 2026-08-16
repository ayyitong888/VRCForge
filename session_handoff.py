from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SESSION_HANDOFF_SCHEMA = "vrcforge.session_handoff.v1"
SESSION_HANDOFF_AUDIT_SCHEMA = "vrcforge.session_handoff_audit.v1"

HANDOFF_STATUS_PENDING_REVIEW = "pending_review"
HANDOFF_STATUS_CLAIMED = "claimed"
HANDOFF_STATUS_MATERIALIZED = "materialized"
HANDOFF_STATUS_DISMISSED = "dismissed"
HANDOFF_STATUS_CANCELLED = "cancelled"
HANDOFF_STATUS_EXPIRED = "expired"
HANDOFF_STATUS_REPLIED = "replied"

TERMINAL_STATUSES = frozenset(
    {
        HANDOFF_STATUS_DISMISSED,
        HANDOFF_STATUS_CANCELLED,
        HANDOFF_STATUS_EXPIRED,
        HANDOFF_STATUS_REPLIED,
    }
)

ALLOWED_PAYLOAD_KEYS = {
    "goal",
    "completed",
    "decisions",
    "blockers",
    "nextAction",
    "question",
}

MAX_ID_CHARS = 180
MAX_TEXT_CHARS = 2000
MAX_LIST_ITEMS = 16
MAX_LIST_ITEM_CHARS = 768
DEFAULT_HANDOFF_TTL_SECONDS = 60 * 60 * 24
DEFAULT_CLAIM_TTL_SECONDS = 300.0
MAX_AUDIT_LINE_BYTES = 4096
ALLOWED_HANDOFF_KINDS = frozenset({"handoff", "question", "reply"})

FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|private[_-]?key|access[_-]?token|password|secret|credential)"),
    re.compile(r"(?i)\b(?:provider|tool|tools|raw\s+transcript|reasoning)\b"),
)
PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(?:^|[\s\"'`])/[^\\s\"'`]+(?:/[^\\s\"'`]+)+"),
    re.compile(r"\.\.[\\/]"),
    re.compile(r"\bfile://", re.IGNORECASE),
    re.compile(r"\b(?:https?|s3|ftp)://[^\s\"']+"),
)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class SessionHandoffError(ValueError):
    """Input validation, authorization, and CAS conflict errors."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assert_regular_or_absent(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(metadata.st_mode) or (attributes & FILE_ATTRIBUTE_REPARSE_POINT):
        raise OSError(f"{label} cannot be a link or reparse point.")
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"{label} must be a regular file.")


def _assert_safe_ancestor_chain(path: Path, *, label: str) -> None:
    candidate = path.resolve(strict=False)
    for ancestor in (candidate, *candidate.parents):
        try:
            metadata = os.lstat(ancestor)
        except FileNotFoundError:
            if ancestor.parent == ancestor:
                break
            continue
        attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(metadata.st_mode) or (attributes & FILE_ATTRIBUTE_REPARSE_POINT):
            raise OSError(f"{label} parent chain cannot contain link or reparse point: {ancestor}.")
        if ancestor != candidate and not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"{label} parent chain entry is not a directory: {ancestor}.")
        if ancestor.parent == ancestor:
            break


def _set_private_mode(path: Path, *, mode: int, label: str) -> None:
    try:
        _assert_regular_or_absent(path, label=label)
    except OSError:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        # best-effort permission hardening
        return


def _open_regular_file(path: Path, flags: int, *, label: str, mode: int = 0o600) -> int:
    _assert_regular_or_absent(path, label=label)
    safe_flags = flags | int(getattr(os, "O_BINARY", 0) or 0)
    safe_flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
    descriptor = os.open(path, safe_flags, mode)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"{label} must be a regular file.")
        _assert_regular_or_absent(path, label=label)
        path_metadata = os.lstat(path)
        if (int(metadata.st_ino), int(metadata.st_dev)) != (
            int(path_metadata.st_ino),
            int(path_metadata.st_dev),
        ):
            raise OSError(f"{label} changed while being opened.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _normalize_scope(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    if lowered in {"", "none", "null", "unscoped", "-"}:
        return ""
    return text


def _normalize_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SessionHandoffError(f"{field} is required.")
    if len(text) > MAX_ID_CHARS:
        raise SessionHandoffError(f"{field} exceeds max length.")
    return text


def _normalize_kind(value: Any) -> str:
    kind = _normalize_id(value, field="kind").casefold()
    if kind not in ALLOWED_HANDOFF_KINDS:
        raise SessionHandoffError("kind must be handoff, question, or reply.")
    return kind


def _normalize_revision(value: Any, *, field: str) -> int:
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise SessionHandoffError(f"{field} must be a positive integer.") from exc
    if revision < 1:
        raise SessionHandoffError(f"{field} must be a positive integer.")
    return revision


def _normalize_text(value: Any, *, field: str, required: bool = True) -> str:
    text = str(value or "").strip().replace("\x00", "")
    if required and not text:
        raise SessionHandoffError(f"{field} is required.")
    if len(text) > MAX_TEXT_CHARS:
        raise SessionHandoffError(f"{field} exceeds max length.")
    return text


def _contains_forbidden_text(value: Any) -> bool:
    text = str(value or "")
    if any(pattern.search(text) for pattern in FORBIDDEN_TEXT_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in PATH_PATTERNS):
        return True
    return False


def _normalize_list_field(values: Any, *, field: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise SessionHandoffError(f"{field} must be a list.")
    if len(values) > MAX_LIST_ITEMS:
        raise SessionHandoffError(f"{field} exceeds list limit.")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise SessionHandoffError(f"{field} items must be strings.")
        text = _normalize_text(item, field=field, required=True)
        if len(text) > MAX_LIST_ITEM_CHARS or _contains_forbidden_text(text):
            raise SessionHandoffError(f"{field} contains forbidden content.")
        normalized.append(text)
    return normalized


def _normalize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise SessionHandoffError("payload must be an object.")
    unknown = sorted(set(payload.keys()) - ALLOWED_PAYLOAD_KEYS)
    if unknown:
        raise SessionHandoffError(f"payload contains unsupported fields: {unknown}")

    goal = _normalize_text(payload.get("goal"), field="goal", required=True)
    question = _normalize_text(payload.get("question"), field="question", required=False)
    next_action = _normalize_text(payload.get("nextAction"), field="nextAction", required=False)
    decisions = _normalize_list_field(payload.get("decisions"), field="decisions")
    blockers = _normalize_list_field(payload.get("blockers"), field="blockers")
    completed = payload.get("completed", False)
    if not isinstance(completed, bool):
        raise SessionHandoffError("completed must be a boolean.")

    for value in (goal, question, next_action, *decisions, *blockers):
        if _contains_forbidden_text(value):
            raise SessionHandoffError("payload contains forbidden content.")
    return {
        "goal": goal,
        "question": question,
        "nextAction": next_action,
        "completed": completed,
        "decisions": decisions,
        "blockers": blockers,
    }


def _payload_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "schema": SESSION_HANDOFF_SCHEMA,
        "id": row["id"],
        "owner_id": row["owner_id"],
        "source_session_id": row["source_session_id"],
        "target_session_id": row["target_session_id"],
        "source_chat_id": row["source_chat_id"],
        "target_chat_id": row["target_chat_id"],
        "source_revision": int(row["source_revision"]),
        "target_revision": int(row["target_revision"]),
        "source_scope": row["source_scope"],
        "target_scope": row["target_scope"],
        "status": row["status"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": float(row["expires_at"]),
        "claim_owner": row["claim_owner"],
        "claim_token": row["claim_token"],
        "claim_expires_at": row["claim_expires_at"],
        "goal": row["goal"],
        "completed": bool(row["completed"]),
        "decisions": json.loads(row["decisions"]),
        "blockers": json.loads(row["blockers"]),
        "nextAction": row["next_action"],
        "question": row["question"],
        "kind": row["kind"],
        "reply_to": row["reply_to"],
        "payloadDigest": row["payload_hash"],
        "materializeReceipt": row["materialize_receipt"],
        "materializeAt": row["materialize_at"],
    }


class SessionHandoffStore:
    """Durable session handoff store with strict schema and CAS mutations."""

    def __init__(
        self,
        db_path: str | Path,
        metadata_audit_path: str | Path,
        *,
        clock: Callable[[], float] | None = None,
        handoff_ttl_seconds: float = float(DEFAULT_HANDOFF_TTL_SECONDS),
        claim_ttl_seconds: float = float(DEFAULT_CLAIM_TTL_SECONDS),
        lock: threading.RLock | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.metadata_audit_path = Path(metadata_audit_path)
        self._clock = clock or (lambda: __import__("time").time())
        self._handoff_ttl_seconds = float(handoff_ttl_seconds)
        self._claim_ttl_seconds = float(claim_ttl_seconds)
        self._lock = lock or threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._open()

    def __enter__(self) -> "SessionHandoffStore":
        self._open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    @property
    def _now(self) -> float:
        return float(self._clock())

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None

    def _open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.metadata_audit_path.parent.mkdir(parents=True, exist_ok=True)
            _assert_safe_ancestor_chain(self.db_path.parent, label="Session handoff database")
            _assert_safe_ancestor_chain(self.metadata_audit_path.parent, label="Session handoff metadata audit")
            try:
                os.chmod(self.db_path.parent, 0o700)
            except OSError:
                pass
            try:
                os.chmod(self.metadata_audit_path.parent, 0o700)
            except OSError:
                pass

            _assert_regular_or_absent(self.db_path, label="Session handoff database")
            _assert_regular_or_absent(self.metadata_audit_path, label="Session handoff metadata audit")
            fd = _open_regular_file(self.db_path, os.O_CREAT | os.O_RDWR, label="Session handoff database", mode=0o600)
            os.close(fd)

            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,
                timeout=5.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_handoffs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    target_session_id TEXT NOT NULL,
                    source_chat_id TEXT NOT NULL,
                    target_chat_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    target_revision INTEGER NOT NULL,
                    source_scope TEXT NOT NULL,
                    target_scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    claim_owner TEXT,
                    claim_token TEXT,
                    claim_expires_at REAL,
                    goal TEXT NOT NULL,
                    completed INTEGER NOT NULL,
                    decisions TEXT NOT NULL,
                    blockers TEXT NOT NULL,
                    next_action TEXT NOT NULL,
                    question TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    reply_to TEXT,
                    materialize_receipt TEXT,
                    materialize_at REAL,
                    payload_hash TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_handoffs_owner_scope_status ON session_handoffs(owner_id, source_scope, target_scope, status)"
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_handoffs_reply_to ON session_handoffs(reply_to) WHERE reply_to IS NOT NULL"
            )
            self._conn.commit()
            _set_private_mode(self.db_path, mode=0o600, label="Session handoff database")
            _set_private_mode(
                Path(f"{self.db_path}-wal"),
                mode=0o600,
                label="Session handoff database WAL",
            )
            _set_private_mode(
                Path(f"{self.db_path}-shm"),
                mode=0o600,
                label="Session handoff database SHM",
            )
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass
            try:
                os.chmod(self.metadata_audit_path, 0o600)
            except OSError:
                pass

    def _cursor(self) -> sqlite3.Cursor:
        if self._conn is None:
            raise RuntimeError("Session handoff store is closed.")
        return self._conn.cursor()

    def _append_audit(self, *, event: str, row: sqlite3.Row) -> None:
        payload = {
            "schema": SESSION_HANDOFF_AUDIT_SCHEMA,
            "event": event,
            "handoffId": row["id"],
            "ownerId": row["owner_id"],
            "scope": row["source_scope"],
            "revision": int(row["revision"]),
            "status": row["status"],
            "createdAt": _utc_now_iso(),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_AUDIT_LINE_BYTES:
            return
        _assert_safe_ancestor_chain(self.metadata_audit_path.parent, label="Session handoff metadata audit")
        descriptor = _open_regular_file(
            self.metadata_audit_path,
            os.O_CREAT | os.O_RDWR | os.O_APPEND,
            label="Session handoff metadata audit",
            mode=0o600,
        )
        with os.fdopen(descriptor, "r+b", closefd=True) as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(-1, os.SEEK_END)
                tail = handle.read(1)
                if tail not in {b"\n", b"\r"}:
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
            handle.write(encoded)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _validate_scopes(source_scope: str, target_scope: str) -> tuple[str, str]:
        source = _normalize_scope(source_scope)
        target = _normalize_scope(target_scope)
        if source != target:
            raise SessionHandoffError("source and target scope must be equal, or both unscoped.")
        return source, target

    def _row(self, handoff_id: str) -> sqlite3.Row:
        row = self._cursor().execute("SELECT * FROM session_handoffs WHERE id = ?", (handoff_id,)).fetchone()
        if row is None:
            raise KeyError(handoff_id)
        return row

    @staticmethod
    def _assert_expected_revision(row: sqlite3.Row, expected_revision: int) -> None:
        if int(expected_revision) != int(row["revision"]):
            raise SessionHandoffError("expected revision mismatch.")

    def _scope_match(self, row: sqlite3.Row, scope: str) -> str:
        normalized_scope = _normalize_scope(scope)
        source_scope = _normalize_scope(row["source_scope"])
        target_scope = _normalize_scope(row["target_scope"])
        if source_scope != target_scope:
            raise RuntimeError("stored handoff scope pair is inconsistent.")
        if normalized_scope != source_scope:
            raise PermissionError("scope does not match handoff binding.")
        return source_scope

    def _check_owner_and_session(
        self,
        row: sqlite3.Row,
        *,
        owner_id: str,
        session_id: str,
        scope: str,
    ) -> None:
        if _normalize_id(owner_id, field="owner_id") != row["owner_id"]:
            raise PermissionError("owner_id does not match handoff owner.")
        if session_id not in {row["source_session_id"], row["target_session_id"]}:
            raise PermissionError("session_id does not match handoff binding.")
        self._scope_match(row, scope)

    def _authorized_row(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
    ) -> sqlite3.Row:
        row = self._row(_normalize_id(handoff_id, field="handoff_id"))
        self._check_owner_and_session(row, owner_id=owner_id, session_id=session_id, scope=scope)
        return self._mark_expired_if_needed(row, now=self._now)

    def binding(self, *, handoff_id: str, owner_id: str) -> dict[str, Any]:
        """Return the authoritative routing binding after an exact owner check."""
        with self._lock:
            row = self._row(_normalize_id(handoff_id, field="handoff_id"))
            if _normalize_id(owner_id, field="owner_id") != row["owner_id"]:
                raise PermissionError("owner_id does not match handoff owner.")
            return {
                "id": row["id"],
                "owner_id": row["owner_id"],
                "source_session_id": row["source_session_id"],
                "target_session_id": row["target_session_id"],
                "source_chat_id": row["source_chat_id"],
                "target_chat_id": row["target_chat_id"],
                "source_scope": row["source_scope"],
                "target_scope": row["target_scope"],
                "revision": int(row["revision"]),
                "status": row["status"],
            }

    def authorize_target_action(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        target_session_id: str,
        scope: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Validate a target-side mutation without mutating on bad authority or CAS."""
        with self._lock:
            row = self._row(_normalize_id(handoff_id, field="handoff_id"))
            if _normalize_id(owner_id, field="owner_id") != row["owner_id"]:
                raise PermissionError("owner_id does not match handoff owner.")
            if _normalize_id(target_session_id, field="target_session_id") != row["target_session_id"]:
                raise PermissionError("target_session_id does not match handoff target.")
            self._scope_match(row, scope)
            self._assert_expected_revision(row, expected_revision)
            row = self._mark_expired_if_needed(row, now=self._now)
            self._assert_expected_revision(row, expected_revision)
            return _as_row(row)

    def _mark_expired_if_needed(self, row: sqlite3.Row, now: float) -> sqlite3.Row:
        if row["status"] in TERMINAL_STATUSES:
            return row
        if now >= float(row["expires_at"]):
            updated = self._conn.execute(
                """
                UPDATE session_handoffs
                SET status = ?, revision = revision + 1, updated_at = ?, claim_owner = NULL,
                    claim_token = NULL, claim_expires_at = NULL
                WHERE id = ? AND status NOT IN (?, ?, ?, ?)
                """,
                (
                    HANDOFF_STATUS_EXPIRED,
                    _utc_now_iso(),
                    row["id"],
                    HANDOFF_STATUS_DISMISSED,
                    HANDOFF_STATUS_CANCELLED,
                    HANDOFF_STATUS_EXPIRED,
                    HANDOFF_STATUS_REPLIED,
                ),
            )
            if updated.rowcount:
                self._conn.commit()
                row = self._row(row["id"])
                self._append_audit(event="expired", row=row)
                return row
            return row
        if row["status"] == HANDOFF_STATUS_CLAIMED and row["claim_expires_at"] and float(row["claim_expires_at"]) <= now:
            return self._clear_expired_claim(row=row, now=now)
        return row

    def _mutation_row(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
        expected_revision: int,
    ) -> sqlite3.Row:
        row = self._authorized_row(
            handoff_id=handoff_id,
            owner_id=owner_id,
            session_id=session_id,
            scope=scope,
        )
        self._assert_expected_revision(row, expected_revision)
        return row

    def _clear_expired_claim(self, row: sqlite3.Row, *, now: float) -> sqlite3.Row:
        if row["status"] != HANDOFF_STATUS_CLAIMED:
            raise RuntimeError("not claimed.")
        if not row["claim_expires_at"] or float(row["claim_expires_at"]) > now:
            return row
        updated = self._conn.execute(
            """
            UPDATE session_handoffs
            SET status = ?, revision = revision + 1, claim_owner = NULL,
                claim_token = NULL, claim_expires_at = NULL, updated_at = ?
            WHERE id = ? AND status = ? AND revision = ? AND (claim_expires_at IS NULL OR claim_expires_at <= ?)
            """,
            (
                HANDOFF_STATUS_PENDING_REVIEW,
                _utc_now_iso(),
                row["id"],
                HANDOFF_STATUS_CLAIMED,
                row["revision"],
                now,
            ),
        )
        if updated.rowcount != 1:
            raise SessionHandoffError("claim has changed during expiry reclaim.")
        self._conn.commit()
        row = self._row(row["id"])
        self._append_audit(event="claim_recovered", row=row)
        return row

    def _validate_reply_target(
        self,
        *,
        current_owner: str,
        current_scope: str,
        current_source_session_id: str,
        current_target_session_id: str,
        current_source_chat_id: str,
        current_target_chat_id: str,
        reply_to: str | None,
    ) -> str | None:
        if reply_to is None:
            return None
        normalized_reply_to = _normalize_id(reply_to, field="reply_to")
        reply_row = self._row(normalized_reply_to)
        reply_row = self._mark_expired_if_needed(reply_row, now=self._now)
        if reply_row["status"] != HANDOFF_STATUS_MATERIALIZED:
            raise SessionHandoffError("reply_to target must be materialized.")
        if reply_row["kind"] not in {"handoff", "question"}:
            raise SessionHandoffError("reply_to target kind is not replyable.")
        if reply_row["owner_id"] != current_owner:
            raise SessionHandoffError("reply_to owner mismatch.")
        if _normalize_scope(reply_row["source_scope"]) != _normalize_scope(current_scope):
            raise SessionHandoffError("reply_to scope mismatch.")
        if (
            reply_row["source_session_id"] != current_target_session_id
            or reply_row["target_session_id"] != current_source_session_id
            or reply_row["source_chat_id"] != current_target_chat_id
            or reply_row["target_chat_id"] != current_source_chat_id
        ):
            raise SessionHandoffError("reply_to session direction is invalid.")
        if reply_row["reply_to"]:
            raise SessionHandoffError("reply_to target already has bound reply reference.")
        return normalized_reply_to

    def create(
        self,
        *,
        owner_id: str,
        source_session_id: str,
        target_session_id: str,
        source_scope: str,
        target_scope: str,
        payload: Mapping[str, Any],
        reply_to: str | None,
        source_chat_id: str,
        target_chat_id: str,
        source_revision: int,
        target_revision: int,
        kind: str,
    ) -> dict[str, Any]:
        normalized_owner = _normalize_id(owner_id, field="owner_id")
        normalized_source_session_id = _normalize_id(source_session_id, field="source_session_id")
        normalized_target_session_id = _normalize_id(target_session_id, field="target_session_id")
        if normalized_source_session_id == normalized_target_session_id:
            raise SessionHandoffError("source_session_id and target_session_id must differ.")
        normalized_source_chat_id = _normalize_id(source_chat_id, field="source_chat_id")
        normalized_target_chat_id = _normalize_id(target_chat_id, field="target_chat_id")
        if normalized_source_chat_id == normalized_target_chat_id:
            raise SessionHandoffError("source_chat_id and target_chat_id must differ.")
        normalized_source_revision = _normalize_revision(source_revision, field="source_revision")
        normalized_target_revision = _normalize_revision(target_revision, field="target_revision")
        normalized_kind = _normalize_kind(kind)
        if normalized_kind == "reply" and reply_to is None:
            raise SessionHandoffError("kind=reply requires reply_to.")
        if reply_to is not None and normalized_kind != "reply":
            raise SessionHandoffError("only kind=reply may set reply_to.")
        source_scope, target_scope = self._validate_scopes(source_scope, target_scope)
        normalized_payload = _normalize_payload(payload)

        with self._lock:
            now = self._now
            handoff_id = f"handoff_{int(now * 1_000_000)}_{secrets.token_hex(6)}"
            now_iso = _utc_now_iso()
            bound_reply_to = self._validate_reply_target(
                current_owner=normalized_owner,
                current_scope=source_scope,
                current_source_session_id=normalized_source_session_id,
                current_target_session_id=normalized_target_session_id,
                current_source_chat_id=normalized_source_chat_id,
                current_target_chat_id=normalized_target_chat_id,
                reply_to=reply_to,
            )
            try:
                self._conn.execute(
                    """
                INSERT INTO session_handoffs (
                    id, owner_id, source_session_id, target_session_id, source_chat_id, target_chat_id,
                    source_revision, target_revision, source_scope, target_scope,
                    status, revision, created_at, updated_at, expires_at, claim_owner, claim_token,
                    claim_expires_at, goal, completed, decisions, blockers, next_action, question, kind,
                    reply_to, materialize_receipt, materialize_at, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                    handoff_id,
                    normalized_owner,
                    normalized_source_session_id,
                    normalized_target_session_id,
                    normalized_source_chat_id,
                    normalized_target_chat_id,
                    normalized_source_revision,
                    normalized_target_revision,
                    source_scope,
                    target_scope,
                    HANDOFF_STATUS_PENDING_REVIEW,
                    1,
                    now_iso,
                    now_iso,
                    now + self._handoff_ttl_seconds,
                    normalized_payload["goal"],
                    1 if normalized_payload["completed"] else 0,
                    json.dumps(normalized_payload["decisions"], ensure_ascii=False),
                    json.dumps(normalized_payload["blockers"], ensure_ascii=False),
                    normalized_payload["nextAction"],
                    normalized_payload["question"],
                    normalized_kind,
                    bound_reply_to,
                    _payload_digest(normalized_payload),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "uq_session_handoffs_reply_to" in str(exc) or "UNIQUE constraint failed: session_handoffs.reply_to" in str(exc):
                    raise SessionHandoffError("reply_to target already replied.") from exc
                raise
            self._conn.commit()
            row = self._row(handoff_id)
            self._append_audit(event="created", row=row)
            return _as_row(row)

    def claim(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._mutation_row(
                handoff_id=handoff_id,
                owner_id=owner_id,
                session_id=session_id,
                scope=scope,
                expected_revision=expected_revision,
            )
            now = self._now
            if row["status"] == HANDOFF_STATUS_CLAIMED:
                claim_token_expires = row["claim_expires_at"]
                claim_owner = row["claim_owner"] or ""
                if claim_token_expires and float(claim_token_expires) > now:
                    if claim_owner == owner_id:
                        return _as_row(row)
                    raise SessionHandoffError("handoff is currently claimed.")
                row = self._clear_expired_claim(row=row, now=now)

            if row["status"] != HANDOFF_STATUS_PENDING_REVIEW:
                raise SessionHandoffError("handoff is not in pending_review state.")

            new_claim_token = self._claim_token()
            updated = self._conn.execute(
                """
                UPDATE session_handoffs
                SET status = ?, revision = revision + 1, claim_owner = ?, claim_token = ?,
                    claim_expires_at = ?, updated_at = ?
                WHERE id = ? AND owner_id = ? AND revision = ? AND status = ?
                """,
                (
                    HANDOFF_STATUS_CLAIMED,
                    _normalize_id(owner_id, field="owner_id"),
                    new_claim_token,
                    now + self._claim_ttl_seconds,
                    _utc_now_iso(),
                    row["id"],
                    row["owner_id"],
                    row["revision"],
                    HANDOFF_STATUS_PENDING_REVIEW,
                ),
            )
            if updated.rowcount != 1:
                raise SessionHandoffError("claim transition failed.")
            self._conn.commit()
            row = self._row(row["id"])
            self._append_audit(event="claimed", row=row)
            return _as_row(row)

    @staticmethod
    def _claim_token() -> str:
        return secrets.token_urlsafe(18)

    def materialize(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
        expected_revision: int,
        claim_token: str,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._mutation_row(
                handoff_id=handoff_id,
                owner_id=owner_id,
                session_id=session_id,
                scope=scope,
                expected_revision=expected_revision,
            )
            if row["status"] == HANDOFF_STATUS_MATERIALIZED:
                if str(claim_token or "") != str(row["materialize_receipt"]):
                    raise SessionHandoffError("handoff already materialized.")
                return _as_row(row)
            if row["status"] != HANDOFF_STATUS_CLAIMED:
                raise SessionHandoffError("handoff is not claimed.")
            if row["claim_owner"] != owner_id:
                raise PermissionError("claim owner does not match.")
            if str(claim_token or "") != str(row["claim_token"]):
                raise PermissionError("claim token mismatch.")
            if not row["claim_expires_at"] or float(row["claim_expires_at"]) <= self._now:
                raise SessionHandoffError("claim has expired.")

            existing_receipt = row["materialize_receipt"]
            materialize_receipt = existing_receipt or f"receipt_{secrets.token_hex(12)}"
            updated = self._conn.execute(
                """
                UPDATE session_handoffs
                SET status = ?, revision = revision + 1, materialize_receipt = ?, materialize_at = ?,
                    claim_owner = NULL, claim_token = NULL, claim_expires_at = NULL, updated_at = ?
                WHERE id = ? AND owner_id = ? AND revision = ? AND status = ? AND claim_owner = ? AND claim_token = ?
                """,
                (
                    HANDOFF_STATUS_MATERIALIZED,
                    materialize_receipt,
                    self._now,
                    _utc_now_iso(),
                    row["id"],
                    row["owner_id"],
                    row["revision"],
                    HANDOFF_STATUS_CLAIMED,
                    owner_id,
                    row["claim_token"],
                ),
            )
            if updated.rowcount != 1:
                raise SessionHandoffError("materialize transition failed.")
            self._conn.commit()
            row = self._row(row["id"])
            self._append_audit(event="materialized", row=row)
            return _as_row(row)

    def dismiss(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._mutation_row(
                handoff_id=handoff_id,
                owner_id=owner_id,
                session_id=session_id,
                scope=scope,
                expected_revision=expected_revision,
            )
            if row["status"] in TERMINAL_STATUSES:
                raise SessionHandoffError("handoff already terminal.")
            updated = self._conn.execute(
                """
                UPDATE session_handoffs
                SET status = ?, revision = revision + 1, updated_at = ?,
                    claim_owner = NULL, claim_token = NULL, claim_expires_at = NULL
                WHERE id = ? AND owner_id = ? AND revision = ?
                """,
                (
                    HANDOFF_STATUS_DISMISSED,
                    _utc_now_iso(),
                    row["id"],
                    row["owner_id"],
                    row["revision"],
                ),
            )
            if updated.rowcount != 1:
                raise SessionHandoffError("dismiss transition failed.")
            self._conn.commit()
            row = self._row(row["id"])
            self._append_audit(event="dismissed", row=row)
            return _as_row(row)

    def cancel(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._mutation_row(
                handoff_id=handoff_id,
                owner_id=owner_id,
                session_id=session_id,
                scope=scope,
                expected_revision=expected_revision,
            )
            if row["status"] != HANDOFF_STATUS_PENDING_REVIEW:
                raise SessionHandoffError("only pending_review can be cancelled.")
            updated = self._conn.execute(
                """
                UPDATE session_handoffs
                SET status = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND owner_id = ? AND revision = ? AND status = ?
                """,
                (
                    HANDOFF_STATUS_CANCELLED,
                    _utc_now_iso(),
                    row["id"],
                    row["owner_id"],
                    row["revision"],
                    HANDOFF_STATUS_PENDING_REVIEW,
                ),
            )
            if updated.rowcount != 1:
                raise SessionHandoffError("cancel transition failed.")
            self._conn.commit()
            row = self._row(row["id"])
            self._append_audit(event="cancelled", row=row)
            return _as_row(row)

    def get(
        self,
        *,
        handoff_id: str,
        owner_id: str,
        session_id: str,
        scope: str,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._authorized_row(
                handoff_id=handoff_id,
                owner_id=owner_id,
                session_id=session_id,
                scope=scope,
            )
            return _as_row(row)

    def list(
        self,
        *,
        owner_id: str,
        session_id: str,
        scope: str,
        include_terminal: bool = False,
    ) -> list[dict[str, Any]]:
        normalized_owner = _normalize_id(owner_id, field="owner_id")
        normalized_scope = _normalize_scope(scope)
        normalized_session = _normalize_id(session_id, field="session_id")
        rows = self._cursor().execute(
            """
            SELECT * FROM session_handoffs
            WHERE owner_id = ? AND source_scope = ? AND (source_session_id = ? OR target_session_id = ?)
            ORDER BY created_at DESC
            """,
            (normalized_owner, normalized_scope, normalized_session, normalized_session),
        ).fetchall()
        now = self._now
        values: list[dict[str, Any]] = []
        for row in rows:
            row = self._mark_expired_if_needed(row=row, now=now)
            self._scope_match(row=row, scope=normalized_scope)
            if not include_terminal and row["status"] in TERMINAL_STATUSES:
                continue
            values.append(_as_row(row))
        return values


def demo_scope_pair(source_scope: str, target_scope: str) -> tuple[str, str]:
    return SessionHandoffStore._validate_scopes(source_scope, target_scope)


__all__ = [
    "SessionHandoffError",
    "SessionHandoffStore",
    "HANDOFF_STATUS_CANCELLED",
    "HANDOFF_STATUS_CLAIMED",
    "HANDOFF_STATUS_DISMISSED",
    "HANDOFF_STATUS_EXPIRED",
    "HANDOFF_STATUS_MATERIALIZED",
    "HANDOFF_STATUS_PENDING_REVIEW",
    "HANDOFF_STATUS_REPLIED",
    "SESSION_HANDOFF_AUDIT_SCHEMA",
    "SESSION_HANDOFF_SCHEMA",
]
