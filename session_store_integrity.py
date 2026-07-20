"""Conservative integrity scanning and repair for app-owned session stores.

The module deliberately does not know about FastAPI, Doctor UI, or the
runtime's writer locks.  Callers must invoke repair while holding the owning
store's writer lock.  Scans are read-only.  Repairs are explicit, snapshot
bound, and never attempt to infer records from a malformed whole-JSON file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


SESSION_STORE_INTEGRITY_SCHEMA = "vrcforge.session_store_integrity.v1"

StoreScope = Literal["app_owned", "project_owned"]
StoreFormat = Literal["json", "jsonl"]
RootType = Literal["object", "array", "any"]
ListItemKind = Literal["any", "nonempty_string", "chat"]

_STORE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SAFE_REASON_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_PAYLOAD_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_CHAT_ITEM_TYPES = frozenset({"user", "streaming", "agent", "result", "error", "compact", "subagent"})
_MAX_JSON_DEPTH = 64
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


@dataclass(frozen=True)
class SessionStoreTarget:
    """One caller-owned persistence target.

    ``known_schemas`` is advisory compatibility metadata.  A syntactically
    valid record carrying an unknown schema is preserved byte-for-byte and is
    never auto-repaired.
    """

    store_id: str
    path: Path
    scope: StoreScope
    format: StoreFormat
    known_schemas: tuple[str, ...] = ()
    schema_field: str = "schema"
    schema_required: bool = False
    root_type: RootType = "object"
    required_string_fields: tuple[str, ...] = ()
    required_object_fields: tuple[str, ...] = ()
    required_list_field: str = ""
    required_list_item_kind: ListItemKind = "any"
    document_version_field: str = ""
    known_document_versions: tuple[int, ...] = ()
    guard_root: Path | None = None
    max_bytes: int = 64 * 1024 * 1024
    max_list_items: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if self.guard_root is not None:
            object.__setattr__(self, "guard_root", Path(self.guard_root))
        if not _STORE_ID_PATTERN.fullmatch(self.store_id):
            raise ValueError("store_id must be a stable lowercase identifier")
        if self.scope not in {"app_owned", "project_owned"}:
            raise ValueError("scope must be app_owned or project_owned")
        if self.format not in {"json", "jsonl"}:
            raise ValueError("format must be json or jsonl")
        if self.root_type not in {"object", "array", "any"}:
            raise ValueError("root_type must be object, array, or any")
        if not self.path.name:
            raise ValueError("path must name a file")
        if not self.schema_field or len(self.schema_field) > 80:
            raise ValueError("schema_field is invalid")
        if self.schema_required and not self.known_schemas:
            raise ValueError("schema_required requires known_schemas")
        for field_name in (*self.required_string_fields, *self.required_object_fields):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", field_name):
                raise ValueError("required record field is invalid")
        if self.required_list_field and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", self.required_list_field):
            raise ValueError("required_list_field is invalid")
        if self.required_list_item_kind not in {"any", "nonempty_string", "chat"}:
            raise ValueError("required_list_item_kind is invalid")
        if self.required_list_item_kind != "any" and not self.required_list_field:
            raise ValueError("required_list_item_kind requires required_list_field")
        if self.document_version_field and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", self.document_version_field):
            raise ValueError("document_version_field is invalid")
        if self.known_document_versions and not self.document_version_field:
            raise ValueError("known_document_versions requires document_version_field")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in self.known_document_versions):
            raise ValueError("known_document_versions must contain integers")
        if not isinstance(self.max_bytes, int) or isinstance(self.max_bytes, bool) or self.max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if not isinstance(self.max_list_items, int) or isinstance(self.max_list_items, bool) or self.max_list_items < 0:
            raise ValueError("max_list_items must be a non-negative integer")
        if self.max_list_items and not self.required_list_field:
            raise ValueError("max_list_items requires required_list_field")
        if any(not isinstance(item, str) or not item.strip() for item in self.known_schemas):
            raise ValueError("known_schemas must contain non-empty strings")


@dataclass(frozen=True)
class _Analysis:
    status: str
    reason: str
    record_count: int = 0
    invalid_count: int = 0
    unknown_schema_count: int = 0
    semantic_issue_count: int = 0
    valid_bytes: bytes = b""
    quarantine_bytes: bytes = b""
    record_recovery: bool = False


class _SnapshotConflict(RuntimeError):
    pass


def scan_session_store(target: SessionStoreTarget) -> dict[str, Any]:
    """Inspect one store without writing, renaming, touching, or repairing it."""

    if path_has_link_like_segment(target.path, target.guard_root):
        return _scan_result(
            target,
            exists=True,
            digest="",
            analysis=_Analysis(status="error", reason="symlink_refused"),
        )
    try:
        if target.path.stat().st_size > target.max_bytes:
            return _scan_result(
                target,
                exists=True,
                digest="",
                analysis=_Analysis(status="unsupported", reason="size_limit_exceeded"),
            )
    except FileNotFoundError:
        pass
    except OSError:
        return _scan_result(
            target,
            exists=True,
            digest="",
            analysis=_Analysis(status="error", reason="read_error"),
        )
    try:
        data = target.path.read_bytes()
    except FileNotFoundError:
        return _scan_result(
            target,
            exists=False,
            digest="",
            analysis=_Analysis(status="missing", reason="missing"),
        )
    except OSError:
        return _scan_result(
            target,
            exists=True,
            digest="",
            analysis=_Analysis(status="error", reason="read_error"),
        )
    if len(data) > target.max_bytes:
        return _scan_result(
            target,
            exists=True,
            digest="",
            analysis=_Analysis(status="unsupported", reason="size_limit_exceeded"),
        )

    return _scan_result(
        target,
        exists=True,
        digest=_sha256(data),
        analysis=_analyze(target, data),
    )


def scan_session_stores(targets: Sequence[SessionStoreTarget]) -> dict[str, Any]:
    """Read-only batch wrapper with a bounded, path-free public result."""

    stores = [scan_session_store(target) for target in targets]
    return {
        "schema": SESSION_STORE_INTEGRITY_SCHEMA,
        "status": "needs_repair"
        if any(item["status"] == "needs_repair" for item in stores)
        else "attention"
        if any(item["status"] in {"error", "unsupported"} for item in stores)
        else "ok",
        "storeCount": len(stores),
        "invalidCount": sum(int(item["invalidCount"]) for item in stores),
        "stores": stores,
    }


def repair_session_store(
    target: SessionStoreTarget,
    scan: Mapping[str, Any],
    *,
    project_write_authorized: bool = False,
) -> dict[str, Any]:
    """Explicitly repair one store against a prior read-only scan snapshot.

    The caller must hold the target owner's writer lock for the full call.
    Project-owned targets are changed only when a caller in the supervised
    project-write lane explicitly sets ``project_write_authorized``.  The
    default remains read-only and returns ``approval_required``.
    """

    if path_has_link_like_segment(target.path, target.guard_root):
        return _repair_result(target, status="conflict", reason="symlink_refused")

    validation_reason = _validate_scan_binding(target, scan)
    if validation_reason:
        return _repair_result(target, status="conflict", reason=validation_reason)

    expected_exists = bool(scan.get("exists"))
    expected_digest = str(scan.get("digest") or "")
    if _already_repaired(target, expected_exists, expected_digest):
        return _repair_result(
            target,
            status="already_repaired",
            reason="already_repaired",
            changed=False,
            before_digest=expected_digest,
            after_digest=_current_digest(target.path),
        )

    current = _read_current(target.path)
    if current is None:
        if not expected_exists:
            return _repair_result(target, status="no_change", reason="missing")
        return _repair_result(target, status="conflict", reason="snapshot_changed")
    current_data, current_digest = current
    if not expected_exists or not expected_digest or current_digest != expected_digest:
        return _repair_result(target, status="conflict", reason="snapshot_changed")

    analysis = _analyze(target, current_data)
    if analysis.status != "needs_repair":
        return _repair_result(
            target,
            status="no_change",
            reason=analysis.reason,
            before_digest=current_digest,
            after_digest=current_digest,
            invalid_count=analysis.invalid_count,
            unknown_schema_count=analysis.unknown_schema_count,
        )
    if target.scope == "project_owned" and not project_write_authorized:
        return _repair_result(
            target,
            status="approval_required",
            reason="project_write_supervision_required",
            before_digest=current_digest,
            after_digest=current_digest,
            invalid_count=analysis.invalid_count,
            unknown_schema_count=analysis.unknown_schema_count,
            requires_approval=True,
        )

    backup_path = _artifact_path(target.path, "backup", current_digest)
    quarantine_path = _artifact_path(target.path, "quarantine", current_digest)
    try:
        # Preserve the exact source before any rename or replacement.
        _write_once(backup_path, current_data)
        if (target.format == "json" and not analysis.record_recovery) or (target.format == "jsonl" and analysis.record_count == 0):
            _assert_digest(target.path, current_digest)
            if quarantine_path.exists():
                if _file_digest(quarantine_path) != current_digest:
                    raise _SnapshotConflict("quarantine collision")
                raise _SnapshotConflict("source and quarantine both exist")
            os.replace(target.path, quarantine_path)
            _fsync_directory(target.path.parent)
            return _repair_result(
                target,
                status="quarantined",
                reason="no_valid_records" if target.format == "jsonl" else analysis.reason,
                changed=True,
                before_digest=current_digest,
                after_digest="",
                invalid_count=analysis.invalid_count,
                unknown_schema_count=analysis.unknown_schema_count,
                backup_basename=backup_path.name,
                quarantine_basename=quarantine_path.name,
            )

        if not analysis.quarantine_bytes:
            return _repair_result(
                target,
                status="no_change",
                reason="no_syntax_damage",
                before_digest=current_digest,
                after_digest=current_digest,
                unknown_schema_count=analysis.unknown_schema_count,
            )
        _write_once(quarantine_path, analysis.quarantine_bytes)
        _replace_bytes_cas(target.path, analysis.valid_bytes, current_digest)
        after_digest = _sha256(analysis.valid_bytes)
        return _repair_result(
            target,
            status="repaired",
            reason=analysis.reason,
            changed=True,
            before_digest=current_digest,
            after_digest=after_digest,
            invalid_count=analysis.invalid_count,
            unknown_schema_count=analysis.unknown_schema_count,
            backup_basename=backup_path.name,
            quarantine_basename=quarantine_path.name,
        )
    except _SnapshotConflict:
        return _repair_result(
            target,
            status="conflict",
            reason="snapshot_changed",
            before_digest=current_digest,
            after_digest=_current_digest(target.path),
        )
    except OSError:
        return _repair_result(
            target,
            status="failed",
            reason="write_error",
            before_digest=current_digest,
            after_digest=_current_digest(target.path),
        )


def _analyze(target: SessionStoreTarget, data: bytes) -> _Analysis:
    if target.format == "jsonl":
        return _analyze_jsonl(target, data)
    return _analyze_json(target, data)


def _analyze_json(target: SessionStoreTarget, data: bytes) -> _Analysis:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _Analysis(status="needs_repair", reason="invalid_utf8", invalid_count=1)
    try:
        payload = load_strict_json(text)
    except (json.JSONDecodeError, ValueError):
        # Whole documents are atomic recovery units.  Do not scan substrings or
        # attempt to reconstruct an object from apparently valid fragments.
        return _Analysis(status="needs_repair", reason="invalid_json", invalid_count=1)

    semantic_issue = int(not _root_type_matches(payload, target.root_type))
    semantic_issue += int(_has_required_schema_issue(payload, target))
    semantic_issue += int(_has_required_field_issue(payload, target))
    unknown_schema = int(_has_unknown_schema(payload, target))
    if semantic_issue:
        return _Analysis(
            status="unsupported",
            reason="invalid_record_shape",
            record_count=1,
            semantic_issue_count=1,
            valid_bytes=data,
        )
    if unknown_schema:
        return _Analysis(
            status="unsupported",
            reason="unknown_schema",
            record_count=1,
            unknown_schema_count=1,
            valid_bytes=data,
        )
    if _has_unknown_document_version(payload, target):
        return _Analysis(
            status="unsupported",
            reason="unknown_version",
            record_count=1,
            semantic_issue_count=1,
            valid_bytes=data,
        )
    required_list_issue = bool(
        target.required_list_field
        and (
            not isinstance(payload, dict)
            or not isinstance(payload.get(target.required_list_field), list)
        )
    )
    if required_list_issue:
        return _Analysis(
            status="needs_repair",
            reason="invalid_record_shape",
            record_count=1,
            invalid_count=1,
            semantic_issue_count=1,
        )
    source_items = payload.get(target.required_list_field, []) if isinstance(payload, dict) else []
    if target.max_list_items and len(source_items) > target.max_list_items:
        return _Analysis(
            status="unsupported",
            reason="record_limit_exceeded",
            record_count=len(source_items),
            semantic_issue_count=1,
            valid_bytes=data,
        )
    invalid_items = _invalid_required_list_items(payload, target)
    if invalid_items:
        valid_payload = dict(payload)
        source_items = payload.get(target.required_list_field, [])
        invalid_indexes = {index for index, _value in invalid_items}
        valid_payload[target.required_list_field] = [
            value for index, value in enumerate(source_items) if index not in invalid_indexes
        ]
        quarantine_payload = {
            "schema": "vrcforge.session_store_quarantine.v1",
            "sourceField": target.required_list_field,
            "records": [{"index": index, "value": value} for index, value in invalid_items],
        }
        return _Analysis(
            status="needs_repair",
            reason="invalid_list_records",
            record_count=len(source_items),
            invalid_count=len(invalid_items),
            semantic_issue_count=len(invalid_items),
            valid_bytes=(json.dumps(valid_payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
            quarantine_bytes=(json.dumps(quarantine_payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
            record_recovery=True,
        )
    return _Analysis(status="ok", reason="ok", record_count=1, valid_bytes=data)


def _analyze_jsonl(target: SessionStoreTarget, data: bytes) -> _Analysis:
    valid_parts: list[bytes] = []
    quarantine_parts: list[bytes] = []
    record_count = 0
    invalid_count = 0
    unknown_schema_count = 0
    semantic_issue_count = 0

    for index, raw_line in enumerate(_split_jsonl_lines(data)):
        content = raw_line.rstrip(b"\r\n")
        if not content.strip(b" \t\r"):
            valid_parts.append(raw_line)
            continue
        try:
            text = content.decode("utf-8-sig" if index == 0 else "utf-8")
            payload = load_strict_json(text)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            invalid_count += 1
            quarantine_parts.append(raw_line)
            continue
        record_count += 1
        if not _root_type_matches(payload, target.root_type):
            semantic_issue_count += 1
        if _has_required_schema_issue(payload, target):
            semantic_issue_count += 1
        if _has_required_field_issue(payload, target):
            semantic_issue_count += 1
        if _has_unknown_schema(payload, target):
            unknown_schema_count += 1
        # Parseable records are never isolated for schema/shape reasons.  This
        # keeps future schemas and semantically unfamiliar rows byte-exact.
        valid_parts.append(raw_line)

    if invalid_count:
        status, reason = "needs_repair", "invalid_jsonl_lines"
    elif unknown_schema_count:
        status, reason = "unsupported", "unknown_schema"
    elif semantic_issue_count:
        status, reason = "unsupported", "invalid_record_shape"
    else:
        status, reason = "ok", "ok"
    return _Analysis(
        status=status,
        reason=reason,
        record_count=record_count,
        invalid_count=invalid_count,
        unknown_schema_count=unknown_schema_count,
        semantic_issue_count=semantic_issue_count,
        valid_bytes=b"".join(valid_parts),
        quarantine_bytes=b"".join(quarantine_parts),
    )


def _scan_result(
    target: SessionStoreTarget,
    *,
    exists: bool,
    digest: str,
    analysis: _Analysis,
) -> dict[str, Any]:
    requires_approval = target.scope == "project_owned" and analysis.status == "needs_repair"
    return {
        "schema": SESSION_STORE_INTEGRITY_SCHEMA,
        "storeId": target.store_id,
        "basename": target.path.name,
        "scope": target.scope,
        "format": target.format,
        "status": analysis.status,
        "reason": _safe_reason(analysis.reason),
        "exists": exists,
        "digest": digest,
        "recordCount": analysis.record_count,
        "invalidCount": analysis.invalid_count,
        "unknownSchemaCount": analysis.unknown_schema_count,
        "semanticIssueCount": analysis.semantic_issue_count,
        "repairable": analysis.status == "needs_repair" and target.scope == "app_owned",
        "requiresApproval": requires_approval,
    }


def _repair_result(
    target: SessionStoreTarget,
    *,
    status: str,
    reason: str,
    changed: bool = False,
    before_digest: str = "",
    after_digest: str = "",
    invalid_count: int = 0,
    unknown_schema_count: int = 0,
    requires_approval: bool = False,
    backup_basename: str = "",
    quarantine_basename: str = "",
) -> dict[str, Any]:
    return {
        "schema": SESSION_STORE_INTEGRITY_SCHEMA,
        "storeId": target.store_id,
        "basename": target.path.name,
        "scope": target.scope,
        "format": target.format,
        "status": status,
        "reason": _safe_reason(reason),
        "changed": changed,
        "beforeDigest": before_digest,
        "afterDigest": after_digest,
        "invalidCount": invalid_count,
        "unknownSchemaCount": unknown_schema_count,
        "requiresApproval": requires_approval,
        "backupBasename": backup_basename,
        "quarantineBasename": quarantine_basename,
    }


def _validate_scan_binding(target: SessionStoreTarget, scan: Mapping[str, Any]) -> str:
    if scan.get("schema") != SESSION_STORE_INTEGRITY_SCHEMA:
        return "invalid_scan"
    expected = {
        "storeId": target.store_id,
        "basename": target.path.name,
        "scope": target.scope,
        "format": target.format,
    }
    if any(scan.get(key) != value for key, value in expected.items()):
        return "invalid_scan"
    digest = scan.get("digest")
    if bool(scan.get("exists")):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return "invalid_scan"
    elif digest not in {"", None}:
        return "invalid_scan"
    return ""


def _already_repaired(target: SessionStoreTarget, expected_exists: bool, expected_digest: str) -> bool:
    if not expected_exists or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        return False
    backup_path = _artifact_path(target.path, "backup", expected_digest)
    quarantine_path = _artifact_path(target.path, "quarantine", expected_digest)
    if _is_link_like(backup_path) or not backup_path.is_file() or _file_digest(backup_path) != expected_digest:
        return False
    if target.format == "json":
        try:
            original = backup_path.read_bytes()
            repaired = _analyze(target, original)
        except OSError:
            return False
        if repaired.record_recovery:
            return (
                bool(repaired.quarantine_bytes)
                and not path_has_link_like_segment(target.path, target.guard_root)
                and target.path.is_file()
                and not _is_link_like(quarantine_path)
                and quarantine_path.is_file()
                and _file_digest(target.path) == _sha256(repaired.valid_bytes)
                and _file_digest(quarantine_path) == _sha256(repaired.quarantine_bytes)
            )
        return (
            not target.path.exists()
            and not _is_link_like(quarantine_path)
            and quarantine_path.is_file()
            and _file_digest(quarantine_path) == expected_digest
        )
    if _is_link_like(quarantine_path) or not quarantine_path.is_file():
        return False
    try:
        original = backup_path.read_bytes()
        repaired = _analyze(target, original)
        if repaired.record_count == 0:
            return (
                not target.path.exists()
                and _file_digest(quarantine_path) == expected_digest
            )
        if not target.path.is_file():
            return False
        quarantine_digest = _sha256(repaired.quarantine_bytes)
        return (
            bool(repaired.quarantine_bytes)
            and _file_digest(quarantine_path) == quarantine_digest
            and _file_digest(target.path) == _sha256(repaired.valid_bytes)
        )
    except OSError:
        return False


def _artifact_path(path: Path, kind: str, digest: str) -> Path:
    return path.with_name(f"{path.name}.vrcforge-{kind}-{digest[:16]}")


def _write_once(path: Path, data: bytes) -> None:
    if os.path.lexists(path):
        if not _is_link_like(path) and path.is_file() and _file_digest(path) == _sha256(data):
            return
        raise _SnapshotConflict("artifact collision")
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _replace_bytes_cas(path: Path, data: bytes, expected_digest: str) -> None:
    temporary = path.with_name(f".{path.name}.vrcforge-{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_digest(path, expected_digest)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _assert_digest(path: Path, expected_digest: str) -> None:
    current = _read_current(path)
    if current is None or current[1] != expected_digest:
        raise _SnapshotConflict("snapshot changed")


def _read_current(path: Path) -> tuple[bytes, str] | None:
    if _is_link_like(path):
        return None
    try:
        data = path.read_bytes()
    except (FileNotFoundError, OSError):
        return None
    return data, _sha256(data)


def path_has_link_like_segment(path: Path, guard_root: Path | None = None) -> bool:
    """Return true when a guarded path escapes its root or crosses a link/junction."""

    candidate = Path(os.path.abspath(path))
    if guard_root is None:
        return _is_link_like(candidate)
    root = Path(os.path.abspath(guard_root))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    if _is_link_like(current):
        return True
    for part in relative.parts:
        current = current / part
        if _is_link_like(current):
            return True
    return False


def _current_digest(path: Path) -> str:
    current = _read_current(path)
    return current[1] if current else ""


def _file_digest(path: Path) -> str:
    try:
        return _sha256(path.read_bytes())
    except OSError:
        return ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link_like(path: Path) -> bool:
    if not os.path.lexists(path):
        return False
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & 0x400)
    except OSError:
        return True


def _root_type_matches(payload: Any, root_type: RootType) -> bool:
    if root_type == "any":
        return True
    if root_type == "object":
        return isinstance(payload, dict)
    return isinstance(payload, list)


def _split_jsonl_lines(data: bytes) -> list[bytes]:
    """Split only on LF, the store's actual record delimiter.

    ``bytes.splitlines`` recognizes additional control bytes such as vertical
    tab and form feed.  Treating those as recovery boundaries would be an
    unsafe attempt to infer records from corrupted data.
    """

    if not data:
        return []
    chunks = data.split(b"\n")
    lines = [chunk + b"\n" for chunk in chunks[:-1]]
    if chunks[-1]:
        lines.append(chunks[-1])
    return lines


def _has_unknown_schema(payload: Any, target: SessionStoreTarget) -> bool:
    if not target.known_schemas or not isinstance(payload, dict):
        return False
    schema = payload.get(target.schema_field)
    return isinstance(schema, str) and bool(schema.strip()) and schema not in target.known_schemas


def _has_required_schema_issue(payload: Any, target: SessionStoreTarget) -> bool:
    if not target.schema_required:
        return False
    if not isinstance(payload, dict):
        return True
    schema = payload.get(target.schema_field)
    return not isinstance(schema, str) or not bool(schema.strip())


def _has_required_field_issue(payload: Any, target: SessionStoreTarget) -> bool:
    if not target.required_string_fields and not target.required_object_fields:
        return False
    if not isinstance(payload, dict):
        return True
    if any(not isinstance(payload.get(field), str) or not str(payload.get(field)).strip() for field in target.required_string_fields):
        return True
    return any(not isinstance(payload.get(field), dict) for field in target.required_object_fields)


def _has_unknown_document_version(payload: Any, target: SessionStoreTarget) -> bool:
    if not target.known_document_versions or not isinstance(payload, dict):
        return False
    if target.document_version_field not in payload:
        # Versionless v1 documents predate the field and remain readable.
        return False
    version = payload.get(target.document_version_field)
    return isinstance(version, bool) or not isinstance(version, int) or version not in target.known_document_versions


def _invalid_required_list_items(payload: Any, target: SessionStoreTarget) -> list[tuple[int, Any]]:
    if target.required_list_item_kind == "any" or not isinstance(payload, dict):
        return []
    values = payload.get(target.required_list_field)
    if not isinstance(values, list):
        return []
    invalid: list[tuple[int, Any]] = []
    seen_chat_ids: set[str] = set()
    for index, value in enumerate(values):
        if target.required_list_item_kind == "nonempty_string":
            valid = isinstance(value, str) and bool(value.strip())
        else:
            chat_id = value.get("id") if isinstance(value, dict) else None
            valid = is_valid_chat_record(value) and chat_id not in seen_chat_ids
            if valid:
                seen_chat_ids.add(chat_id)
        if not valid:
            invalid.append((index, value))
    return invalid


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _is_safe_nonnegative_integer(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and 0 <= value <= _MAX_SAFE_JSON_INTEGER
    )


def _is_valid_payload_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_PAYLOAD_HASH_PATTERN.fullmatch(value))


def _is_valid_attachment_payload_vault(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for payload_hash, entry in value.items():
        if not _is_valid_payload_hash(payload_hash) or not isinstance(entry, dict):
            return False
        if entry.get("payloadHash") != payload_hash or entry.get("payloadKind") not in {"text", "data_url"}:
            return False
        body_field = "text" if entry["payloadKind"] == "text" else "dataUrl"
        body = entry.get(body_field)
        if not isinstance(body, str):
            return False
        digest = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        if digest != payload_hash:
            return False
        other_field = "dataUrl" if body_field == "text" else "text"
        if other_field in entry and not isinstance(entry.get(other_field), str):
            return False
    return True


def _is_valid_compacted_attachment_reference(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if any(not isinstance(value.get(field), str) for field in ("id", "name", "type")):
        return False
    if not str(value.get("id") or "").strip() or not _is_safe_nonnegative_integer(value.get("size")):
        return False
    if value.get("payloadKind") not in {"text", "data_url", "vault_file"}:
        return False
    if not _is_valid_payload_hash(value.get("payloadHash")):
        return False
    if "vaultPayloadHash" in value and not _is_valid_payload_hash(value.get("vaultPayloadHash")):
        return False
    if "vaultKind" in value and not isinstance(value.get("vaultKind"), str):
        return False
    if "truncated" in value and not isinstance(value.get("truncated"), bool):
        return False
    # Compacted references are metadata only; payload bodies stay in the vault.
    return "text" not in value and "dataUrl" not in value


def _is_valid_context_usage(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if any(
        field in value and not isinstance(value.get(field), str)
        for field in ("schema", "source", "provider", "providerLabel", "model", "unavailableReason")
    ):
        return False
    if "exact" in value and not isinstance(value.get("exact"), bool):
        return False
    numeric_fields = (
        "inputTokens",
        "outputTokens",
        "totalTokens",
        "cumulativeInputTokens",
        "cumulativeOutputTokens",
        "cumulativeTotalTokens",
        "lastInputTokens",
        "lastOutputTokens",
        "lastTotalTokens",
        "peakInputTokens",
        "peakTotalTokens",
        "cacheReadTokens",
        "requestCount",
        "sentHistoryEntryCount",
        "sentHistoryCharacterCount",
        "promptCharacterCount",
        "lastPromptCharacterCount",
    )
    return all(field not in value or _is_finite_number(value.get(field)) for field in numeric_fields)


def _is_valid_context_compaction(value: Any, *, applied_required: bool) -> bool:
    if not isinstance(value, dict):
        return False
    if applied_required and not isinstance(value.get("applied"), bool):
        return False
    if "applied" in value and not isinstance(value.get("applied"), bool):
        return False
    if "blocked" in value and not isinstance(value.get("blocked"), bool):
        return False
    if any(
        field in value and not isinstance(value.get(field), str)
        for field in (
            "schema",
            "status",
            "generation",
            "trigger",
            "phase",
            "summary",
            "sourceDigest",
            "summaryDigest",
            "provider",
            "model",
            "fidelity",
            "failureClass",
            "suppressionReason",
            "startedAt",
            "completedAt",
            "message",
            "prefireOutcome",
        )
    ):
        return False
    numeric_fields = (
        "beforeTokens",
        "afterTokens",
        "contextLimit",
        "triggerTokens",
        "hardLimitTokens",
        "minimumReductionTokens",
        "targetAfterTokens",
        "entryCount",
        "retainedEntryCount",
        "attempts",
        "latencyMs",
        "retainedSummaryCharacters",
    )
    return all(field not in value or _is_finite_number(value.get(field)) for field in numeric_fields)


def _is_json_safe_runtime_value(value: Any) -> bool:
    """Accept bounded JSON values used by non-shell write result envelopes."""

    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if current is None or isinstance(current, (str, bool)):
            continue
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            if not math.isfinite(float(current)):
                return False
            continue
        if depth > _MAX_JSON_DEPTH:
            return False
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                return False
            pending.extend((item, depth + 1) for item in current.values())
            continue
        return False
    return True


def is_valid_chat_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    chat_id = value.get("id")
    items = value.get("items")
    if not isinstance(chat_id, str) or not chat_id.strip() or not isinstance(items, list):
        return False
    if any(
        field in value and not isinstance(value.get(field), str)
        for field in ("sessionId", "title", "projectPath", "createdAt", "updatedAt", "agentName")
    ):
        return False
    if any(field in value and not isinstance(value.get(field), bool) for field in ("pinned", "archived")):
        return False
    if "revision" in value and not _is_safe_nonnegative_integer(value.get("revision")):
        return False
    if "attachmentPayloads" in value and not _is_valid_attachment_payload_vault(value.get("attachmentPayloads")):
        return False
    if "compactedAttachmentRefs" in value:
        references = value.get("compactedAttachmentRefs")
        if not isinstance(references, list) or any(not _is_valid_compacted_attachment_reference(item) for item in references):
            return False
    if "contextUsageCache" in value and not _is_valid_context_usage(value.get("contextUsageCache")):
        return False
    if "compaction" in value and not _is_valid_context_compaction(value.get("compaction"), applied_required=False):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        item_id = item.get("id")
        item_type = item.get("type")
        if not isinstance(item_id, str) or not item_id.strip():
            return False
        if not isinstance(item_type, str) or item_type not in _CHAT_ITEM_TYPES:
            return False
        if any(
            field in item and not isinstance(item.get(field), str)
            for field in ("createdAt", "providerLabel", "model", "error")
        ):
            return False
        if item_type in {"user", "error", "compact"} and not isinstance(item.get("text"), str):
            return False
        if item_type == "user" and "attachments" in item:
            attachments = item.get("attachments")
            if not isinstance(attachments, list) or any(not _is_valid_chat_attachment(value) for value in attachments):
                return False
        if item_type == "user" and "queuedFrom" in item and not isinstance(item.get("queuedFrom"), bool):
            return False
        if item_type == "streaming" and (
            not isinstance(item.get("clientTurnId"), str)
            or not str(item.get("clientTurnId")).strip()
            or not isinstance(item.get("text"), str)
        ):
            return False
        if item_type == "agent":
            response = item.get("response")
            if not _is_valid_agent_response(response):
                return False
            if "elapsedSeconds" in item and not _is_finite_number(item.get("elapsedSeconds")):
                return False
        if item_type == "result" and (
            not isinstance(item.get("approvalId"), str) or not str(item.get("approvalId")).strip()
        ):
            return False
        if item_type == "result" and "result" in item and not _is_valid_shell_result(item.get("result")):
            return False
        if item_type == "subagent":
            task = item.get("task")
            if not isinstance(task, dict):
                return False
            if any(not isinstance(task.get(field), str) or not str(task.get(field)).strip() for field in ("id", "role", "displayName", "task", "status")):
                return False
            if any(field in task and not isinstance(task.get(field), str) for field in ("summary", "error", "mergeDecision", "mergedAt")):
                return False
        if item_type == "compact":
            if any(field in item and not isinstance(item.get(field), str) for field in ("detail", "status")):
                return False
            if any(
                field in item and not _is_finite_number(item.get(field))
                for field in ("entryCount", "beforeTokens", "afterTokens", "contextLimit")
            ):
                return False
    return True


def _is_valid_chat_attachment(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if any(not isinstance(value.get(field), str) for field in ("id", "name", "type")) or not str(value.get("id") or "").strip():
        return False
    size = value.get("size")
    if not _is_safe_nonnegative_integer(size):
        return False
    if any(field in value and not isinstance(value.get(field), str) for field in ("dataUrl", "text", "vaultKind", "error")):
        return False
    if "payloadKind" in value and value.get("payloadKind") not in {"data_url", "text", "metadata", "vault_file"}:
        return False
    if any(
        field in value and not _is_valid_payload_hash(value.get(field))
        for field in ("payloadHash", "vaultPayloadHash")
    ):
        return False
    return "truncated" not in value or isinstance(value.get("truncated"), bool)


def _is_valid_shell_result(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("ok"), bool) or not isinstance(value.get("timedOut"), bool):
        return False
    if any(not isinstance(value.get(field), str) for field in ("command", "cwd", "stdout", "stderr")):
        return False
    if any(
        field in value and not isinstance(value.get(field), bool)
        for field in ("stdoutTruncated", "stderrTruncated")
    ):
        return False
    return all(
        not isinstance(value.get(field), bool)
        and isinstance(value.get(field), (int, float))
        and math.isfinite(float(value.get(field)))
        for field in ("exitCode", "durationSeconds")
    )


def _is_valid_string_choices(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for choice in value:
        if not isinstance(choice, dict) or not isinstance(choice.get("label"), str):
            return False
        if any(field in choice and not isinstance(choice.get(field), str) for field in ("id", "description", "value")):
            return False
    return True


def _is_valid_agent_checkpoint(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not str(value.get("id") or "").strip():
        return False
    if any(
        field in value and not isinstance(value.get(field), str)
        for field in (
            "createdAt",
            "approvalId",
            "targetTool",
            "status",
            "error",
            "projectRoot",
            "gitRoot",
            "checkpointRef",
            "baseCommit",
        )
    ):
        return False
    if any(field in value and not isinstance(value.get(field), bool) for field in ("ok", "createdCommit")):
        return False
    for field in ("pathspecs", "statusBefore"):
        if field in value and (
            not isinstance(value.get(field), list)
            or any(not isinstance(item, str) for item in value.get(field, []))
        ):
            return False
    return True


def _is_valid_agent_approval(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if any(
        not isinstance(value.get(field), str) or not str(value.get(field) or "").strip()
        for field in ("id", "status")
    ):
        return False
    if any(
        field in value and not isinstance(value.get(field), str)
        for field in ("targetTool", "riskLevel", "reason", "createdAt")
    ):
        return False
    if any(field in value and not isinstance(value.get(field), dict) for field in ("arguments", "paramsSummary")):
        return False
    preview = value.get("preview")
    if preview is not None:
        if not isinstance(preview, dict):
            return False
        if any(field in preview and not isinstance(preview.get(field), str) for field in ("command", "cwd", "workspaceRoot")):
            return False
        if "riskReasons" in preview and (
            not isinstance(preview.get("riskReasons"), list)
            or any(not isinstance(reason, str) for reason in preview.get("riskReasons", []))
        ):
            return False
    return "checkpoint" not in value or _is_valid_agent_checkpoint(value.get("checkpoint"))


def _is_valid_choice_prompt(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("question"), str):
        return False
    if "id" in value and not isinstance(value.get("id"), str):
        return False
    return _is_valid_string_choices(value.get("choices"))


def _is_valid_agent_response(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if "ok" in value and not isinstance(value.get("ok"), bool):
        return False
    if any(
        field in value and not isinstance(value.get(field), str)
        for field in (
            "session_id",
            "sessionId",
            "turn_id",
            "turnId",
            "clientTurnId",
            "goalDeliveryId",
            "approval_id",
            "approvalId",
        )
    ):
        return False
    if "observe" in value and not isinstance(value.get("observe"), dict):
        return False
    if "choicePrompt" in value and not _is_valid_choice_prompt(value.get("choicePrompt")):
        return False
    if "contextUsage" in value and not _is_valid_context_usage(value.get("contextUsage")):
        return False
    if "contextCompaction" in value and not _is_valid_context_compaction(value.get("contextCompaction"), applied_required=True):
        return False
    if "attachments" in value:
        attachments = value.get("attachments")
        if not isinstance(attachments, list) or any(not _is_valid_chat_attachment(item) for item in attachments):
            return False
    write = value.get("write")
    if write is not None:
        if not isinstance(write, dict):
            return False
        if "ok" in write and not isinstance(write.get("ok"), bool):
            return False
        if any(
            field in write and not isinstance(write.get(field), str)
            for field in ("status", "tool", "approval_id", "approvalId", "error")
        ):
            return False
        if "paramsSummary" in write and not isinstance(write.get("paramsSummary"), dict):
            return False
        if "result" in write and not _is_json_safe_runtime_value(write.get("result")):
            return False
    if "result" in value and value.get("result") is not None:
        if write is None and not _is_valid_shell_result(value.get("result")):
            return False
        if write is not None and not _is_json_safe_runtime_value(value.get("result")):
            return False
    plan = value.get("plan")
    if not isinstance(plan, dict):
        return False
    if any(not isinstance(plan.get(field), str) for field in ("summary", "planner")):
        return False
    if not isinstance(plan.get("shellNeeded"), bool):
        return False
    if "skillNeeded" in plan and not isinstance(plan.get("skillNeeded"), bool):
        return False
    if any(
        field in plan and not isinstance(plan.get(field), str)
        for field in (
            "reply",
            "plannerLabel",
            "shellCommand",
            "skillTool",
            "skillCategory",
            "skillReason",
            "expectedResult",
            "nextStep",
        )
    ):
        return False
    if "skillParams" in plan and not isinstance(plan.get("skillParams"), dict):
        return False
    if "choices" in plan and not _is_valid_string_choices(plan.get("choices")):
        return False
    vision = value.get("vision")
    if vision is not None:
        if not isinstance(vision, dict):
            return False
        if "imageNames" in vision and (
            not isinstance(vision.get("imageNames"), list)
            or any(not isinstance(name, str) for name in vision.get("imageNames", []))
        ):
            return False
        if "usage" in vision and not isinstance(vision.get("usage"), dict):
            return False
        if "usage" in vision and not _is_valid_context_usage(vision.get("usage")):
            return False
        if "imageCount" in vision and not _is_finite_number(vision.get("imageCount")):
            return False
        if any(
            field in vision and not isinstance(vision.get(field), str)
            for field in ("status", "text", "provider", "providerLabel", "model", "source", "reason", "error", "notice")
        ):
            return False
    shell = value.get("shell")
    if shell is not None:
        if not isinstance(shell, dict):
            return False
        if "ok" in shell and not isinstance(shell.get("ok"), bool):
            return False
        if any(
            field in shell and not isinstance(shell.get(field), str)
            for field in ("status", "error", "approval_id", "approvalId")
        ):
            return False
        if "approval" in shell and not _is_valid_agent_approval(shell.get("approval")):
            return False
        classification = shell.get("classification")
        if classification is not None:
            if not isinstance(classification, dict):
                return False
            if any(not isinstance(classification.get(field), str) for field in ("risk", "command", "cwd")):
                return False
            reasons = classification.get("reasons")
            if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
                return False
        if "result" in shell and shell.get("result") is not None and not _is_valid_shell_result(shell.get("result")):
            return False
    reasoning = value.get("reasoning")
    if reasoning is not None:
        if not isinstance(reasoning, dict):
            return False
        items = reasoning.get("items", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return False
        if any(
            any(field in item and not isinstance(item.get(field), str) for field in ("title", "kind", "text"))
            or ("opaque" in item and not isinstance(item.get("opaque"), bool))
            for item in items
        ):
            return False
        if any(
            field in reasoning and not isinstance(reasoning.get(field), str)
            for field in ("schema", "provider", "providerLabel", "model", "source")
        ):
            return False
        if any(
            field in reasoning and not isinstance(reasoning.get(field), bool)
            for field in ("collapsedDefault", "redacted")
        ):
            return False
        if "itemCount" in reasoning and not _is_finite_number(reasoning.get("itemCount")):
            return False
    steps = value.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            return False
        for step in steps:
            if any(
                field in step and not isinstance(step.get(field), str)
                for field in ("kind", "tool", "summary", "status", "provider", "providerLabel", "model", "source")
            ):
                return False
            if any(
                field in step
                and (
                    isinstance(step.get(field), bool)
                    or not isinstance(step.get(field), (int, float))
                    or not math.isfinite(float(step.get(field)))
                )
                for field in ("index", "imageCount")
            ):
                return False
            if "usage" in step and not _is_valid_context_usage(step.get("usage")):
                return False
    skill = value.get("skill")
    if skill is not None:
        if not isinstance(skill, dict):
            return False
        if any(field in skill and not isinstance(skill.get(field), str) for field in ("status", "tool", "category", "summary", "error")):
            return False
        if any(field in skill and not isinstance(skill.get(field), bool) for field in ("ok", "write", "advanced")):
            return False
        if "paramsSummary" in skill and not isinstance(skill.get("paramsSummary"), dict):
            return False
    return True


def load_strict_json(text: str) -> Any:
    """Parse bounded standards-compliant JSON and reject Python's NaN extensions."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not supported: {value}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number is not supported: {value}")
        return parsed

    try:
        payload = json.loads(text, parse_constant=reject_constant, parse_float=parse_finite_float)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported limit") from exc
    pending: list[tuple[Any, int]] = [(payload, 1)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, (dict, list)):
            continue
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds the supported limit")
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return payload


def _safe_reason(value: str) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if _SAFE_REASON_PATTERN.fullmatch(normalized) else "unknown"


def _fsync_directory(path: Path) -> None:
    # Windows does not expose portable directory fsync through Python.  POSIX
    # callers get the stronger durability boundary; failure stays best-effort
    # because the file itself has already been fsynced.
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = [
    "SESSION_STORE_INTEGRITY_SCHEMA",
    "SessionStoreTarget",
    "repair_session_store",
    "path_has_link_like_segment",
    "is_valid_chat_record",
    "load_strict_json",
    "scan_session_store",
    "scan_session_stores",
]
