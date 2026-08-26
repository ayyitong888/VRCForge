from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import zipfile
import zlib
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agent_command_safety import (
    is_path_within,
    looks_like_absolute_path,
    normalize_filesystem_path,
)
from agent_gateway import (
    ADJUSTMENT_CHECKPOINT_TARGETS,
    APPLY_RECOVERY_SCHEMA,
    AgentGatewayConfig,
    AgentGatewayError,
    CHECKPOINT_ARCHIVE_BYTES_PER_MB,
    CHECKPOINT_ARCHIVE_PROTECTED_RECENT_COUNT,
    LOCAL_STATE_CHECKPOINT_SCOPE,
    PROJECT_CHAT_CHECKPOINT_MEMBER,
    ROLLBACK_COVERAGE_AUDIT_SCHEMA,
    ROLLBACK_FRAMEWORK_PACKAGES,
    UNITY_PROJECT_CHECKPOINT_SCOPE,
    UNITY_RESTORE_GENERATED_CACHE_DIRS,
    UNITY_RESTORE_PRESERVED_CACHE_DIRS,
    _checkpoint_record_state,
    _load_strict_json,
    _path_is_link_like,
    _split_lf_jsonl_lines,
    atomic_write_json,
    ensure_dict,
    ensure_list,
    ensure_string_list,
    flush_and_fsync,
    fsync_directory_best_effort,
    fsync_file_path,
    normalize_checkpoint_archive_dir,
    normalize_checkpoint_archive_max_size_mb,
    redact_sensitive,
    utc_now_iso,
)


class _LocalStateRestoreRecoveryError(RuntimeError):
    """Report a failed rollback without losing the only recovery copy."""

    def __init__(self, message: str, recovery_paths: list[Path]) -> None:
        self.recovery_paths = tuple(recovery_paths)
        rendered = ", ".join(str(path) for path in self.recovery_paths)
        super().__init__(f"{message}; recovery data remains at: {rendered}")


@dataclass(frozen=True, slots=True)
class CheckpointApprovalRecoveryPorts:
    apply_recovery_blocks_writes: Callable[[dict[str, Any]], bool]
    create_pre_write_checkpoint: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]
    finish_apply_recovery: Callable[..., dict[str, Any]]
    resolve_apply_recoveries_for_checkpoint: Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CheckpointSkillsPort:
    write_lock: AbstractContextManager[object]
    user_skills_dir: Callable[[], Path]


@dataclass(frozen=True, slots=True)
class CheckpointRecoveryState:
    checkpoint_storage_lock: AbstractContextManager[object]
    skill_package_write_lock: AbstractContextManager[object]


@dataclass(frozen=True, slots=True)
class CheckpointRecoveryPorts:
    state: CheckpointRecoveryState
    approval: CheckpointApprovalRecoveryPorts
    project_chat_checkpoint_lock: AbstractContextManager[object]
    checkpoint_log_path: Callable[[], Path]
    adjustment_checkpoint_log_path: Callable[[], Path]
    apply_recovery_log_path: Callable[[], Path]
    checkpoint_store_dir: Callable[[], Path]
    default_checkpoint_store_dir: Callable[[], Path]
    audit_dir: Callable[[], Path]
    user_constraints_path: Callable[[], Path]
    skills: CheckpointSkillsPort
    ensure_config: Callable[[], AgentGatewayConfig]
    save_config: Callable[[AgentGatewayConfig], None]
    append_audit: Callable[[dict[str, Any]], None]
    recent_audit_logs: Callable[..., list[dict[str, Any]]]
    run_git: Callable[..., dict[str, Any]]
    ensure_jsonl_append_boundary_locked: Callable[[Path], None]


class AgentCheckpointRecoveryService:
    """Own checkpoint, recovery, and adjustment-timeline behavior.

    The gateway supplies existing paths, locks, audit/config persistence, and
    narrow approval callbacks through typed ports. This owner keeps the
    project-chat lock binding and Unity restore hooks that belong to checkpoint
    lifecycle. It creates no process, task, file handle, lock, or communication
    endpoint of its own.
    """

    __slots__ = (
        "_archive_validation_cache",
        "_checkpoint_entry_cache",
        "_checkpoint_project_root_resolver",
        "_checkpoint_restore_handler",
        "_checkpoint_restore_prepare_handler",
        "_ports",
        "_project_chat_checkpoint_lock",
    )

    def __init__(self, ports: CheckpointRecoveryPorts) -> None:
        self._ports = ports
        # Access is serialized by checkpoint_storage_lock. Cache entries are
        # bounded and tied to both archive identity and recovery metadata, so a
        # replaced archive or edited pathspec can never reuse a stale verdict.
        self._archive_validation_cache: dict[tuple[str, int, int, str], tuple[bool, str]] = {}
        self._checkpoint_entry_cache: dict[
            tuple[str, int, int], tuple[dict[str, Any], ...]
        ] = {}
        self._project_chat_checkpoint_lock = ports.project_chat_checkpoint_lock
        self._checkpoint_project_root_resolver: Callable[[], str] | None = None
        self._checkpoint_restore_prepare_handler: Callable[[Path], dict[str, Any]] | None = None
        self._checkpoint_restore_handler: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None

    @property
    def project_chat_checkpoint_lock(self) -> AbstractContextManager[object]:
        return self._project_chat_checkpoint_lock

    def bind_project_chat_checkpoint_lock(self, lock: AbstractContextManager[object]) -> None:
        if lock is None or not hasattr(lock, "__enter__") or not hasattr(lock, "__exit__"):
            raise ValueError("project chat checkpoint lock must be a context manager")
        self._project_chat_checkpoint_lock = lock

    @property
    def checkpoint_project_root_resolver(self) -> Callable[[], str] | None:
        return self._checkpoint_project_root_resolver

    @checkpoint_project_root_resolver.setter
    def checkpoint_project_root_resolver(self, callback: Callable[[], str] | None) -> None:
        self._checkpoint_project_root_resolver = callback

    @property
    def checkpoint_restore_prepare_handler(self) -> Callable[[Path], dict[str, Any]] | None:
        return self._checkpoint_restore_prepare_handler

    @checkpoint_restore_prepare_handler.setter
    def checkpoint_restore_prepare_handler(self, callback: Callable[[Path], dict[str, Any]] | None) -> None:
        self._checkpoint_restore_prepare_handler = callback

    @property
    def checkpoint_restore_handler(self) -> Callable[[Path, dict[str, Any]], dict[str, Any]] | None:
        return self._checkpoint_restore_handler

    @checkpoint_restore_handler.setter
    def checkpoint_restore_handler(
        self, callback: Callable[[Path, dict[str, Any]], dict[str, Any]] | None
    ) -> None:
        self._checkpoint_restore_handler = callback

    def list_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._list_checkpoints_locked(params)

    def inspect_checkpoint_storage(self) -> dict[str, Any]:
        """Inspect checkpoint persistence without mutating it.

        Doctor uses this instead of ``list_checkpoints`` because the normal
        projection intentionally skips malformed JSONL rows.  The response is
        deliberately path-free; local paths and malformed row contents belong
        in the local quarantine evidence, never in diagnostics/UI payloads.
        """

        with self._ports.state.checkpoint_storage_lock:
            return self._inspect_checkpoint_storage_locked()

    def _inspect_checkpoint_storage_locked(self) -> dict[str, Any]:
        store_dir = self._ports.checkpoint_store_dir()
        log_path = self._ports.checkpoint_log_path()
        issues: list[str] = []

        try:
            if store_dir.exists():
                if _path_is_link_like(store_dir) or not store_dir.is_dir():
                    issues.append("unsafe_store_directory")
            else:
                issues.append("missing_store_directory")
        except OSError:
            issues.append("unreadable_store_directory")

        raw = b""
        valid_count = 0
        invalid_count = 0
        unknown_schema_count = 0
        if log_path.exists():
            try:
                if _path_is_link_like(log_path) or not log_path.is_file():
                    issues.append("unsafe_checkpoint_log")
                else:
                    raw = log_path.read_bytes()
                    for index, raw_line in enumerate(_split_lf_jsonl_lines(raw)):
                        if not raw_line.strip():
                            continue
                        try:
                            payload = _load_strict_json(raw_line.decode("utf-8-sig" if index == 0 else "utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                            invalid_count += 1
                            continue
                        state = _checkpoint_record_state(payload)
                        if state == "valid":
                            valid_count += 1
                        elif state == "unknown_schema":
                            unknown_schema_count += 1
                        else:
                            invalid_count += 1
            except OSError:
                issues.append("unreadable_checkpoint_log")
        if invalid_count:
            issues.append("malformed_checkpoint_rows")
        if unknown_schema_count:
            issues.append("unknown_checkpoint_schema")

        status = "error" if any(
            issue in {"unsafe_store_directory", "unreadable_store_directory", "unsafe_checkpoint_log", "unreadable_checkpoint_log"}
            for issue in issues
        ) else "warning" if issues else "ok"
        return {
            "ok": status != "error",
            "schema": "vrcforge.checkpoint_storage.inspect.v1",
            "status": status,
            "issues": issues,
            "storeDirectoryExists": store_dir.is_dir() if store_dir.exists() else False,
            "logExists": log_path.is_file() if log_path.exists() else False,
            "validRowCount": valid_count,
            "invalidRowCount": invalid_count,
            "unknownSchemaCount": unknown_schema_count,
            "snapshot": hashlib.sha256(raw).hexdigest(),
            "fixable": not any(issue.startswith("unsafe_") or issue.startswith("unreadable_") for issue in issues),
        }

    def repair_checkpoint_storage(self, *, expected_snapshot: str = "") -> dict[str, Any]:
        """Repair app-owned checkpoint persistence under its consistency lock.

        Missing directories are recreated.  Malformed rows are copied to a
        content-addressed quarantine sidecar before the valid JSONL projection
        is atomically rewritten.  Nothing is deleted and a stale inspection
        snapshot fails closed.
        """

        with self._ports.state.checkpoint_storage_lock:
            before = self._inspect_checkpoint_storage_locked()
            if expected_snapshot and not hmac.compare_digest(str(before["snapshot"]), str(expected_snapshot)):
                return {
                    "ok": False,
                    "schema": "vrcforge.checkpoint_storage.repair.v1",
                    "status": "busy",
                    "changed": False,
                    "reason": "snapshot_changed",
                    "before": before,
                    "after": before,
                }
            if not before.get("fixable"):
                return {
                    "ok": False,
                    "schema": "vrcforge.checkpoint_storage.repair.v1",
                    "status": "needs_user_action",
                    "changed": False,
                    "reason": "unsafe_or_unreadable_storage",
                    "before": before,
                    "after": before,
                }

            changed = False
            store_dir = self._ports.checkpoint_store_dir()
            if not store_dir.exists():
                store_dir.mkdir(parents=True, exist_ok=False)
                fsync_directory_best_effort(store_dir.parent)
                changed = True

            log_path = self._ports.checkpoint_log_path()
            raw = log_path.read_bytes() if log_path.exists() else b""
            valid_lines: list[bytes] = []
            invalid_lines: list[bytes] = []
            for index, raw_line in enumerate(_split_lf_jsonl_lines(raw, keepends=True)):
                candidate = raw_line.rstrip(b"\r\n")
                if not candidate.strip():
                    valid_lines.append(raw_line)
                    continue
                try:
                    payload = _load_strict_json(candidate.decode("utf-8-sig" if index == 0 else "utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    invalid_lines.append(raw_line)
                    continue
                if _checkpoint_record_state(payload) in {"valid", "unknown_schema"}:
                    valid_lines.append(raw_line)
                else:
                    invalid_lines.append(raw_line)

            quarantine_id = ""
            if invalid_lines:
                invalid_bytes = b"".join(invalid_lines)
                quarantine_id = hashlib.sha256(invalid_bytes).hexdigest()[:16]
                quarantine_dir = self._ports.audit_dir() / "quarantine"
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                quarantine_path = quarantine_dir / f"checkpoints.invalid.{quarantine_id}.jsonl"
                if os.path.lexists(quarantine_path):
                    if (
                        _path_is_link_like(quarantine_path)
                        or not quarantine_path.is_file()
                        or quarantine_path.read_bytes() != invalid_bytes
                    ):
                        return {
                            "ok": False,
                            "schema": "vrcforge.checkpoint_storage.repair.v1",
                            "status": "conflict",
                            "changed": changed,
                            "reason": "quarantine_collision",
                            "quarantineId": quarantine_id,
                            "before": before,
                            "after": self._inspect_checkpoint_storage_locked(),
                        }
                else:
                    temporary = quarantine_path.with_name(f".{quarantine_path.name}.{secrets.token_hex(8)}.tmp")
                    with temporary.open("xb") as handle:
                        handle.write(invalid_bytes)
                        flush_and_fsync(handle)
                    temporary.replace(quarantine_path)
                    fsync_directory_best_effort(quarantine_dir)
                    if _path_is_link_like(quarantine_path) or quarantine_path.read_bytes() != invalid_bytes:
                        return {
                            "ok": False,
                            "schema": "vrcforge.checkpoint_storage.repair.v1",
                            "status": "conflict",
                            "changed": changed,
                            "reason": "quarantine_verification_failed",
                            "quarantineId": quarantine_id,
                            "before": before,
                            "after": self._inspect_checkpoint_storage_locked(),
                        }

                repaired_bytes = b"".join(valid_lines)
                if repaired_bytes and not repaired_bytes.endswith((b"\n", b"\r")):
                    repaired_bytes += b"\n"
                temporary = log_path.with_name(f".{log_path.name}.{secrets.token_hex(8)}.tmp")
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("xb") as handle:
                    handle.write(repaired_bytes)
                    flush_and_fsync(handle)
                temporary.replace(log_path)
                fsync_directory_best_effort(log_path.parent)
                changed = True

            after = self._inspect_checkpoint_storage_locked()
            after_ok = after.get("status") == "ok"
            return {
                "ok": after_ok,
                "schema": "vrcforge.checkpoint_storage.repair.v1",
                "status": "repaired" if changed and after_ok else "healthy" if after_ok else "needs_user_action",
                "changed": changed,
                "quarantineId": quarantine_id,
                "before": before,
                "after": after,
            }

    def _list_checkpoints_locked(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        limit = max(1, min(int(params.get("limit") or 50), 500))
        project_filter = str(params.get("project_root") or params.get("projectRoot") or "").strip()
        entries = self._read_checkpoint_entries(limit=500)
        if project_filter:
            normalized = normalize_filesystem_path(project_filter)
            entries = [entry for entry in entries if normalize_filesystem_path(str(entry.get("projectRoot") or "")) == normalized]
        entries = entries[:limit]
        projected: list[dict[str, Any]] = []
        for entry in entries:
            metadata = self._checkpoint_archive_metadata_available(entry)
            projected.append(entry if metadata.get("ok") else ensure_dict(metadata.get("checkpoint")))
        return {"ok": True, "checkpoints": projected, "count": len(projected)}

    def checkpoint_archive_usage(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._checkpoint_archive_usage_locked(config)

    def _checkpoint_archive_usage_locked(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self._ports.ensure_config()
        archives = self._checkpoint_archive_files()
        total_bytes = sum(item["sizeBytes"] for item in archives)
        active_recovery_ids = self._protected_checkpoint_archive_ids(include_recent=False)
        protected_ids = self._protected_checkpoint_archive_ids(include_recent=True)
        labels = self._checkpoint_archive_labels()
        items = [
            {
                "checkpointId": item["checkpointId"],
                "path": str(item["path"]),
                "sizeBytes": item["sizeBytes"],
                "sizeMb": round(item["sizeBytes"] / CHECKPOINT_ARCHIVE_BYTES_PER_MB, 2),
                "modifiedAt": item["modifiedAt"],
                "protected": item["checkpointId"] in protected_ids,
                "protectionReason": (
                    "active_recovery"
                    if item["checkpointId"] in active_recovery_ids
                    else "recent"
                    if item["checkpointId"] in protected_ids
                    else ""
                ),
                "label": labels.get(item["checkpointId"], ""),
            }
            for item in sorted(archives, key=lambda x: x["modifiedAt"], reverse=True)
        ]
        return {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_storage.v2",
            "directory": str(self._ports.checkpoint_store_dir()),
            "defaultDirectory": str(self._ports.default_checkpoint_store_dir()),
            "relocated": getattr(self, "_checkpoint_store_override", None) is not None,
            "sizeBytes": total_bytes,
            "sizeMb": round(total_bytes / CHECKPOINT_ARCHIVE_BYTES_PER_MB, 2),
            "archiveCount": len(archives),
            "protectedCount": sum(1 for item in items if item["protected"]),
            "maxSizeMb": normalize_checkpoint_archive_max_size_mb(config.checkpoint_archive_max_size_mb),
            "archives": items[:500],
        }

    def _checkpoint_archive_labels(self) -> dict[str, str]:
        """checkpointId -> 简短标签，便于前端列表辨认存档来源。"""
        labels: dict[str, str] = {}
        for entry in self._read_checkpoint_entries(limit=1000):
            cid = str(entry.get("id") or "").strip()
            if not cid or cid in labels:
                continue
            label = str(
                entry.get("targetTool")
                or entry.get("reason")
                or entry.get("strategy")
                or ""
            ).strip()
            created = str(entry.get("createdAt") or "").strip()
            labels[cid] = (f"{label} · {created}" if label and created else label or created)
        return labels

    def delete_checkpoint_archives(self, checkpoint_ids: Any) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._delete_checkpoint_archives_locked(checkpoint_ids)

    def _delete_checkpoint_archives_locked(self, checkpoint_ids: Any) -> dict[str, Any]:
        """删除用户在面板里勾选的存档；活跃恢复检查点强制保护，不会被删。"""
        requested = {
            str(cid).strip()
            for cid in (checkpoint_ids or [])
            if str(cid).strip()
        }
        archives = self._checkpoint_archive_files()
        protected_ids = self._protected_checkpoint_archive_ids(include_recent=True)
        deleted: list[dict[str, Any]] = []
        protected_skipped: list[str] = []
        for archive in archives:
            cid = archive["checkpointId"]
            if cid not in requested:
                continue
            if cid in protected_ids:
                protected_skipped.append(cid)
                continue
            path = archive["path"]
            try:
                path.unlink()
            except OSError as exc:
                self._ports.append_audit(
                    {
                        "event": "checkpoint_archive_delete_failed",
                        "path": str(path),
                        "error": str(exc),
                    }
                )
                continue
            deleted.append(
                {
                    "path": str(path),
                    "checkpointId": cid,
                    "sizeBytes": archive["sizeBytes"],
                }
            )
            self._remove_empty_checkpoint_archive_parents(path.parent)
        if deleted:
            self._ports.append_audit(
                {
                    "event": "checkpoint_archive_deleted",
                    "deletedCount": len(deleted),
                    "deletedBytes": sum(item["sizeBytes"] for item in deleted),
                    "protectedSkipped": protected_skipped,
                }
            )
        usage = self.checkpoint_archive_usage()
        return {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_delete.v1",
            "directory": str(self._ports.checkpoint_store_dir()),
            "requestedCount": len(requested),
            "deletedCount": len(deleted),
            "deletedBytes": sum(item["sizeBytes"] for item in deleted),
            "protectedSkipped": protected_skipped,
            "deleted": deleted[:50],
            "sizeBytes": usage["sizeBytes"],
            "sizeMb": usage["sizeMb"],
            "archiveCount": usage["archiveCount"],
        }

    def relocate_checkpoint_archives(self, target_directory: Any) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._relocate_checkpoint_archives_locked(target_directory)

    def _relocate_checkpoint_archives_locked(self, target_directory: Any) -> dict[str, Any]:
        """把检查点存档目录迁到新位置：先复制 ZIP、改写 checkpoints.jsonl 中的
        archivePath、再切换配置、最后删除旧文件。任何一步崩溃都不会让回滚失效，
        因为旧目录在改写+切配置成功前始终保持可用。"""
        config = self._ports.ensure_config()
        raw = normalize_checkpoint_archive_dir(target_directory)
        if not raw:
            return {"ok": False, "code": "directory_required", "error": "checkpoint archive directory required"}
        # 安全闸：有未结的写入恢复/回滚时拒绝迁移，避免迁移途中回滚找不到旧存档。
        if self._active_apply_recoveries():
            return {
                "ok": False,
                "code": "active_recovery",
                "error": "an apply rollback is still pending; resolve it before relocating",
            }
        new_dir = Path(raw)
        if not new_dir.is_absolute():
            return {"ok": False, "code": "not_absolute", "error": "directory must be an absolute path"}
        current_dir = self._ports.checkpoint_store_dir()
        try:
            current_resolved = current_dir.resolve()
        except OSError:
            current_resolved = current_dir
        try:
            new_resolved = new_dir.resolve()
        except OSError:
            new_resolved = new_dir
        if new_resolved == current_resolved:
            # 目录没变，仅确保配置持久化。
            config.checkpoint_archive_dir = str(new_resolved)
            self._ports.save_config(config)
            usage = self.checkpoint_archive_usage(config)
            return {
                "ok": True,
                "schema": "vrcforge.checkpoint_archive_relocate.v1",
                "unchanged": True,
                "directory": str(self._ports.checkpoint_store_dir()),
                "copiedCount": 0,
                "rewrittenCount": 0,
                "removedOldCount": 0,
                "sizeBytes": usage["sizeBytes"],
                "archiveCount": usage["archiveCount"],
            }
        # 禁止新旧目录互相嵌套，否则复制/删除会自噬。
        if current_resolved == new_resolved or current_resolved in new_resolved.parents or new_resolved in current_resolved.parents:
            return {"ok": False, "code": "nested", "error": "new directory must not nest with the current one"}
        try:
            new_resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "code": "mkdir_failed", "error": str(exc)}
        probe = new_resolved / ".vrcforge-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return {"ok": False, "code": "not_writable", "error": str(exc)}

        # 1) 复制全部 ZIP（保持相对结构），同时记录 checkpointId -> 新绝对路径。
        id_to_new_path: dict[str, str] = {}
        copied = 0
        if current_dir.is_dir():
            for src in current_dir.rglob("*.zip"):
                if not src.is_file():
                    continue
                rel = src.relative_to(current_dir)
                dst = new_resolved / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                except OSError as exc:
                    self._ports.append_audit(
                        {"event": "checkpoint_archive_relocate_copy_failed", "path": str(src), "error": str(exc)}
                    )
                    return {"ok": False, "code": "copy_failed", "error": f"{src}: {exc}"}
                id_to_new_path[src.stem] = str(dst)
                copied += 1

        # 2) 改写 checkpoints.jsonl 中已复制存档的 archivePath（按 checkpointId 精确映射）。
        rewritten = self._rewrite_checkpoint_archive_paths(id_to_new_path)

        # 3) 切换配置 + 内存覆盖，从此 checkpoint_store_dir 指向新目录。
        config.checkpoint_archive_dir = str(new_resolved)
        self._ports.save_config(config)

        # 4) 复制与改写都成功后，再清理旧目录里的 ZIP（尽力而为）。
        removed_old = 0
        if current_dir.is_dir():
            for src in list(current_dir.rglob("*.zip")):
                try:
                    src.unlink()
                    removed_old += 1
                    self._remove_old_relocate_parents(src.parent, current_resolved)
                except OSError:
                    continue
            try:
                if not any(current_dir.iterdir()):
                    current_dir.rmdir()
            except OSError:
                pass

        self._ports.append_audit(
            {
                "event": "checkpoint_archive_relocated",
                "from": str(current_resolved),
                "to": str(new_resolved),
                "copiedCount": copied,
                "rewrittenCount": rewritten,
                "removedOldCount": removed_old,
            }
        )
        usage = self.checkpoint_archive_usage(config)
        return {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_relocate.v1",
            "directory": str(self._ports.checkpoint_store_dir()),
            "from": str(current_resolved),
            "to": str(new_resolved),
            "copiedCount": copied,
            "rewrittenCount": rewritten,
            "removedOldCount": removed_old,
            "sizeBytes": usage["sizeBytes"],
            "archiveCount": usage["archiveCount"],
        }

    def _rewrite_checkpoint_archive_paths(self, id_to_new_path: dict[str, str]) -> int:
        """按 checkpointId 把 checkpoints.jsonl 里命中的 archivePath 改写成新路径。"""
        if not id_to_new_path:
            return 0
        path = self._ports.checkpoint_log_path()
        if not path.exists():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        changed = 0
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)
                continue
            if isinstance(record, dict):
                stored = record.get("archivePath")
                if isinstance(stored, str) and stored:
                    cid = str(record.get("id") or Path(stored).stem)
                    new_path = id_to_new_path.get(cid)
                    if new_path and new_path != stored:
                        record["archivePath"] = new_path
                        changed += 1
                        out.append(json.dumps(record, ensure_ascii=False))
                        continue
            out.append(line)
        if changed:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        return changed

    def _remove_old_relocate_parents(self, start: Path, root: Path) -> None:
        current = start
        while True:
            try:
                resolved = current.resolve()
            except OSError:
                break
            if resolved == root or root not in resolved.parents:
                break
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def prune_checkpoint_archives(
        self,
        max_size_mb: int | None = None,
        *,
        protected_checkpoint_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._prune_checkpoint_archives_locked(
                max_size_mb,
                protected_checkpoint_ids=protected_checkpoint_ids,
            )

    def _prune_checkpoint_archives_locked(
        self,
        max_size_mb: int | None = None,
        *,
        protected_checkpoint_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        config = self._ports.ensure_config()
        normalized_max = normalize_checkpoint_archive_max_size_mb(
            config.checkpoint_archive_max_size_mb if max_size_mb is None else max_size_mb
        )
        archives = self._checkpoint_archive_files()
        total_bytes = sum(item["sizeBytes"] for item in archives)
        protected_ids = set(protected_checkpoint_ids or set())
        protected_ids.update(self._protected_checkpoint_archive_ids(include_recent=True, archives=archives))
        if normalized_max <= 0:
            return {
                **self.checkpoint_archive_usage(config),
                "maxSizeMb": normalized_max,
                "limitEnabled": False,
                "deletedCount": 0,
                "deletedBytes": 0,
                "protectedCount": len(protected_ids),
            }

        target_bytes = normalized_max * CHECKPOINT_ARCHIVE_BYTES_PER_MB
        deleted: list[dict[str, Any]] = []
        remaining_bytes = total_bytes
        for archive in sorted(archives, key=lambda item: item["modifiedAt"]):
            if remaining_bytes <= target_bytes:
                break
            if archive["checkpointId"] in protected_ids:
                continue
            path = archive["path"]
            try:
                path.unlink()
            except OSError as exc:
                self._ports.append_audit(
                    {
                        "event": "checkpoint_archive_prune_failed",
                        "path": str(path),
                        "error": str(exc),
                    }
                )
                continue
            deleted.append({"path": str(path), "checkpointId": archive["checkpointId"], "sizeBytes": archive["sizeBytes"]})
            remaining_bytes -= archive["sizeBytes"]
            self._remove_empty_checkpoint_archive_parents(path.parent)

        summary = {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_prune.v1",
            "directory": str(self._ports.checkpoint_store_dir()),
            "maxSizeMb": normalized_max,
            "limitEnabled": True,
            "initialBytes": total_bytes,
            "remainingBytes": max(0, remaining_bytes),
            "remainingMb": round(max(0, remaining_bytes) / CHECKPOINT_ARCHIVE_BYTES_PER_MB, 2),
            "archiveCount": len(self._checkpoint_archive_files()),
            "deletedCount": len(deleted),
            "deletedBytes": sum(item["sizeBytes"] for item in deleted),
            "protectedCount": len(protected_ids),
            "deleted": deleted[:20],
        }
        if deleted:
            self._ports.append_audit({"event": "checkpoint_archives_pruned", **summary})
        return summary

    def list_interrupted_apply_recoveries(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        include_resolved = bool(params.get("includeResolved") or params.get("include_resolved"))
        limit = max(1, min(int(params.get("limit") or 50), 500))
        project_filter = str(params.get("project_root") or params.get("projectRoot") or "").strip()
        recoveries = self._coalesced_apply_recoveries(include_resolved=include_resolved)
        if project_filter:
            normalized = normalize_filesystem_path(project_filter)
            recoveries = [
                recovery for recovery in recoveries
                if normalize_filesystem_path(str(recovery.get("projectRoot") or "")) == normalized
            ]
        recoveries = recoveries[:limit]
        active = [recovery for recovery in recoveries if self._ports.approval.apply_recovery_blocks_writes(recovery)]
        return {
            "ok": True,
            "schema": APPLY_RECOVERY_SCHEMA,
            "recoveries": recoveries,
            "count": len(recoveries),
            "activeCount": len(active),
            "blockingWrites": bool(active),
            "restoreTool": "vrcforge_restore_checkpoint",
            "resolveTool": "vrcforge_resolve_interrupted_apply_recovery",
        }

    def preview_interrupted_apply_recovery(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        recovery = self._select_apply_recovery(params, include_resolved=bool(params.get("includeResolved") or params.get("include_resolved")))
        if not recovery:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "interrupted apply recovery was not found."}
        checkpoint_id = str(recovery.get("checkpointId") or recovery.get("checkpoint_id") or "").strip()
        checkpoint_preview = (
            self.preview_restore_checkpoint({"checkpointId": checkpoint_id})
            if checkpoint_id
            else {"ok": False, "error": "recovery has no checkpointId."}
        )
        payload = {
            "ok": True,
            "schema": APPLY_RECOVERY_SCHEMA,
            "recovery": recovery,
            "checkpointPreview": checkpoint_preview,
            "blockingWrites": self._ports.approval.apply_recovery_blocks_writes(recovery),
            "restoreRequest": {
                "targetTool": "vrcforge_restore_checkpoint",
                "arguments": {
                    "checkpointId": checkpoint_id,
                    "confirmRestore": True,
                    **(
                        {
                            "currentStateDigest": str(
                                checkpoint_preview.get("currentStateDigest") or ""
                            )
                        }
                        if checkpoint_preview.get("currentStateDigest")
                        else {}
                    ),
                },
            },
            "manualResolveRequest": {
                "targetTool": "vrcforge_resolve_interrupted_apply_recovery",
                "arguments": {"recoveryId": str(recovery.get("id") or ""), "confirmResolved": True},
            },
        }
        return payload

    def export_interrupted_apply_incident_bundle(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        recovery = self._select_apply_recovery(params, include_resolved=True)
        if not recovery:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "interrupted apply recovery was not found."}
        preview = self.preview_interrupted_apply_recovery({"recoveryId": recovery.get("id"), "includeResolved": True})
        generated_at = utc_now_iso()
        bundle = {
            "schema": "vrcforge.interrupted_apply_incident_bundle.v1",
            "generatedAt": generated_at,
            "recovery": recovery,
            "preview": preview,
            "recentAuditLogs": self._ports.recent_audit_logs(limit=80),
        }
        bundle_dir = self._ports.audit_dir() / "incident-bundles"
        filename = f"{recovery.get('id') or 'recovery'}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        bundle_path = bundle_dir / filename
        atomic_write_json(bundle_path, bundle)
        self._ports.append_audit({"event": "apply_recovery_incident_bundle_exported", "recoveryId": recovery.get("id"), "path": str(bundle_path)})
        return {"ok": True, "schema": bundle["schema"], "path": str(bundle_path), "bundle": bundle}

    def resolve_interrupted_apply_recovery(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if params.get("confirm_resolved") is not True and params.get("confirmResolved") is not True:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "confirmResolved=true is required to resolve an interrupted apply recovery."}
        recovery = self._select_apply_recovery(params, include_resolved=True)
        if not recovery:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "interrupted apply recovery was not found."}
        if not self._ports.approval.apply_recovery_blocks_writes(recovery):
            return {"ok": True, "schema": APPLY_RECOVERY_SCHEMA, "status": "already_resolved", "recovery": recovery}
        resolution_note = str(params.get("note") or params.get("reason") or "User confirmed the interrupted write was handled outside VRCForge.").strip()
        resolved = self._ports.approval.finish_apply_recovery(
            recovery,
            status="dismissed",
            resolution="manual_confirmed",
            note=resolution_note,
        )
        return {"ok": True, "schema": APPLY_RECOVERY_SCHEMA, "status": "resolved", "recovery": resolved}

    def list_adjustment_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        limit = max(1, min(int(params.get("limit") or 50), 500))
        include_deleted = bool(params.get("includeDeleted") or params.get("include_deleted"))
        kind_filter = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=False)
        raw_project_filter = str(params.get("project_root") or params.get("projectRoot") or "").strip()
        project_filter = normalize_filesystem_path(raw_project_filter) if raw_project_filter else ""
        avatar_filter = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
        entries = self._read_adjustment_checkpoint_entries()
        if not include_deleted:
            entries = [entry for entry in entries if not entry.get("deletedAt")]
        if kind_filter:
            entries = [entry for entry in entries if entry.get("kind") == kind_filter]
        if project_filter:
            entries = [
                entry for entry in entries
                if normalize_filesystem_path(str(entry.get("projectRoot") or "")) == project_filter
            ]
        if avatar_filter:
            entries = [entry for entry in entries if str(entry.get("avatarPath") or "") == avatar_filter]
        entries = entries[:limit]
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoints": entries, "count": len(entries)}

    def get_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any]:
        entry = self._load_adjustment_checkpoint(entry_id)
        if not entry:
            return {"ok": False, "error": "adjustment checkpoint was not found."}
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": entry}

    def create_adjustment_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=True)
        checkpoint = self._resolve_or_create_adjustment_base_checkpoint(params)
        if not checkpoint.get("ok"):
            return checkpoint
        entry = self._build_adjustment_checkpoint_entry(params, checkpoint, kind=kind, existing={})
        entries = self._read_adjustment_checkpoint_entries()
        requested_id = str(params.get("id") or "").strip()
        if requested_id and any(item.get("id") == requested_id for item in entries) and not bool(params.get("overwrite")):
            return {"ok": False, "error": "adjustment checkpoint id already exists; pass overwrite=true or use overwrite endpoint."}
        if requested_id:
            entry["id"] = requested_id
            entries = [item for item in entries if item.get("id") != requested_id]
        entries.insert(0, entry)
        self._write_adjustment_checkpoint_entries(entries)
        self._ports.append_audit({"event": "adjustment_checkpoint_created", "checkpoint": entry})
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": entry, "baseCheckpoint": checkpoint}

    def update_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any]) -> dict[str, Any]:
        entries = self._read_adjustment_checkpoint_entries()
        for index, entry in enumerate(entries):
            if entry.get("id") != entry_id:
                continue
            updated = dict(entry)
            self._apply_adjustment_checkpoint_metadata(updated, params)
            if "kind" in params:
                updated["kind"] = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=True)
            if "checkpointId" in params or "checkpoint_id" in params:
                checkpoint = self._load_checkpoint(str(params.get("checkpointId") or params.get("checkpoint_id") or "").strip())
                if not checkpoint:
                    return {"ok": False, "error": "checkpointId was not found."}
                updated["checkpointId"] = str(checkpoint.get("id") or "")
                updated["targetTool"] = str(checkpoint.get("targetTool") or updated.get("targetTool") or "")
            updated["updatedAt"] = utc_now_iso()
            entries[index] = updated
            self._write_adjustment_checkpoint_entries(entries)
            self._ports.append_audit({"event": "adjustment_checkpoint_updated", "checkpoint": updated})
            return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": updated}
        return {"ok": False, "error": "adjustment checkpoint was not found."}

    def delete_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        hard_delete = bool(params.get("hardDelete") or params.get("hard_delete"))
        entries = self._read_adjustment_checkpoint_entries()
        for index, entry in enumerate(entries):
            if entry.get("id") != entry_id:
                continue
            deleted = dict(entry)
            if hard_delete:
                entries.pop(index)
            else:
                deleted["deletedAt"] = utc_now_iso()
                deleted["updatedAt"] = deleted["deletedAt"]
                entries[index] = deleted
            self._write_adjustment_checkpoint_entries(entries)
            self._ports.append_audit({"event": "adjustment_checkpoint_deleted", "checkpoint": deleted, "hardDelete": hard_delete})
            return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": deleted, "hardDelete": hard_delete}
        return {"ok": False, "error": "adjustment checkpoint was not found."}

    def select_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        entries = self._read_adjustment_checkpoint_entries()
        selected_entry: dict[str, Any] | None = None
        slot = self._normalize_adjustment_selection_slot(params.get("slot") or params.get("compareSlot") or params.get("compare_slot"))
        for entry in entries:
            if entry.get("id") == entry_id and not entry.get("deletedAt"):
                selected_entry = dict(entry)
                break
        if not selected_entry:
            return {"ok": False, "error": "adjustment checkpoint was not found."}
        kind = str(selected_entry.get("kind") or "")
        compare_group = str(params.get("compareGroup") or params.get("compare_group") or selected_entry.get("compareGroup") or kind)
        now = utc_now_iso()
        updated_entries: list[dict[str, Any]] = []
        for entry in entries:
            current = dict(entry)
            if current.get("kind") == kind and str(current.get("compareGroup") or kind) == compare_group:
                selected_slots = [
                    item for item in ensure_string_list(current.get("selectedSlots"))
                    if item.upper() != slot
                ]
                if current.get("id") == entry_id:
                    selected_slots.append(slot)
                    current["selectedSlots"] = selected_slots
                    current["selectedAt"] = now
                    current["selected"] = True
                    current["selectionSlot"] = slot
                else:
                    current["selectedSlots"] = selected_slots
                    if not selected_slots:
                        current.pop("selectedAt", None)
                        current["selected"] = False
                        current.pop("selectionSlot", None)
            updated_entries.append(current)
        self._write_adjustment_checkpoint_entries(updated_entries)
        selected = self._load_adjustment_checkpoint(entry_id) or selected_entry
        self._ports.append_audit({"event": "adjustment_checkpoint_selected", "checkpoint": selected, "slot": slot})
        return {
            "ok": True,
            "schema": "vrcforge.adjustment_checkpoint_timeline.v1",
            "checkpoint": selected,
            "selection": {"kind": kind, "compareGroup": compare_group, "slot": slot, "checkpointId": selected.get("checkpointId")},
        }

    def overwrite_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any]) -> dict[str, Any]:
        entries = self._read_adjustment_checkpoint_entries()
        for index, entry in enumerate(entries):
            if entry.get("id") != entry_id:
                continue
            checkpoint = self._resolve_or_create_adjustment_base_checkpoint({**entry, **params})
            if not checkpoint.get("ok"):
                return checkpoint
            updated = self._build_adjustment_checkpoint_entry(
                params,
                checkpoint,
                kind=self._normalize_adjustment_checkpoint_kind(params.get("kind") or entry.get("kind"), required=True),
                existing=entry,
            )
            updated["id"] = entry_id
            revisions = ensure_list(entry.get("revisions"))
            revisions.append(
                {
                    "checkpointId": entry.get("checkpointId"),
                    "overwrittenAt": utc_now_iso(),
                    "label": entry.get("label"),
                }
            )
            updated["revisions"] = revisions
            updated["overwriteCount"] = len(revisions)
            entries[index] = updated
            self._write_adjustment_checkpoint_entries(entries)
            self._ports.append_audit({"event": "adjustment_checkpoint_overwritten", "checkpoint": updated})
            return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": updated, "baseCheckpoint": checkpoint}
        return {"ok": False, "error": "adjustment checkpoint was not found."}

    def preview_restore_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any]:
        entry = self._load_adjustment_checkpoint(entry_id)
        if not entry or entry.get("deletedAt"):
            return {"ok": False, "error": "adjustment checkpoint was not found."}
        preview = self.preview_restore_checkpoint({"checkpointId": str(entry.get("checkpointId") or "")})
        preview["adjustmentCheckpoint"] = entry
        return preview

    def get_selected_adjustment_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        kind_filter = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=False)
        compare_group = str(params.get("compareGroup") or params.get("compare_group") or "").strip()
        entries = [
            entry for entry in self._read_adjustment_checkpoint_entries()
            if not entry.get("deletedAt") and ensure_string_list(entry.get("selectedSlots"))
        ]
        if kind_filter:
            entries = [entry for entry in entries if entry.get("kind") == kind_filter]
        if compare_group:
            entries = [entry for entry in entries if str(entry.get("compareGroup") or entry.get("kind") or "") == compare_group]
        selections: dict[str, dict[str, Any]] = {}
        for entry in entries:
            key_base = f"{entry.get('kind')}:{entry.get('compareGroup') or entry.get('kind')}"
            for slot in ensure_string_list(entry.get("selectedSlots")):
                selections[f"{key_base}:{slot.upper()}"] = entry
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "selections": selections, "count": len(selections)}

    def _normalize_adjustment_selection_slot(self, value: Any) -> str:
        slot = str(value or "current").strip().upper()
        if slot in {"A", "B", "CURRENT"}:
            return slot
        raise AgentGatewayError("selection slot must be A, B, or current.", status_code=400)

    def preview_restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._preview_restore_checkpoint_locked(params)

    def _preview_restore_checkpoint_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip())
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_id was not found."}
        available = self._checkpoint_available(checkpoint)
        if not available.get("ok"):
            return available
        if checkpoint.get("strategy") == "local_state_archive":
            return self._preview_local_state_checkpoint(checkpoint)
        if checkpoint.get("strategy") == "project_chat_archive":
            return self._preview_project_chat_checkpoint(checkpoint)
        if checkpoint.get("strategy") == "archive":
            return self._preview_archive_checkpoint(checkpoint)
        git_root = Path(str(checkpoint["gitRoot"]))
        ref = str(checkpoint["checkpointRef"])
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        diff = self._ports.run_git(git_root, ["diff", "--name-status", ref, "--", *pathspecs])
        status = self._ports.run_git(git_root, ["status", "--porcelain", "--", *pathspecs])
        payload = {
            "ok": diff["ok"] and status["ok"],
            "checkpoint": checkpoint,
            "changedFiles": [line for line in diff["stdout"].splitlines() if line.strip()],
            "workingTreeStatus": [line for line in status["stdout"].splitlines() if line.strip()],
            "error": diff.get("error") or status.get("error") or "",
        }
        payload["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview")
        return payload

    def restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._ports.state.checkpoint_storage_lock:
            return self._restore_checkpoint_locked(params)

    def _restore_checkpoint_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip())
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_id was not found."}
        if params.get("confirm_restore") is not True and params.get("confirmRestore") is not True:
            return {"ok": False, "error": "confirmRestore=true is required to restore a checkpoint."}
        available = self._checkpoint_available(checkpoint)
        if not available.get("ok"):
            return available
        local_state_restore = checkpoint.get("strategy") in {"local_state_archive", "project_chat_archive"}
        project_root = Path(str(checkpoint.get("projectRoot") or ""))
        restore_prepare: dict[str, Any] = {}
        if not local_state_restore and self._checkpoint_restore_prepare_handler is not None:
            if self._checkpoint_restore_handler is None:
                return {
                    "ok": False,
                    "checkpoint": checkpoint,
                    "status": "restore_prepare_failed",
                    "error": "Unity restore preparation is configured without a matching reload handler.",
                }
            try:
                restore_prepare = ensure_dict(
                    self._checkpoint_restore_prepare_handler(project_root)
                )
            except Exception as exc:  # noqa: BLE001 - fail before touching project files.
                restore_prepare = {"ok": False, "error": str(exc)}
            if not restore_prepare.get("ok"):
                payload = {
                    "ok": False,
                    "checkpoint": checkpoint,
                    "status": "restore_prepare_failed",
                    "unityRestorePrepare": restore_prepare,
                    "error": str(
                        restore_prepare.get("error")
                        or "Unity did not close restored scenes before file recovery."
                    ),
                }
                self._ports.append_audit({"event": "checkpoint_restore_prepare_failed", **payload})
                return payload
        if local_state_restore:
            payload = (
                self._restore_project_chat_checkpoint(checkpoint)
                if checkpoint.get("strategy") == "project_chat_archive"
                else self._restore_local_state_checkpoint(
                    checkpoint,
                    expected_current_state_digest=str(
                        params.get("current_state_digest")
                        or params.get("currentStateDigest")
                        or ""
                    ),
                )
            )
        elif checkpoint.get("strategy") == "archive":
            payload = self._restore_archive_checkpoint(checkpoint)
        else:
            git_root = Path(str(checkpoint["gitRoot"]))
            ref = str(checkpoint["checkpointRef"])
            pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
            restore = self._ports.run_git(git_root, ["restore", "--source", ref, "--staged", "--worktree", "--", *pathspecs], timeout_seconds=120)
            if not restore["ok"]:
                payload = {
                    "ok": False,
                    "checkpoint": checkpoint,
                    "error": restore["error"],
                    "stdout": restore["stdout"],
                    "stderr": restore["stderr"],
                }
            else:
                clean = self._ports.run_git(git_root, ["clean", "-fd", "--", *pathspecs], timeout_seconds=120)
                payload = {
                    "ok": clean["ok"],
                    "checkpoint": checkpoint,
                    "restoredRef": ref,
                    "cleaned": [line for line in clean["stdout"].splitlines() if line.strip()],
                    "error": clean.get("error") or "",
                }
        if payload.get("ok"):
            payload["checkpointId"] = str(checkpoint.get("id") or "")
            payload["restored"] = True
        if payload.get("ok") and not local_state_restore:
            cache_cleanup = self._cleanup_checkpoint_restore_unity_caches(checkpoint, payload)
            payload["unityCacheCleanup"] = cache_cleanup
            if cache_cleanup.get("errors"):
                payload["unityCacheCleanupWarning"] = "; ".join(ensure_string_list(cache_cleanup.get("errors")))
        should_reload_unity = bool(
            not local_state_restore
            and self._checkpoint_restore_handler is not None
            and (payload.get("ok") or restore_prepare.get("ok"))
        )
        if should_reload_unity:
            checkpoint_prepare = ensure_dict(checkpoint.get("unityPrepare"))
            original_scene_context: dict[str, Any] = {}
            checkpoint_scenes = checkpoint_prepare.get("scenes")
            if (
                checkpoint_prepare.get("ok") is True
                and isinstance(checkpoint_scenes, list)
                and (
                    "activeScenePath" in checkpoint_prepare
                    or not checkpoint_scenes
                )
            ):
                original_scene_context = {
                    "scenes": ensure_string_list(checkpoint_scenes),
                    "activeScenePath": str(
                        checkpoint_prepare.get("activeScenePath") or ""
                    ).strip(),
                }
            reload_context = {
                **restore_prepare,
                **original_scene_context,
                "restoredFiles": ensure_string_list(payload.get("restoredFiles")),
                "deletedFiles": ensure_string_list(payload.get("deletedFiles")),
            }
            try:
                reload_result = ensure_dict(
                    self._checkpoint_restore_handler(project_root, reload_context)
                )
            except Exception as exc:  # noqa: BLE001
                reload_result = {"ok": False, "error": str(exc)}
            if restore_prepare:
                payload["unityRestorePrepare"] = restore_prepare
            payload["unityReload"] = reload_result
            if payload.get("ok") and not reload_result.get("ok"):
                payload["ok"] = False
                payload["status"] = "restored_unity_reload_failed"
                payload["checkpointRecoveryRequired"] = True
                payload["error"] = str(
                    reload_result.get("error") or "Unity did not reload after checkpoint restore."
                )
            elif payload.get("ok"):
                payload["status"] = "restored"
            elif not reload_result.get("ok"):
                payload["status"] = "restore_failed_unity_reopen_failed"
                payload["checkpointRecoveryRequired"] = True
                payload["error"] = str(
                    reload_result.get("error") or "Unity did not reopen scenes after restore failed."
                )
        elif payload.get("ok") and local_state_restore:
            payload["status"] = "restored"
        if payload.get("ok"):
            payload["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(
                checkpoint,
                phase="restore",
                restore_payload=payload,
            )
            resolved_recoveries = self._ports.approval.resolve_apply_recoveries_for_checkpoint(
                str(checkpoint.get("id") or ""),
                resolution="checkpoint_restored",
                restore_payload=payload,
            )
            if resolved_recoveries:
                payload["resolvedApplyRecoveries"] = resolved_recoveries
        self._ports.append_audit({"event": "checkpoint_restored", **payload})
        return payload

    def _create_project_chat_checkpoint(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        """Archive only the project-owned chat store, never the whole hidden directory."""

        with self._project_chat_checkpoint_lock:
            return self._create_project_chat_checkpoint_locked(project_root, record)

    def _create_project_chat_checkpoint_locked(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        """Create a project-chat archive while holding the host writer lock."""

        checkpoint_id = str(record["id"])
        archive_dir = self._ports.checkpoint_store_dir() / self._checkpoint_project_key(project_root)
        archive_path = archive_dir / f"{checkpoint_id}.zip"
        temp_path = archive_path.with_suffix(".zip.tmp")
        source = project_root / Path(*PurePosixPath(PROJECT_CHAT_CHECKPOINT_MEMBER).parts)
        source_existed = source.exists()
        source_bytes = b""
        try:
            hidden_root = source.parent
            if (os.path.lexists(hidden_root) and _path_is_link_like(hidden_root)) or (
                os.path.lexists(source) and _path_is_link_like(source)
            ):
                raise ValueError("Project chat checkpoint refuses linked or reparse-point paths.")
            if source_existed:
                if not source.is_file():
                    raise ValueError("Project chat store is not a regular file.")
                source_bytes = source.read_bytes()
            expected_digest = str(record.get("expectedSourceDigest") or "")
            current_digest = hashlib.sha256(source_bytes).hexdigest() if source_existed else ""
            if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or not source_existed or current_digest != expected_digest:
                raise ValueError("Project chat store changed after the approval snapshot; retry recovery from a fresh scan.")
            archive_dir.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                if source_existed:
                    archive.writestr(PROJECT_CHAT_CHECKPOINT_MEMBER, source_bytes)
            fsync_file_path(temp_path)
            os.replace(temp_path, archive_path)
            fsync_directory_best_effort(archive_path.parent)
        except Exception as exc:  # noqa: BLE001
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "strategy": "project_chat_archive",
                    "archivePath": str(archive_path),
                    "pathspecs": [PROJECT_CHAT_CHECKPOINT_MEMBER],
                    "sourceExisted": source_existed,
                    "error": f"Project chat checkpoint failed: {exc}",
                }
            )
            self._append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "project_chat_archive",
                "archivePath": str(archive_path),
                "pathspecs": [PROJECT_CHAT_CHECKPOINT_MEMBER],
                "sourceExisted": source_existed,
                "sourceDigest": hashlib.sha256(source_bytes).hexdigest() if source_existed else "",
                "fileCount": int(source_existed),
                "uncompressedBytes": len(source_bytes),
            }
        )
        record["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._append_checkpoint(record)
        self._ports.append_audit({"event": "checkpoint_created", "checkpoint": record})
        self.prune_checkpoint_archives(protected_checkpoint_ids={checkpoint_id})
        return record

    def _create_archive_checkpoint(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(record["id"])
        project_key = self._checkpoint_project_key(project_root)
        archive_dir = self._ports.checkpoint_store_dir() / project_key
        archive_path = archive_dir / f"{checkpoint_id}.zip"
        temp_path = archive_path.with_suffix(".zip.tmp")
        pathspecs = [name for name in ("Assets", "Packages", "ProjectSettings") if (project_root / name).is_dir()]
        file_count = 0
        total_bytes = 0
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                for name in pathspecs:
                    root = project_root / name
                    for source in sorted(root.rglob("*")):
                        if not source.is_file():
                            continue
                        relative = source.relative_to(project_root).as_posix()
                        archive.write(source, relative)
                        file_count += 1
                        total_bytes += source.stat().st_size
            fsync_file_path(temp_path)
            os.replace(temp_path, archive_path)
            fsync_directory_best_effort(archive_path.parent)
        except Exception as exc:  # noqa: BLE001
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "strategy": "archive",
                    "archivePath": str(archive_path),
                    "pathspecs": pathspecs,
                    "error": f"Archive checkpoint failed: {exc}",
                }
            )
            self._append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "archive",
                "archivePath": str(archive_path),
                "pathspecs": pathspecs,
                "fileCount": file_count,
                "uncompressedBytes": total_bytes,
            }
        )
        record["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._append_checkpoint(record)
        self._ports.append_audit({"event": "checkpoint_created", "checkpoint": record})
        self.prune_checkpoint_archives(protected_checkpoint_ids={checkpoint_id})
        return record

    def _create_local_state_checkpoint(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._ports.state.skill_package_write_lock, self._ports.skills.write_lock:
            return self._create_local_state_checkpoint_locked(record)

    def _create_local_state_checkpoint_locked(self, record: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(record["id"])
        archive_dir = self._ports.checkpoint_store_dir() / "local-state"
        archive_path = archive_dir / f"{checkpoint_id}.zip"
        temp_path = archive_path.with_suffix(".zip.tmp")
        roots = self._local_state_checkpoint_roots()
        state_roots: list[dict[str, Any]] = []
        file_count = 0
        total_bytes = 0
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                for scope, root in roots.items():
                    if os.path.lexists(root) and (_path_is_link_like(root) or not root.is_dir()):
                        raise ValueError(f"Local state root is not a regular directory: {root}")
                    resolved = root.resolve()
                    root_file_count = 0
                    root_directory_count = 0
                    if resolved.exists():
                        for source in sorted(resolved.rglob("*")):
                            if _path_is_link_like(source):
                                raise ValueError(f"Refusing to checkpoint linked local state path: {source}")
                            relative = f"{scope}/{source.relative_to(resolved).as_posix()}"
                            if source.is_dir():
                                directory_info = zipfile.ZipInfo(relative.rstrip("/") + "/")
                                directory_info.external_attr = (stat.S_IFDIR | 0o755) << 16
                                archive.writestr(directory_info, b"")
                                root_directory_count += 1
                                continue
                            if not source.is_file():
                                continue
                            archive.write(source, relative)
                            size = source.stat().st_size
                            file_count += 1
                            root_file_count += 1
                            total_bytes += size
                    state_roots.append(
                        {
                            "id": scope,
                            "path": str(resolved),
                            "exists": resolved.exists(),
                            "fileCount": root_file_count,
                            "directoryCount": root_directory_count,
                        }
                    )
            fsync_file_path(temp_path)
            os.replace(temp_path, archive_path)
            fsync_directory_best_effort(archive_path.parent)
        except Exception as exc:  # noqa: BLE001
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "strategy": "local_state_archive",
                    "archivePath": str(archive_path),
                    "pathspecs": list(LOCAL_STATE_CHECKPOINT_SCOPE),
                    "stateRoots": state_roots,
                    "error": f"Local state checkpoint failed: {exc}",
                }
            )
            self._append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "local_state_archive",
                "archivePath": str(archive_path),
                "pathspecs": list(LOCAL_STATE_CHECKPOINT_SCOPE),
                "stateRoots": state_roots,
                "fileCount": file_count,
                "uncompressedBytes": total_bytes,
            }
        )
        record["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._append_checkpoint(record)
        self._ports.append_audit({"event": "checkpoint_created", "checkpoint": record})
        return record

    def _checkpoint_project_key(self, project_root: Path) -> str:
        return hashlib.sha256(normalize_filesystem_path(str(project_root)).encode("utf-8")).hexdigest()[:16]

    def _resolve_checkpoint_archive_path(self, checkpoint: dict[str, Any], expected_strategy: str) -> Path:
        strategy = str(checkpoint.get("strategy") or "")
        if strategy != expected_strategy:
            raise ValueError("Checkpoint strategy does not match archive type.")
        checkpoint_id = str(checkpoint.get("id") or "").strip()
        if not checkpoint_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", checkpoint_id) or checkpoint_id in {".", ".."}:
            raise ValueError("Checkpoint id is invalid.")
        raw_archive = str(checkpoint.get("archivePath") or "").strip()
        if not raw_archive:
            raise ValueError("Checkpoint archive path is missing.")

        archive_path = Path(raw_archive).resolve()
        store_root = self._ports.checkpoint_store_dir().resolve()
        if not is_path_within(archive_path, store_root):
            raise ValueError("Checkpoint archive is outside configured storage.")
        if archive_path.name != f"{checkpoint_id}.zip":
            raise ValueError("Checkpoint archive filename does not match checkpoint id.")

        if expected_strategy in {"archive", "project_chat_archive"}:
            project_root_text = str(checkpoint.get("projectRoot") or "").strip()
            if not project_root_text:
                raise ValueError("Checkpoint project root is missing.")
            expected_parent = (store_root / self._checkpoint_project_key(Path(project_root_text).resolve())).resolve()
            if archive_path.parent != expected_parent:
                raise ValueError("Checkpoint archive does not match the recorded project root.")
        elif expected_strategy == "local_state_archive":
            expected_parent = (store_root / "local-state").resolve()
            if archive_path.parent != expected_parent:
                raise ValueError("Local state archive is outside its managed storage folder.")
        return archive_path

    def _normalize_project_archive_member(self, name: str, allowed_roots: set[str]) -> str:
        text = str(name or "").replace("\\", "/")
        member = PurePosixPath(text)
        parts = member.parts
        if (
            len(parts) < 2
            or member.is_absolute()
            or Path(str(name)).is_absolute()
            or looks_like_absolute_path(text)
            or parts[0] not in allowed_roots
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError(f"Unsafe archive member: {name}")
        return member.as_posix()

    def _project_chat_checkpoint_source(self, checkpoint: dict[str, Any]) -> Path:
        project_root = Path(str(checkpoint.get("projectRoot") or "")).resolve()
        hidden_root = project_root / ".vrcforge"
        raw_source = hidden_root / "chat-transcripts.json"
        if (os.path.lexists(hidden_root) and _path_is_link_like(hidden_root)) or (
            os.path.lexists(raw_source) and _path_is_link_like(raw_source)
        ):
            raise ValueError("Project chat checkpoint target is linked or a reparse point.")
        source = raw_source.resolve()
        if not is_path_within(source, project_root) or source.parent != hidden_root.resolve():
            raise ValueError("Project chat checkpoint target is unsafe.")
        return source

    def _read_project_chat_checkpoint_bytes(self, checkpoint: dict[str, Any]) -> bytes | None:
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "project_chat_archive")
        expected_exists = checkpoint.get("sourceExisted") is True
        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("archive CRC validation failed")
            members = [info for info in archive.infolist() if not info.is_dir()]
            expected_names = [PROJECT_CHAT_CHECKPOINT_MEMBER] if expected_exists else []
            if [info.filename for info in members] != expected_names:
                raise ValueError("Project chat checkpoint members do not match metadata.")
            if not expected_exists:
                return None
            data = archive.read(PROJECT_CHAT_CHECKPOINT_MEMBER)
        digest = str(checkpoint.get("sourceDigest") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Project chat checkpoint digest verification failed.")
        return data

    def _preview_project_chat_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        with self._project_chat_checkpoint_lock:
            return self._preview_project_chat_checkpoint_locked(checkpoint)

    def _preview_project_chat_checkpoint_locked(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        try:
            source = self._project_chat_checkpoint_source(checkpoint)
            archived = self._read_project_chat_checkpoint_bytes(checkpoint)
            current = source.read_bytes() if source.is_file() else None
            changed = [] if current == archived else [
                f"{'A' if archived is None else 'D' if current is None else 'M'}\t{PROJECT_CHAT_CHECKPOINT_MEMBER}"
            ]
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "changedFiles": changed,
                "workingTreeStatus": changed,
                "rollbackCoverageAudit": self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview"),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": str(exc)}

    def _restore_project_chat_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        with self._project_chat_checkpoint_lock:
            return self._restore_project_chat_checkpoint_locked(checkpoint)

    def _restore_project_chat_checkpoint_locked(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        try:
            source = self._project_chat_checkpoint_source(checkpoint)
            archived = self._read_project_chat_checkpoint_bytes(checkpoint)
            restored: list[str] = []
            deleted: list[str] = []
            if archived is None:
                if source.exists():
                    source.unlink()
                    fsync_directory_best_effort(source.parent)
                    deleted.append(PROJECT_CHAT_CHECKPOINT_MEMBER)
            else:
                source.parent.mkdir(parents=True, exist_ok=True)
                temporary = source.with_name(f".{source.name}.{secrets.token_hex(8)}.restore.tmp")
                try:
                    with temporary.open("xb") as handle:
                        handle.write(archived)
                        flush_and_fsync(handle)
                    os.replace(temporary, source)
                    fsync_directory_best_effort(source.parent)
                finally:
                    temporary.unlink(missing_ok=True)
                restored.append(PROJECT_CHAT_CHECKPOINT_MEMBER)
                digest = hashlib.sha256(archived).hexdigest()
                quarantine = source.with_name(f"{source.name}.vrcforge-quarantine-{digest[:16]}")
                if quarantine.exists() and not _path_is_link_like(quarantine) and quarantine.is_file():
                    if hashlib.sha256(quarantine.read_bytes()).hexdigest() == digest:
                        quarantine.unlink()
                        fsync_directory_best_effort(quarantine.parent)
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "restoredFileCount": len(restored),
                "restoredFiles": restored,
                "deletedFileCount": len(deleted),
                "deletedFiles": deleted,
                "cleaned": deleted,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": f"Project chat restore failed: {exc}"}

    def _preview_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        project_root = Path(str(checkpoint["projectRoot"])).resolve()
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "archive")
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        allowed = set(UNITY_PROJECT_CHECKPOINT_SCOPE)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archived: dict[str, tuple[int, int]] = {}
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = self._normalize_project_archive_member(info.filename, allowed)
                    if name in archived:
                        raise ValueError(f"Duplicate archive member: {name}")
                    archived[name] = (info.file_size, info.CRC)
            current: dict[str, tuple[int, int]] = {}
            for name in pathspecs:
                root = project_root / name
                if not root.is_dir():
                    continue
                for source in root.rglob("*"):
                    if not source.is_file():
                        continue
                    relative = source.relative_to(project_root).as_posix()
                    crc = 0
                    with source.open("rb") as handle:
                        while chunk := handle.read(1024 * 1024):
                            crc = zlib.crc32(chunk, crc)
                    current[relative] = (source.stat().st_size, crc & 0xFFFFFFFF)
            changed = [f"M\t{name}" for name in sorted(archived.keys() & current.keys()) if archived[name] != current[name]]
            changed.extend(f"D\t{name}" for name in sorted(archived.keys() - current.keys()))
            changed.extend(f"A\t{name}" for name in sorted(current.keys() - archived.keys()))
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "changedFiles": changed,
                "workingTreeStatus": changed,
                "rollbackCoverageAudit": self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview"),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": str(exc)}

    def _preview_local_state_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        with self._ports.state.skill_package_write_lock, self._ports.skills.write_lock:
            return self._preview_local_state_checkpoint_locked(checkpoint)

    def _preview_local_state_checkpoint_locked(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "local_state_archive")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archived = {
                    info.filename: (info.file_size, info.CRC)
                    for info in archive.infolist()
                    if not info.is_dir()
                }
                for name in archived:
                    self._validate_local_state_archive_member(name)
            current = self._local_state_archive_contents()
            current_state_digest = self._local_state_current_digest()
            changed = [f"M\t{name}" for name in sorted(archived.keys() & current.keys()) if archived[name] != current[name]]
            changed.extend(f"D\t{name}" for name in sorted(archived.keys() - current.keys()))
            changed.extend(f"A\t{name}" for name in sorted(current.keys() - archived.keys()))
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "changedFiles": changed,
                "workingTreeStatus": changed,
                "currentStateDigest": current_state_digest,
                "rollbackCoverageAudit": self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview"),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": str(exc)}

    def _restore_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        project_root = Path(str(checkpoint["projectRoot"])).resolve()
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "archive")
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        allowed = set(UNITY_PROJECT_CHECKPOINT_SCOPE)
        if not pathspecs or any(name not in allowed for name in pathspecs):
            return {"ok": False, "checkpoint": checkpoint, "error": "Archive checkpoint pathspecs are unsafe."}
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                archived: dict[str, zipfile.ZipInfo] = {}
                for info in members:
                    name = self._normalize_project_archive_member(info.filename, allowed)
                    if name in archived:
                        raise ValueError(f"Duplicate archive member: {name}")
                    archived[name] = info
                current: dict[str, Path] = {}
                for name in pathspecs:
                    target = (project_root / name).resolve()
                    if target.parent != project_root or target.name not in allowed:
                        raise ValueError(f"Unsafe restore target: {target}")
                    target.mkdir(parents=True, exist_ok=True)
                    for source in target.rglob("*"):
                        if source.is_file():
                            current[source.relative_to(project_root).as_posix()] = source

                deleted: list[str] = []
                for relative in sorted(current.keys() - archived.keys()):
                    current[relative].unlink()
                    deleted.append(relative)

                restored: list[str] = []
                for relative, info in archived.items():
                    target = (project_root / Path(*PurePosixPath(relative).parts)).resolve()
                    if not is_path_within(target, project_root):
                        raise ValueError(f"Unsafe restore target: {target}")
                    needs_restore = not target.is_file() or target.stat().st_size != info.file_size
                    if not needs_restore:
                        crc = 0
                        with target.open("rb") as handle:
                            while chunk := handle.read(1024 * 1024):
                                crc = zlib.crc32(chunk, crc)
                        needs_restore = (crc & 0xFFFFFFFF) != info.CRC
                    if not needs_restore:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp_target = target.with_name(target.name + ".vrcforge-restore-tmp")
                    with archive.open(info, "r") as source, temp_target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                        flush_and_fsync(destination)
                    os.replace(temp_target, target)
                    restored.append(relative)

                for name in pathspecs:
                    root = project_root / name
                    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "restoredArchive": str(archive_path),
                "restoredFileCount": len(restored),
                "restoredFiles": restored,
                "deletedFileCount": len(deleted),
                "deletedFiles": deleted,
                "cleaned": deleted,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": f"Archive restore failed: {exc}"}

    def _restore_local_state_checkpoint(
        self,
        checkpoint: dict[str, Any],
        expected_current_state_digest: str = "",
    ) -> dict[str, Any]:
        with self._ports.state.skill_package_write_lock, self._ports.skills.write_lock:
            return self._restore_local_state_checkpoint_locked(
                checkpoint,
                expected_current_state_digest=expected_current_state_digest,
            )

    def _validated_local_state_restore_members(
        self,
        archive: zipfile.ZipFile,
        checkpoint: dict[str, Any],
        roots: dict[str, Path],
        state_roots: dict[str, dict[str, Any]],
    ) -> tuple[list[tuple[str, zipfile.ZipInfo]], dict[str, bool]]:
        members: list[tuple[str, zipfile.ZipInfo]] = []
        seen: set[str] = set()
        scope_counts = {scope: 0 for scope in roots}
        scope_directory_counts = {scope: 0 for scope in roots}
        total_bytes = 0
        expected_exists = {
            scope: state_roots.get(scope, {}).get("exists") is True
            for scope in roots
        }
        for info in archive.infolist():
            self._validate_local_state_archive_member(info.filename)
            normalized = PurePosixPath(str(info.filename)).as_posix()
            collision_key = normalized.casefold()
            if collision_key in seen:
                raise ValueError(
                    f"Duplicate local state archive member: {normalized}"
                )
            seen.add(collision_key)
            mode = (int(info.external_attr) >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                raise ValueError(
                    f"Linked local state archive member is not allowed: {normalized}"
                )
            scope = PurePosixPath(normalized).parts[0]
            if info.is_dir():
                scope_directory_counts[scope] += 1
                members.append((normalized, info))
                continue
            if not expected_exists.get(scope, False):
                raise ValueError(
                    f"Local state archive contains files for an absent root: {scope}"
                )
            if info.file_size < 0:
                raise ValueError(
                    f"Local state archive member has an invalid size: {normalized}"
                )
            scope_counts[scope] += 1
            total_bytes += int(info.file_size)
            members.append((normalized, info))

        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(
                f"Local state archive CRC validation failed: {bad_member}"
            )

        for scope, metadata in state_roots.items():
            if scope not in roots or "fileCount" not in metadata:
                continue
            expected_count = metadata.get("fileCount")
            if (
                isinstance(expected_count, bool)
                or not isinstance(expected_count, int)
                or expected_count < 0
                or scope_counts[scope] != expected_count
            ):
                raise ValueError(
                    f"Local state archive file count does not match metadata: {scope}"
                )
            if "directoryCount" in metadata:
                expected_directory_count = metadata.get("directoryCount")
                if (
                    isinstance(expected_directory_count, bool)
                    or not isinstance(expected_directory_count, int)
                    or expected_directory_count < 0
                    or scope_directory_counts[scope]
                    != expected_directory_count
                ):
                    raise ValueError(
                        "Local state archive directory count does not match "
                        f"metadata: {scope}"
                    )
        if "fileCount" in checkpoint:
            expected_count = checkpoint.get("fileCount")
            if (
                isinstance(expected_count, bool)
                or not isinstance(expected_count, int)
                or expected_count < 0
                or sum(1 for _name, info in members if not info.is_dir())
                != expected_count
            ):
                raise ValueError(
                    "Local state archive total file count does not match metadata."
                )
        if "uncompressedBytes" in checkpoint:
            expected_bytes = checkpoint.get("uncompressedBytes")
            if (
                isinstance(expected_bytes, bool)
                or not isinstance(expected_bytes, int)
                or expected_bytes < 0
                or total_bytes != expected_bytes
            ):
                raise ValueError(
                    "Local state archive byte size does not match metadata."
                )
        return members, expected_exists

    @staticmethod
    def _remove_local_state_restore_tree(path: Path) -> None:
        if not os.path.lexists(path):
            return
        if _path_is_link_like(path) or not path.is_dir():
            raise RuntimeError(
                f"Refusing to remove unsafe local state restore path: {path}"
            )
        shutil.rmtree(path)

    def _stage_local_state_restore(
        self,
        archive: zipfile.ZipFile,
        roots: dict[str, Path],
        members: list[tuple[str, zipfile.ZipInfo]],
        expected_exists: dict[str, bool],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        token = secrets.token_hex(16)
        records: list[dict[str, Any]] = []
        try:
            for scope, root in roots.items():
                root.parent.mkdir(parents=True, exist_ok=True)
                if _path_is_link_like(root.parent) or not root.parent.is_dir():
                    raise ValueError(
                        f"Local state restore parent is not a safe directory: {root.parent}"
                    )
                stage = root.parent / f".{root.name}.vrcforge-restore-{token}.staged"
                backup = root.parent / f".{root.name}.vrcforge-restore-{token}.backup"
                if os.path.lexists(stage) or os.path.lexists(backup):
                    raise RuntimeError(
                        f"Local state restore workspace already exists: {root.parent}"
                    )
                record = {
                    "scope": scope,
                    "root": root,
                    "stage": stage,
                    "backup": backup,
                    "expected": bool(expected_exists.get(scope)),
                    "backupMoved": False,
                    "published": False,
                }
                records.append(record)
                if record["expected"]:
                    stage.mkdir()
                    fsync_directory_best_effort(stage.parent)

            restored: list[str] = []
            for normalized, info in members:
                parts = PurePosixPath(normalized).parts
                record = next(item for item in records if item["scope"] == parts[0])
                stage = Path(record["stage"])
                target = stage.joinpath(*parts[1:])
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    fsync_directory_best_effort(target.parent)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                    flush_and_fsync(destination)
                metadata = target.stat(follow_symlinks=False)
                crc = 0
                size = 0
                with target.open("rb") as staged_file:
                    while chunk := staged_file.read(1024 * 1024):
                        size += len(chunk)
                        crc = zlib.crc32(chunk, crc)
                if size != info.file_size or (crc & 0xFFFFFFFF) != info.CRC:
                    raise ValueError(
                        f"Staged local state member failed size or CRC validation: {normalized}"
                    )
                if metadata.st_size != info.file_size:
                    raise ValueError(
                        f"Staged local state member size changed after fsync: {normalized}"
                    )
                fsync_directory_best_effort(target.parent)
                restored.append(normalized)

            for record in records:
                stage = Path(record["stage"])
                if not record["expected"]:
                    continue
                directories = [stage]
                directories.extend(
                    sorted(
                        (path for path in stage.rglob("*") if path.is_dir()),
                        key=lambda path: len(path.parts),
                        reverse=True,
                    )
                )
                for directory in directories:
                    fsync_directory_best_effort(directory)
                fsync_directory_best_effort(stage.parent)
            return records, restored
        except Exception as exc:
            cleanup_failures: list[Path] = []
            for record in records:
                stage = Path(record["stage"])
                try:
                    self._remove_local_state_restore_tree(stage)
                except Exception:
                    if os.path.lexists(stage):
                        cleanup_failures.append(stage)
            if cleanup_failures:
                raise _LocalStateRestoreRecoveryError(
                    "Local state restore staging failed and temporary data could not be cleaned",
                    list(dict.fromkeys(cleanup_failures)),
                ) from exc
            raise

    def _rollback_local_state_restore_publish(
        self,
        records: list[dict[str, Any]],
        publish_error: Exception,
    ) -> None:
        rollback_errors: list[str] = []
        recovery_paths: list[Path] = []
        for record in reversed(records):
            root = Path(record["root"])
            stage = Path(record["stage"])
            backup = Path(record["backup"])
            restored = False
            try:
                if record["published"] and os.path.lexists(root):
                    if os.path.lexists(stage):
                        raise RuntimeError(
                            f"Local state rollback staging path was recreated: {stage}"
                        )
                    os.replace(root, stage)
                    record["published"] = False
                if record["backupMoved"]:
                    if os.path.lexists(root):
                        raise RuntimeError(
                            f"Local state rollback target was recreated: {root}"
                        )
                    os.replace(backup, root)
                    record["backupMoved"] = False
                restored = True
                fsync_directory_best_effort(root.parent)
            except Exception as exc:  # noqa: BLE001 - restore every independent root.
                rollback_errors.append(f"{record['scope']}: {exc}")
                if os.path.lexists(backup):
                    recovery_paths.append(backup)
                elif os.path.lexists(root):
                    recovery_paths.append(root)
                elif os.path.lexists(stage):
                    recovery_paths.append(stage)
            finally:
                if restored or os.path.lexists(backup):
                    try:
                        self._remove_local_state_restore_tree(stage)
                    except Exception as exc:  # noqa: BLE001 - live/backup state remains authoritative.
                        rollback_errors.append(f"{record['scope']} staging cleanup: {exc}")
                        if os.path.lexists(stage):
                            recovery_paths.append(stage)

        if rollback_errors:
            unique_paths = list(dict.fromkeys(recovery_paths))
            if not unique_paths:
                unique_paths = [
                    Path(record["backup"])
                    for record in records
                    if os.path.lexists(record["backup"])
                ]
            raise _LocalStateRestoreRecoveryError(
                "Local state restore publish failed and prior state could not be restored"
                f" ({'; '.join(rollback_errors)})",
                unique_paths,
            ) from publish_error
        raise publish_error

    def _publish_local_state_restore(self, records: list[dict[str, Any]]) -> None:
        try:
            for record in records:
                root = Path(record["root"])
                stage = Path(record["stage"])
                backup = Path(record["backup"])
                if os.path.lexists(root) and (
                    _path_is_link_like(root) or not root.is_dir()
                ):
                    raise ValueError(
                        f"Local state restore root is not a regular directory: {root}"
                    )
                if os.path.lexists(backup):
                    raise RuntimeError(
                        f"Local state restore backup already exists: {backup}"
                    )
                if record["expected"] and (
                    not stage.is_dir() or _path_is_link_like(stage)
                ):
                    raise RuntimeError(
                        f"Local state restore staging root is unavailable: {stage}"
                    )
                if not record["expected"] and os.path.lexists(stage):
                    raise RuntimeError(
                        f"Unexpected local state restore staging root: {stage}"
                    )
                if root.parent.stat().st_dev != stage.parent.stat().st_dev:
                    raise RuntimeError(
                        f"Local state restore staging is not on the target filesystem: {root}"
                    )
        except Exception as exc:
            cleanup_failures: list[Path] = []
            for record in records:
                stage = Path(record["stage"])
                try:
                    self._remove_local_state_restore_tree(stage)
                except Exception:
                    if os.path.lexists(stage):
                        cleanup_failures.append(stage)
            if cleanup_failures:
                raise _LocalStateRestoreRecoveryError(
                    "Local state restore preflight failed and staging could not be cleaned",
                    list(dict.fromkeys(cleanup_failures)),
                ) from exc
            raise

        try:
            for record in records:
                root = Path(record["root"])
                stage = Path(record["stage"])
                backup = Path(record["backup"])
                if os.path.lexists(root):
                    os.replace(root, backup)
                    record["backupMoved"] = True
                if record["expected"]:
                    os.replace(stage, root)
                    record["published"] = True
                fsync_directory_best_effort(root.parent)
        except Exception as exc:  # noqa: BLE001 - rollback every root before returning.
            self._rollback_local_state_restore_publish(records, exc)

        cleanup_failures: list[Path] = []
        for record in records:
            for key in ("backup", "stage"):
                path = Path(record[key])
                try:
                    self._remove_local_state_restore_tree(path)
                except Exception:
                    if os.path.lexists(path):
                        cleanup_failures.append(path)
            fsync_directory_best_effort(Path(record["root"]).parent)
        if cleanup_failures:
            raise _LocalStateRestoreRecoveryError(
                "Local state restore committed but temporary state could not be cleaned",
                list(dict.fromkeys(cleanup_failures)),
            )

    def _restore_local_state_checkpoint_locked(
        self,
        checkpoint: dict[str, Any],
        *,
        expected_current_state_digest: str,
    ) -> dict[str, Any]:
        if re.fullmatch(r"[0-9a-f]{64}", expected_current_state_digest) is None:
            return {
                "ok": False,
                "checkpoint": checkpoint,
                "status": "current_state_digest_required",
                "error": "currentStateDigest from the restore preview is required.",
            }
        try:
            current_state_digest = self._local_state_current_digest()
        except Exception as exc:  # noqa: BLE001 - validation must fail before staging.
            return {
                "ok": False,
                "checkpoint": checkpoint,
                "status": "current_state_unavailable",
                "error": f"Local state could not be verified before restore: {exc}",
            }
        if current_state_digest != expected_current_state_digest:
            return {
                "ok": False,
                "checkpoint": checkpoint,
                "status": "current_state_changed",
                "currentStateDigest": current_state_digest,
                "error": "currentStateDigest no longer matches local skill state; preview and approve again.",
            }
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "local_state_archive")
        roots = self._local_state_checkpoint_roots()
        state_roots = {
            str(item.get("id") or ""): ensure_dict(item)
            for item in ensure_list(checkpoint.get("stateRoots"))
            if isinstance(item, dict)
        }
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members, expected_exists = self._validated_local_state_restore_members(
                    archive,
                    checkpoint,
                    roots,
                    state_roots,
                )
                current = self._local_state_archive_contents()
                app_state_root = self._ports.user_constraints_path().parent.resolve()
                for scope, root in roots.items():
                    if os.path.lexists(root) and (_path_is_link_like(root) or not root.is_dir()):
                        raise ValueError(f"Local state restore root is not a regular directory: {root}")
                    target_root = root.resolve()
                    if not is_path_within(target_root, app_state_root):
                        raise ValueError(f"Unsafe local state restore root: {target_root}")
                records, restored = self._stage_local_state_restore(
                    archive,
                    roots,
                    members,
                    expected_exists,
                )
            deleted = [
                name
                for scope in roots
                for name in sorted(current)
                if name == scope or name.startswith(scope + "/")
            ]
            self._publish_local_state_restore(records)

            return {
                "ok": True,
                "checkpoint": checkpoint,
                "restoredArchive": str(archive_path),
                "restoredFileCount": len(restored),
                "restoredFiles": restored,
                "deletedFileCount": len(deleted),
                "deletedFiles": deleted,
                "cleaned": deleted,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            failure = {
                "ok": False,
                "checkpoint": checkpoint,
                "error": f"Local state restore failed: {exc}",
            }
            recovery_paths = getattr(exc, "recovery_paths", ())
            if recovery_paths:
                failure["recoveryPaths"] = [str(path) for path in recovery_paths]
            return failure

    def _build_checkpoint_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        restore_payload = restore_payload or {}
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        if checkpoint.get("strategy") == "local_state_archive":
            return self._build_local_state_rollback_coverage_audit(checkpoint, phase, restore_payload)
        if checkpoint.get("strategy") == "project_chat_archive":
            return self._build_project_chat_rollback_coverage_audit(checkpoint, phase, restore_payload)
        touches_assets = self._checkpoint_touches_top_level(checkpoint, "Assets")
        touches_packages = self._checkpoint_touches_top_level(checkpoint, "Packages")
        touches_project_settings = self._checkpoint_touches_top_level(checkpoint, "ProjectSettings")
        project_root = Path(str(checkpoint.get("projectRoot") or "")).resolve() if checkpoint.get("projectRoot") else None
        stored_framework_snapshot = self._stored_checkpoint_framework_package_snapshot(checkpoint)
        framework_snapshot = (
            stored_framework_snapshot
            if phase != "checkpoint" and stored_framework_snapshot
            else self._checkpoint_framework_package_snapshot(project_root)
        )

        blocking_gaps: list[str] = []
        todos: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []

        def add_check(check_id: str, title: str, status: str, details: dict[str, Any]) -> None:
            checks.append({"id": check_id, "title": title, "status": status, **details})
            if status == "missing":
                blocking_gaps.append(check_id)

        add_check(
            "scene_prefab_component_state",
            "Scene, prefab, and serialized component state",
            "covered" if touches_assets else "missing",
            {
                "pathspec": "Assets",
                "covers": [
                    "scene files",
                    "prefabs",
                    "serialized Unity components",
                    "Modular Avatar and VRCFury components saved under Assets",
                    "NDMF plugin component settings saved under Assets",
                ],
            },
        )
        add_check(
            "generated_assets",
            "Generated assets",
            "covered" if touches_assets else "missing",
            {
                "pathspec": "Assets",
                "covers": [
                    "VRCForge generated assets under Assets",
                    "optimizer, wardrobe, shader, and import artifacts saved as project assets",
                ],
            },
        )
        add_check(
            "packages_manifest",
            "Packages manifest and lock state",
            "covered" if touches_packages else "missing",
            {
                "pathspec": "Packages",
                "covers": [
                    "Packages/manifest.json",
                    "Packages/packages-lock.json",
                    "MA, VRCF, NDMF, and optimizer package dependency versions",
                ],
                "frameworkPackages": framework_snapshot,
            },
        )
        add_check(
            "project_settings",
            "Project settings",
            "covered" if touches_project_settings else "missing",
            {
                "pathspec": "ProjectSettings",
                "covers": ["Unity project settings that can affect import, build, and validation behavior"],
            },
        )

        cache_cleanup = ensure_dict(restore_payload.get("unityCacheCleanup"))
        cache_status = "planned" if touches_packages else "skipped"
        cache_details: dict[str, Any] = {
            "requiresPackagesRestore": touches_packages,
            "targets": [f"Library/{name}" for name in UNITY_RESTORE_GENERATED_CACHE_DIRS],
            "preserved": [f"Library/{name}" for name in UNITY_RESTORE_PRESERVED_CACHE_DIRS],
        }
        if phase == "restore":
            if not touches_packages:
                cache_status = "skipped"
            elif not cache_cleanup:
                cache_status = "missing"
            elif cache_cleanup.get("ok"):
                cache_status = "passed"
                cache_details["deleted"] = ensure_string_list(cache_cleanup.get("deleted"))
            else:
                cache_status = "warning"
                cache_details["errors"] = ensure_string_list(cache_cleanup.get("errors"))
        add_check(
            "package_cache_generated_folders",
            "Generated compiler folders",
            cache_status,
            cache_details,
        )

        reload_result = ensure_dict(restore_payload.get("unityReload"))
        if phase == "restore":
            if not self._checkpoint_restore_handler:
                reload_status = "missing"
            elif reload_result.get("ok"):
                reload_status = "passed"
            else:
                reload_status = "warning"
        else:
            reload_status = "planned" if self._checkpoint_restore_handler else "missing"
        add_check(
            "unity_reload_after_restore",
            "Unity reload after restore",
            reload_status,
            {
                "tool": "vrc_reload_after_checkpoint_restore",
                "reason": "Restored scenes/assets must be reloaded before MA/VRCF/NDMF scanners or validation can be trusted.",
            },
        )

        validation_status = "todo"
        validation_details = {
            "required": True,
            "tools": ["vrcforge_run_validation_report", "vrcforge_build_test_readiness"],
            "covers": [
                "Unity compile status",
                "package dependency status",
                "MA/VRCF conflict context",
                "generated residue",
                "avatar hierarchy, FX, menu, parameter, material, and performance scanners where available",
            ],
        }
        post_restore_validation = ensure_dict(restore_payload.get("postRestoreValidation"))
        if phase == "restore" and post_restore_validation:
            validation_status = "passed" if post_restore_validation.get("ok") else "warning"
            validation_details["result"] = post_restore_validation
        else:
            todos.append(
                {
                    "id": "run_post_restore_validation",
                    "status": "todo",
                    "required": True,
                    "reason": "Rollback proof must run read-only validation after restore, especially for MA/VRCF/NDMF-heavy avatars.",
                    "tools": validation_details["tools"],
                }
            )
        add_check("validation_after_restore", "Validation after restore", validation_status, validation_details)

        if blocking_gaps:
            gate_status = "blocked"
        elif todos:
            gate_status = "todo"
        else:
            gate_status = "ready"
        return {
            "ok": not blocking_gaps,
            "schema": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "phase": phase,
            "gateStatus": gate_status,
            "pathspecs": pathspecs,
            "checks": checks,
            "blockingGaps": blocking_gaps,
            "todos": todos,
            "caveats": [
                "Raw Unity MCP writes outside VRCForge cannot be checkpointed by this audit.",
                "Unity reload confirms restored files are reloaded; semantic avatar safety still requires the post-restore validation TODO.",
            ],
        }

    def _build_local_state_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any],
    ) -> dict[str, Any]:
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        restored_files = ensure_string_list(restore_payload.get("restoredFiles"))
        deleted_files = ensure_string_list(restore_payload.get("deletedFiles"))
        checks = [
            {
                "id": "local_skill_package_store",
                "title": "Community skill package store",
                "status": "covered" if "skill-packages" in pathspecs else "missing",
                "pathspec": "skill-packages",
                "covers": ["installed .vsk package versions", "skill package registry", "installed package metadata"],
            },
            {
                "id": "local_projected_user_skills",
                "title": "Projected user skills",
                "status": "covered" if "skills" in pathspecs else "missing",
                "pathspec": "skills",
                "covers": ["SKILL.md projections created by .vsk imports", "user skill enable/disable metadata"],
            },
        ]
        blocking_gaps = [str(item["id"]) for item in checks if item["status"] == "missing"]
        todos: list[dict[str, Any]] = []
        if phase == "restore":
            checks.append(
                {
                    "id": "local_state_restore_applied",
                    "title": "Local state restore applied",
                    "status": "passed" if restore_payload.get("ok") else "missing",
                    "restoredFileCount": len(restored_files),
                    "deletedFileCount": len(deleted_files),
                }
            )
        else:
            todos.append(
                {
                    "id": "preview_or_restore_local_state_checkpoint",
                    "status": "todo",
                    "required": True,
                    "reason": "Preview or restore this checkpoint to verify the exact skill-package/user-skill delta.",
                    "tools": ["vrcforge_preview_restore_checkpoint", "vrcforge_restore_checkpoint"],
                }
            )
        gate_status = "blocked" if blocking_gaps else ("todo" if todos else "ready")
        return {
            "ok": not blocking_gaps,
            "schema": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "phase": phase,
            "gateStatus": gate_status,
            "pathspecs": pathspecs,
            "checks": checks,
            "blockingGaps": blocking_gaps,
            "todos": todos,
            "caveats": [
                "This checkpoint covers VRCForge local skill-package state, not Unity project files.",
                "Unity project writes still require the Unity project checkpoint policy.",
            ],
        }

    def _build_project_chat_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any],
    ) -> dict[str, Any]:
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        covered = pathspecs == [PROJECT_CHAT_CHECKPOINT_MEMBER]
        checks = [
            {
                "id": "project_chat_transcript_store",
                "title": "Project chat transcript store",
                "status": "covered" if covered else "missing",
                "pathspec": PROJECT_CHAT_CHECKPOINT_MEMBER,
                "covers": ["the exact project-owned chat transcript bytes and missing-file state"],
            }
        ]
        todos: list[dict[str, Any]] = []
        if phase == "restore":
            checks.append(
                {
                    "id": "project_chat_restore_applied",
                    "title": "Project chat restore applied",
                    "status": "passed" if restore_payload.get("ok") else "missing",
                    "restoredFileCount": len(ensure_string_list(restore_payload.get("restoredFiles"))),
                    "deletedFileCount": len(ensure_string_list(restore_payload.get("deletedFiles"))),
                }
            )
        else:
            todos.append(
                {
                    "id": "preview_or_restore_project_chat_checkpoint",
                    "status": "todo",
                    "required": True,
                    "reason": "Preview or restore the exact project chat file checkpoint to verify rollback.",
                    "tools": ["vrcforge_preview_restore_checkpoint", "vrcforge_restore_checkpoint"],
                }
            )
        blocking_gaps = [] if covered else ["project_chat_transcript_store"]
        return {
            "ok": covered,
            "schema": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "phase": phase,
            "gateStatus": "blocked" if blocking_gaps else ("todo" if todos else "ready"),
            "pathspecs": pathspecs,
            "checks": checks,
            "blockingGaps": blocking_gaps,
            "todos": todos,
            "caveats": [
                "This checkpoint covers only the project chat transcript store; it does not collect other hidden project data.",
                "Content-addressed recovery artifacts remain local evidence unless an exact rollback cleanup removes the matching quarantine copy.",
            ],
        }

    def _checkpoint_framework_package_snapshot(self, project_root: Path | None) -> dict[str, Any]:
        packages: dict[str, Any] = {
            key: {
                "label": info["label"],
                "packageIds": list(info["packageIds"]),
                "detected": False,
                "manifestDependency": False,
                "lockDependency": False,
                "versions": [],
            }
            for key, info in ROLLBACK_FRAMEWORK_PACKAGES.items()
        }
        if project_root is None:
            return {"ok": False, "projectReadable": False, "packages": packages}

        packages_dir = project_root / "Packages"
        manifest_deps, manifest_error = self._read_package_dependency_file(packages_dir / "manifest.json")
        lock_deps, lock_error = self._read_package_dependency_file(packages_dir / "packages-lock.json")
        for key, info in ROLLBACK_FRAMEWORK_PACKAGES.items():
            item = packages[key]
            versions: list[str] = []
            for package_id in info["packageIds"]:
                if package_id in manifest_deps:
                    item["manifestDependency"] = True
                    versions.append(str(manifest_deps[package_id]))
                if package_id in lock_deps:
                    item["lockDependency"] = True
                    versions.append(str(lock_deps[package_id]))
            item["detected"] = bool(item["manifestDependency"] or item["lockDependency"])
            item["versions"] = sorted({version for version in versions if version})

        return {
            "ok": not (manifest_error or lock_error),
            "projectReadable": packages_dir.is_dir(),
            "manifestPath": "Packages/manifest.json",
            "manifestReadable": bool(manifest_deps) or (packages_dir / "manifest.json").is_file(),
            "manifestError": manifest_error,
            "lockPath": "Packages/packages-lock.json",
            "lockReadable": bool(lock_deps) or (packages_dir / "packages-lock.json").is_file(),
            "lockError": lock_error,
            "packages": packages,
        }

    def _read_package_dependency_file(self, path: Path) -> tuple[dict[str, Any], str]:
        if not path.is_file():
            return {}, ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            return {}, str(exc)
        dependencies = payload.get("dependencies") if isinstance(payload, dict) else {}
        if not isinstance(dependencies, dict):
            return {}, ""
        result: dict[str, Any] = {}
        for key, value in dependencies.items():
            if isinstance(value, dict):
                result[str(key)] = value.get("version") or value.get("source") or ""
            else:
                result[str(key)] = value
        return result, ""

    def _stored_checkpoint_framework_package_snapshot(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        audit = ensure_dict(checkpoint.get("rollbackCoverageAudit"))
        for item in audit.get("checks") or []:
            if isinstance(item, dict) and item.get("id") == "packages_manifest":
                return ensure_dict(item.get("frameworkPackages"))
        return {}

    def _local_state_checkpoint_roots(self) -> dict[str, Path]:
        app_state_root = self._ports.user_constraints_path().parent
        return {
            "skill-packages": app_state_root / "skill-packages",
            "skills": self._ports.skills.user_skills_dir(),
        }

    def _local_state_archive_contents(self) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for scope, root in self._local_state_checkpoint_roots().items():
            if os.path.lexists(root) and (_path_is_link_like(root) or not root.is_dir()):
                raise ValueError(f"Local state root is not a regular directory: {root}")
            if not root.is_dir():
                continue
            for source in sorted(root.rglob("*")):
                if _path_is_link_like(source):
                    raise ValueError(f"Local state contains a linked path: {source}")
                if not source.is_file():
                    continue
                crc = 0
                with source.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        crc = zlib.crc32(chunk, crc)
                relative = f"{scope}/{source.relative_to(root).as_posix()}"
                result[relative] = (source.stat().st_size, crc & 0xFFFFFFFF)
        return result

    def _local_state_current_digest(self) -> str:
        """Bind a restore approval to the exact current local-state trees."""

        roots = self._local_state_checkpoint_roots()
        root_rows: list[list[Any]] = []
        entry_rows: list[list[Any]] = []
        for scope, root in sorted(roots.items()):
            exists = os.path.lexists(root)
            if exists and (_path_is_link_like(root) or not root.is_dir()):
                raise ValueError(
                    f"Local state root is not a regular directory: {root}"
                )
            root_rows.append([scope, str(root.absolute()), exists])
            if not exists:
                continue
            pending: list[tuple[Path, PurePosixPath]] = [
                (root, PurePosixPath("."))
            ]
            while pending:
                directory, relative_directory = pending.pop()
                children = sorted(
                    directory.iterdir(),
                    key=lambda path: (path.name.casefold(), path.name),
                )
                for child in children:
                    if _path_is_link_like(child):
                        raise ValueError(
                            f"Local state contains a linked path: {child}"
                        )
                    relative = (
                        PurePosixPath(child.name)
                        if relative_directory == PurePosixPath(".")
                        else relative_directory / child.name
                    )
                    relative_text = relative.as_posix()
                    if child.is_dir():
                        entry_rows.append([scope, "directory", relative_text])
                        pending.append((child, relative))
                        continue
                    if not child.is_file():
                        raise ValueError(
                            f"Local state contains a non-regular path: {child}"
                        )
                    before = child.stat(follow_symlinks=False)
                    file_digest = hashlib.sha256()
                    consumed = 0
                    with child.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            consumed += len(chunk)
                            file_digest.update(chunk)
                    after = child.stat(follow_symlinks=False)
                    if (
                        consumed != before.st_size
                        or after.st_size != before.st_size
                        or after.st_mtime_ns != before.st_mtime_ns
                    ):
                        raise ValueError(
                            f"Local state changed while being verified: {child}"
                        )
                    entry_rows.append(
                        [
                            scope,
                            "file",
                            relative_text,
                            consumed,
                            file_digest.hexdigest(),
                        ]
                    )
        payload = {
            "roots": root_rows,
            "entries": sorted(
                entry_rows,
                key=lambda row: (
                    str(row[0]),
                    str(row[2]).casefold(),
                    str(row[2]),
                    str(row[1]),
                ),
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def _validate_local_state_archive_member(self, name: str) -> None:
        text = str(name or "").replace("\\", "/")
        if text.endswith("/"):
            text = text[:-1]
        member = PurePosixPath(text)
        parts = member.parts
        raw_parts = text.split("/")
        if (
            len(parts) < 2
            or parts[0] not in LOCAL_STATE_CHECKPOINT_SCOPE
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise ValueError(f"Unsafe local state archive member: {name}")
        if (
            member.is_absolute()
            or Path(str(name)).is_absolute()
            or looks_like_absolute_path(text)
        ):
            raise ValueError(f"Unsafe local state archive member: {name}")

    def _cleanup_checkpoint_restore_unity_caches(
        self,
        checkpoint: dict[str, Any],
        restore_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._checkpoint_touches_packages(checkpoint):
            return {"ok": True, "skipped": True, "reason": "checkpoint does not restore Packages", "deleted": [], "errors": []}
        if checkpoint.get("strategy") == "archive":
            restored_paths = [
                *ensure_string_list(restore_payload.get("restoredFiles")),
                *ensure_string_list(restore_payload.get("deletedFiles")),
            ]
            if not any(
                Path(str(path).replace("\\", "/")).parts[:1] == ("Packages",)
                for path in restored_paths
            ):
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "checkpoint did not restore changed Packages files",
                    "deleted": [],
                    "errors": [],
                }
        project_root = Path(str(checkpoint.get("projectRoot") or "")).resolve()
        library_root = (project_root / "Library").resolve()
        deleted: list[str] = []
        errors: list[str] = []
        for name in UNITY_RESTORE_GENERATED_CACHE_DIRS:
            target = (library_root / name).resolve()
            if not is_path_within(target, library_root):
                errors.append(f"Unsafe Unity cache path skipped: {target}")
                continue
            if not target.exists():
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                deleted.append(str(target))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{target}: {exc}")
        return {
            "ok": not errors,
            "skipped": False,
            "reason": "checkpoint restores Packages",
            "deleted": deleted,
            "preserved": [str((library_root / name).resolve()) for name in UNITY_RESTORE_PRESERVED_CACHE_DIRS],
            "errors": errors,
        }

    def _checkpoint_touches_packages(self, checkpoint: dict[str, Any]) -> bool:
        return self._checkpoint_touches_top_level(checkpoint, "Packages")

    def _checkpoint_touches_top_level(self, checkpoint: dict[str, Any], top_level: str) -> bool:
        for pathspec in ensure_string_list(checkpoint.get("pathspecs")):
            parts = Path(str(pathspec).replace("\\", "/")).parts
            if top_level in parts:
                return True
        return False

    def _resolve_checkpoint_project_root(self, arguments: dict[str, Any]) -> Path | None:
        for key in (
            "project_root",
            "projectRoot",
            "project_path",
            "projectPath",
            "unity_project",
            "unityProject",
            "workspace_root",
            "workspaceRoot",
            "cwd",
        ):
            value = str(arguments.get(key) or "").strip()
            if value:
                return Path(value)
        checkpoint_id = str(arguments.get("checkpoint_id") or arguments.get("checkpointId") or "").strip()
        if checkpoint_id:
            checkpoint = self._load_checkpoint(checkpoint_id)
            if checkpoint and checkpoint.get("projectRoot"):
                return Path(str(checkpoint["projectRoot"]))
        if self._checkpoint_project_root_resolver is not None:
            value = str(self._checkpoint_project_root_resolver() or "").strip()
            if value:
                return Path(value)
        return None

    @staticmethod
    def _checkpoint_unavailable(
        checkpoint: dict[str, Any],
        *,
        error: str,
        reason_code: str,
        next_action: str,
    ) -> dict[str, Any]:
        projected = dict(checkpoint)
        projected.update(
            {
                "status": "unavailable",
                "available": False,
                "availabilityError": error,
                "availabilityReasonCode": reason_code,
                "nextAction": next_action,
            }
        )
        return {
            "ok": False,
            "status": "unavailable",
            "available": False,
            "checkpoint": projected,
            "reasonCode": reason_code,
            "nextAction": next_action,
            "error": error,
        }

    def _checkpoint_archive_metadata_available(
        self, checkpoint: dict[str, Any]
    ) -> dict[str, Any]:
        strategy = str(checkpoint.get("strategy") or "")
        if strategy not in {"archive", "local_state_archive", "project_chat_archive"}:
            return {"ok": True}
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        if strategy == "project_chat_archive":
            valid_pathspecs = pathspecs == [PROJECT_CHAT_CHECKPOINT_MEMBER]
        elif strategy == "local_state_archive":
            valid_pathspecs = bool(pathspecs) and set(pathspecs) <= set(LOCAL_STATE_CHECKPOINT_SCOPE)
        else:
            valid_pathspecs = bool(pathspecs) and set(pathspecs) <= set(UNITY_PROJECT_CHECKPOINT_SCOPE)
        if not valid_pathspecs:
            return self._checkpoint_unavailable(
                checkpoint,
                error="Checkpoint recovery scope metadata is missing or unsafe.",
                reason_code="checkpoint_scope_metadata_invalid",
                next_action="Create a new checkpoint before attempting this restore.",
            )
        try:
            archive_path = self._resolve_checkpoint_archive_path(checkpoint, strategy)
        except (OSError, ValueError):
            return self._checkpoint_unavailable(
                checkpoint,
                error="Checkpoint archive metadata is missing or invalid.",
                reason_code="checkpoint_archive_metadata_invalid",
                next_action="Verify checkpoint storage settings or create a new checkpoint.",
            )
        try:
            exists = archive_path.is_file()
        except OSError:
            exists = False
        if not exists:
            return self._checkpoint_unavailable(
                checkpoint,
                error="Checkpoint archive file is missing from configured storage.",
                reason_code="checkpoint_archive_missing",
                next_action="Verify checkpoint storage settings or create a new checkpoint.",
            )
        return {"ok": True, "archivePath": archive_path}

    @staticmethod
    def _checkpoint_archive_metadata_digest(checkpoint: dict[str, Any]) -> str:
        protected_metadata = {
            key: checkpoint.get(key)
            for key in (
                "id",
                "strategy",
                "archivePath",
                "projectRoot",
                "pathspecs",
                "stateRoots",
                "fileCount",
                "uncompressedBytes",
                "sourceExisted",
                "sourceDigest",
            )
        }
        encoded = json.dumps(
            protected_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _cached_archive_validation(
        self,
        checkpoint: dict[str, Any],
        archive_path: Path,
        validate: Callable[[Path], None],
    ) -> tuple[bool, str]:
        try:
            metadata = archive_path.stat()
            key = (
                os.path.normcase(str(archive_path.resolve())),
                int(metadata.st_size),
                int(metadata.st_mtime_ns),
                self._checkpoint_archive_metadata_digest(checkpoint),
            )
        except (OSError, TypeError, ValueError) as exc:
            return False, f"archive stat failed: {exc}"
        cached = self._archive_validation_cache.get(key)
        if cached is not None:
            return cached
        try:
            validate(archive_path)
            result = (True, "")
        except Exception as exc:  # noqa: BLE001 - validation failure is returned, never bypassed.
            result = (False, str(exc))
        self._archive_validation_cache[key] = result
        while len(self._archive_validation_cache) > 128:
            self._archive_validation_cache.pop(next(iter(self._archive_validation_cache)))
        return result

    def _checkpoint_available(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        if not checkpoint.get("ok"):
            return self._checkpoint_unavailable(
                checkpoint,
                error=str(checkpoint.get("error") or "Checkpoint is unavailable."),
                reason_code="checkpoint_creation_failed",
                next_action="Review the checkpoint failure and create a new checkpoint.",
            )
        if checkpoint.get("strategy") == "local_state_archive":
            metadata = self._checkpoint_archive_metadata_available(checkpoint)
            if not metadata.get("ok"):
                return metadata
            archive_path = Path(metadata["archivePath"])

            def validate_local_state(path: Path) -> None:
                with zipfile.ZipFile(path, "r") as archive:
                    if archive.testzip() is not None:
                        raise ValueError("archive CRC validation failed")
                    for info in archive.infolist():
                        if not info.is_dir():
                            self._validate_local_state_archive_member(info.filename)
            valid, error = self._cached_archive_validation(
                checkpoint, archive_path, validate_local_state
            )
            if not valid:
                return self._checkpoint_unavailable(
                    checkpoint,
                    error=f"Local state checkpoint is unreadable: {error}",
                    reason_code="checkpoint_archive_unreadable",
                    next_action="Create a new checkpoint; do not restore the damaged archive.",
                )
            return {"ok": True}
        if checkpoint.get("strategy") == "project_chat_archive":
            metadata = self._checkpoint_archive_metadata_available(checkpoint)
            if not metadata.get("ok"):
                return metadata
            try:
                self._project_chat_checkpoint_source(checkpoint)
                self._read_project_chat_checkpoint_bytes(checkpoint)
            except Exception as exc:  # noqa: BLE001
                return self._checkpoint_unavailable(
                    checkpoint,
                    error=f"Project chat checkpoint is unreadable: {exc}",
                    reason_code="checkpoint_archive_unreadable",
                    next_action="Create a new checkpoint; do not restore the damaged archive.",
                )
            return {"ok": True}
        if checkpoint.get("strategy") == "archive":
            metadata = self._checkpoint_archive_metadata_available(checkpoint)
            if not metadata.get("ok"):
                return metadata
            archive_path = Path(metadata["archivePath"])

            def validate_project_archive(path: Path) -> None:
                with zipfile.ZipFile(path, "r") as archive:
                    if archive.testzip() is not None:
                        raise ValueError("archive CRC validation failed")
                    allowed = set(UNITY_PROJECT_CHECKPOINT_SCOPE)
                    for info in archive.infolist():
                        if not info.is_dir():
                            self._normalize_project_archive_member(info.filename, allowed)
            valid, error = self._cached_archive_validation(
                checkpoint, archive_path, validate_project_archive
            )
            if not valid:
                return self._checkpoint_unavailable(
                    checkpoint,
                    error=f"Archive checkpoint is unreadable: {error}",
                    reason_code="checkpoint_archive_unreadable",
                    next_action="Create a new checkpoint; do not restore the damaged archive.",
                )
            return {"ok": True}
        git_root = Path(str(checkpoint.get("gitRoot") or ""))
        ref = str(checkpoint.get("checkpointRef") or "")
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        if not git_root.exists() or not ref or not pathspecs:
            return {"ok": False, "checkpoint": checkpoint, "error": "Checkpoint metadata is incomplete."}
        verify = self._ports.run_git(git_root, ["cat-file", "-e", f"{ref}^{{commit}}"])
        if not verify["ok"]:
            return {"ok": False, "checkpoint": checkpoint, "error": "Checkpoint git ref is no longer available."}
        return {"ok": True}

    def _append_checkpoint(self, record: dict[str, Any]) -> None:
        with self._ports.state.checkpoint_storage_lock:
            self._ports.checkpoint_log_path().parent.mkdir(parents=True, exist_ok=True)
            self._ports.ensure_jsonl_append_boundary_locked(self._ports.checkpoint_log_path())
            with self._ports.checkpoint_log_path().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                flush_and_fsync(handle)
            self._checkpoint_entry_cache.clear()
        self._maybe_record_adjustment_checkpoint(record)

    def _checkpoint_archive_files(self) -> list[dict[str, Any]]:
        root = self._ports.checkpoint_store_dir()
        if not root.is_dir():
            return []
        archives: list[dict[str, Any]] = []
        for path in root.rglob("*.zip"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            archives.append(
                {
                    "path": path,
                    "checkpointId": path.stem,
                    "sizeBytes": stat.st_size,
                    "modifiedAt": stat.st_mtime,
                }
            )
        return archives

    def _protected_checkpoint_archive_ids(
        self,
        *,
        include_recent: bool = False,
        archives: list[dict[str, Any]] | None = None,
    ) -> set[str]:
        protected: set[str] = set()
        for recovery in self._active_apply_recoveries():
            checkpoint_id = str(recovery.get("checkpointId") or recovery.get("checkpoint_id") or "").strip()
            if checkpoint_id:
                protected.add(checkpoint_id)
        if include_recent:
            candidates = archives if archives is not None else self._checkpoint_archive_files()
            for archive in sorted(candidates, key=lambda item: item["modifiedAt"], reverse=True)[
                :CHECKPOINT_ARCHIVE_PROTECTED_RECENT_COUNT
            ]:
                protected.add(str(archive["checkpointId"]))
        return protected

    def _remove_empty_checkpoint_archive_parents(self, start: Path) -> None:
        root = self._ports.checkpoint_store_dir().resolve()
        current = start
        while True:
            try:
                resolved = current.resolve()
            except OSError:
                break
            if resolved == root or root not in resolved.parents:
                break
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _read_checkpoint_entries(self, limit: int = 500) -> list[dict[str, Any]]:
        log_path = self._ports.checkpoint_log_path()
        try:
            metadata = log_path.stat()
            cache_key = (
                os.path.normcase(str(log_path.resolve())),
                int(metadata.st_size),
                int(metadata.st_mtime_ns),
            )
        except OSError:
            return []
        entries = self._checkpoint_entry_cache.get(cache_key)
        if entries is None:
            try:
                lines = _split_lf_jsonl_lines(log_path.read_bytes())
            except OSError:
                return []
            parsed: list[dict[str, Any]] = []
            for index, raw_line in enumerate(lines):
                try:
                    line = raw_line.decode("utf-8-sig" if index == 0 else "utf-8")
                    payload = _load_strict_json(line)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    continue
                if _checkpoint_record_state(payload) == "valid":
                    parsed.append(payload)
            entries = tuple(parsed[-1000:])
            self._checkpoint_entry_cache[cache_key] = entries
            while len(self._checkpoint_entry_cache) > 4:
                self._checkpoint_entry_cache.pop(next(iter(self._checkpoint_entry_cache)))
        return list(reversed(entries[-max(1, min(limit, 1000)) :]))

    def _load_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        if not checkpoint_id:
            return None
        for entry in self._read_checkpoint_entries(limit=1000):
            if entry.get("id") == checkpoint_id:
                return entry
        return None

    def _read_apply_recovery_entries(self, limit: int = 1000) -> list[dict[str, Any]]:
        if not self._ports.apply_recovery_log_path().exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._ports.apply_recovery_log_path().read_text(encoding="utf-8").splitlines()[-max(1, limit):]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("schema") == APPLY_RECOVERY_SCHEMA:
                entries.append(payload)
        return entries

    def _append_apply_recovery_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        payload = redact_sensitive(
            {
                "schema": APPLY_RECOVERY_SCHEMA,
                "updatedAt": now,
                **entry,
            }
        )
        if not payload.get("createdAt"):
            payload["createdAt"] = now
        self._ports.apply_recovery_log_path().parent.mkdir(parents=True, exist_ok=True)
        with self._ports.apply_recovery_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            flush_and_fsync(handle)
        return payload

    def _coalesced_apply_recoveries(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for entry in self._read_apply_recovery_entries(limit=2000):
            recovery_id = str(entry.get("id") or "").strip()
            if not recovery_id:
                continue
            previous = states.get(recovery_id, {})
            merged = {**previous, **entry}
            merged["blockingWrites"] = self._ports.approval.apply_recovery_blocks_writes(merged)
            states[recovery_id] = merged
        recoveries = list(states.values())
        if not include_resolved:
            recoveries = [recovery for recovery in recoveries if self._ports.approval.apply_recovery_blocks_writes(recovery)]
        return sorted(
            recoveries,
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            reverse=True,
        )

    def _active_apply_recoveries(self) -> list[dict[str, Any]]:
        return self._coalesced_apply_recoveries(include_resolved=False)

    def _select_apply_recovery(self, params: dict[str, Any], *, include_resolved: bool = False) -> dict[str, Any] | None:
        requested_id = str(
            params.get("recovery_id")
            or params.get("recoveryId")
            or params.get("id")
            or ""
        ).strip()
        checkpoint_id = str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip()
        recoveries = self._coalesced_apply_recoveries(include_resolved=include_resolved)
        if requested_id:
            for recovery in recoveries:
                if recovery.get("id") == requested_id:
                    return recovery
            return None
        if checkpoint_id:
            for recovery in recoveries:
                if str(recovery.get("checkpointId") or "") == checkpoint_id:
                    return recovery
            return None
        return recoveries[0] if recoveries else None

    def _classify_apply_recovery_incident(self, text: str, target_tool: str = "") -> str:
        lowered = f"{text or ''} {target_tool or ''}".lower()
        if any(token in lowered for token in ("timeout", "timed out", "hang", "hung", "not responding")):
            return "unity_timeout_or_hang"
        if any(token in lowered for token in ("crash", "crashed", "exited", "exit", "process died", "quit")):
            return "unity_process_exit"
        if any(token in lowered for token in ("modal", "dialog", "busy", "locked", "license")):
            return "unity_modal_or_busy"
        if any(token in lowered for token in ("mcp", "bridge", "connect", "disconnected", "unavailable", "offline")):
            return "unity_bridge_unavailable"
        if any(token in lowered for token in ("package", "manifest", "dependency", "compile", "compiler")):
            return "package_or_compile_conflict"
        return "write_interrupted"

    def _read_adjustment_checkpoint_entries(self) -> list[dict[str, Any]]:
        if not self._ports.adjustment_checkpoint_log_path().exists():
            return []
        try:
            payload = json.loads(self._ports.adjustment_checkpoint_log_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_entries = payload.get("checkpoints") if isinstance(payload, dict) else []
        if not isinstance(raw_entries, list):
            return []
        entries = [item for item in raw_entries if isinstance(item, dict)]
        return sorted(entries, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)

    def _write_adjustment_checkpoint_entries(self, entries: list[dict[str, Any]]) -> None:
        self._ports.adjustment_checkpoint_log_path().parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "vrcforge.adjustment_checkpoint_timeline.v1",
            "updatedAt": utc_now_iso(),
            "checkpoints": entries,
        }
        self._ports.adjustment_checkpoint_log_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any] | None:
        if not entry_id:
            return None
        for entry in self._read_adjustment_checkpoint_entries():
            if entry.get("id") == entry_id:
                return entry
        return None

    def _normalize_adjustment_checkpoint_kind(self, value: Any, *, required: bool) -> str:
        kind = str(value or "").strip().lower().replace("_", "-")
        if kind in {"blendshape", "face-tuning", "facial", "face"}:
            return "face"
        if kind in {"material", "shader-material", "shader-tuning", "shader"}:
            return "shader"
        if required:
            raise AgentGatewayError("kind must be one of: face, shader.", status_code=400)
        return ""

    def _resolve_or_create_adjustment_base_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(params.get("checkpointId") or params.get("checkpoint_id") or "").strip()
        if checkpoint_id:
            checkpoint = self._load_checkpoint(checkpoint_id)
            if not checkpoint:
                return {"ok": False, "error": "checkpointId was not found."}
            return checkpoint
        target_tool = str(params.get("targetTool") or params.get("target_tool") or "vrcforge_manual_adjustment_checkpoint")
        if target_tool == "vrcforge_restore_checkpoint":
            return {"ok": False, "error": "restore checkpoints cannot be used as adjustment snapshots."}
        fake_approval = {"id": str(params.get("approvalId") or ""), "targetTool": target_tool}
        arguments = {
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or "").strip(),
            "avatarPath": str(params.get("avatarPath") or params.get("avatar_path") or "").strip(),
        }
        return self._ports.approval.create_pre_write_checkpoint(fake_approval, arguments) or {"ok": False, "error": "checkpoint creation was skipped."}

    def _build_adjustment_checkpoint_entry(
        self,
        params: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        kind: str,
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        entry = {
            "id": existing.get("id") or f"adj_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
            "schema": "vrcforge.adjustment_checkpoint.v1",
            "kind": kind,
            "createdAt": existing.get("createdAt") or now,
            "updatedAt": now,
            "checkpointId": str(checkpoint.get("id") or ""),
            "targetTool": str(checkpoint.get("targetTool") or params.get("targetTool") or params.get("target_tool") or ""),
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or checkpoint.get("projectRoot") or existing.get("projectRoot") or ""),
            "avatarPath": str(params.get("avatarPath") or params.get("avatar_path") or existing.get("avatarPath") or ""),
            "label": str(params.get("label") or existing.get("label") or self._default_adjustment_checkpoint_label(kind, checkpoint)),
            "description": str(params.get("description") if "description" in params else existing.get("description") or ""),
            "tags": self._normalize_tags(params.get("tags") if "tags" in params else existing.get("tags")),
            "compareGroup": str(params.get("compareGroup") or params.get("compare_group") or existing.get("compareGroup") or kind),
            "source": str(params.get("source") or existing.get("source") or "manual"),
            "checkpoint": {
                "id": checkpoint.get("id"),
                "status": checkpoint.get("status"),
                "ok": checkpoint.get("ok"),
                "strategy": checkpoint.get("strategy"),
                "createdAt": checkpoint.get("createdAt"),
                "targetTool": checkpoint.get("targetTool"),
            },
            "restoreTool": "vrcforge_restore_checkpoint",
            "manualCrud": {"create": True, "read": True, "update": True, "delete": True, "overwrite": True},
        }
        if existing.get("revisions"):
            entry["revisions"] = ensure_list(existing.get("revisions"))
            entry["overwriteCount"] = int(existing.get("overwriteCount") or len(entry["revisions"]))
        return entry

    def _apply_adjustment_checkpoint_metadata(self, entry: dict[str, Any], params: dict[str, Any]) -> None:
        for source_key, target_key in (
            ("label", "label"),
            ("description", "description"),
            ("avatarPath", "avatarPath"),
            ("avatar_path", "avatarPath"),
            ("projectRoot", "projectRoot"),
            ("project_root", "projectRoot"),
            ("compareGroup", "compareGroup"),
            ("compare_group", "compareGroup"),
        ):
            if source_key in params:
                entry[target_key] = str(params.get(source_key) or "")
        if "tags" in params:
            entry["tags"] = self._normalize_tags(params.get("tags"))

    def _normalize_tags(self, value: Any) -> list[str]:
        if isinstance(value, list):
            raw = value
        elif isinstance(value, str):
            raw = re.split(r"[,;\s]+", value)
        else:
            raw = []
        tags: list[str] = []
        for item in raw:
            tag = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(item or "").strip()).strip("-")
            if tag and tag not in tags:
                tags.append(tag[:48])
        return tags[:24]

    def _default_adjustment_checkpoint_label(self, kind: str, checkpoint: dict[str, Any]) -> str:
        target_tool = str(checkpoint.get("targetTool") or "")
        prefix = "Face" if kind == "face" else "Shader"
        if target_tool:
            return f"{prefix} checkpoint before {target_tool}"
        return f"{prefix} checkpoint"

    def _maybe_record_adjustment_checkpoint(self, record: dict[str, Any]) -> None:
        target_tool = str(record.get("targetTool") or "")
        kind = ADJUSTMENT_CHECKPOINT_TARGETS.get(target_tool)
        if not kind or not record.get("ok") or not record.get("id"):
            return
        entries = self._read_adjustment_checkpoint_entries()
        checkpoint_id = str(record.get("id") or "")
        if any(entry.get("checkpointId") == checkpoint_id for entry in entries):
            return
        entry = self._build_adjustment_checkpoint_entry(
            {"source": "automatic", "projectRoot": record.get("projectRoot") or ""},
            record,
            kind=kind,
            existing={},
        )
        entry["source"] = "automatic"
        entries.insert(0, entry)
        self._write_adjustment_checkpoint_entries(entries)
