from __future__ import annotations

import hashlib
import hmac
import getpass
import ipaddress
import json
import math
import os
import re
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from urllib.parse import unquote


IDENTITY_SCHEMA = "vrcforge.diagnostic-identities.v1"
IDENTITY_RETENTION = timedelta(days=5)
IDENTITY_MAX_RECORDS = 1_000

_SECRET_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "accesstoken",
    "refreshtoken",
    "apptoken",
    "artifactsig",
    "sessiontoken",
    "clientsecret",
    "oauthtoken",
    "credential",
    "credentials",
    "privatekey",
    "passwd",
}
_PROJECT_KEYS = {
    "project",
    "projectpath",
    "projectroot",
    "selectedproject",
    "selectedprojectpath",
    "unityproject",
    "unityprojectpath",
}
_AVATAR_PATH_KEYS = {"avatar", "avatarpath", "avatarroot", "selectedavatar", "selectedavatarpath"}
_AVATAR_NAME_KEYS = {"avatarname", "selectedavatarname"}
_BLUEPRINT_KEYS = {"blueprintid", "avatarblueprintid", "pipelineblueprintid"}
_USER_KEYS = {"user", "username", "windowsuser", "windowsusername"}

_AVATAR_ID_RE = re.compile(r"(?i)\bavtr_[a-z0-9_-]{6,}\b")
_WINDOWS_USER_RE = re.compile(r"(?i)(?:^|[\\/])Users[\\/]([^\\/]+)")
_QUOTED_WINDOWS_PATH_RE = re.compile(r"(?i)([\"'])((?:[a-z]:[\\/]|\\\\)[^\r\n\"']+)\1")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![a-z0-9_])(?:[a-z]:[\\/]|\\\\)[^\r\n\"'<>|,;]+")
_QUOTED_UNIX_USER_PATH_RE = re.compile(r"([\"'])(/(?:Users|home)/[^\r\n\"']+)\1")
_UNIX_USER_PATH_RE = re.compile(r"(?<![:a-zA-Z0-9_])/(?:Users|home)/[^\r\n\"'<>|,;]+")
_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])")
_PERCENT_ENCODED_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![a-z0-9%])(?:[a-z]|%[0-9a-f]{2})%3a(?:%5c|%2f)[^&\s\"'<>|,;]+"
)
_IDENTITY_QUERY_RE = re.compile(
    r"(?i)([?&])((?:projectPath|projectRoot|selectedProjectPath|blueprintId|avatarBlueprintId|avatarPath|avatarName))="
    r"([^&#\s]+)"
)
_STABLE_ALIAS_RE = re.compile(r"^(?:usr|prj|avt|path|net)_[0-9a-f]{16}$")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{4,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|app[_-]?token|artifact[_-]?sig|"
    r"authorization|password|passwd|secret|cookie|session[_-]?token)\b\s*[:=]\s*)([^\s,;]+)"
)
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|client[_-]?secret|oauth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"session[_-]?token|app[_-]?token|artifact[_-]?sig|private[_-]?key|authorization|password|passwd|"
    r"credential(?:s)?|secret|cookie|token)=)([^&#\s]+)"
)
_KNOWN_KEY_RE = re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,})\b")
_CHALLENGE_PATH_RE = re.compile(
    r"(?i)(/api/app/advanced-settings/developer-challenge/)[A-Za-z0-9_-]{24,128}"
)
_AUTHORIZATION_OR_COOKIE_RE = re.compile(
    r"(?im)((?:[\"']?)(?:proxy[-_ ]?authorization|authorization|set[-_ ]?cookie|cookie)(?:[\"']?)\s*[:=]\s*)[^\r\n]*"
)
_COMPOSITE_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?ix)("
    r"[\"']?(?:api[-_ ]?key|client[-_ ]?secret|oauth[-_ ]?token|access[-_ ]?token|refresh[-_ ]?token|"
    r"session[-_ ]?token|app[-_ ]?token|artifact[-_ ]?sig|private[-_ ]?key|authorization|password|passwd|"
    r"credential(?:s)?|secret|cookie|token)[\"']?\s*[:=]\s*"
    r")(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\r\n,;}]+)"
)
_SAFE_SECRET_METRIC_SUFFIXES = ("count", "counts", "length", "limit", "limits", "usage", "budget", "index")
_PUBLIC_EVIDENCE_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:[a-z]:[\\/]|\\\\|/(?:Users|home|root|tmp)/|/var/folders/)[^\r\n\"'<>|,;]*"
)
_PUBLIC_EVIDENCE_RELATIVE_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:\.ssh|AppData|Library[\\/]Application Support)(?:[\\/]|$)"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_secret_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    if _is_secret_metric_key(normalized):
        return False
    if normalized in _SECRET_KEYS:
        return True
    if any(
        marker in normalized
        for marker in (
            "apikey",
            "clientsecret",
            "oauthtoken",
            "accesstoken",
            "refreshtoken",
            "sessiontoken",
            "authorization",
            "password",
            "passwd",
            "credential",
            "privatekey",
            "cookie",
        )
    ):
        return True
    return normalized == "token" or normalized.startswith("tokenvalue") or normalized.endswith("token")


def _is_secret_metric_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    if not normalized.endswith(_SAFE_SECRET_METRIC_SUFFIXES):
        return False
    return any(
        marker in normalized
        for marker in ("token", "secret", "password", "passwd", "credential", "privatekey", "apikey")
    )


def _basename(value: str) -> str:
    parts = [part for part in re.split(r"[\\/]", value.rstrip("\\/")) if part]
    return parts[-1][:160] if parts else ""


def _scrub_secret_text(value: str) -> str:
    text = str(value)
    text = _CHALLENGE_PATH_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _SECRET_QUERY_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _AUTHORIZATION_OR_COOKIE_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _COMPOSITE_SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return _KNOWN_KEY_RE.sub("[REDACTED]", text)


def redact_public_evidence(value: Any, *, depth: int = 0) -> Any:
    """Deterministically scrub secrets and machine-private paths from evidence.

    Callers that require a fail-closed public contract can compare the returned
    value with the original and reject the document whenever they differ.
    This helper intentionally has no identity store or filesystem side effect.
    """

    if depth > 16:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:500]:
            key_text = str(key)
            if _is_secret_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_public_evidence(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_public_evidence(item, depth=depth + 1) for item in list(value)[:500]]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        text = _scrub_secret_text(value)
        text = re.sub(r"(?i)file://[^\s\"']+", "[REDACTED_PATH]", text)
        text = _PUBLIC_EVIDENCE_PRIVATE_PATH_RE.sub("[REDACTED_PATH]", text)
        if _PUBLIC_EVIDENCE_RELATIVE_PRIVATE_PATH_RE.search(text):
            return "[REDACTED_PATH]"
        return text
    if isinstance(value, float) and not math.isfinite(value):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[REDACTED]"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class DiagnosticPrivacy:
    """Pre-persistence redaction with stable, install-local identity aliases."""

    _PREFIX_BY_KIND = {
        "user": "usr",
        "project": "prj",
        "avatar": "avt",
        "path": "path",
        "network": "net",
    }

    def __init__(
        self,
        config_dir: Path,
        *,
        now_fn: Callable[[], datetime] = _utc_now,
        current_user_fn: Callable[[], str] = getpass.getuser,
        max_records: int = IDENTITY_MAX_RECORDS,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.key_path = self.config_dir / "diagnostic-alias.key"
        self.mapping_path = self.config_dir / "diagnostic-identities.json"
        self._now_fn = now_fn
        self._current_user_fn = current_user_fn
        self.max_records = max(25, int(max_records))
        self._lock = RLock()
        self._key: bytes | None = None
        self._records: dict[str, dict[str, Any]] | None = None
        self._dirty = False
        self.mapping_available = True

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _private_mode(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _load_key(self) -> bytes:
        if self._key is not None:
            return self._key
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            existing = self.key_path.read_bytes()
        except OSError:
            existing = b""
        if len(existing) == 32:
            self._key = existing
            self._private_mode(self.key_path)
            return existing

        generated = secrets.token_bytes(32)
        try:
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raced = self.key_path.read_bytes()
            if len(raced) != 32:
                raise RuntimeError("Diagnostic alias key is invalid.")
            self._key = raced
        else:
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(generated)
                key_file.flush()
                os.fsync(key_file.fileno())
            self._private_mode(self.key_path)
            self._key = generated
        return self._key

    def _load_records(self) -> dict[str, dict[str, Any]]:
        if self._records is not None:
            return self._records
        try:
            payload = json.loads(self.mapping_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            payload = {}
        except (OSError, json.JSONDecodeError):
            payload = {}
            self.mapping_available = False
        rows = payload.get("records") if isinstance(payload, dict) else []
        records: dict[str, dict[str, Any]] = {}
        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict):
                    continue
                alias = str(item.get("alias") or "")
                kind = str(item.get("kind") or "")
                if alias and kind in self._PREFIX_BY_KIND:
                    records[alias] = dict(item)
        self._records = records
        self._prune_records()
        return records

    def _prune_records(self) -> None:
        if self._records is None:
            return
        cutoff = self._now() - IDENTITY_RETENTION
        ranked: list[tuple[datetime, str]] = []
        for alias, record in list(self._records.items()):
            try:
                seen = datetime.fromisoformat(str(record.get("lastSeenAt") or "").replace("Z", "+00:00"))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                seen = seen.astimezone(timezone.utc)
            except ValueError:
                seen = datetime.min.replace(tzinfo=timezone.utc)
            if seen < cutoff:
                del self._records[alias]
                self._dirty = True
            else:
                ranked.append((seen, alias))
        overflow = max(0, len(ranked) - self.max_records)
        for _, alias in sorted(ranked)[:overflow]:
            self._records.pop(alias, None)
            self._dirty = True

    def _save_records(self) -> None:
        if not self._dirty or self._records is None:
            return
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": IDENTITY_SCHEMA,
            "updatedAt": _iso(self._now()),
            "records": sorted(self._records.values(), key=lambda item: str(item.get("alias") or "")),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".diagnostic-identities-",
            suffix=".tmp",
            dir=str(self.config_dir),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            self._private_mode(temporary_path)
            os.replace(temporary_path, self.mapping_path)
            self._private_mode(self.mapping_path)
            self._dirty = False
            self.mapping_available = True
        finally:
            temporary_path.unlink(missing_ok=True)

    def _alias(self, kind: str, value: str) -> str:
        canonical = str(value).strip()
        prefix = self._PREFIX_BY_KIND[kind]
        digest = hmac.new(
            self._load_key(),
            f"vrcforge-diagnostic:{kind}\0{canonical.casefold()}".encode("utf-8", errors="replace"),
            hashlib.sha256,
        ).hexdigest()[:16]
        return f"{prefix}_{digest}"

    def _register(
        self,
        kind: str,
        value: str,
        *,
        label: str = "",
        user_alias: str = "",
        project_alias: str = "",
        windows_user: str = "",
        project_name: str = "",
        avatar_name: str = "",
        alias_seed: str = "",
    ) -> str:
        raw = str(value).strip()
        if not raw:
            return ""
        records = self._load_records()
        alias = self._alias(kind, alias_seed or raw)
        now = _iso(self._now())
        record = records.get(alias, {})
        record.update(
            {
                "alias": alias,
                "kind": kind,
                "value": raw,
                "label": (label or record.get("label") or _basename(raw))[:160],
                "lastSeenAt": now,
            }
        )
        record.setdefault("firstSeenAt", now)
        for key, candidate in (
            ("userAlias", user_alias),
            ("projectAlias", project_alias),
            ("windowsUser", windows_user),
            ("projectName", project_name),
            ("avatarName", avatar_name),
        ):
            if candidate:
                record[key] = str(candidate)[:160]
        records[alias] = record
        self._dirty = True
        return alias

    @staticmethod
    def _windows_user_from_path(value: str) -> str:
        match = _WINDOWS_USER_RE.search(str(value))
        return match.group(1).strip() if match else ""

    def _discover_context(self, value: Any) -> dict[str, str]:
        context: dict[str, str] = {}

        def walk(item: Any, depth: int = 0) -> None:
            if depth > 8:
                return
            if isinstance(item, dict):
                for key, child in list(item.items())[:200]:
                    if _is_secret_key(key):
                        continue
                    normalized = _normalized_key(key)
                    if isinstance(child, (str, Path)):
                        text = _scrub_secret_text(str(child).strip())
                        if _STABLE_ALIAS_RE.fullmatch(text):
                            continue
                        if normalized in _PROJECT_KEYS and text:
                            context.setdefault("projectPath", text)
                        elif normalized in _AVATAR_PATH_KEYS and text:
                            context.setdefault("avatarPath", text)
                        elif normalized in _AVATAR_NAME_KEYS and text:
                            context.setdefault("avatarName", text)
                        elif normalized in _BLUEPRINT_KEYS and text:
                            context.setdefault("blueprintId", text)
                        elif normalized in _USER_KEYS and text:
                            context.setdefault("windowsUser", text)
                    walk(child, depth + 1)
            elif isinstance(item, (list, tuple)):
                for child in list(item)[:200]:
                    walk(child, depth + 1)

        walk(value)
        project_path = context.get("projectPath", "")
        if project_path and not context.get("windowsUser"):
            context["windowsUser"] = self._windows_user_from_path(project_path)
        return context

    def _register_context(self, context: dict[str, str]) -> dict[str, str]:
        project_path = context.get("projectPath", "")
        if _STABLE_ALIAS_RE.fullmatch(project_path):
            project_path = ""
        project_name = _basename(project_path)
        avatar_name = context.get("avatarName", "") or _basename(context.get("avatarPath", ""))
        avatar_identity = context.get("blueprintId", "") or context.get("avatarPath", "") or avatar_name
        if _STABLE_ALIAS_RE.fullmatch(avatar_identity):
            avatar_identity = ""
        windows_user = context.get("windowsUser", "")
        if _STABLE_ALIAS_RE.fullmatch(windows_user):
            windows_user = ""
        if not windows_user and (project_path or avatar_identity):
            try:
                windows_user = str(self._current_user_fn() or "").strip()
            except Exception:  # noqa: BLE001 - identity mapping must never block diagnostic redaction.
                windows_user = ""
        user_alias = self._register("user", windows_user, label=windows_user, windows_user=windows_user) if windows_user else ""
        project_alias = (
            self._register(
                "project",
                project_path,
                label=project_name,
                user_alias=user_alias,
                windows_user=windows_user,
                project_name=project_name,
                alias_seed=f"{user_alias}\0{project_path}",
            )
            if project_path
            else ""
        )
        avatar_alias = (
            self._register(
                "avatar",
                avatar_identity,
                label=avatar_name,
                user_alias=user_alias,
                project_alias=project_alias,
                windows_user=windows_user,
                project_name=project_name,
                avatar_name=avatar_name,
                alias_seed=f"{project_alias}\0{avatar_identity}",
            )
            if avatar_identity
            else ""
        )
        return {
            **context,
            "userAlias": user_alias,
            "projectAlias": project_alias,
            "avatarAlias": avatar_alias,
            "projectName": project_name,
            "avatarName": avatar_name,
        }

    def _replace_network(self, match: re.Match[str]) -> str:
        value = match.group(0)
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
        return self._register("network", value, label="network address")

    def _replace_path_value(self, value: str) -> str:
        user = self._windows_user_from_path(value)
        user_alias = self._register("user", user, label=user, windows_user=user) if user else ""
        return self._register("path", value, label="local path", user_alias=user_alias, windows_user=user)

    def _replace_path(self, match: re.Match[str]) -> str:
        value = match.group(0).rstrip(".,;:)]}")
        suffix = match.group(0)[len(value):]
        return self._replace_path_value(value) + suffix

    @staticmethod
    def _same_identity(left: str, right: str) -> bool:
        return str(left).strip().replace("/", "\\").rstrip("\\").casefold() == str(right).strip().replace(
            "/", "\\"
        ).rstrip("\\").casefold()

    def _fallback_windows_user(self) -> str:
        try:
            return str(self._current_user_fn() or "").strip()
        except Exception:  # noqa: BLE001 - OS identity lookup is optional.
            return ""

    def _register_project_value(self, value: str) -> str:
        windows_user = self._windows_user_from_path(value) or self._fallback_windows_user()
        user_alias = (
            self._register("user", windows_user, label=windows_user, windows_user=windows_user) if windows_user else ""
        )
        project_name = _basename(value)
        return self._register(
            "project",
            value,
            label=project_name,
            user_alias=user_alias,
            windows_user=windows_user,
            project_name=project_name,
            alias_seed=f"{user_alias}\0{value}",
        )

    def _register_avatar_value(self, value: str, context: dict[str, str], *, label: str = "") -> str:
        project_alias = context.get("projectAlias", "")
        return self._register(
            "avatar",
            value,
            label=label or _basename(value),
            user_alias=context.get("userAlias", ""),
            project_alias=project_alias,
            windows_user=context.get("windowsUser", ""),
            project_name=context.get("projectName", ""),
            avatar_name=label or _basename(value),
            alias_seed=f"{project_alias}\0{value}",
        )

    def _replace_identity_query(self, match: re.Match[str], context: dict[str, str]) -> str:
        prefix, key, encoded_value = match.groups()
        value = _scrub_secret_text(unquote(encoded_value))
        normalized = _normalized_key(key)
        if normalized in _PROJECT_KEYS:
            alias = self._register_project_value(value)
        elif normalized in _BLUEPRINT_KEYS:
            alias = self._register_avatar_value(value, context, label=context.get("avatarName", ""))
        elif normalized in (_AVATAR_PATH_KEYS | _AVATAR_NAME_KEYS):
            alias = self._register_avatar_value(value, context, label=_basename(value))
        else:
            alias = self._replace_path_value(value)
        return f"{prefix}{key}={alias}"

    @staticmethod
    def _replace_context_names(text: str, context: dict[str, str]) -> str:
        replacements = [
            (context.get("windowsUser", ""), context.get("userAlias", "")),
            (context.get("projectName", ""), context.get("projectAlias", "")),
            (context.get("avatarName", ""), context.get("avatarAlias", "")),
        ]
        for raw, alias in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
            if not alias or len(raw.strip()) < 3 or raw.startswith(("usr_", "prj_", "avt_")):
                continue
            text = re.sub(rf"(?<!\w){re.escape(raw)}(?!\w)", alias, text, flags=re.IGNORECASE)
        return text

    def _redact_string(self, value: str, semantic_key: str, context: dict[str, str]) -> str:
        text = _scrub_secret_text(str(value))
        if _STABLE_ALIAS_RE.fullmatch(text):
            return text
        text = _IDENTITY_QUERY_RE.sub(lambda match: self._replace_identity_query(match, context), text)

        normalized = _normalized_key(semantic_key)
        if normalized in _PROJECT_KEYS and text.strip():
            if context.get("projectAlias") and self._same_identity(text, context.get("projectPath", "")):
                return context["projectAlias"]
            return self._register_project_value(text)
        if normalized in _BLUEPRINT_KEYS and text.strip():
            if context.get("avatarAlias") and self._same_identity(text, context.get("blueprintId", "")):
                return context["avatarAlias"]
            return self._register_avatar_value(text, context, label=context.get("avatarName", ""))
        if normalized in _USER_KEYS and text.strip():
            if context.get("userAlias") and self._same_identity(text, context.get("windowsUser", "")):
                return context["userAlias"]
            return self._register("user", text, label=text, windows_user=text)
        if normalized in (_AVATAR_PATH_KEYS | _AVATAR_NAME_KEYS) and text.strip():
            expected = context.get("avatarPath", "") if normalized in _AVATAR_PATH_KEYS else context.get("avatarName", "")
            if context.get("avatarAlias") and self._same_identity(text, expected):
                return context["avatarAlias"]
            return self._register_avatar_value(text, context, label=_basename(text))

        text = self._replace_context_names(text, context)

        def replace_avatar(match: re.Match[str]) -> str:
            raw = match.group(0)
            return self._register(
                "avatar",
                raw,
                label=context.get("avatarName", ""),
                user_alias=context.get("userAlias", ""),
                project_alias=context.get("projectAlias", ""),
                windows_user=context.get("windowsUser", ""),
                project_name=context.get("projectName", ""),
                avatar_name=context.get("avatarName", ""),
                alias_seed=f"{context.get('projectAlias', '')}\0{raw}",
            )

        text = _AVATAR_ID_RE.sub(replace_avatar, text)
        text = _PERCENT_ENCODED_WINDOWS_PATH_RE.sub(
            lambda match: self._replace_path_value(unquote(match.group(0))),
            text,
        )
        text = _QUOTED_WINDOWS_PATH_RE.sub(
            lambda match: f"{match.group(1)}{self._replace_path_value(match.group(2))}{match.group(1)}",
            text,
        )
        text = _WINDOWS_PATH_RE.sub(self._replace_path, text)
        text = _QUOTED_UNIX_USER_PATH_RE.sub(
            lambda match: f"{match.group(1)}{self._replace_path_value(match.group(2))}{match.group(1)}",
            text,
        )
        text = _UNIX_USER_PATH_RE.sub(self._replace_path, text)
        text = _MAC_RE.sub(lambda match: self._register("network", match.group(0), label="network address"), text)
        text = _IPV4_RE.sub(self._replace_network, text)
        text = _IPV6_RE.sub(self._replace_network, text)
        return text

    def _redact_value(self, value: Any, semantic_key: str, context: dict[str, str], depth: int = 0) -> Any:
        if depth > 12:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            discovered = self._discover_context(value)
            if discovered:
                raw_context = {
                    key: str(context.get(key) or "")
                    for key in ("projectPath", "avatarPath", "avatarName", "blueprintId", "windowsUser")
                    if context.get(key)
                }
                context = self._register_context({**raw_context, **discovered})
            result: dict[str, Any] = {}
            for key, item in list(value.items())[:500]:
                key_text = str(key)
                if _is_secret_key(key_text):
                    result[key_text] = "[REDACTED]"
                elif _is_secret_metric_key(key_text) and not (
                    isinstance(item, (int, float))
                    and not isinstance(item, bool)
                    and (not isinstance(item, float) or math.isfinite(item))
                ):
                    result[key_text] = "[REDACTED]"
                else:
                    result[key_text] = self._redact_value(item, key_text, context, depth + 1)
            return result
        if isinstance(value, (list, tuple, set)):
            return [self._redact_value(item, semantic_key, context, depth + 1) for item in list(value)[:500]]
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            return self._redact_string(value, semantic_key, context)
        if isinstance(value, float) and not math.isfinite(value):
            if math.isnan(value):
                return "NaN"
            return "Infinity" if value > 0 else "-Infinity"
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self._redact_string(str(value), semantic_key, context)

    def redact(self, value: Any, *, context: dict[str, Any] | None = None) -> Any:
        with self._lock:
            discovered = self._discover_context(value)
            if context:
                discovered = {**self._discover_context(context), **discovered}
            registered = self._register_context(discovered)
            safe = self._redact_value(value, "", registered)
            self._prune_records()
            self._save_records()
            return safe

    def redact_text(self, value: str, *, context: dict[str, Any] | None = None) -> str:
        safe = self.redact(str(value), context=context)
        return str(safe)

    def cleanup(self) -> None:
        with self._lock:
            self._load_records()
            self._prune_records()
            self._save_records()

    def safe_identity_summaries(self) -> list[dict[str, str]]:
        with self._lock:
            records = self._load_records()
            self._prune_records()
            self._save_records()
            result: list[dict[str, str]] = []
            for record in sorted(records.values(), key=lambda item: str(item.get("lastSeenAt") or ""), reverse=True):
                kind = str(record.get("kind") or "")
                if kind not in {"user", "project", "avatar"}:
                    continue
                summary = {
                    "alias": str(record.get("alias") or ""),
                    "kind": kind,
                    "lastSeenAt": str(record.get("lastSeenAt") or ""),
                }
                if kind == "user":
                    summary["windowsUser"] = str(record.get("windowsUser") or record.get("label") or "")
                    result.append(summary)
                elif kind == "project":
                    summary.update(
                        {
                            "userAlias": str(record.get("userAlias") or ""),
                            "windowsUser": str(record.get("windowsUser") or ""),
                            "projectName": str(record.get("projectName") or record.get("label") or ""),
                        }
                    )
                    result.append(summary)
                else:
                    summary.update(
                        {
                            "userAlias": str(record.get("userAlias") or ""),
                            "projectAlias": str(record.get("projectAlias") or ""),
                            "windowsUser": str(record.get("windowsUser") or ""),
                            "projectName": str(record.get("projectName") or ""),
                            "avatarName": str(record.get("avatarName") or record.get("label") or ""),
                        }
                    )
                    result.append(summary)
            return result


__all__ = [
    "DiagnosticPrivacy",
    "IDENTITY_MAX_RECORDS",
    "IDENTITY_RETENTION",
    "IDENTITY_SCHEMA",
    "redact_public_evidence",
]
