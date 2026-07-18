from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


COMPACTION_SCHEMA = "vrcforge.context_compaction.v1"
DEFAULT_TARGET_TOKENS = 12_000
MAX_INPUT_BUDGET_TOKENS = 64_000
MAX_SUMMARY_CHARS = 6_000
MAX_PROVIDER_ATTEMPTS = 3

_STRUCTURED_FIELDS = (
    "currentGoal",
    "completed",
    "decisions",
    "constraints",
    "todo",
    "references",
    "recentContext",
)
_LIST_FIELDS = _STRUCTURED_FIELDS[1:]
_ROLE_ALIASES = {
    "assistant": "assistant",
    "agent": "assistant",
    "model": "assistant",
    "system": "system",
    "tool": "tool",
    "user": "user",
}


class ContextCompactionInputError(ValueError):
    """Raised only for invalid caller input, never for provider failures."""


@dataclass
class _RedactionReport:
    paths: int = 0
    secrets: int = 0
    avatar_blueprint_ids: int = 0

    @property
    def total(self) -> int:
        return self.paths + self.secrets + self.avatar_blueprint_ids

    def as_dict(self) -> dict[str, int]:
        return {
            "paths": self.paths,
            "secrets": self.secrets,
            "avatarBlueprintIds": self.avatar_blueprint_ids,
            "total": self.total,
        }


@dataclass
class _Redactor:
    report: _RedactionReport = field(default_factory=_RedactionReport)

    _pem_pattern = re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    )
    _bearer_pattern = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
    _assignment_pattern = re.compile(
        r"(?i)\b((?:[a-z0-9]+[_-])*(?:api[_-]?key|secret[_-]?access[_-]?key|access[_-]?token|"
        r"auth[_-]?token|refresh[_-]?token|token|authorization|client[_-]?secret|password|passwd|"
        r"secret|private[_-]?key))"
        r"([\"']?)\s*([:=])\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)"
    )
    _known_token_pattern = re.compile(
        r"\b(?:(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|hf_|xox[baprs]-)[A-Za-z0-9_-]{4,}|"
        r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,})",
        re.IGNORECASE,
    )
    _jwt_pattern = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
    _avatar_pattern = re.compile(r"\bavtr_[A-Za-z0-9_-]{3,}\b", re.IGNORECASE)
    _quoted_path_pattern = re.compile(
        r"(?P<quote>[\"'])(?P<path>(?:[A-Za-z]:[\\/]|/(?:Users|home|root|mnt|opt|var|tmp)/)[^\r\n\"']+)(?P=quote)",
        re.IGNORECASE,
    )
    _delimited_path_pattern = re.compile(
        r"(?<![A-Za-z0-9_:])(?P<path>(?:"
        r"[A-Za-z]:[\\/]|\\\\|"
        r"/(?:Users|home|root|mnt|opt|var|tmp|workspace|srv|data|etc|usr|run|app|code)/"
        r")[^:*?\"<>|\r\n]+?)"
        r"(?=\s+(?:(?:[A-Za-z]:[\\/]|/(?:Users|home|root|mnt|opt|var|tmp|workspace|srv|data|etc|usr|run|app|code)/)"
        r"|\[REDACTED_SECRET\]|\{\{avatar:|(?:and|with|then|from|to|for|using|plus|but)\b)"
        r"|\s*[|,;()，。；（）、\r\n]|$)",
        re.IGNORECASE,
    )
    _spaced_home_pattern = re.compile(
        r"(?<![A-Za-z0-9_])(?:"
        r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\r\n]*\s+[^\\/\r\n]*(?=[\\/])|"
        r"/(?:Users|home)/[^/\r\n]*\s+[^/\r\n]*(?=/)"
        r")",
        re.IGNORECASE,
    )
    _windows_path_pattern = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|,;()\[\]]+")
    _posix_path_pattern = re.compile(
        r"(?<![A-Za-z0-9_:])/(?:Users|home|root|mnt|opt|var|tmp|workspace|srv|data|etc|usr|run|app|code)/"
        r"[^\s\"'<>|,;()\[\]]+",
        re.IGNORECASE,
    )

    @staticmethod
    def _stable_hash(kind: str, value: str) -> str:
        normalized = value.replace("\\", "/").rstrip("/")
        if re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("//"):
            normalized = normalized.casefold()
        return hashlib.sha256(f"vrcforge:{kind}:{normalized}".encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _is_project_path(value: str) -> bool:
        normalized = value.replace("\\", "/").casefold()
        return any(
            marker in normalized
            for marker in (
                "/assets/",
                "/packages/",
                "/projectsettings/",
                "/library/",
                ".unity",
            )
        )

    @staticmethod
    def _project_root(value: str) -> str:
        normalized = value.replace("\\", "/").rstrip("/")
        folded = normalized.casefold()
        marker_indexes = [
            folded.find(marker)
            for marker in ("/assets/", "/packages/", "/projectsettings/", "/library/")
            if folded.find(marker) >= 0
        ]
        if marker_indexes:
            return normalized[: min(marker_indexes)]
        if folded.endswith(".unity") and "/" in normalized:
            return normalized.rsplit("/", 1)[0]
        return normalized

    def _replace_path(self, match: re.Match[str]) -> str:
        value = match.groupdict().get("path") or match.group(0)
        kind = "project" if self._is_project_path(value) else "path"
        hash_basis = self._project_root(value) if kind == "project" else value
        self.report.paths += 1
        return "{{" + kind + ":" + self._stable_hash(kind, hash_basis) + "}}"

    def _replace_secret(self, match: re.Match[str]) -> str:
        self.report.secrets += 1
        return "[REDACTED_SECRET]"

    def _replace_assignment(self, match: re.Match[str]) -> str:
        self.report.secrets += 1
        return f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED_SECRET]"

    def _replace_avatar(self, match: re.Match[str]) -> str:
        self.report.avatar_blueprint_ids += 1
        digest = self._stable_hash("avatar", match.group(0))
        return "{{avatar:" + digest + "}}"

    def redact(self, value: str) -> str:
        text = str(value or "")
        text = self._pem_pattern.sub(self._replace_secret, text)
        text = self._bearer_pattern.sub(self._replace_secret, text)
        text = self._assignment_pattern.sub(self._replace_assignment, text)
        text = self._known_token_pattern.sub(self._replace_secret, text)
        text = self._jwt_pattern.sub(self._replace_secret, text)
        text = self._avatar_pattern.sub(self._replace_avatar, text)
        text = self._quoted_path_pattern.sub(self._replace_path, text)
        text = self._delimited_path_pattern.sub(self._replace_path, text)
        text = self._spaced_home_pattern.sub(self._replace_path, text)
        text = self._windows_path_pattern.sub(self._replace_path, text)
        text = self._posix_path_pattern.sub(self._replace_path, text)
        return text


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_entries(
    history: Sequence[Mapping[str, Any]],
    redactor: _Redactor,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        raw_text = item.get("text")
        if raw_text is None:
            raw_text = item.get("content")
        text = str(raw_text or "").strip()
        if not text:
            continue
        raw_role = str(item.get("role") or "user").strip().lower()
        role = _ROLE_ALIASES.get(raw_role, "assistant")
        entries.append({"role": role, "text": redactor.redact(text)})
    return entries


def _estimate_entry_tokens(entry: Mapping[str, str]) -> int:
    text = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    units = 0.0
    for character in text:
        codepoint = ord(character)
        if (
            0x2E80 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x20000 <= codepoint <= 0x2FA1F
            or 0x3040 <= codepoint <= 0x30FF
            or 0x1100 <= codepoint <= 0x11FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            units += 1.0
        else:
            units += len(character.encode("utf-8")) / 4.0
    return max(1, int(units + 0.999))


def _effective_budget(target_tokens: int | None, real_context_limit: int | None) -> int:
    requested = int(target_tokens or DEFAULT_TARGET_TOKENS)
    requested = max(64, min(requested, MAX_INPUT_BUDGET_TOKENS))
    if real_context_limit:
        context_bound = max(64, int(real_context_limit) // 2)
        requested = min(requested, context_bound)
    return requested


def _fit_entries(
    entries: Sequence[dict[str, str]],
    budget: int,
) -> tuple[list[dict[str, str]], str, int]:
    costs = [_estimate_entry_tokens(entry) for entry in entries]
    total = sum(costs)
    if total <= budget:
        return list(entries), "full", total

    first_user = next((index for index, entry in enumerate(entries) if entry["role"] == "user"), 0)
    blocks: list[list[int]] = []
    current: list[int] = []
    for index, entry in enumerate(entries):
        if entry["role"] == "user" and current:
            blocks.append(current)
            current = []
        current.append(index)
    if current:
        blocks.append(current)

    # Continuity wins over an unrealistically small caller target: never split
    # or truncate the original goal or the latest conversational block.
    selected_indexes = {first_user, *blocks[-1]}
    used = sum(costs[index] for index in selected_indexes)
    effective_budget = max(budget, used)

    for block in reversed(blocks):
        if first_user in block or all(index in selected_indexes for index in block):
            continue
        block_cost = sum(costs[index] for index in block)
        if used + block_cost > effective_budget:
            break
        selected_indexes.update(block)
        used += block_cost

    fitted = [entry for index, entry in enumerate(entries) if index in selected_indexes]
    return fitted, "fitted", used


def _safe_metadata(value: str, limit: int = 128) -> str:
    candidate = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit]
    if not candidate:
        return ""
    scanner = _Redactor()
    sanitized = scanner.redact(candidate)
    return sanitized if scanner.report.total == 0 else ""


def _language_family(language: str) -> str:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if normalized.startswith("zh"):
        return "zh"
    if normalized.startswith("ja"):
        return "ja"
    return "en"


def _safe_language(language: str) -> str:
    candidate = str(language or "").strip()[:32]
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]+", candidate) else ""


def _build_prompt(
    entries: Sequence[dict[str, str]],
    *,
    language: str,
    trigger: str,
    phase: str,
    target_tokens: int,
) -> str:
    contract = {
        "currentGoal": "string",
        "completed": ["string"],
        "decisions": ["string"],
        "constraints": ["string"],
        "todo": ["string"],
        "references": ["string"],
        "recentContext": ["string"],
    }
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return (
        "Create a continuity-preserving context summary from the REDACTED entries below. "
        "Do not reconstruct, guess, or emit secrets, raw absolute paths, home directory names, "
        "private keys, tokens, or Avatar Blueprint IDs. Preserve concrete decisions and pending work. "
        f"Write in language '{language or 'auto'}'. Trigger={trigger}; phase={phase}. "
        f"Keep the result comfortably below {max(256, target_tokens)} tokens. "
        "Return exactly one JSON object matching this schema (all keys required): "
        f"{json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}. "
        "Each list item must be a string. Do not use Markdown fences.\n"
        f"REDACTED_ENTRIES={payload}"
    )


def _extract_provider_payload(raw: Any) -> tuple[str, Any]:
    if isinstance(raw, Mapping):
        return "json", dict(raw)
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty_response")
    candidate = text
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                return "json", json.loads(candidate[start : end + 1])
            except json.JSONDecodeError as exc:
                if candidate.startswith("{"):
                    raise ValueError("schema_error") from exc
                return "summary", candidate
        if candidate.startswith(("{", "[")) or candidate.endswith(("}", "]")):
            raise ValueError("schema_error")
        return "summary", candidate
    if isinstance(parsed, Mapping):
        return "json", dict(parsed)
    if isinstance(parsed, str) and parsed.strip():
        return "summary", parsed.strip()
    raise ValueError("schema_error")


def _validate_string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if text:
            result.append(text)
    return result


def _render_structured(payload: Mapping[str, Any], language: str) -> str:
    if set(payload).issubset({"summary", "schema"}) and isinstance(payload.get("summary"), str):
        summary = str(payload["summary"]).strip()
        if summary:
            return summary
        raise ValueError("empty_response")

    if not all(field in payload for field in _STRUCTURED_FIELDS):
        raise ValueError("schema_error")
    current_goal = payload.get("currentGoal")
    if not isinstance(current_goal, str):
        raise ValueError("schema_error")
    lists: dict[str, list[str]] = {}
    for field_name in _LIST_FIELDS:
        normalized = _validate_string_list(payload.get(field_name))
        if normalized is None:
            raise ValueError("schema_error")
        lists[field_name] = normalized

    labels = {
        "en": {
            "currentGoal": "Current goal",
            "completed": "Completed",
            "decisions": "Decisions",
            "constraints": "Constraints",
            "todo": "Todo",
            "references": "References",
            "recentContext": "Recent context",
        },
        "zh": {
            "currentGoal": "当前目标",
            "completed": "已完成",
            "decisions": "关键决定",
            "constraints": "约束",
            "todo": "待办",
            "references": "引用",
            "recentContext": "近期上下文",
        },
        "ja": {
            "currentGoal": "現在の目標",
            "completed": "完了",
            "decisions": "決定事項",
            "constraints": "制約",
            "todo": "TODO",
            "references": "参照",
            "recentContext": "直近の文脈",
        },
    }[_language_family(language)]

    sections: list[str] = []
    goal = current_goal.strip()
    if goal:
        sections.append(f"{labels['currentGoal']}:\n- {goal}")
    for field_name in _LIST_FIELDS:
        values = lists[field_name]
        if values:
            sections.append(f"{labels[field_name]}:\n" + "\n".join(f"- {item}" for item in values))
    if not sections:
        raise ValueError("empty_response")
    return "\n\n".join(sections)


def _bound_summary(summary: str, max_chars: int) -> str:
    text = str(summary or "").strip()
    if len(text) <= max_chars:
        return text
    marker = "\n[summary bounded]"
    return text[: max(1, max_chars - len(marker))].rstrip() + marker


def _provider_error_kind(exc: Exception) -> tuple[str, bool]:
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "status", None)
        or getattr(exc, "code", None)
    )
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    message = str(exc).casefold()
    message_status_match = re.search(r"\b(400|401|402|403|404|408|413|422|429|5\d\d)\b", message)
    if status_code is None and message_status_match:
        status_code = int(message_status_match.group(1))
    if status_code in {400, 401, 402, 403, 404, 413, 422}:
        if status_code in {401, 403}:
            return "provider_auth", False
        if status_code == 402:
            return "provider_credit", False
        if status_code in {413, 422}:
            return "provider_size", False
        return "provider_request", False
    if any(term in message for term in ("api key", "unauthorized", "forbidden", "authentication")):
        return "provider_auth", False
    if any(term in message for term in ("credit", "billing", "quota")):
        return "provider_credit", False
    if any(term in message for term in ("context length", "too large", "payload size", "prompt is too")):
        return "provider_size", False
    if status_code == 408 or status_code == 429 or (status_code is not None and status_code >= 500):
        return "provider_transient", True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "provider_transient", True
    if any(term in message for term in ("timeout", "timed out", "temporary", "temporarily", "rate limit", "unavailable", "connection reset")):
        return "provider_transient", True
    return "provider_error", False


def _fallback_summary(
    entries: Sequence[dict[str, str]],
    retained: Sequence[dict[str, str]],
    reason: str,
    language: str,
    max_chars: int,
) -> str:
    family = _language_family(language)
    labels = {
        "en": ("Deterministic fallback", "Current goal", "Recent context", "entry omitted"),
        "zh": ("确定性回退摘要", "当前目标", "近期上下文", "条目已省略"),
        "ja": ("決定論的フォールバック", "現在の目標", "直近の文脈", "項目を省略"),
    }[family]
    goal = next((entry for entry in entries if entry["role"] == "user"), entries[0])

    def whole_or_digest(entry: Mapping[str, str], available: int) -> str:
        line = f"{entry['role']}: {entry['text']}"
        if len(line) <= max(0, available):
            return line
        digest = _canonical_digest(entry)[:12]
        return f"[{labels[3]}: {digest}]"

    parts = [f"[{labels[0]}: {reason}]", f"{labels[1]}:"]
    parts.append("- " + whole_or_digest(goal, max_chars - len("\n".join(parts)) - 4))
    recent = [entry for entry in retained if entry is not goal][-6:]
    if recent:
        parts.append(f"{labels[2]}:")
        recent_lines: list[str] = []
        for entry in reversed(recent):
            current = "\n".join(parts + list(reversed(recent_lines)))
            available = max_chars - len(current) - 4
            candidate = "- " + whole_or_digest(entry, available)
            if len("\n".join(parts + list(reversed(recent_lines)) + [candidate])) > max_chars:
                break
            recent_lines.append(candidate)
        parts.extend(reversed(recent_lines))
    return _bound_summary("\n".join(parts), max_chars)


def compact_context(
    history: Sequence[Mapping[str, Any]],
    *,
    summarizer: Callable[[str], Any] | None = None,
    source_digest: str = "",
    trigger: str = "manual",
    phase: str = "pre_turn",
    language: str = "",
    provider: str = "",
    model: str = "",
    target_tokens: int | None = None,
    real_context_limit: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Compact chat history without ever sending unredacted content to a provider."""

    if trigger not in {"manual", "auto"}:
        raise ContextCompactionInputError("trigger must be manual or auto")
    if phase not in {"standalone", "pre_turn", "mid_turn"}:
        raise ContextCompactionInputError("phase must be standalone, pre_turn, or mid_turn")

    redactor = _Redactor()
    entries = _normalize_entries(history, redactor)
    if not entries:
        raise ContextCompactionInputError("history is empty; nothing to compact.")

    computed_source_digest = _canonical_digest(entries)
    client_digest = str(source_digest or "").strip().casefold()
    client_digest_matched: bool | None = None
    if re.fullmatch(r"[0-9a-f]{64}", client_digest):
        client_digest_matched = client_digest == computed_source_digest.casefold()

    normalized_real_context_limit = (
        int(real_context_limit) if real_context_limit and int(real_context_limit) > 0 else None
    )
    normalized_language = _safe_language(language)
    budget = _effective_budget(target_tokens, normalized_real_context_limit)
    retained, input_fidelity, estimated_tokens = _fit_entries(entries, budget)
    prompt = _build_prompt(
        retained,
        language=normalized_language,
        trigger=trigger,
        phase=phase,
        target_tokens=budget,
    )
    summary_max_chars = max(1_000, min(MAX_SUMMARY_CHARS, budget * 4))
    attempts = 0
    fallback_reason = "provider_unavailable"
    summary = ""
    fidelity = "fallback"
    input_over_budget = estimated_tokens > budget
    if input_over_budget:
        fallback_reason = "input_oversize"

    if summarizer is not None and not input_over_budget:
        while attempts < MAX_PROVIDER_ATTEMPTS:
            attempts += 1
            try:
                raw = summarizer(prompt)
            except Exception as exc:  # noqa: BLE001 - provider adapters have heterogeneous errors.
                fallback_reason, retryable = _provider_error_kind(exc)
                if not retryable or attempts >= MAX_PROVIDER_ATTEMPTS:
                    break
                sleep(0.05 * (2 ** (attempts - 1)))
                continue
            try:
                payload_kind, payload = _extract_provider_payload(raw)
                if payload_kind == "summary":
                    candidate = str(payload).strip()
                elif isinstance(payload, Mapping):
                    candidate = _render_structured(payload, normalized_language)
                else:
                    raise ValueError("schema_error")
                if not candidate:
                    raise ValueError("empty_response")
                output_scanner = _Redactor()
                output_scanner.redact(candidate)
                if output_scanner.report.total:
                    fallback_reason = "sensitive_provider_output"
                    break
                summary = _bound_summary(candidate, summary_max_chars)
                post_bound_scanner = _Redactor()
                post_bound_scanner.redact(summary)
                if post_bound_scanner.report.total:
                    summary = ""
                    fallback_reason = "sensitive_provider_output"
                    break
                fidelity = input_fidelity
                fallback_reason = ""
                break
            except ValueError as exc:
                fallback_reason = str(exc) if str(exc) in {"empty_response", "schema_error"} else "schema_error"
                break
            except Exception:  # noqa: BLE001 - malformed provider objects must fail closed.
                fallback_reason = "schema_error"
                break

    if not summary:
        summary = _fallback_summary(
            entries,
            retained,
            fallback_reason,
            normalized_language,
            summary_max_chars,
        )

    result: dict[str, Any] = {
        "ok": True,
        "schema": COMPACTION_SCHEMA,
        "summary": summary,
        "entryCount": len(entries),
        "retainedEntryCount": len(retained),
        "sourceDigest": computed_source_digest,
        "summaryDigest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        "clientDigestMatched": client_digest_matched,
        "fidelity": fidelity,
        "redactions": redactor.report.as_dict(),
        "provider": _safe_metadata(provider),
        "model": _safe_metadata(model),
        "trigger": trigger,
        "phase": phase,
        "language": normalized_language,
        "targetTokens": budget,
        "realContextLimit": normalized_real_context_limit,
        "estimatedInputTokens": estimated_tokens,
        "providerAttempts": attempts,
    }
    if fallback_reason:
        result["fallbackReason"] = fallback_reason
    return result
