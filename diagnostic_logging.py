from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock, get_ident
from typing import Any, Callable, TextIO

from diagnostic_privacy import DiagnosticPrivacy


DIAGNOSTICS_SCHEMA = "vrcforge.diagnostics.v2"
LOG_LEVELS = ("error", "warn", "info", "debug", "trace")
LOG_RETENTION_DAYS = 5
LOG_MAX_FILES = 40
LOG_MAX_TOTAL_BYTES = 52_428_800
LOG_MAX_FILE_BYTES = 8_388_608
MAX_EVENT_DATA_BYTES = 262_144

_LEVEL_RANK = {level: index for index, level in enumerate(LOG_LEVELS)}
_LEVEL_ALIASES = {"success": "info", "warning": "warn"}
_LOG_NAME_RE = re.compile(r"^vrcforge_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_(\d+)\.log$")
_LEGACY_RAW_LOG_NAMES = {
    "dashboard.log",
    "backend_stdout.log",
    "backend_stderr.log",
    "interactions.jsonl",
}
_STREAM_LEVEL_RE = re.compile(r"^\s*(TRACE|DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s*:\s*", re.IGNORECASE)
_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}) "
    r"\[(?P<level>ERROR|WARN|INFO|DEBUG|TRACE)\] "
    r"\[(?P<scope>(?:\\.|[^\]])*)\] (?P<message>.*?) \| data=(?P<data>\{.*\})$"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_log_level(value: Any, default: str = "info") -> str:
    normalized = str(value or "").strip().lower()
    normalized = _LEVEL_ALIASES.get(normalized, normalized)
    return normalized if normalized in _LEVEL_RANK else default


def parse_log_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _escape_field(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("]", "\\]")
        .replace("|", "\\|")
    )


def _unescape_field(value: str) -> str:
    result: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            result.append({"r": "\r", "n": "\n"}.get(character, character))
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            result.append(character)
    if escaped:
        result.append("\\")
    return "".join(result)


def _normalize_json_value(value: Any, depth: int = 0) -> Any:
    if depth > 16:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(item, depth + 1) for key, item in list(value.items())[:500]}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_json_value(item, depth + 1) for item in list(value)[:500]]
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def format_log_line(entry: dict[str, Any]) -> str:
    parsed = parse_log_timestamp(entry.get("timestamp")) or _utc_now()
    display_timestamp = parsed.astimezone().isoformat(timespec="milliseconds").replace("T", " ")
    level = normalize_log_level(entry.get("level")).upper()
    scope = _escape_field(entry.get("scope") or "backend")
    message = _escape_field(entry.get("message") or "")
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {"value": entry.get("data")}
    encoded_data = json.dumps(
        _normalize_json_value(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{display_timestamp} [{level}] [{scope}] {message} | data={encoded_data}"


def parse_log_line(line: str) -> dict[str, Any] | None:
    match = _LOG_LINE_RE.fullmatch(str(line).rstrip("\r\n"))
    if match is None:
        return None
    try:
        parsed_timestamp = datetime.fromisoformat(match.group("timestamp"))
        data = json.loads(match.group("data"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        data = {"value": data}
    return {
        "timestamp": parsed_timestamp.isoformat(),
        "level": match.group("level").lower(),
        "scope": _unescape_field(match.group("scope")),
        "message": _unescape_field(match.group("message")),
        "data": data,
    }


class DiagnosticLogManager:
    """Thread-safe rotating text logger that redacts before every observable sink."""

    def __init__(
        self,
        log_dir: Path,
        config_path: Path,
        privacy: DiagnosticPrivacy,
        *,
        now_fn: Callable[[], datetime] = _utc_now,
        retention_days: int = LOG_RETENTION_DAYS,
        max_files: int = LOG_MAX_FILES,
        max_total_bytes: int = LOG_MAX_TOTAL_BYTES,
        max_file_bytes: int = LOG_MAX_FILE_BYTES,
        recent_limit: int = 300,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.config_path = Path(config_path)
        self.privacy = privacy
        self._now_fn = now_fn
        self.retention_days = max(1, int(retention_days))
        self.max_files = max(1, int(max_files))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.recent_entries: deque[dict[str, Any]] = deque(maxlen=max(1, int(recent_limit)))
        self.lock = RLock()
        self._active_path: Path | None = None
        self._log_level = self._read_config_level()

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _read_config_level(self) -> str:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return "info"
        if not isinstance(payload, dict):
            return "info"
        explicit = normalize_log_level(payload.get("logLevel"), default="")
        if explicit:
            return explicit
        legacy = payload.get("debugLogging", payload.get("debug_logging"))
        return "debug" if legacy is True else "info"

    @property
    def log_level(self) -> str:
        with self.lock:
            return self._log_level

    @property
    def active_path(self) -> Path | None:
        with self.lock:
            return self._active_path

    def should_record(self, level: Any) -> bool:
        normalized = normalize_log_level(level)
        with self.lock:
            return _LEVEL_RANK[normalized] <= _LEVEL_RANK[self._log_level]

    def _write_config_locked(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": DIAGNOSTICS_SCHEMA,
            "logLevel": self._log_level,
            "debugLogging": self._log_level in {"debug", "trace"},
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".diagnostics-",
            suffix=".tmp",
            dir=str(self.config_path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.config_path)
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
        finally:
            temporary_path.unlink(missing_ok=True)

    def update_config(self, *, log_level: Any = None, debug_logging: Any = None) -> dict[str, Any]:
        with self.lock:
            previous_level = self._log_level
            try:
                if log_level is not None:
                    candidate = str(log_level).strip().lower()
                    if candidate not in LOG_LEVELS:
                        raise ValueError("Unsupported diagnostic log level.")
                    self._log_level = candidate
                elif debug_logging is not None:
                    self._log_level = "debug" if bool(debug_logging) else "info"
                self._write_config_locked()
            except Exception:
                self._log_level = previous_level
                raise
        return self.status()

    def status(self) -> dict[str, Any]:
        with self.lock:
            active = self._active_path
            if active is None:
                files = self._log_files_locked()
                active = files[-1] if files else None
            level = self._log_level
        try:
            identities = self.privacy.safe_identity_summaries()
            mapping_available = bool(getattr(self.privacy, "mapping_available", True))
        except Exception:  # noqa: BLE001 - Settings must survive private mapping I/O failure.
            identities = []
            mapping_available = False
        return {
            "ok": True,
            "schema": DIAGNOSTICS_SCHEMA,
            "logLevel": level,
            "logLevels": list(LOG_LEVELS),
            "debugLogging": level in {"debug", "trace"},
            "retentionDays": self.retention_days,
            "maxFiles": self.max_files,
            "maxTotalBytes": self.max_total_bytes,
            "maxFileBytes": self.max_file_bytes,
            "activeLogFile": active.name if active is not None else "",
            "redaction": {
                "enabled": True,
                "beforeDisk": True,
                "stableAliases": True,
                "mappingExcludedFromBundles": True,
                "mappingAvailable": mapping_available,
            },
            "identities": identities,
        }

    def _bounded_data(self, value: Any) -> dict[str, Any]:
        data = _normalize_json_value(value if isinstance(value, dict) else {"value": value})
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) <= MAX_EVENT_DATA_BYTES:
            return data
        return {
            "truncated": True,
            "originalBytes": len(encoded),
            "safeSha256": hashlib.sha256(encoded).hexdigest(),
        }

    def emit(
        self,
        level: Any,
        scope: Any,
        message: Any,
        data: Any = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        normalized_level = normalize_log_level(level)
        with self.lock:
            if _LEVEL_RANK[normalized_level] > _LEVEL_RANK[self._log_level]:
                return None
            try:
                raw_entry = {
                    "timestamp": self._now().isoformat(),
                    "level": normalized_level,
                    "scope": str(scope or "backend")[:128],
                    "message": str(message or "")[:32_768],
                    "data": data if isinstance(data, dict) else ({"value": data} if data is not None else {}),
                }
                safe_entry = self.privacy.redact(raw_entry, context=context)
                if context and any(str(value or "").strip() for value in context.values()):
                    safe_context = self.privacy.redact(context)
                    safe_data = safe_entry.get("data") if isinstance(safe_entry.get("data"), dict) else {}
                    safe_entry["data"] = {**safe_data, "identityContext": safe_context}
                safe_entry["level"] = normalized_level
                safe_entry["data"] = self._bounded_data(safe_entry.get("data"))
                safe_entry["id"] = (
                    f"log-{hashlib.sha256(format_log_line(safe_entry).encode('utf-8')).hexdigest()[:16]}"
                )
                line = format_log_line(safe_entry)
                self._append_line_locked(line)
                self.recent_entries.append(dict(safe_entry))
                self._prune_recent_locked()
            except Exception:  # noqa: BLE001 - never break product work or fall back to raw output.
                return None
            return dict(safe_entry)

    def _create_log_file_locked(self, when: datetime) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = when.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        for suffix in range(100_000):
            candidate = self.log_dir / f"vrcforge_{stamp}_{suffix}.log"
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            else:
                os.close(descriptor)
                try:
                    os.chmod(candidate, 0o600)
                except OSError:
                    pass
                self._active_path = candidate
                return candidate
        raise RuntimeError("Unable to allocate a diagnostic log shard.")

    def _append_line_locked(self, line: str) -> None:
        encoded = (line + "\n").encode("utf-8")
        active = self._active_path
        now = self._now()
        if active is not None:
            match = _LOG_NAME_RE.fullmatch(active.name)
            if match is None or match.group(1) != now.astimezone().strftime("%Y-%m-%d"):
                active = None
                self._active_path = None
        try:
            current_size = active.stat().st_size if active is not None else 0
        except OSError:
            active = None
            current_size = 0
        if active is None or (current_size > 0 and current_size + len(encoded) > self.max_file_bytes):
            active = self._create_log_file_locked(now)
        with active.open("ab") as output:
            output.write(encoded)
            output.flush()
        self._cleanup_locked()

    def _log_files_locked(self) -> list[Path]:
        if not self.log_dir.exists():
            return []
        try:
            files = [path for path in self.log_dir.iterdir() if path.is_file() and _LOG_NAME_RE.fullmatch(path.name)]
        except OSError:
            return []
        return sorted(files, key=lambda path: path.name)

    def cleanup(self) -> None:
        with self.lock:
            try:
                self._cleanup_locked()
                self._prune_recent_locked()
            except Exception:  # noqa: BLE001 - retention I/O failure cannot block startup or API work.
                pass
            try:
                self.privacy.cleanup()
            except Exception:  # noqa: BLE001 - private mapping cleanup is independently best-effort.
                pass

    def _cleanup_locked(self) -> None:
        if self.log_dir.exists():
            for name in _LEGACY_RAW_LOG_NAMES:
                legacy_path = self.log_dir / name
                try:
                    if legacy_path.is_file():
                        legacy_path.unlink()
                except OSError:
                    pass
        files = self._log_files_locked()
        now = self._now()
        if self._active_path is not None:
            active_match = _LOG_NAME_RE.fullmatch(self._active_path.name)
            if active_match is None or active_match.group(1) != now.astimezone().strftime("%Y-%m-%d"):
                self._active_path = None
        active = self._active_path.resolve() if self._active_path is not None and self._active_path.exists() else None
        cutoff = now.astimezone(timezone.utc) - timedelta(days=self.retention_days)
        survivors: list[Path] = []
        for path in files:
            try:
                resolved = path.resolve()
                stat = path.stat()
                match = _LOG_NAME_RE.fullmatch(path.name)
                if match is not None:
                    try:
                        created_local = datetime.strptime(
                            f"{match.group(1)} {match.group(2)}",
                            "%Y-%m-%d %H-%M-%S",
                        ).astimezone()
                    except ValueError:
                        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                    else:
                        modified = created_local.astimezone(timezone.utc)
                else:
                    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            except OSError:
                continue
            if resolved != active and modified < cutoff:
                try:
                    path.unlink()
                except OSError:
                    survivors.append(path)
            else:
                survivors.append(path)

        def snapshot(paths: list[Path]) -> tuple[list[tuple[float, Path, int]], int]:
            rows: list[tuple[float, Path, int]] = []
            total = 0
            for item in paths:
                try:
                    stat = item.stat()
                except OSError:
                    continue
                rows.append((stat.st_mtime, item, stat.st_size))
                total += stat.st_size
            return sorted(rows, key=lambda row: (row[0], row[1].name)), total

        ranked, total_bytes = snapshot(survivors)
        while len(ranked) > self.max_files or total_bytes > self.max_total_bytes:
            removable_index = next(
                (
                    index
                    for index, (_, path, _) in enumerate(ranked)
                    if active is None or path.resolve() != active
                ),
                None,
            )
            if removable_index is None:
                break
            _, path, size = ranked.pop(removable_index)
            try:
                path.unlink()
            except OSError:
                continue
            total_bytes -= size

    def _prune_recent_locked(self) -> None:
        cutoff = self._now().astimezone(timezone.utc) - timedelta(days=self.retention_days)
        while self.recent_entries:
            timestamp = parse_log_timestamp(self.recent_entries[0].get("timestamp"))
            if timestamp is not None and timestamp >= cutoff:
                break
            self.recent_entries.popleft()

    def recent_snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            self._prune_recent_locked()
            return [dict(item) for item in self.recent_entries]

    def tail_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        entries: list[dict[str, Any]] = []
        with self.lock:
            files = self._log_files_locked()
            for path in reversed(files):
                remaining = bounded - len(entries)
                if remaining <= 0:
                    break
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                parsed = [item for item in (parse_log_line(line) for line in lines[-remaining:]) if item is not None]
                entries[0:0] = parsed
        return entries[-bounded:]

    def tail_lines(self, limit: int = 200) -> list[str]:
        lines: list[str] = []
        for entry in self.tail_entries(limit):
            try:
                lines.append(format_log_line(self.privacy.redact(entry)))
            except Exception:  # noqa: BLE001 - omit a line that cannot be safely re-redacted.
                continue
        return lines


class DiagnosticTextStream(io.TextIOBase):
    def __init__(
        self,
        manager: DiagnosticLogManager,
        *,
        level: str,
        scope: str,
        detect_prefixed_level: bool = False,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.level = normalize_log_level(level)
        self.scope = scope
        self.detect_prefixed_level = detect_prefixed_level
        self._buffers: dict[int, str] = {}
        self._lock = RLock()

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    @property
    def errors(self) -> str | None:  # type: ignore[override]
        return "replace"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, value: str) -> int:
        text = str(value)
        thread_id = get_ident()
        complete: list[str] = []
        with self._lock:
            buffered = self._buffers.get(thread_id, "") + text
            parts = buffered.split("\n")
            self._buffers[thread_id] = parts.pop()
            complete.extend(part.rstrip("\r") for part in parts)
        for line in complete:
            if line:
                self._emit_line(line)
        return len(text)

    def flush(self) -> None:
        pending: list[str] = []
        with self._lock:
            pending = [value for value in self._buffers.values() if value]
            self._buffers.clear()
        for line in pending:
            self._emit_line(line.rstrip("\r"))

    def _emit_line(self, line: str) -> None:
        level = self.level
        if self.detect_prefixed_level:
            match = _STREAM_LEVEL_RE.match(line)
            if match is not None:
                prefix = match.group(1).lower()
                level = {
                    "critical": "error",
                    "warning": "warn",
                }.get(prefix, normalize_log_level(prefix))
        try:
            self.manager.emit(level, self.scope, line)
        except Exception:  # noqa: BLE001 - stream capture must not recurse into or break the runtime.
            return


def install_standard_stream_capture(manager: DiagnosticLogManager) -> tuple[TextIO, TextIO]:
    previous = (sys.stdout, sys.stderr)
    sys.stdout = DiagnosticTextStream(manager, level="info", scope="backend.stdout")
    sys.stderr = DiagnosticTextStream(
        manager,
        level="error",
        scope="backend.stderr",
        detect_prefixed_level=True,
    )
    return previous


__all__ = [
    "DIAGNOSTICS_SCHEMA",
    "DiagnosticLogManager",
    "DiagnosticTextStream",
    "LOG_LEVELS",
    "LOG_MAX_FILES",
    "LOG_MAX_FILE_BYTES",
    "LOG_MAX_TOTAL_BYTES",
    "LOG_RETENTION_DAYS",
    "format_log_line",
    "install_standard_stream_capture",
    "normalize_log_level",
    "parse_log_line",
    "parse_log_timestamp",
]
