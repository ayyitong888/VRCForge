from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse


DOCTOR_FIX_SCHEMA = "vrcforge.doctor_fix.v1"
PAYLOAD_INTEGRITY_SCHEMA = "vrcforge.payload-integrity.v1"
DOCTOR_MODES = frozenset({"safe", "force"})
DETECT_STATUSES = frozenset({"ok", "warning", "error", "unknown", "skipped"})
REPAIR_OK_STATUSES = frozenset({"healthy", "repaired", "queued_for_approval"})
PHASE_STATUSES = frozenset({"ok", "warning", "error", "skipped"})

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_PATH_RE = re.compile(r"(?:^|\s|[\"'])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']*")
_UNIX_PATH_RE = re.compile(r"(?:^|\s|[\"'])/(?:Users|home|var|tmp|opt|mnt|Volumes)/[^\s\"']*")
_TOKEN_RE = re.compile(r"(?i)(?:bearer\s+|sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{8,})\S*")
_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(?:token|api[_-]?key|secret|password|fingerprint)\s*[:=]\s*[^\s,;&]+"
)
_DIGEST_RE = re.compile(r"(?i)\b[0-9a-f]{32,128}\b")
_EMBEDDED_URL_RE = re.compile(r"(?i)\b(?:https?|wss?)://[^\s<>\"']+")
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "fingerprint",
    "private_key",
    "privatekey",
    "raw",
    "content",
    "commandline",
    "cmdline",
    "executable",
    "path",
)


class DoctorServiceError(RuntimeError):
    """Transport-neutral error carrying the HTTP status a route should use."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _ConcurrentConfigWrite(OSError):
    pass


DetectCallback = Callable[[Mapping[str, Any]], Mapping[str, Any]]
RepairCallback = Callable[[Mapping[str, Any], str, "PhaseLog"], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class DoctorRule:
    check_id: str
    section: str
    title: str
    detect: DetectCallback
    repair: RepairCallback | None = None

    @property
    def fixable(self) -> bool:
        return self.repair is not None


class PhaseLog:
    """Collect a bounded, redacted phase trail for one repair attempt."""

    def __init__(self) -> None:
        self._phases: list[dict[str, Any]] = []

    def add(
        self,
        phase_id: str,
        status: str,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_status = status if status in PHASE_STATUSES else "error"
        phase = {
            "id": _safe_identifier(phase_id, fallback="phase"),
            "status": normalized_status,
            "message": _sanitize_string(str(message))[:500],
            "timestamp": _utc_now(),
        }
        if detail:
            phase["detail"] = _sanitize_value(detail)
        self._phases.append(phase)
        return phase

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(phase) for phase in self._phases]


class DoctorService:
    """Shared Doctor rule executor with dependency-injected runtime context."""

    def __init__(
        self,
        context: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
        rules: Iterable[DoctorRule] | None = None,
    ) -> None:
        self._context_source = context
        selected_rules = tuple(BUILTIN_DOCTOR_RULES if rules is None else rules)
        self._rules: dict[str, DoctorRule] = {}
        self._fix_locks: dict[str, threading.Lock] = {}
        self._registry_lock = threading.RLock()
        for rule in selected_rules:
            self.register_rule(rule)

    @property
    def rules(self) -> Mapping[str, DoctorRule]:
        with self._registry_lock:
            return MappingProxyType(dict(self._rules))

    def register_rule(self, rule: DoctorRule, *, replace: bool = False) -> None:
        if not isinstance(rule, DoctorRule):
            raise TypeError("rule must be a DoctorRule")
        if not _valid_check_id(rule.check_id):
            raise ValueError("Doctor rule check_id is invalid.")
        with self._registry_lock:
            if rule.check_id in self._rules and not replace:
                raise ValueError(f"Doctor rule is already registered: {rule.check_id}")
            self._rules[rule.check_id] = rule
            self._fix_locks.setdefault(rule.check_id, threading.Lock())

    def detect(self, check_id: str) -> dict[str, Any]:
        rule = self._require_rule(check_id)
        return self._detect_rule(rule, self._resolve_context())

    def detect_all(self) -> list[dict[str, Any]]:
        context = self._resolve_context()
        with self._registry_lock:
            rules = tuple(self._rules.values())
        return [self._detect_rule(rule, context) for rule in rules]

    def fix(
        self,
        check_id: str,
        mode: str = "safe",
        context_overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in DOCTOR_MODES:
            raise DoctorServiceError(422, "Doctor mode must be 'safe' or 'force'.")

        rule = self._require_rule(check_id)
        if rule.repair is None:
            raise DoctorServiceError(409, f"Doctor check is read-only: {rule.check_id}")

        lock = self._fix_locks[rule.check_id]
        if not lock.acquire(blocking=False):
            snapshot = self._detect_rule(rule, self._resolve_context_with_overrides(context_overrides))
            phases = PhaseLog()
            phases.add("single_flight", "warning", "Another repair for this check is already running.")
            return self._fix_result(
                rule=rule,
                mode=normalized_mode,
                status="busy",
                changed=False,
                phases=phases,
                before=snapshot,
                after=snapshot,
            )

        try:
            context = self._resolve_context_with_overrides(context_overrides)
            before = self._detect_rule(rule, context)
            phases = PhaseLog()
            if before["status"] == "ok":
                phases.add("already_healthy", "ok", "The check is already healthy; no changes were made.")
                return self._fix_result(
                    rule=rule,
                    mode=normalized_mode,
                    status="healthy",
                    changed=False,
                    phases=phases,
                    before=before,
                    after=before,
                )

            try:
                raw_outcome = rule.repair(context, normalized_mode, phases)
            except Exception:  # Repair errors must not disclose local data.
                phases.add("repair_failed", "error", "The repair failed before it could be completed safely.")
                raw_outcome = {"status": "failed", "changed": False}
            if not isinstance(raw_outcome, Mapping):
                phases.add(
                    "repair_contract",
                    "error",
                    "The repair returned an invalid result and was treated as failed.",
                )
                raw_outcome = {"status": "failed", "changed": False}

            status = str(raw_outcome.get("status") or "failed")
            if status not in {
                "healthy",
                "repaired",
                "queued_for_approval",
                "needs_user_action",
                "failed",
                "busy",
                "unsupported",
            }:
                status = "failed"
            changed = bool(raw_outcome.get("changed", False))
            after = self._detect_rule(rule, self._resolve_context_with_overrides(context_overrides))
            return self._fix_result(
                rule=rule,
                mode=normalized_mode,
                status=status,
                changed=changed,
                phases=phases,
                before=before,
                after=after,
            )
        finally:
            lock.release()

    def _resolve_context(self) -> Mapping[str, Any]:
        value = self._context_source() if callable(self._context_source) else self._context_source
        if not isinstance(value, Mapping):
            raise DoctorServiceError(500, "Doctor context is unavailable.")
        return value

    def _resolve_context_with_overrides(self, overrides: Mapping[str, Any] | None) -> Mapping[str, Any]:
        context = self._resolve_context()
        if not overrides:
            return context
        if not isinstance(overrides, Mapping):
            raise DoctorServiceError(422, "Doctor context overrides must be an object.")
        return {**context, **dict(overrides)}

    def _require_rule(self, check_id: str) -> DoctorRule:
        normalized = str(check_id or "").strip()
        with self._registry_lock:
            rule = self._rules.get(normalized)
        if rule is None:
            raise DoctorServiceError(404, "Unknown Doctor check id.")
        return rule

    @staticmethod
    def _detect_rule(rule: DoctorRule, context: Mapping[str, Any]) -> dict[str, Any]:
        try:
            raw = rule.detect(context)
            if not isinstance(raw, Mapping):
                raise TypeError("Detector returned a non-mapping value.")
            status = str(raw.get("status") or "unknown").strip().lower()
            if status not in DETECT_STATUSES:
                status = "unknown"
            message = _sanitize_string(str(raw.get("message") or "Check completed."))[:500]
            detail = _sanitize_value(raw.get("detail") if isinstance(raw.get("detail"), Mapping) else {})
        except Exception:  # Detector failures are represented without exception text.
            status = "error"
            message = "The check could not be completed safely."
            detail = {"reason": "detector_failed"}
        return {
            "id": rule.check_id,
            "section": _sanitize_string(str(rule.section))[:100],
            "title": _sanitize_string(str(rule.title))[:200],
            "status": status,
            "message": message,
            "fixable": rule.fixable,
            "detail": detail,
        }

    @staticmethod
    def _fix_result(
        *,
        rule: DoctorRule,
        mode: str,
        status: str,
        changed: bool,
        phases: PhaseLog,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> dict[str, Any]:
        return _sanitize_value(
            {
                "schema": DOCTOR_FIX_SCHEMA,
                "ok": status in REPAIR_OK_STATUSES,
                "checkId": rule.check_id,
                "mode": mode,
                "status": status,
                "changed": bool(changed),
                "generatedAt": _utc_now(),
                "phases": phases.snapshot(),
                "before": dict(before),
                "after": dict(after),
            }
        )


@dataclass(slots=True)
class _ConfigInspection:
    status: str
    message: str
    detail: dict[str, Any]
    path: Path | None = None
    original: bytes | None = None
    canonical: bytes | None = None
    repairable: bool = False


_API_KEY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("provider", ("provider",)),
    ("api_key", ("api_key", "apiKey")),
    ("base_url", ("base_url", "baseUrl")),
    ("model", ("model",)),
    ("thinking_level", ("thinking_level", "thinkingLevel")),
)
_VISION_KEY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("provider", ("provider",)),
    ("api_key", ("api_key", "apiKey")),
    ("base_url", ("base_url", "baseUrl")),
    ("model", ("model",)),
    ("enabled", ("enabled",)),
)
_TOP_LEVEL_API_ALIASES = {
    "provider": "provider",
    "api_key": "api_key",
    "apiKey": "api_key",
    "base_url": "base_url",
    "baseUrl": "base_url",
    "model": "model",
    "thinking_level": "thinking_level",
    "thinkingLevel": "thinking_level",
}
_TOP_LEVEL_VISION_ALIASES = {
    "visionProvider": "provider",
    "visionApiKey": "api_key",
    "visionBaseUrl": "base_url",
    "visionModel": "model",
    "visionEnabled": "enabled",
}
_SUPPORTED_PROVIDER_NAMES = frozenset(
    {"gemini", "deepseek", "openai", "openrouter", "anthropic", "ollama", "vertexai", "custom"}
)
_PROVIDER_ALIASES = {
    "google": "gemini",
    "google_ai": "gemini",
    "googleai": "gemini",
    "google-ai": "gemini",
    "google_ai_studio": "gemini",
    "google-ai-studio": "gemini",
    "ai_studio": "gemini",
    "aistudio": "gemini",
    "google_vertex": "vertexai",
    "google-vertex": "vertexai",
    "google_vertex_ai": "vertexai",
    "google-vertex-ai": "vertexai",
    "vertex": "vertexai",
    "vertex_ai": "vertexai",
    "vertex-ai": "vertexai",
}


def _app_config_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("app_config")
    if not isinstance(value, Mapping):
        value = context.get("config")
    return value if isinstance(value, Mapping) else {}


def _inspect_app_config(context: Mapping[str, Any]) -> _ConfigInspection:
    config_context = _app_config_context(context)
    path_value = config_context.get("path")
    if not isinstance(path_value, (str, os.PathLike)) or not str(path_value):
        return _ConfigInspection(
            "unknown",
            "App configuration location is not available.",
            {"configured": False},
        )
    path = Path(path_value)
    if _is_reparse_point(path) or _is_reparse_point(path.parent):
        return _ConfigInspection(
            "error",
            "App configuration uses an unsafe redirected file and was not inspected.",
            {"configured": True, "safeFile": False},
            path=path,
        )
    if not path.exists():
        return _ConfigInspection(
            "warning",
            "App configuration is missing; VRCForge will not invent replacement values.",
            {"configured": True, "exists": False},
            path=path,
        )
    if not path.is_file():
        return _ConfigInspection(
            "error",
            "App configuration is not a regular file.",
            {"configured": True, "exists": True, "regularFile": False},
            path=path,
        )

    try:
        original = path.read_bytes()
    except OSError:
        return _ConfigInspection(
            "error",
            "App configuration cannot be read.",
            {"configured": True, "exists": True, "readable": False},
            path=path,
        )
    try:
        parsed = json.loads(original.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _ConfigInspection(
            "error",
            "App configuration is not valid JSON; the original file will be preserved.",
            {"configured": True, "exists": True, "validJson": False, "preserved": True},
            path=path,
            original=original,
        )
    if not isinstance(parsed, dict):
        return _ConfigInspection(
            "error",
            "App configuration must be a JSON object; the original file will be preserved.",
            {
                "configured": True,
                "exists": True,
                "validJson": True,
                "objectDocument": False,
                "preserved": True,
            },
            path=path,
            original=original,
        )

    canonical, metrics = _canonicalize_app_config(parsed)
    if canonical is None:
        return _ConfigInspection(
            "error",
            "App configuration has an ambiguous section shape; the original file will be preserved.",
            {
                "configured": True,
                "exists": True,
                "validJson": True,
                "objectDocument": True,
                "canonicalizable": False,
                "preserved": True,
            },
            path=path,
            original=original,
        )
    semantically_canonical = parsed == canonical
    canonical_bytes = (
        original
        if semantically_canonical
        else (json.dumps(canonical, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    detail = {
        "configured": True,
        "exists": True,
        "validJson": True,
        "objectDocument": True,
        "canonicalizable": True,
        "canonical": semantically_canonical,
        **metrics,
    }
    if semantically_canonical:
        return _ConfigInspection(
            "ok",
            "App configuration is valid and canonical.",
            detail,
            path=path,
            original=original,
            canonical=canonical_bytes,
            repairable=True,
        )
    return _ConfigInspection(
        "warning",
        "App configuration can be canonicalized with a verified backup.",
        detail,
        path=path,
        original=original,
        canonical=canonical_bytes,
        repairable=True,
    )


def _canonicalize_app_config(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, int]]:
    raw_api = payload.get("api", {})
    raw_vision = payload.get("vision", {})
    if raw_api is not None and not isinstance(raw_api, Mapping):
        return None, {}
    if raw_vision is not None and not isinstance(raw_vision, Mapping):
        return None, {}
    api_source = raw_api if isinstance(raw_api, Mapping) else {}
    vision_source = raw_vision if isinstance(raw_vision, Mapping) else {}

    api_aliases = {alias for _target, aliases in _API_KEY_ALIASES for alias in aliases}
    vision_aliases = {alias for _target, aliases in _VISION_KEY_ALIASES for alias in aliases}
    api: dict[str, Any] = {key: value for key, value in api_source.items() if key not in api_aliases}
    vision: dict[str, Any] = {key: value for key, value in vision_source.items() if key not in vision_aliases}
    legacy_count = 0
    recognized_api: set[str] = set()
    recognized_vision: set[str] = set()
    for target, aliases in _API_KEY_ALIASES:
        present = [(alias, api_source[alias]) for alias in aliases if alias in api_source]
        if present and any(value != present[0][1] for _alias, value in present[1:]):
            return None, {}
        if present:
            api[target] = present[0][1]
            recognized_api.update(alias for alias, _value in present)
            legacy_count += sum(alias != target for alias, _value in present)
    for target, aliases in _VISION_KEY_ALIASES:
        present = [(alias, vision_source[alias]) for alias in aliases if alias in vision_source]
        if present and any(value != present[0][1] for _alias, value in present[1:]):
            return None, {}
        if present:
            vision[target] = present[0][1]
            recognized_vision.update(alias for alias, _value in present)
            legacy_count += sum(alias != target for alias, _value in present)

    for alias, target in _TOP_LEVEL_API_ALIASES.items():
        if alias in payload:
            if target in api and api[target] != payload[alias]:
                return None, {}
            api[target] = payload[alias]
            legacy_count += 1
    for alias, target in _TOP_LEVEL_VISION_ALIASES.items():
        if alias in payload:
            if target in vision and vision[target] != payload[alias]:
                return None, {}
            vision[target] = payload[alias]
            legacy_count += 1

    if not _provider_section_is_semantically_valid(api) or not _vision_section_is_semantically_valid(vision):
        return None, {}

    recognized_top = {"api", "vision", *_TOP_LEVEL_API_ALIASES, *_TOP_LEVEL_VISION_ALIASES}
    unknown_top_count = sum(1 for key in payload if key not in recognized_top)
    unknown_nested_count = sum(1 for key in api_source if key not in recognized_api) + sum(
        1 for key in vision_source if key not in recognized_vision
    )
    canonical: dict[str, Any] = {
        key: value
        for key, value in payload.items()
        if key not in recognized_top
    }
    if api or "api" in payload or any(alias in payload for alias in _TOP_LEVEL_API_ALIASES):
        canonical["api"] = api
    if vision or "vision" in payload or any(alias in payload for alias in _TOP_LEVEL_VISION_ALIASES):
        canonical["vision"] = vision
    return canonical, {
        "legacyKeyCount": legacy_count,
        "unknownTopLevelCount": unknown_top_count,
        "unknownNestedCount": unknown_nested_count,
    }


def _provider_section_is_semantically_valid(section: Mapping[str, Any]) -> bool:
    provider = section.get("provider")
    if provider is not None:
        if not isinstance(provider, str) or not provider.strip():
            return False
        normalized = provider.strip().lower().replace(" ", "_")
        if _PROVIDER_ALIASES.get(normalized, normalized) not in _SUPPORTED_PROVIDER_NAMES:
            return False
    for key in ("api_key", "base_url", "model", "thinking_level"):
        if key in section and section[key] is not None and not isinstance(section[key], str):
            return False
    return True


def _vision_section_is_semantically_valid(section: Mapping[str, Any]) -> bool:
    if not _provider_section_is_semantically_valid(section):
        return False
    return "enabled" not in section or isinstance(section["enabled"], bool)


def _detect_app_config(context: Mapping[str, Any]) -> Mapping[str, Any]:
    inspection = _inspect_app_config(context)
    return {"status": inspection.status, "message": inspection.message, "detail": inspection.detail}


def _repair_app_config(context: Mapping[str, Any], mode: str, phases: PhaseLog) -> Mapping[str, Any]:
    inspection = _inspect_app_config(context)
    phases.add(
        "inspect",
        "ok" if inspection.repairable else "warning",
        "App configuration was inspected without changing it.",
        {"status": inspection.status, "repairable": inspection.repairable},
    )
    if (
        not inspection.repairable
        or inspection.path is None
        or inspection.original is None
        or inspection.canonical is None
    ):
        phases.add(
            "preserve_original",
            "warning",
            "The original configuration was preserved because recovery is not reliable.",
            {"mode": mode, "changed": False},
        )
        return {"status": "needs_user_action", "changed": False}
    if inspection.original == inspection.canonical:
        return {"status": "healthy", "changed": False}

    try:
        backup_created = _write_content_addressed_backup(inspection.path, inspection.original)
    except OSError:
        phases.add("backup", "error", "A verified backup could not be created; no configuration write was attempted.")
        return {"status": "failed", "changed": False}
    phases.add(
        "backup",
        "ok",
        "An exact content-addressed backup was verified before the configuration write.",
        {"created": backup_created, "verified": True},
    )

    try:
        _atomic_replace_bytes(inspection.path, inspection.canonical, expected_current=inspection.original)
        verified = _inspect_app_config(context)
        if verified.status != "ok" or verified.original != inspection.canonical:
            raise OSError("post-write validation failed")
    except _ConcurrentConfigWrite:
        phases.add(
            "write",
            "warning",
            "Configuration changed after inspection; this repair made no changes and must be retried.",
        )
        return {"status": "failed", "changed": False}
    except OSError:
        restored = False
        try:
            _atomic_replace_bytes(inspection.path, inspection.original, expected_current=inspection.canonical)
            restored = inspection.path.read_bytes() == inspection.original
        except (OSError, _ConcurrentConfigWrite):
            restored = False
        phases.add(
            "write",
            "error",
            "Configuration replacement failed validation; the verified original was restored when possible.",
            {"restored": restored},
        )
        changed = False
        try:
            changed = inspection.path.read_bytes() != inspection.original
        except OSError:
            changed = True
        return {"status": "failed", "changed": changed}

    phases.add("write", "ok", "Canonical configuration was written atomically and re-read successfully.")
    return {"status": "repaired", "changed": True}


def _write_content_addressed_backup(path: Path, content: bytes) -> bool:
    digest = hashlib.sha256(content).hexdigest()
    backup = path.with_name(f"{path.name}.backup-{digest}.bak")
    if _is_reparse_point(backup) or (backup.exists() and not backup.is_file()):
        raise OSError("backup target is not a regular file")
    try:
        with backup.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        created = True
    except FileExistsError:
        created = False
    if backup.read_bytes() != content:
        raise OSError("backup verification failed")
    _fsync_directory(path.parent)
    return created


def _atomic_replace_bytes(path: Path, content: bytes, *, expected_current: bytes | None = None) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    original_mode: int | None = None
    try:
        try:
            original_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            original_mode = None
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_bytes() != content:
            raise OSError("temporary file verification failed")
        if original_mode is not None:
            try:
                os.chmod(temporary, original_mode)
            except OSError:
                pass
        if expected_current is not None:
            try:
                current = path.read_bytes()
            except OSError as exc:
                raise _ConcurrentConfigWrite("configuration changed during repair") from exc
            if current != expected_current:
                raise _ConcurrentConfigWrite("configuration changed during repair")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _doctor_port_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("doctor_port")
    if not isinstance(value, Mapping):
        value = context.get("port")
    return value if isinstance(value, Mapping) else {}


def _detect_doctor_port(context: Mapping[str, Any]) -> Mapping[str, Any]:
    port_context = _doctor_port_context(context)
    backend_host = str(port_context.get("host") or "127.0.0.1").strip()
    backend_port = _coerce_port(port_context.get("port"), 8757)
    gateway_url = str(port_context.get("gateway_url") or port_context.get("gatewayUrl") or "").strip()
    parsed_gateway = urlparse(gateway_url) if gateway_url else None
    gateway_host = str(
        port_context.get("gateway_host")
        or port_context.get("gatewayHost")
        or (parsed_gateway.hostname if parsed_gateway else "")
        or backend_host
    )
    gateway_port = _coerce_port(
        port_context.get("gateway_port")
        or port_context.get("gatewayPort")
        or (parsed_gateway.port if parsed_gateway else None),
        backend_port,
    )
    targets: dict[tuple[str, int], set[str]] = {}
    targets.setdefault((backend_host, backend_port), set()).add("backend")
    targets.setdefault((gateway_host, gateway_port), set()).add("gateway")

    current_pid = _coerce_positive_int(port_context.get("current_pid") or port_context.get("currentPid")) or os.getpid()
    owner_pid = _coerce_positive_int(port_context.get("owner_pid") or port_context.get("ownerPid"))
    owner_lease = bool(port_context.get("owner_lease_owned") or port_context.get("ownerLeaseOwned"))
    if owner_lease and owner_pid is None:
        owner_pid = current_pid
    lease_owned_by_current = owner_lease and owner_pid == current_pid

    listeners_provided = "listeners" in port_context
    raw_listeners = port_context.get("listeners") if listeners_provided else _collect_psutil_listeners()
    listener_rows = raw_listeners if isinstance(raw_listeners, list) else []
    listener_by_port: dict[int, list[dict[str, Any]]] = {}
    for item in listener_rows:
        if not isinstance(item, Mapping):
            continue
        port = _coerce_port(item.get("port"), 0)
        if port <= 0:
            continue
        listener_by_port.setdefault(port, []).append(
            {
                "pid": _coerce_positive_int(item.get("pid")),
                "processName": _safe_process_name(
                    item.get("process_name") or item.get("processName") or item.get("name")
                ),
            }
        )

    probe = port_context.get("can_bind") or port_context.get("canBind")
    can_bind = probe if callable(probe) else _can_bind_loopback
    rows: list[dict[str, Any]] = []
    warnings = False
    unknown = False
    for (host, port), roles in targets.items():
        loopback = _is_loopback_host(host)
        matching = listener_by_port.get(port, [])
        owned_listener = next((row for row in matching if row.get("pid") == current_pid), None)
        foreign_listener = next((row for row in matching if row.get("pid") not in {None, current_pid}), None)
        row: dict[str, Any] = {
            "port": port,
            "roles": sorted(roles),
            "loopback": loopback,
            "state": "unknown",
        }
        if not loopback:
            row["state"] = "non_loopback"
            warnings = True
        elif owned_listener is not None or (
            lease_owned_by_current and host == backend_host and port == backend_port
        ):
            row["state"] = "owned"
            row["pid"] = current_pid
            process_name = (owned_listener or {}).get("processName")
            if process_name:
                row["processName"] = process_name
        elif foreign_listener is not None:
            row["state"] = "foreign"
            row.update(foreign_listener)
            warnings = True
        else:
            try:
                available = can_bind(host, port)
            except Exception:
                available = None
            if available is True:
                row["state"] = "available"
            elif available is False:
                row["state"] = "occupied_unknown"
                unknown = True
            else:
                row["state"] = "unknown"
                unknown = True
        rows.append(row)

    if warnings:
        status = "warning"
        message = "One or more Doctor listener targets have an unsafe or foreign owner."
    elif unknown:
        status = "unknown"
        message = "Doctor could not identify every listener owner."
    else:
        status = "ok"
        message = "Doctor listener targets are loopback-only and available or owned by this runtime."
    return {
        "status": status,
        "message": message,
        "detail": {
            "sharedListener": backend_host == gateway_host and backend_port == gateway_port,
            "ownerLeaseOwned": lease_owned_by_current,
            "listeners": rows,
            "processInspectionAvailable": listeners_provided or raw_listeners is not None,
            "readOnly": True,
        },
    }


def _collect_psutil_listeners() -> list[dict[str, Any]] | None:
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:
        return None
    rows: list[dict[str, Any]] = []
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception:
        return None
    listen_status = str(getattr(psutil, "CONN_LISTEN", "LISTEN"))
    for connection in connections:
        if str(getattr(connection, "status", "")) != listen_status:
            continue
        address = getattr(connection, "laddr", None)
        port = getattr(address, "port", None)
        if port is None and isinstance(address, tuple) and len(address) >= 2:
            port = address[1]
        pid = _coerce_positive_int(getattr(connection, "pid", None))
        name = ""
        if pid is not None:
            try:
                name = str(psutil.Process(pid).name())
            except Exception:
                name = ""
        rows.append({"port": _coerce_port(port, 0), "pid": pid, "processName": _safe_process_name(name)})
    return rows


def _can_bind_loopback(host: str, port: int) -> bool | None:
    if not _is_loopback_host(host):
        return None
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe: socket.socket | None = None
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        if probe is not None:
            probe.close()


def _desktop_install_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("desktop_install")
    if not isinstance(value, Mapping):
        value = context.get("install_integrity")
    return value if isinstance(value, Mapping) else {}


def _detect_desktop_install_integrity(context: Mapping[str, Any]) -> Mapping[str, Any]:
    install = _desktop_install_context(context)
    packaged = bool(install.get("packaged", getattr(sys, "frozen", False)))
    if not packaged:
        return {
            "status": "skipped",
            "message": "Install integrity is only evaluated for packaged desktop builds.",
            "detail": {"packaged": False, "readOnly": True},
        }

    errors: list[str] = []
    warnings: list[str] = []
    unknowns: list[str] = []
    manifest = install.get("manifest") if isinstance(install.get("manifest"), Mapping) else None
    manifest_path = _optional_path(install.get("manifest_path") or install.get("manifestPath"))
    if manifest is None and manifest_path is not None:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            manifest = loaded if isinstance(loaded, Mapping) else None
            if manifest is None:
                errors.append("manifest_shape")
        except FileNotFoundError:
            unknowns.append("manifest_missing")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append("manifest_invalid")
    elif manifest is None:
        unknowns.append("manifest_missing")

    schema_valid: bool | None = None
    manifest_version_matches: bool | None = None
    expected_version = str(install.get("desktop_version") or install.get("desktopVersion") or "").strip()
    expected_hashes: dict[str, str] = {}
    if manifest is not None:
        schema_valid = manifest.get("schema") == PAYLOAD_INTEGRITY_SCHEMA
        if not schema_valid:
            errors.append("schema_mismatch")
        manifest_version = str(manifest.get("version") or "").strip()
        if expected_version:
            manifest_version_matches = manifest_version == expected_version
            if not manifest_version_matches:
                errors.append("manifest_version_mismatch")
        else:
            unknowns.append("desktop_version_unavailable")
        expected_hashes = _manifest_hashes(manifest)

    file_paths = {
        "desktop": _optional_path(install.get("desktop_path") or install.get("desktopPath")),
        "backend": _optional_path(install.get("backend_path") or install.get("backendPath")),
        "version": _optional_path(install.get("version_path") or install.get("versionPath")),
    }
    file_checks: list[dict[str, Any]] = []
    for component in ("desktop", "backend", "version"):
        path = file_paths[component]
        expected_hash = expected_hashes.get(component)
        exists = bool(path and path.is_file())
        hash_matched: bool | None = None
        if manifest is not None and not expected_hash:
            errors.append(f"{component}_hash_missing")
        elif path is None:
            unknowns.append(f"{component}_location_unavailable")
        elif not exists:
            errors.append(f"{component}_missing")
        elif expected_hash:
            try:
                hash_matched = _sha256_file(path) == expected_hash.lower()
            except OSError:
                hash_matched = False
            if not hash_matched:
                errors.append(f"{component}_hash_mismatch")
        file_checks.append({"component": component, "exists": exists, "hashMatched": hash_matched})

    version_file_matches: bool | None = None
    version_path = file_paths["version"]
    if expected_version and version_path is not None and version_path.is_file():
        try:
            version_file_matches = version_path.read_text(encoding="utf-8-sig").strip() == expected_version
        except OSError:
            version_file_matches = False
        if not version_file_matches:
            errors.append("version_file_mismatch")

    state_dir = _optional_path(install.get("state_dir") or install.get("stateDir"))
    state_writable: bool | None = None
    if state_dir is None:
        unknowns.append("state_location_unavailable")
    elif not state_dir.is_dir():
        state_writable = False
        errors.append("state_directory_missing")
    else:
        # Detection is strictly read-only. os.access is an advisory ACL check;
        # an actual write remains part of explicit repair/package smoke only.
        state_writable = os.access(state_dir, os.W_OK)
        if not state_writable:
            errors.append("state_directory_unwritable")

    relevant_paths = [path for path in [manifest_path, state_dir, *file_paths.values()] if path is not None]
    reparse_detected = any(_path_or_parent_has_reparse(path) for path in relevant_paths)
    cloud_providers = sorted({provider for path in relevant_paths for provider in _cloud_sync_providers(path)})
    if reparse_detected:
        warnings.append("reparse_point")
    if cloud_providers:
        warnings.append("cloud_sync")

    if errors:
        status = "error"
        message = "Packaged desktop integrity validation failed."
    elif unknowns:
        status = "unknown"
        message = "Packaged desktop integrity could not be fully established."
    elif warnings:
        status = "warning"
        message = "Packaged files are valid, but the install location has synchronization or redirection risk."
    else:
        status = "ok"
        message = "Packaged desktop, backend, version marker, and state directory passed integrity checks."
    return {
        "status": status,
        "message": message,
        "detail": {
            "packaged": True,
            "schemaValid": schema_valid,
            "manifestVersionMatched": manifest_version_matches,
            "versionFileMatched": version_file_matches,
            "fileChecks": file_checks,
            "stateWritable": state_writable,
            "stateProbePerformed": False,
            "reparsePointDetected": reparse_detected,
            "cloudSyncProviders": cloud_providers,
            "issueCodes": sorted(set(errors + warnings + unknowns)),
            "readOnly": True,
        },
    }


def _manifest_hashes(manifest: Mapping[str, Any]) -> dict[str, str]:
    files = manifest.get("files")
    hashes: dict[str, str] = {}
    if isinstance(files, Mapping):
        for component in ("desktop", "backend", "version"):
            entry = files.get(component)
            candidate = entry.get("sha256") if isinstance(entry, Mapping) else entry
            if isinstance(candidate, str) and _SHA256_RE.fullmatch(candidate):
                hashes[component] = candidate.lower()
        return hashes
    if not isinstance(files, list):
        return hashes
    for item in files:
        if not isinstance(item, Mapping):
            continue
        candidate = item.get("sha256")
        if not isinstance(candidate, str) or not _SHA256_RE.fullmatch(candidate):
            continue
        label = str(
            item.get("component") or item.get("name") or item.get("relativePath") or ""
        ).replace("\\", "/").lower()
        component = ""
        if label in {"desktop", "vrcforge.exe"} or label.endswith("/vrcforge.exe") and "/backend/" not in label:
            component = "desktop"
        elif label == "backend" or label.endswith("vrcforge_backend.exe"):
            component = "backend"
        elif label in {"version", "./version"} or label.endswith("/version"):
            component = "version"
        if component:
            hashes[component] = candidate.lower()
    return hashes


def _security_context(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    root = context.get("security")
    if not isinstance(root, Mapping):
        return {}
    value = root.get(key)
    return value if isinstance(value, Mapping) else {}


def _detect_security_external_writes(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _security_context(context, "external_writes")
    flags = _security_flags(
        value,
        {
            "broadPermissions": ("broad_permissions", "broadPermissions"),
            "approvalRequired": ("approval_required", "approvalRequired"),
            "checkpointRequired": ("checkpoint_required", "checkpointRequired"),
        },
    )
    if len(flags) < 3:
        return _unknown_security("External write posture is not fully available.", flags)
    unsafe = flags["broadPermissions"] or not flags["approvalRequired"] or not flags["checkpointRequired"]
    return _security_result(
        unsafe,
        "External write requests remain supervised.",
        "External write permissions are too broad.",
        flags,
    )


def _detect_security_bind_auth(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _security_context(context, "bind_auth")
    flags = _security_flags(
        value,
        {
            "publicBind": ("public_bind", "publicBind"),
            "tokenRequired": ("token_required", "tokenRequired"),
            "tokenStrong": ("token_strong", "tokenStrong"),
        },
    )
    if len(flags) < 3:
        return _unknown_security("Bind and authentication posture is not fully available.", flags)
    unsafe = flags["publicBind"] or not flags["tokenRequired"] or not flags["tokenStrong"]
    return _security_result(
        unsafe,
        "Runtime bind and authentication posture is bounded.",
        "Runtime bind or token posture needs attention.",
        flags,
    )


def _detect_security_mcp_exposure(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _security_context(context, "mcp_exposure")
    flags = _security_flags(
        value,
        {
            "broadExposure": ("broad_exposure", "broadExposure"),
            "writeToolsSupervised": ("write_tools_supervised", "writeToolsSupervised"),
        },
    )
    if len(flags) < 2:
        return _unknown_security("MCP exposure posture is not fully available.", flags)
    unsafe = flags["broadExposure"] or not flags["writeToolsSupervised"]
    return _security_result(
        unsafe,
        "MCP exposure is bounded and writes are supervised.",
        "MCP exposure is broader than the supervised contract.",
        flags,
    )


def _detect_security_process_exec(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _security_context(context, "process_exec")
    flags = _security_flags(
        value,
        {
            "unsafeExec": ("unsafe_exec", "unsafeExec"),
            "approvalRequired": ("approval_required", "approvalRequired"),
            "policyBounded": ("policy_bounded", "policyBounded"),
        },
    )
    if len(flags) < 3:
        return _unknown_security("Process execution posture is not fully available.", flags)
    unsafe = flags["unsafeExec"] or not flags["approvalRequired"] or not flags["policyBounded"]
    return _security_result(
        unsafe,
        "Process execution remains policy-bounded and supervised.",
        "Process execution policy needs attention.",
        flags,
    )


def _security_flags(value: Mapping[str, Any], aliases: Mapping[str, tuple[str, ...]]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for output_key, candidates in aliases.items():
        for candidate in candidates:
            if candidate in value and isinstance(value[candidate], bool):
                flags[output_key] = value[candidate]
                break
    return flags


def _security_result(
    unsafe: bool,
    healthy_message: str,
    warning_message: str,
    flags: Mapping[str, bool],
) -> Mapping[str, Any]:
    return {
        "status": "warning" if unsafe else "ok",
        "message": warning_message if unsafe else healthy_message,
        "detail": {**flags, "readOnly": True},
    }


def _unknown_security(message: str, flags: Mapping[str, bool]) -> Mapping[str, Any]:
    return {"status": "unknown", "message": message, "detail": {**flags, "readOnly": True}}


BUILTIN_DOCTOR_RULES: tuple[DoctorRule, ...] = (
    DoctorRule("app.config", "App", "App configuration", _detect_app_config, _repair_app_config),
    DoctorRule("doctor.port", "Runtime", "Doctor listener ports", _detect_doctor_port),
    DoctorRule(
        "desktop.install_integrity",
        "Desktop",
        "Desktop install integrity",
        _detect_desktop_install_integrity,
    ),
    DoctorRule(
        "security.external_writes",
        "Security",
        "External write permissions",
        _detect_security_external_writes,
    ),
    DoctorRule("security.bind_auth", "Security", "Bind and authentication", _detect_security_bind_auth),
    DoctorRule("security.mcp_exposure", "Security", "MCP exposure", _detect_security_mcp_exposure),
    DoctorRule("security.process_exec", "Security", "Process execution policy", _detect_security_process_exec),
)
DOCTOR_RULE_REGISTRY: Mapping[str, DoctorRule] = MappingProxyType(
    {rule.check_id: rule for rule in BUILTIN_DOCTOR_RULES}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_check_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_.-]{1,95}", str(value or "")))


def _safe_identifier(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip())[:96]
    return normalized or fallback


def _sanitize_value(value: Any, key_hint: str = "") -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_value(item, str(key).lower()) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item, key_hint) for item in value]
    if isinstance(value, Path):
        return "[local path redacted]"
    if isinstance(value, bool) or value is None:
        return value
    if any(part in key_hint for part in _SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if isinstance(value, (int, float)):
        return value
    return _sanitize_string(str(value))


def _sanitize_string(value: str) -> str:
    def redact_url(match: re.Match[str]) -> str:
        try:
            parsed = urlparse(match.group(0))
            host = parsed.hostname
            if not host:
                return "[url redacted]"
            rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
            port = f":{parsed.port}" if parsed.port is not None else ""
            return f"{parsed.scheme.lower()}://{rendered_host}{port}"
        except (TypeError, ValueError):
            return "[url redacted]"

    cleaned = _EMBEDDED_URL_RE.sub(redact_url, value)
    cleaned = _TOKEN_RE.sub("[credential redacted]", cleaned)
    cleaned = _NAMED_SECRET_RE.sub("[credential redacted]", cleaned)
    cleaned = _DIGEST_RE.sub("[digest redacted]", cleaned)
    if _WINDOWS_PATH_RE.search(cleaned) or _UNIX_PATH_RE.search(cleaned):
        return "[local detail redacted]"
    return cleaned


def sanitize_doctor_text(value: Any) -> str:
    """Return bounded Doctor text with paths, credentials, and digests removed."""

    return _sanitize_string(str(value or ""))[:1000]


def sanitize_doctor_value(value: Any) -> Any:
    return _sanitize_value(value)


def _coerce_port(value: Any, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _coerce_positive_int(value: Any) -> int | None:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _safe_process_name(value: Any) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_. -]+", "", name)[:80]


def _optional_path(value: Any) -> Path | None:
    if isinstance(value, (str, os.PathLike)) and str(value):
        return Path(value)
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse_flag and attributes & reparse_flag)
    except OSError:
        return False


def _path_or_parent_has_reparse(path: Path) -> bool:
    current = path
    for _ in range(32):
        if _is_reparse_point(current):
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


def _cloud_sync_providers(path: Path) -> set[str]:
    providers: set[str] = set()
    for part in path.parts:
        normalized = part.casefold().replace(" ", "")
        if normalized.startswith("onedrive"):
            providers.add("OneDrive")
        elif normalized == "dropbox":
            providers.add("Dropbox")
        elif normalized in {"googledrive", "googledrives"}:
            providers.add("Google Drive")
        elif normalized in {"icloud", "iclouddrive"}:
            providers.add("iCloud")
    return providers


__all__ = [
    "BUILTIN_DOCTOR_RULES",
    "DOCTOR_FIX_SCHEMA",
    "DOCTOR_MODES",
    "DOCTOR_RULE_REGISTRY",
    "PAYLOAD_INTEGRITY_SCHEMA",
    "DoctorRule",
    "DoctorService",
    "DoctorServiceError",
    "PhaseLog",
    "sanitize_doctor_text",
    "sanitize_doctor_value",
]
