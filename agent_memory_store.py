"""Compatibility store and idempotent promotion for accepted VRCForge Memory."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from durable_audit_outbox import DurableMetadataAudit

AGENT_MEMORY_SCHEMA = "vrcforge.agent_memory.v1"
MEMORY_REVIEW_AUDIT_SCHEMA = "vrcforge.memory_review_audit.v1"
MAX_MEMORY_TEXT_CHARS = 2_000
MAX_MEMORY_JSONL_LINE_BYTES = 1_048_576

PathSource = str | Path | Callable[[], str | Path]

_BACKUP_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")
_ATOMIC_TEMP_TOKEN_RE = re.compile(r"^\d+\.[0-9a-f]{8}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _assert_regular_or_absent(path: Path, *, label: str) -> None:
    """Reject link-like and non-file store targets without following them."""

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError(f"{label} cannot be a link or reparse point.")
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"{label} must be a regular file.")


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    left_identity = (int(getattr(left, "st_dev", 0) or 0), int(getattr(left, "st_ino", 0) or 0))
    right_identity = (int(getattr(right, "st_dev", 0) or 0), int(getattr(right, "st_ino", 0) or 0))
    if left_identity[1] and right_identity[1]:
        return left_identity == right_identity
    return True


def _open_regular_file(path: Path, flags: int, *, label: str, mode: int = 0o600) -> int:
    """Open one exact regular file and verify the handle before any content I/O."""

    _assert_regular_or_absent(path, label=label)
    safe_flags = flags | int(getattr(os, "O_BINARY", 0) or 0)
    safe_flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
    descriptor = os.open(path, safe_flags, mode)
    try:
        handle_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(handle_metadata.st_mode):
            raise OSError(f"{label} must be a regular file.")
        _assert_regular_or_absent(path, label=label)
        path_metadata = os.lstat(path)
        if not _same_file_identity(handle_metadata, path_metadata):
            raise OSError(f"{label} changed while it was being opened.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    descriptor = _open_regular_file(path, os.O_RDONLY, label=label)
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        return handle.read()


def _resolve_path(source: PathSource) -> Path:
    value = source() if callable(source) else source
    return Path(value)


def _path_identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_managed_backup_name(primary: Path, candidate: Path) -> bool:
    """Recognize only app-owned backup names beside one primary store."""

    name = candidate.name
    primary_name = primary.name
    stem = primary.stem
    suffix = primary.suffix
    if name in {f"{primary_name}.bak", f"{stem}.backup{suffix}"}:
        return True
    stem_prefix = f"{stem}.backup-"
    if suffix and name.startswith(stem_prefix) and name.endswith(suffix):
        token = name[len(stem_prefix) : -len(suffix)]
        return bool(_BACKUP_TOKEN_RE.fullmatch(token))
    full_prefix = f"{primary_name}.backup-"
    if name.startswith(full_prefix) and name.endswith(".bak"):
        token = name[len(full_prefix) : -len(".bak")]
        return bool(_BACKUP_TOKEN_RE.fullmatch(token))
    return False


def managed_backup_paths(primary: Path, explicit_paths: Iterable[Path] = ()) -> tuple[Path, ...]:
    """Return same-directory explicit and strictly named managed backups."""

    _assert_regular_or_absent(Path(primary), label="Accepted Memory store")
    primary = primary.resolve(strict=False)
    managed_parent = primary.parent
    primary_identity = _path_identity(primary)
    paths: dict[str, Path] = {}
    for raw_path in explicit_paths:
        explicit = Path(raw_path)
        _assert_regular_or_absent(explicit, label="Memory backup")
        resolved = explicit.resolve(strict=False)
        if _path_identity(resolved.parent) != _path_identity(managed_parent):
            raise ValueError("Memory backup path must stay in the managed store directory.")
        identity = _path_identity(resolved)
        if identity != primary_identity:
            paths[identity] = resolved

    if managed_parent.exists():
        for candidate in managed_parent.iterdir():
            if not _is_managed_backup_name(primary, candidate):
                continue
            _assert_regular_or_absent(candidate, label="Managed Memory backup")
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=False)
            identity = _path_identity(resolved)
            if identity != primary_identity:
                paths[identity] = resolved
    return tuple(sorted(paths.values(), key=lambda path: path.name.casefold()))


def managed_atomic_temp_paths(targets: Iterable[Path]) -> tuple[Path, ...]:
    """Return only app-owned atomic-write fragments beside exact targets."""

    normalized_targets: dict[str, Path] = {}
    for raw_target in targets:
        raw_path = Path(raw_target)
        _assert_regular_or_absent(raw_path, label="Accepted Memory store target")
        target = raw_path.resolve(strict=False)
        normalized_targets[_path_identity(target)] = target

    paths: dict[str, Path] = {}
    targets_by_parent: dict[str, list[Path]] = {}
    for target in normalized_targets.values():
        targets_by_parent.setdefault(_path_identity(target.parent), []).append(target)
    for parent_targets in targets_by_parent.values():
        parent = parent_targets[0].parent
        if not parent.exists():
            continue
        for candidate in parent.iterdir():
            matched = False
            for target in parent_targets:
                prefix = f".{target.name}."
                if candidate.name.startswith(prefix) and candidate.name.endswith(".tmp"):
                    token = candidate.name[len(prefix) : -len(".tmp")]
                    matched = bool(_ATOMIC_TEMP_TOKEN_RE.fullmatch(token))
                    if matched:
                        break
            if not matched:
                continue
            _assert_regular_or_absent(candidate, label="Managed Memory atomic temporary")
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=False)
            if _path_identity(resolved.parent) != _path_identity(parent):
                raise ValueError("Managed Memory atomic temporary escaped its store directory.")
            paths[_path_identity(resolved)] = resolved
    return tuple(sorted(paths.values(), key=lambda path: path.name.casefold()))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_text(value: Any, *, field: str, limit: int, required: bool = True) -> str:
    text = str(value or "").strip().replace("\x00", "")
    if required and not text:
        raise ValueError(f"{field} is required.")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds its size limit.")
    return text


def _summarize_text(value: Any, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _safe_id(value: Any, *, field: str) -> str:
    text = _bounded_text(value, field=field, limit=200)
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in text):
        raise ValueError(f"{field} is invalid.")
    return text


class AgentMemoryStore:
    """Read legacy Memory JSONL and add stable review promotions.

    The event shape stays compatible with the existing gateway projection.
    Review-specific audit rows contain identity and digests only, never prose.
    """

    def __init__(
        self,
        log_path: PathSource,
        metadata_audit_path: PathSource,
        *,
        backup_paths: Iterable[PathSource] = (),
        lock: threading.RLock | None = None,
    ) -> None:
        self._log_path_source = log_path
        self._metadata_audit_path_source = metadata_audit_path
        self._backup_path_sources = tuple(backup_paths)
        self._lock = lock or threading.RLock()
        self._metadata_audit = DurableMetadataAudit(
            lambda: self.metadata_audit_path,
            schema=MEMORY_REVIEW_AUDIT_SCHEMA,
            allowed_fields={
                "event",
                "memoryId",
                "candidateId",
                "promotionId",
                "scope",
                "projectRootDigest",
                "contentDigest",
                "reasonCode",
            },
        )

    @property
    def log_path(self) -> Path:
        path = _resolve_path(self._log_path_source)
        _assert_regular_or_absent(path, label="Accepted Memory store")
        return path

    @property
    def metadata_audit_path(self) -> Path:
        return _resolve_path(self._metadata_audit_path_source)

    @property
    def backup_paths(self) -> tuple[Path, ...]:
        explicit = tuple(_resolve_path(path) for path in self._backup_path_sources)
        return managed_backup_paths(self.log_path, explicit)

    def _read_rows_from(self, path: Path) -> Iterator[dict[str, Any]]:
        try:
            descriptor = _open_regular_file(path, os.O_RDONLY, label="Accepted Memory store")
        except FileNotFoundError:
            return
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            while True:
                raw_line = handle.readline(MAX_MEMORY_JSONL_LINE_BYTES + 3)
                if not raw_line:
                    break
                complete = raw_line.endswith(b"\n")
                content = raw_line.rstrip(b"\r\n") if complete else raw_line
                oversized = len(content) > MAX_MEMORY_JSONL_LINE_BYTES
                if not complete and len(raw_line) > MAX_MEMORY_JSONL_LINE_BYTES:
                    oversized = True
                    while raw_line and not raw_line.endswith(b"\n"):
                        raw_line = handle.readline(MAX_MEMORY_JSONL_LINE_BYTES + 3)
                if oversized:
                    continue
                try:
                    payload = json.loads(content.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    yield payload

    def _project(self, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        self._cleanup_atomic_temporaries((self.log_path, *self.backup_paths))
        all_records = self._project_path(self.log_path, include_deleted=True)
        self._reconcile_metadata_audit(all_records)
        if include_deleted:
            return all_records
        return self._project_path(self.log_path, include_deleted=False)

    def _reconcile_metadata_audit(
        self,
        records: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for row in self._metadata_audit.pending():
            event = str(row.get("event") or "")
            memory_id = str(row.get("memoryId") or "")
            record = records.get(memory_id)
            committed = False
            if event == "promotion_committed":
                committed = bool(
                    record
                    and str(record.get("promotionId") or "")
                    == str(row.get("promotionId") or "")
                    and str(record.get("candidateId") or "")
                    == str(row.get("candidateId") or "")
                )
            elif event == "memory_physically_erased":
                committed = (
                    record is None
                    and bool(memory_id)
                    and self._memory_absent_from_managed_stores(memory_id)
                )
            if committed:
                self._metadata_audit.commit_staged(row)

    def _memory_absent_from_managed_stores(self, memory_id: str) -> bool:
        """Fail closed until primary, backups, and atomic fragments are clean."""

        try:
            targets = (self.log_path, *self.backup_paths)
            fragments = managed_atomic_temp_paths(targets)
            return not any(
                self._contains_memory_identity(path, {memory_id})
                for path in (*targets, *fragments)
            )
        except (OSError, ValueError):
            return False

    @staticmethod
    def _cleanup_atomic_temporaries(targets: Iterable[Path]) -> None:
        normalized_targets = tuple(Path(path) for path in targets)
        for target in normalized_targets:
            _assert_regular_or_absent(target, label="Accepted Memory store target")
        for temporary in managed_atomic_temp_paths(normalized_targets):
            temporary.unlink()
        if managed_atomic_temp_paths(normalized_targets):
            raise OSError("Accepted Memory atomic temporary cleanup verification failed.")

    def _project_path(self, path: Path, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        memories: dict[str, dict[str, Any]] = {}
        deleted: set[str] = set()
        for event in self._read_rows_from(path):
            memory_id = str(event.get("memoryId") or "").strip()
            if not memory_id:
                continue
            if str(event.get("status") or "") == "deleted" or event.get("event") == "memory_deleted":
                deleted.add(memory_id)
            previous = memories.get(memory_id, {})
            memories[memory_id] = {
                **previous,
                **event,
                "id": memory_id,
                "memoryId": memory_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
            }
        if include_deleted:
            return memories
        return {memory_id: memory for memory_id, memory in memories.items() if memory_id not in deleted}

    def project(self, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._project(include_deleted=include_deleted))

    def cleanup_atomic_temporaries(self) -> None:
        with self._lock:
            self._cleanup_atomic_temporaries((self.log_path, *self.backup_paths))

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._project().values())
        values.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        return values

    def get(self, memory_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        normalized_id = _safe_id(memory_id, field="memoryId")
        with self._lock:
            return self._project(include_deleted=include_deleted).get(normalized_id)

    def create(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        values = dict(params or {})
        text = _summarize_text(values.get("text") or values.get("content"), MAX_MEMORY_TEXT_CHARS)
        if not text:
            raise ValueError("Memory text is required.")
        scope = str(values.get("scope") or "project").strip().casefold()
        if scope not in {"user", "project"}:
            raise ValueError("Memory scope must be user or project.")
        project_root = str(values.get("projectRoot") or values.get("project_root") or values.get("projectPath") or "").strip()
        if scope == "project" and not project_root:
            raise ValueError("Project Memory requires projectRoot.")
        if scope == "user":
            project_root = ""
        memory_id = f"mem_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        now = _utc_now_iso()
        row = {
            "schema": AGENT_MEMORY_SCHEMA,
            "id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
            "createdAt": now,
            "updatedAt": now,
            "event": "memory_created",
            "status": "active",
            "memoryId": memory_id,
            "scope": scope,
            "kind": _summarize_text(values.get("kind") or "preference", 80),
            "text": text,
            "projectRoot": project_root,
            "source": _summarize_text(values.get("source") or "user", 120),
        }
        with self._lock:
            self._append_jsonl(self.log_path, row)
            created = self._project().get(memory_id)
            if created is None:
                raise OSError("Memory create event was not durable.")
            return dict(created)

    def delete(self, memory_id: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        normalized_id = _safe_id(memory_id, field="memoryId")
        with self._lock:
            current = self._project(include_deleted=True)
            if normalized_id not in current:
                raise KeyError(normalized_id)
            if str(current[normalized_id].get("status") or "") == "deleted":
                return dict(current[normalized_id])
            now = _utc_now_iso()
            row = {
                "schema": AGENT_MEMORY_SCHEMA,
                "id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": now,
                "updatedAt": now,
                "event": "memory_deleted",
                "status": "deleted",
                "memoryId": normalized_id,
                "reason": _summarize_text((params or {}).get("reason"), 500),
            }
            self._append_jsonl(self.log_path, row)
            return dict(self._project(include_deleted=True)[normalized_id])

    @staticmethod
    def _normalized_project(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            text = str(Path(text).resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            pass
        return os.path.normcase(os.path.normpath(text)).replace("\\", "/").rstrip("/").casefold()

    def clear(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        values = dict(params or {})
        scope = str(values.get("scope") or "").strip().casefold()
        if scope not in {"user", "project"}:
            raise ValueError("Memory scope must be user or project.")
        project_root = str(values.get("projectRoot") or values.get("project_root") or "").strip()
        if scope == "project" and not project_root:
            raise ValueError("Clearing project Memory requires projectRoot.")
        target_project = self._normalized_project(project_root)
        cleared = 0
        with self._lock:
            for memory_id, memory in list(self._project().items()):
                if str(memory.get("scope") or "") != scope:
                    continue
                if scope == "project" and self._normalized_project(str(memory.get("projectRoot") or "")) != target_project:
                    continue
                self.delete(memory_id, {"reason": values.get("reason") or "clear"})
                cleared += 1
        return {"cleared": cleared}

    def list(self, *, limit: int = 50, project_root: str = "", scope: str = "") -> list[dict[str, Any]]:
        normalized_project = self._normalized_project(project_root)
        with self._lock:
            memories = list(self._project().values())
        if normalized_project:
            memories = [
                memory
                for memory in memories
                if str(memory.get("scope") or "") == "user"
                or (
                    str(memory.get("scope") or "") == "project"
                    and self._normalized_project(str(memory.get("projectRoot") or "")) == normalized_project
                )
            ]
        else:
            memories = [memory for memory in memories if str(memory.get("scope") or "") == "user"]
        normalized_scope = str(scope or "").strip().casefold()
        if normalized_scope:
            memories = [memory for memory in memories if str(memory.get("scope") or "") == normalized_scope]
        memories.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        return [dict(memory) for memory in memories[: max(1, min(int(limit), 200))]]

    # Compatibility facade for a later strangler migration of the existing
    # gateway methods. No caller needs to change route schemas during extraction.
    def create_agent_memory(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "memory": self.create(params)}

    def delete_agent_memory(self, memory_id: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "memory": self.delete(memory_id, params)}

    def clear_agent_memory(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, **self.clear(params)}

    def list_agent_memory(self, *, limit: int = 50, project_root: str = "", scope: str = "") -> dict[str, Any]:
        memories = self.list(limit=limit, project_root=project_root, scope=scope)
        return {
            "ok": True,
            "schema": "vrcforge.agent_memory_list.v1",
            "memories": memories,
            "count": len(memories),
        }

    def _project_agent_memory(self, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        return self.project(include_deleted=include_deleted)

    @staticmethod
    def stable_memory_id(promotion_id: str) -> str:
        normalized = _safe_id(promotion_id, field="promotionId")
        digest = hashlib.sha256(f"vrcforge:memory-promotion:{normalized}".encode("utf-8")).hexdigest()[:24]
        return f"mem_review_{digest}"

    def promote(
        self,
        *,
        promotion_id: str,
        candidate_id: str,
        scope: str,
        project_root: str,
        kind: str,
        text: str,
    ) -> dict[str, Any]:
        normalized_promotion = _safe_id(promotion_id, field="promotionId")
        normalized_candidate = _safe_id(candidate_id, field="candidateId")
        normalized_scope = str(scope or "").strip().casefold()
        if normalized_scope not in {"user", "project"}:
            raise ValueError("scope must be user or project.")
        normalized_project = str(project_root or "").strip()
        if normalized_scope == "project" and not normalized_project:
            raise ValueError("Project promotion requires projectRoot.")
        if normalized_scope == "user" and normalized_project:
            raise ValueError("User promotion cannot carry projectRoot.")
        normalized_kind = _bounded_text(kind or "preference", field="kind", limit=80)
        normalized_text = _bounded_text(text, field="text", limit=MAX_MEMORY_TEXT_CHARS)
        memory_id = self.stable_memory_id(normalized_promotion)

        with self._lock:
            existing_by_promotion = next(
                (
                    memory
                    for memory in self._project(include_deleted=True).values()
                    if str(memory.get("promotionId") or "") == normalized_promotion
                ),
                None,
            )
            if existing_by_promotion is not None:
                comparable = {
                    "memoryId": memory_id,
                    "candidateId": normalized_candidate,
                    "scope": normalized_scope,
                    "projectRoot": normalized_project,
                    "kind": normalized_kind,
                    "text": normalized_text,
                }
                actual = {key: existing_by_promotion.get(key) for key in comparable}
                if actual != comparable:
                    raise ValueError("promotionId already exists with different content or scope.")
                return dict(existing_by_promotion)

            now = _utc_now_iso()
            row = {
                "schema": AGENT_MEMORY_SCHEMA,
                "id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": now,
                "updatedAt": now,
                "event": "memory_created",
                "status": "active",
                "memoryId": memory_id,
                "scope": normalized_scope,
                "kind": normalized_kind,
                "text": normalized_text,
                "projectRoot": normalized_project,
                "source": "consolidation_review",
                "candidateId": normalized_candidate,
                "promotionId": normalized_promotion,
            }
            audit_row = self._metadata_audit.stage(
                {
                    "event": "promotion_committed",
                    "memoryId": memory_id,
                    "candidateId": normalized_candidate,
                    "promotionId": normalized_promotion,
                    "scope": normalized_scope,
                    "projectRootDigest": (
                        hashlib.sha256(os.path.normcase(normalized_project).encode("utf-8")).hexdigest()
                        if normalized_project
                        else ""
                    ),
                    "contentDigest": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
                }
            )
            self._append_jsonl(self.log_path, row)
            self._metadata_audit.commit_staged(audit_row)
            result = self._project().get(memory_id)
            if result is None:
                raise OSError("Accepted Memory promotion was not durable.")
            return result

    def _append_jsonl(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = _open_regular_file(
            path,
            os.O_RDWR | os.O_CREAT,
            label="Accepted Memory store",
        )
        encoded = (
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "r+b", closefd=True) as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) not in {b"\n", b"\r"}:
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
            handle.seek(0, os.SEEK_END)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def _append_metadata_audit(self, payload: Mapping[str, Any]) -> None:
        self._metadata_audit.append(payload)

    @staticmethod
    def _atomic_rewrite(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _assert_regular_or_absent(path, label="Accepted Memory store target")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            descriptor = _open_regular_file(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                label="Accepted Memory atomic temporary",
            )
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _assert_regular_or_absent(path, label="Accepted Memory store target")
            os.replace(temporary, path)
            _assert_regular_or_absent(path, label="Accepted Memory store target")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _rewrite_without_memories(self, path: Path, memory_ids: set[str]) -> None:
        try:
            raw_lines = _read_regular_bytes(path, label="Accepted Memory store target").splitlines()
        except FileNotFoundError:
            return
        retained: list[bytes] = []
        target_bytes = {memory_id.encode("utf-8") for memory_id in memory_ids}
        for raw_line in raw_lines:
            remove = False
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                # A crash-fragment containing the exact target identity cannot
                # remain as hidden content after an explicit permanent erase.
                remove = any(target in raw_line for target in target_bytes)
            else:
                remove = isinstance(payload, dict) and str(payload.get("memoryId") or "") in memory_ids
            if not remove:
                retained.append(raw_line)
        content = b"\n".join(retained)
        if content:
            content += b"\n"
        self._atomic_rewrite(path, content)

    def _contains_memory_identity(self, path: Path, memory_ids: set[str]) -> bool:
        """Verify target records, without treating duplicate prose as identity."""

        try:
            raw_lines = _read_regular_bytes(path, label="Accepted Memory store target").splitlines()
        except FileNotFoundError:
            return False
        target_bytes = {memory_id.encode("utf-8") for memory_id in memory_ids}
        for raw_line in raw_lines:
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                if any(target in raw_line for target in target_bytes):
                    return True
                continue
            if isinstance(payload, dict) and str(payload.get("memoryId") or "") in memory_ids:
                return True
        return False

    def physical_erase(self, memory_id: str) -> dict[str, Any]:
        normalized_id = _safe_id(memory_id, field="memoryId")
        result = self.physical_erase_many([normalized_id])
        if normalized_id not in result["memoryIds"]:
            return {"erased": False, "memoryId": normalized_id, "alreadyAbsent": True}
        return {
            "erased": True,
            "memoryId": normalized_id,
            "contentDigest": result["contentDigests"].get(normalized_id, ""),
        }

    def physical_erase_many(self, memory_ids: Iterable[str]) -> dict[str, Any]:
        normalized_ids = {_safe_id(memory_id, field="memoryId") for memory_id in memory_ids}
        if not normalized_ids:
            return {"erased": False, "memoryIds": [], "contentDigests": {}}
        with self._lock:
            targets = (self.log_path, *self.backup_paths)
            self._cleanup_atomic_temporaries(targets)
            records: dict[str, dict[str, Any]] = {}
            primary = self._project(include_deleted=True)
            for memory_id in normalized_ids:
                if memory_id in primary:
                    records[memory_id] = primary[memory_id]
            if len(records) < len(normalized_ids):
                for backup in targets[1:]:
                    projected = self._project_path(backup, include_deleted=True)
                    for memory_id in normalized_ids.difference(records):
                        if memory_id in projected:
                            records[memory_id] = projected[memory_id]
                    if len(records) == len(normalized_ids):
                        break
            present_ids = set(records)
            for path in targets:
                try:
                    data = _read_regular_bytes(path, label="Accepted Memory store target")
                except FileNotFoundError:
                    continue
                for memory_id in normalized_ids.difference(present_ids):
                    if memory_id.encode("utf-8") in data:
                        present_ids.add(memory_id)
            if not present_ids:
                return {"erased": False, "memoryIds": [], "contentDigests": {}}
            texts = {memory_id: str(record.get("text") or "") for memory_id, record in records.items()}
            content_digests = {
                memory_id: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for memory_id, text in texts.items()
            }
            staged_audits = []
            for memory_id in sorted(present_ids):
                existing = records.get(memory_id, {})
                staged_audits.append(
                    self._metadata_audit.stage(
                        {
                            "event": "memory_physically_erased",
                            "memoryId": memory_id,
                            "candidateId": existing.get("candidateId"),
                            "promotionId": existing.get("promotionId"),
                            "scope": existing.get("scope"),
                            "projectRootDigest": (
                                hashlib.sha256(
                                    os.path.normcase(
                                        str(existing.get("projectRoot") or "")
                                    ).encode("utf-8")
                                ).hexdigest()
                                if existing.get("projectRoot")
                                else ""
                            ),
                            "contentDigest": content_digests.get(memory_id, ""),
                        }
                    )
                )
            for path in targets:
                self._rewrite_without_memories(path, present_ids)
            self._cleanup_atomic_temporaries(targets)
            for path in targets:
                _assert_regular_or_absent(path, label="Accepted Memory store target")
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                if self._contains_memory_identity(path, present_ids):
                    raise OSError("Permanent erase verification failed for accepted Memory content.")
            for audit_row in staged_audits:
                self._metadata_audit.commit_staged(audit_row)
            return {
                "erased": True,
                "memoryIds": sorted(present_ids),
                "contentDigests": content_digests,
            }


__all__ = [
    "AGENT_MEMORY_SCHEMA",
    "AgentMemoryStore",
    "MAX_MEMORY_JSONL_LINE_BYTES",
    "MAX_MEMORY_TEXT_CHARS",
    "MEMORY_REVIEW_AUDIT_SCHEMA",
    "PathSource",
    "managed_atomic_temp_paths",
    "managed_backup_paths",
]
