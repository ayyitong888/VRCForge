"""Exact-scope source admission for VRCForge Memory Review.

The source adapter is intentionally narrow.  It accepts only documented,
first-party semantic projections and never traverses arbitrary payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from context_compaction import redact_context_text


SOURCE_PROJECTION_SCHEMA = "vrcforge.memory_review_source.v1"
MAX_SOURCE_TEXT_CHARS = 2_000
MAX_SOURCE_ID_CHARS = 160
MAX_SOURCE_REVISION_CHARS = 120

_ALLOWED_SIGNAL_KINDS = frozenset({"preference", "fact", "correction", "decision"})
_ALWAYS_EXCLUDED_TYPES = frozenset(
    {
        "approval",
        "attachment",
        "background_goal",
        "capture",
        "compaction",
        "consolidator_output",
        "context_compaction",
        "cron",
        "diagnostic",
        "goal_delivery",
        "memory",
        "memory_candidate",
        "provider_payload",
        "provider_result",
        "question",
        "reminder",
        "schedule",
        "support",
        "system",
        "tool",
        "tool_result",
    }
)
_UNSAFE_SOURCE_STATUSES = frozenset(
    {
        "blocked",
        "cancelled",
        "denied",
        "dismissed",
        "failed",
        "incomplete",
        "pending",
        "rejected",
        "timed_out",
    }
)
_SECRET_QUERY_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|access[_-]?token|auth|authorization|credential|password|passwd|secret|token)(?:$|[_-])"
)
_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_WHITESPACE_PATTERN = re.compile(r"[\t\f\v ]+")


class ScopeResolutionError(ValueError):
    """Raised when a requested Memory scope is missing, ambiguous, or unauthorized."""


class SourceAdmissionError(ValueError):
    """Raised when an otherwise eligible source violates its scope contract."""


@dataclass(frozen=True)
class MemoryScope:
    kind: str
    scope_key: str
    project_root: str = ""

    def as_storage_dict(self) -> dict[str, str]:
        payload = {"kind": self.kind, "scopeKey": self.scope_key}
        if self.kind == "project":
            payload["projectRootDigest"] = self.scope_key.removeprefix("project:")
        return payload


@dataclass(frozen=True)
class SourceProjection:
    source_type: str
    source_id: str
    source_revision: str
    source_digest: str
    kind: str
    text: str
    scope: MemoryScope
    observed_at: str = ""
    origin_group: str = ""

    def reference(self) -> dict[str, str]:
        return {
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "sourceRevision": self.source_revision,
            "sourceDigest": self.source_digest,
        }

    def as_provider_dict(self) -> dict[str, str]:
        payload = {
            "schema": SOURCE_PROJECTION_SCHEMA,
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "sourceRevision": self.source_revision,
            "sourceDigest": self.source_digest,
            "kind": self.kind,
            "text": self.text,
        }
        if self.observed_at:
            payload["observedAt"] = self.observed_at
        return payload


def _canonical_json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_project_root(value: str, *, require_existing: bool = True) -> str:
    raw = str(value or "").strip().strip('"')
    if not raw:
        raise ScopeResolutionError("Project Memory Review requires one project root.")
    try:
        resolved = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScopeResolutionError("Project root could not be normalized.") from exc
    if require_existing and (not resolved.exists() or not resolved.is_dir()):
        raise ScopeResolutionError("Project root does not exist or is not a directory.")
    normalized = os.path.normcase(os.path.normpath(str(resolved))).replace("\\", "/").rstrip("/")
    if not normalized:
        raise ScopeResolutionError("Project root could not be normalized.")
    return normalized.casefold()


def project_scope_key(project_root: str, *, require_existing: bool = True) -> str:
    normalized = normalize_project_root(project_root, require_existing=require_existing)
    return "project:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_memory_scope(
    scope: str,
    project_root: str = "",
    *,
    authorized_project_roots: Iterable[str] | None = None,
    require_existing: bool = True,
) -> MemoryScope:
    normalized_scope = str(scope or "").strip().casefold().replace("-", "_")
    if normalized_scope == "user":
        if str(project_root or "").strip():
            raise ScopeResolutionError("Project material cannot be resolved as user Memory.")
        return MemoryScope(kind="user", scope_key="user")
    if normalized_scope != "project":
        raise ScopeResolutionError("Memory Review scope must be user or project.")

    normalized_root = normalize_project_root(project_root, require_existing=require_existing)
    if authorized_project_roots is not None:
        authorized: set[str] = set()
        for candidate in authorized_project_roots:
            try:
                authorized.add(normalize_project_root(candidate, require_existing=require_existing))
            except ScopeResolutionError:
                continue
        if normalized_root not in authorized:
            raise ScopeResolutionError("Project root is not in the authorized scope set.")
    return MemoryScope(
        kind="project",
        scope_key="project:" + hashlib.sha256(normalized_root.encode("utf-8")).hexdigest(),
        project_root=normalized_root,
    )


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,;:!?)]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if not hostname:
            return "[REDACTED_URL]" + trailing
        port = parsed.port
    except (TypeError, ValueError):
        return "[REDACTED_URL]" + trailing
    host = hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    # Only the scheme and host are useful diagnostic context. User info,
    # path, query, and fragment may all carry private material.
    return f"{parsed.scheme.casefold()}://{host}/[REDACTED_URL]" + trailing


def redact_memory_text(value: Any, *, limit: int = MAX_SOURCE_TEXT_CHARS) -> tuple[str, dict[str, int]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    raw = str(value or "")
    url_count = len(_URL_PATTERN.findall(raw))
    text = _URL_PATTERN.sub(_redact_url, raw)
    text, report = redact_context_text(text)
    text = text.replace("\x00", "")
    text = "\n".join(_WHITESPACE_PATTERN.sub(" ", line).strip() for line in text.splitlines())
    text = "\n".join(line for line in text.splitlines() if line).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    report["urls"] = url_count
    report["total"] = int(report.get("total", 0)) + url_count
    return text, report


def _bounded_identifier(value: Any, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(character in text for character in "\r\n\x00"):
        raise SourceAdmissionError(f"{field} is missing or invalid.")
    redacted, report = redact_memory_text(text, limit=limit)
    if int(report.get("total", 0)) or redacted != text:
        raise SourceAdmissionError(f"{field} failed the privacy boundary.")
    return text


def _bounded_observed_at(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 80 or any(character in text for character in "\r\n\x00"):
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return text


def _source_project_matches(source: Mapping[str, Any], scope: MemoryScope) -> bool:
    raw_project = str(
        source.get("projectRoot")
        or source.get("project_root")
        or source.get("projectPath")
        or ""
    ).strip()
    if scope.kind == "user":
        return not raw_project
    if not raw_project:
        return False
    try:
        return project_scope_key(raw_project) == scope.scope_key
    except ScopeResolutionError:
        return False


def _build_projection(
    source: Mapping[str, Any],
    *,
    scope: MemoryScope,
    source_type: str,
    text_field: str,
    kind: str,
) -> SourceProjection:
    if not _source_project_matches(source, scope):
        raise SourceAdmissionError("Source project does not match the resolved Memory scope.")
    source_id = _bounded_identifier(source.get("sourceId") or source.get("id"), field="sourceId", limit=MAX_SOURCE_ID_CHARS)
    source_revision = _bounded_identifier(
        source.get("sourceRevision") or source.get("revision"),
        field="sourceRevision",
        limit=MAX_SOURCE_REVISION_CHARS,
    )
    redacted_text, _report = redact_memory_text(source.get(text_field))
    if not redacted_text:
        raise SourceAdmissionError("Eligible source text is empty after redaction.")
    source_digest = _canonical_json_digest(
        {
            "scopeKey": scope.scope_key,
            "sourceType": source_type,
            "sourceId": source_id,
            "sourceRevision": source_revision,
            "kind": kind,
            "text": redacted_text,
        }
    )
    supplied_digest = str(source.get("sourceDigest") or "").strip().casefold()
    if source_type == "validated_project_result":
        if not re.fullmatch(r"[0-9a-f]{64}", supplied_digest):
            raise SourceAdmissionError("Validated project evidence requires a stable sourceDigest.")
        source_digest = _canonical_json_digest({"projectionDigest": source_digest, "sourceDigest": supplied_digest})
    return SourceProjection(
        source_type=source_type,
        source_id=source_id,
        source_revision=source_revision,
        source_digest=source_digest,
        kind=kind,
        text=redacted_text,
        scope=scope,
        observed_at=_bounded_observed_at(source.get("observedAt") or source.get("completedAt")),
        origin_group=(
            _bounded_identifier(source.get("originGroup"), field="originGroup", limit=160)
            if str(source.get("originGroup") or "").strip()
            else ""
        ),
    )


def admit_memory_source(source: Mapping[str, Any], *, scope: MemoryScope) -> SourceProjection | None:
    if not isinstance(source, Mapping):
        return None
    source_type = str(source.get("sourceType") or source.get("type") or "").strip().casefold().replace("-", "_")
    if source_type in _ALWAYS_EXCLUDED_TYPES or not source_type:
        return None
    status = str(source.get("status") or "").strip().casefold().replace("-", "_")
    if status in _UNSAFE_SOURCE_STATUSES:
        return None

    if source_type == "user_chat":
        if str(source.get("role") or "").strip().casefold() != "user" or status != "completed":
            return None
        kind = str(source.get("signalKind") or "").strip().casefold().replace("-", "_")
        if kind not in _ALLOWED_SIGNAL_KINDS:
            return None
        if scope.kind == "user":
            requested_scope = str(source.get("memoryScope") or source.get("scope") or "").strip().casefold()
            if requested_scope != "user":
                return None
        return _build_projection(source, scope=scope, source_type=source_type, text_field="text", kind=kind)

    if source_type == "adopted_task":
        if scope.kind != "project" or status != "completed":
            return None
        decision = str(source.get("mergeDecision") or source.get("decision") or "").strip().casefold()
        if decision != "adopted" or not str(source.get("parentChatId") or "").strip():
            return None
        return _build_projection(source, scope=scope, source_type=source_type, text_field="summary", kind="decision")

    if source_type == "validated_project_result":
        if scope.kind != "project" or source.get("applied") is not True or source.get("validated") is not True:
            return None
        return _build_projection(source, scope=scope, source_type=source_type, text_field="summary", kind="fact")

    # Unknown source types are not forward-compatible by default. New source
    # adapters require a deliberate allowlist change and regression coverage.
    return None


def admit_memory_sources(
    sources: Iterable[Mapping[str, Any]],
    *,
    scope: MemoryScope,
) -> tuple[list[SourceProjection], dict[str, int]]:
    admitted: list[SourceProjection] = []
    excluded = 0
    invalid = 0
    for source in sources:
        try:
            projection = admit_memory_source(source, scope=scope)
        except SourceAdmissionError:
            invalid += 1
            continue
        if projection is None:
            excluded += 1
        else:
            admitted.append(projection)
    return admitted, {"admitted": len(admitted), "excluded": excluded, "invalid": invalid}


__all__ = [
    "MAX_SOURCE_TEXT_CHARS",
    "MemoryScope",
    "SOURCE_PROJECTION_SCHEMA",
    "ScopeResolutionError",
    "SourceAdmissionError",
    "SourceProjection",
    "admit_memory_source",
    "admit_memory_sources",
    "normalize_project_root",
    "project_scope_key",
    "redact_memory_text",
    "resolve_memory_scope",
]
