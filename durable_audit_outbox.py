from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

PathSource = str | Path | Callable[[], str | Path]

MAX_AUDIT_LINE_BYTES = 256 * 1024
MAX_AUDIT_FILE_BYTES = 16 * 1024 * 1024
MAX_AUDIT_ROWS = 50_000


def _assert_regular_or_absent(path: Path, *, label: str) -> None:
    # Imported lazily because the accepted Memory store uses this audit class.
    from agent_memory_store import _assert_regular_or_absent as assert_regular

    assert_regular(path, label=label)


def _open_regular_file(path: Path, flags: int, *, label: str, mode: int = 0o600) -> int:
    from agent_memory_store import _open_regular_file as open_regular

    return open_regular(path, flags, label=label, mode=mode)


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    from agent_memory_store import _same_file_identity as same_identity

    return same_identity(left, right)


def _resolve_path(source: PathSource) -> Path:
    return Path(source() if callable(source) else source)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DurableMetadataAudit:
    """Metadata-only audit writer with an idempotent local outbox."""

    def __init__(
        self,
        audit_path: PathSource,
        *,
        schema: str,
        allowed_fields: Iterable[str],
    ) -> None:
        self._audit_path = audit_path
        self._schema = str(schema)
        self._allowed_fields = frozenset(str(field) for field in allowed_fields)
        self._lock = threading.RLock()

    @property
    def audit_path(self) -> Path:
        path = _resolve_path(self._audit_path)
        _assert_regular_or_absent(path, label="Durable audit ledger")
        return path

    @property
    def outbox_path(self) -> Path:
        path = self.audit_path
        outbox = path.with_name(f"{path.stem}.outbox{path.suffix or '.jsonl'}")
        _assert_regular_or_absent(outbox, label="Durable audit outbox")
        return outbox

    def prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        projected = {
            key: payload.get(key)
            for key in sorted(self._allowed_fields)
            if key in payload
        }
        identity = hashlib.sha256(
            json.dumps(
                {"schema": self._schema, **projected},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "schema": self._schema,
            "eventId": f"audit_{identity[:32]}",
            "createdAt": _utc_now_iso(),
            **projected,
        }

    def validate_prepared(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("Prepared audit row is invalid.")
        allowed = {"schema", "eventId", "createdAt", *self._allowed_fields}
        if any(str(key) not in allowed for key in value):
            raise ValueError("Prepared audit row contains unsupported fields.")
        if value.get("schema") != self._schema:
            raise ValueError("Prepared audit row schema is invalid.")
        event_id = str(value.get("eventId") or "")
        created_at = str(value.get("createdAt") or "")
        if (
            not event_id.startswith("audit_")
            or len(event_id) != 38
            or any(character not in "0123456789abcdef" for character in event_id[6:])
            or not created_at
            or len(created_at) > 80
        ):
            raise ValueError("Prepared audit row identity is invalid.")
        projected = {
            key: value.get(key)
            for key in self._allowed_fields
            if key in value
        }
        expected = self.prepare(projected)["eventId"]
        if expected != event_id:
            raise ValueError("Prepared audit row digest is invalid.")
        return dict(value)

    @staticmethod
    def _assert_open_identity(path: Path, descriptor: int, *, label: str) -> None:
        handle_metadata = os.fstat(descriptor)
        _assert_regular_or_absent(path, label=label)
        try:
            path_metadata = os.lstat(path)
        except FileNotFoundError as exc:
            raise OSError(f"{label} changed while it was open.") from exc
        if not _same_file_identity(handle_metadata, path_metadata):
            raise OSError(f"{label} changed while it was open.")

    @staticmethod
    def _serialize_row(row: Mapping[str, Any]) -> bytes:
        encoded = (
            json.dumps(
                dict(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_AUDIT_LINE_BYTES:
            raise ValueError("Durable audit row exceeds its size limit.")
        return encoded

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("Durable audit write did not make progress.")
            remaining = remaining[written:]

    @staticmethod
    def _read_descriptor(descriptor: int, *, label: str) -> list[dict[str, Any]]:
        metadata = os.fstat(descriptor)
        if metadata.st_size > MAX_AUDIT_FILE_BYTES:
            raise ValueError(f"{label} exceeds its size limit.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        rows: list[dict[str, Any]] = []
        total_bytes = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                raw_line = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                if not raw_line:
                    break
                total_bytes += len(raw_line)
                if total_bytes > MAX_AUDIT_FILE_BYTES:
                    raise ValueError(f"{label} exceeds its size limit.")
                if len(raw_line) > MAX_AUDIT_LINE_BYTES:
                    raise ValueError(f"{label} contains an oversized row.")
                content = raw_line.rstrip(b"\r\n")
                if not content:
                    raise ValueError(f"{label} contains an empty row.")
                try:
                    value = json.loads(content.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"{label} contains an invalid row.") from exc
                if not isinstance(value, dict) or not str(value.get("eventId") or ""):
                    raise ValueError(f"{label} contains an invalid row.")
                rows.append(value)
                if len(rows) > MAX_AUDIT_ROWS:
                    raise ValueError(f"{label} exceeds its row limit.")
        return rows

    @staticmethod
    def _read(path: Path, *, label: str) -> list[dict[str, Any]]:
        try:
            descriptor = _open_regular_file(path, os.O_RDONLY, label=label)
        except FileNotFoundError:
            return []
        try:
            rows = DurableMetadataAudit._read_descriptor(descriptor, label=label)
            DurableMetadataAudit._assert_open_identity(path, descriptor, label=label)
            return rows
        finally:
            os.close(descriptor)

    def _append(self, path: Path, row: Mapping[str, Any], *, label: str) -> None:
        validated = self.validate_prepared(row)
        encoded = self._serialize_row(validated)
        _assert_regular_or_absent(path, label=label)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = _open_regular_file(
            path,
            os.O_RDWR | os.O_CREAT | os.O_APPEND,
            label=label,
        )
        try:
            existing = self._read_descriptor(descriptor, label=label)
            for stored in existing:
                self.validate_prepared(stored)
            if len(existing) >= MAX_AUDIT_ROWS:
                raise ValueError(f"{label} exceeds its row limit.")
            size = int(os.fstat(descriptor).st_size)
            if size > MAX_AUDIT_FILE_BYTES:
                raise ValueError(f"{label} exceeds its size limit.")
            separator = b""
            if size:
                os.lseek(descriptor, -1, os.SEEK_END)
                if os.read(descriptor, 1) not in {b"\n", b"\r"}:
                    separator = b"\n"
            if size + len(separator) + len(encoded) > MAX_AUDIT_FILE_BYTES:
                raise ValueError(f"{label} exceeds its size limit.")
            os.lseek(descriptor, 0, os.SEEK_END)
            self._write_all(descriptor, separator + encoded)
            os.fsync(descriptor)
            verified = self._read_descriptor(descriptor, label=label)
            for stored in verified:
                self.validate_prepared(stored)
            self._assert_open_identity(path, descriptor, label=label)
        finally:
            os.close(descriptor)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self.validate_prepared(row)
                for row in self._read(
                    self.outbox_path,
                    label="Durable audit outbox",
                )
            ]

    @staticmethod
    def _unlink_regular_path(path: Path, *, label: str) -> None:
        try:
            descriptor = _open_regular_file(path, os.O_RDONLY, label=label)
        except FileNotFoundError:
            return
        try:
            handle_metadata = os.fstat(descriptor)
            DurableMetadataAudit._assert_open_identity(path, descriptor, label=label)
        finally:
            os.close(descriptor)
        _assert_regular_or_absent(path, label=label)
        try:
            path_metadata = os.lstat(path)
        except FileNotFoundError as exc:
            raise OSError(f"{label} changed before cleanup.") from exc
        if not _same_file_identity(handle_metadata, path_metadata):
            raise OSError(f"{label} changed before cleanup.")
        path.unlink()
        try:
            os.lstat(path)
        except FileNotFoundError:
            return
        raise OSError(f"{label} cleanup could not be verified.")

    def _rewrite_outbox(self, rows: Iterable[Mapping[str, Any]]) -> bool:
        temporary: Path | None = None
        success = False
        try:
            materialized = [self.validate_prepared(row) for row in rows]
            if len(materialized) > MAX_AUDIT_ROWS:
                raise ValueError("Durable audit outbox exceeds its row limit.")
            path = self.outbox_path
            if not materialized:
                self._unlink_regular_path(path, label="Durable audit outbox")
                return True
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(
                f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.rewrite.tmp"
            )
            descriptor = _open_regular_file(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                label="Durable audit rewrite temporary",
            )
            try:
                total_bytes = 0
                for row in materialized:
                    encoded = self._serialize_row(row)
                    total_bytes += len(encoded)
                    if total_bytes > MAX_AUDIT_FILE_BYTES:
                        raise ValueError("Durable audit outbox exceeds its size limit.")
                    self._write_all(descriptor, encoded)
                os.fsync(descriptor)
                self._assert_open_identity(
                    temporary,
                    descriptor,
                    label="Durable audit rewrite temporary",
                )
            finally:
                os.close(descriptor)
            verified = self._read(
                temporary,
                label="Durable audit rewrite temporary",
            )
            if [self.validate_prepared(row) for row in verified] != materialized:
                raise OSError("Durable audit rewrite verification failed.")
            _assert_regular_or_absent(path, label="Durable audit outbox")
            os.replace(temporary, path)
            verified = self._read(path, label="Durable audit outbox")
            if [self.validate_prepared(row) for row in verified] != materialized:
                raise OSError("Durable audit outbox verification failed.")
            success = True
        except (OSError, ValueError, TypeError):
            success = False
        finally:
            if temporary is not None:
                try:
                    self._unlink_regular_path(
                        temporary,
                        label="Durable audit rewrite temporary",
                    )
                except OSError:
                    success = False
        return success

    def stage(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            row = self.prepare(payload)
            event_id = str(row["eventId"])
            existing = {str(item.get("eventId") or ""): item for item in self.pending()}
            if event_id not in existing:
                self._append(
                    self.outbox_path,
                    row,
                    label="Durable audit outbox",
                )
            return row

    def commit_staged(self, value: Mapping[str, Any]) -> bool:
        with self._lock:
            row = self.validate_prepared(value)
            if not self.append_prepared(row):
                return False
            event_id = str(row["eventId"])
            try:
                remaining = [
                    item
                    for item in self.pending()
                    if str(item.get("eventId") or "") != event_id
                ]
            except (OSError, ValueError):
                return True
            self._rewrite_outbox(remaining)
            return True

    def flush(self) -> bool:
        with self._lock:
            try:
                outbox = self.outbox_path
                pending = [
                    self.validate_prepared(row)
                    for row in self._read(
                        outbox,
                        label="Durable audit outbox",
                    )
                ]
                if not pending:
                    return True
                audit_path = self.audit_path
                existing_rows = [
                    self.validate_prepared(row)
                    for row in self._read(
                        audit_path,
                        label="Durable audit ledger",
                    )
                ]
            except (OSError, ValueError, TypeError):
                return False
            existing_ids = {
                str(row.get("eventId") or "")
                for row in existing_rows
            }
            deduplicated: dict[str, dict[str, Any]] = {}
            for row in pending:
                deduplicated[str(row["eventId"])] = row
            try:
                for event_id, row in deduplicated.items():
                    if event_id not in existing_ids:
                        self._append(
                            audit_path,
                            row,
                            label="Durable audit ledger",
                        )
                        existing_ids.add(event_id)
                current_outbox = [
                    self.validate_prepared(row)
                    for row in self._read(
                        outbox,
                        label="Durable audit outbox",
                    )
                ]
                if current_outbox != pending:
                    return False
                self._unlink_regular_path(
                    outbox,
                    label="Durable audit outbox",
                )
            except (OSError, ValueError, TypeError):
                return False
            return True

    def append_prepared(self, value: Mapping[str, Any]) -> bool:
        with self._lock:
            row = self.validate_prepared(value)
            event_id = str(row["eventId"])
            try:
                audit_path = self.audit_path
                existing = [
                    self.validate_prepared(item)
                    for item in self._read(
                        audit_path,
                        label="Durable audit ledger",
                    )
                ]
                if event_id in {
                    str(item.get("eventId") or "")
                    for item in existing
                }:
                    return True
                self._append(
                    audit_path,
                    row,
                    label="Durable audit ledger",
                )
            except (OSError, ValueError, TypeError):
                return False
            return True

    def append(self, payload: Mapping[str, Any]) -> bool:
        with self._lock:
            row = self.prepare(payload)
            flushed = self.flush()
            if flushed and self.append_prepared(row):
                return True
            # The durable state mutation may already have committed. Preserve
            # its metadata evidence locally and let startup/idempotent reads
            # drain it without repeating the state change.
            try:
                self._append(
                    self.outbox_path,
                    row,
                    label="Durable audit outbox",
                )
            except (OSError, ValueError, TypeError):
                pass
            return False


__all__ = ["DurableMetadataAudit"]
