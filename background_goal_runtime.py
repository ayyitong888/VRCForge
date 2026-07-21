"""Bounded runtime policy primitives for VRCForge background goal runs.

This module intentionally contains no scheduler, provider client, persistence,
or UI code.  It supplies the small deterministic decisions that those layers
share so capacity and retry behavior remain testable in isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit


TOTAL_CONCURRENCY_LIMIT = 5
BACKGROUND_CONCURRENCY_LIMIT = 2
PROVIDER_PREFLIGHT_CACHE_SECONDS = 300
TOTAL_PROVIDER_ATTEMPTS = 3
PROVIDER_RETRY_BACKOFF_SECONDS = (60, 120, 300)
QUESTION_REMINDER_SECONDS = 1_800
MAX_WAKE_STAGGER_SECONDS = 45
REPEATED_FAILURE_THRESHOLD = 3

# Defaults are deliberately phase-specific.  Integration may wrap a shorter
# operation inside these ceilings, but must not collapse them into one generic
# timeout because the persisted failure label needs to identify the stalled
# phase.
PHASE_TIMEOUT_SECONDS: Mapping[str, int] = MappingProxyType(
    {
        "wake": 30,
        "project_lock": 120,
        "provider_call": 300,
        "apply": 900,
        "deliver": 60,
    }
)

MAX_USAGE_RECORDS = 512
MAX_USAGE_TOKENS = 1_000_000_000_000

_LANES = frozenset({"background", "interactive"})
_LOCAL_PROVIDER_IDS = frozenset({"local"})
_DEFAULT_LOCAL_PROVIDER_BASE_URL = "http://127.0.0.1:11434"


def classify_runtime_step_failure(payload: Any) -> str:
    """Return one bounded failure class for a runtime tool result."""

    if not isinstance(payload, dict):
        return "tool_error"
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    statuses = {
        str(payload.get("status") or "").strip().casefold().replace("-", "_"),
        str(result.get("status") or "").strip().casefold().replace("-", "_"),
    }
    bad_statuses = {
        "blocked",
        "cancelled",
        "denied",
        "error",
        "failed",
        "permission_denied",
        "rejected",
        "timed_out",
        "timeout",
        "unavailable",
    }
    failed = (
        payload.get("ok") is False
        or result.get("ok") is False
        or bool(statuses & bad_statuses)
        or result.get("timedOut") is True
    )
    if not failed:
        return ""
    combined = " ".join(
        str(value or "").strip().casefold()
        for value in (
            payload.get("status"),
            payload.get("failureClass"),
            payload.get("failure_class"),
            payload.get("reason"),
            payload.get("error"),
            payload.get("message"),
            result.get("status"),
            result.get("code"),
            result.get("failureClass"),
            result.get("failure_class"),
            result.get("reason"),
            result.get("error"),
            result.get("message"),
        )
    )
    if result.get("timedOut") is True or any(
        marker in combined for marker in ("timeout", "timed out", "deadline")
    ):
        return "timeout"
    if statuses & {"denied", "rejected", "permission_denied"}:
        return "permission_denied"
    if any(
        marker in combined
        for marker in (
            "permission",
            "forbidden",
            "not allowed",
            "disallowed",
            "approval denied",
        )
    ):
        return "permission_denied"
    if "unavailable" in statuses or any(
        marker in combined for marker in ("not found", "unavailable", "not registered")
    ):
        return "unavailable"
    if "cancelled" in statuses or "cancel" in combined:
        return "cancelled"
    return "tool_error"


def classify_runtime_plan_outcome(plan: Any) -> tuple[str, str]:
    """Classify explicit plan terminal states without inferring from prose."""

    if not isinstance(plan, dict):
        return "", ""
    next_step = str(plan.get("nextStep") or "").strip().casefold().replace("-", "_")
    if next_step == "cancelled":
        return "cancelled", "cancelled"
    if next_step == "loop_suppressed":
        return "failed", "loop_suppressed"
    if next_step == "context_compaction_required":
        return "parked", "context_compaction_required"
    if next_step == "await_user_instruction":
        return "parked", "await_user_instruction"
    if next_step == "paused":
        return "parked", "step_limit_reached" if plan.get("stepLimitReached") else "paused"
    if next_step == "done":
        return "completed", "done"
    return "", ""


def retry_backoff_seconds(attempt: int) -> int:
    """Return the bounded delay after a one-based failed attempt.

    The third value remains the ceiling for callers recording or displaying a
    later attempt, while ``TOTAL_PROVIDER_ATTEMPTS`` remains the authoritative
    total-attempt policy.
    """

    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return PROVIDER_RETRY_BACKOFF_SECONDS[min(attempt, len(PROVIDER_RETRY_BACKOFF_SECONDS)) - 1]


def deterministic_wake_stagger_seconds(
    goal_id: str,
    scheduled_for: datetime | date | int | float | str,
    max_seconds: int = MAX_WAKE_STAGGER_SECONDS,
) -> int:
    """Return a stable inclusive ``0..max_seconds`` wake offset.

    Both the goal identifier and scheduled occurrence participate in the hash,
    so a recurring goal does not receive one permanent offset for every run.
    """

    normalized_goal_id = str(goal_id or "").strip()
    if not normalized_goal_id:
        raise ValueError("goal_id must not be empty")
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, int):
        raise ValueError("max_seconds must be an integer")
    if max_seconds < 0 or max_seconds > MAX_WAKE_STAGGER_SECONDS:
        raise ValueError(f"max_seconds must be between 0 and {MAX_WAKE_STAGGER_SECONDS}")

    normalized_schedule = _normalize_scheduled_for(scheduled_for)
    digest = hashlib.sha256(
        b"vrcforge.background.wake.v1\0"
        + normalized_goal_id.encode("utf-8")
        + b"\0"
        + normalized_schedule.encode("utf-8")
    ).digest()
    if max_seconds == 0:
        return 0
    return int.from_bytes(digest[:8], "big") % (max_seconds + 1)


def _normalize_scheduled_for(value: datetime | date | int | float | str) -> str:
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        else:
            normalized = normalized.astimezone(timezone.utc)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        raise ValueError("scheduled_for must not be boolean")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("scheduled_for must be finite")
        return format(value, ".17g")
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("scheduled_for must not be empty")
    return normalized


@dataclass(frozen=True)
class _LaneLease:
    lane: str
    acquired_at: float


class RuntimeLaneBudget:
    """Thread-safe, non-waiting capacity accounting for runtime lanes."""

    def __init__(
        self,
        *,
        total_limit: int = TOTAL_CONCURRENCY_LIMIT,
        background_limit: int = BACKGROUND_CONCURRENCY_LIMIT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(total_limit, bool) or not isinstance(total_limit, int) or total_limit < 1:
            raise ValueError("total_limit must be a positive integer")
        if (
            isinstance(background_limit, bool)
            or not isinstance(background_limit, int)
            or background_limit < 0
            or background_limit > total_limit
        ):
            raise ValueError("background_limit must be between zero and total_limit")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self.total_limit = total_limit
        self.background_limit = background_limit
        self._clock = clock
        self._leases: dict[str, _LaneLease] = {}
        self._lock = threading.RLock()

    def acquire(self, lane: str, token: str) -> bool:
        """Acquire immediately or return ``False`` without waiting.

        An already leased token is rejected. This prevents two concurrent
        callers for the same durable delivery from sharing one lease and lets
        only the actual owner release its capacity.
        """

        return self.acquire_result(lane, token) == "acquired"

    def acquire_result(self, lane: str, token: str) -> str:
        """Acquire a lane and distinguish duplicate ownership from capacity."""

        normalized_lane = str(lane or "").strip().casefold()
        if normalized_lane not in _LANES:
            raise ValueError("lane must be 'background' or 'interactive'")
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise ValueError("token must not be empty")

        with self._lock:
            existing = self._leases.get(normalized_token)
            if existing is not None:
                return "duplicate"

            if len(self._leases) >= self.total_limit:
                return "capacity"
            if normalized_lane == "background" and self._lane_count_locked("background") >= self.background_limit:
                return "capacity"

            self._leases[normalized_token] = _LaneLease(
                lane=normalized_lane,
                acquired_at=float(self._clock()),
            )
            return "acquired"

    def release(self, token: str) -> bool:
        """Release one token immediately; missing tokens are harmless."""

        normalized_token = str(token or "").strip()
        if not normalized_token:
            return False
        with self._lock:
            return self._leases.pop(normalized_token, None) is not None

    def snapshot(self) -> dict[str, int | float]:
        """Return bounded aggregate diagnostics without exposing lease tokens."""

        with self._lock:
            background = self._lane_count_locked("background")
            interactive = len(self._leases) - background
            total = len(self._leases)
            now = float(self._clock())
            oldest_age = max(
                (max(0.0, now - lease.acquired_at) for lease in self._leases.values()),
                default=0.0,
            )
            return {
                "total": total,
                "background": background,
                "interactive": interactive,
                "totalLimit": self.total_limit,
                "backgroundLimit": self.background_limit,
                "availableTotal": max(0, self.total_limit - total),
                "availableBackground": max(0, self.background_limit - background),
                "oldestLeaseAgeSeconds": oldest_age,
            }

    def _lane_count_locked(self, lane: str) -> int:
        return sum(1 for lease in self._leases.values() if lease.lane == lane)


@dataclass(frozen=True)
class ProviderPreflightResult:
    required: bool
    reachable: bool
    cached: bool
    provider: str
    base_url: str
    failure_reason: str | None = None
    consumes_retry: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "reachable": self.reachable,
            "cached": self.cached,
            "provider": self.provider,
            "baseUrl": self.base_url,
            "failureReason": self.failure_reason,
            "consumesRetry": self.consumes_retry,
        }


class ProviderPreflightCache:
    """TTL cache for explicit loopback reachability probes only."""

    def __init__(
        self,
        probe: Callable[[str, str], bool | Mapping[str, Any]],
        *,
        ttl_seconds: int = PROVIDER_PREFLIGHT_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(probe):
            raise TypeError("probe must be callable")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ValueError("ttl_seconds must be a positive integer")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._probe = probe
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, ProviderPreflightResult]] = {}
        self._lock = threading.RLock()

    def check(self, provider: str, base_url: str | None) -> ProviderPreflightResult:
        normalized_provider = str(provider or "").strip().casefold()
        normalized_url = _normalize_loopback_base_url(normalized_provider, base_url)
        if normalized_url is None:
            return ProviderPreflightResult(
                required=False,
                reachable=True,
                cached=False,
                provider=normalized_provider,
                base_url="",
            )

        key = (normalized_provider, normalized_url)
        now = float(self._clock())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now < cached[0]:
                return replace(cached[1], cached=True)
            if cached is not None:
                self._cache.pop(key, None)

        try:
            probe_value = self._probe(normalized_provider, normalized_url)
            if isinstance(probe_value, Mapping):
                reachable = bool(probe_value.get("reachable", probe_value.get("ok", False)))
            else:
                reachable = bool(probe_value)
        except Exception:  # noqa: BLE001 - preflight converts probe errors to one bounded state.
            reachable = False

        result = ProviderPreflightResult(
            required=True,
            reachable=reachable,
            cached=False,
            provider=normalized_provider,
            base_url=normalized_url,
            failure_reason=None if reachable else "provider_unreachable",
            consumes_retry=False,
        )
        expires_at = now + self._ttl_seconds
        with self._lock:
            self._cache[key] = (expires_at, result)
        return result

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _normalize_loopback_base_url(provider: str, base_url: str | None) -> str | None:
    raw_url = str(base_url or "").strip()
    if not raw_url and provider in _LOCAL_PROVIDER_IDS:
        raw_url = _DEFAULT_LOCAL_PROVIDER_BASE_URL
    if not raw_url:
        return None

    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return None

    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                return None
        except ValueError:
            return None

    if ":" in hostname:
        host_for_netloc = f"[{hostname}]"
    else:
        host_for_netloc = hostname
    default_port = 80 if scheme == "http" else 443
    netloc = host_for_netloc if port is None or port == default_port else f"{host_for_netloc}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


@dataclass(frozen=True)
class ProviderFailureDecision:
    failure_class: str
    retryable: bool


def classify_provider_failure(
    error: BaseException | str | None,
    status_code: int | str | None = None,
) -> ProviderFailureDecision:
    """Classify one provider failure without exposing its original text."""

    if isinstance(error, asyncio.CancelledError):
        return ProviderFailureDecision("cancelled", False)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ProviderFailureDecision("timeout", True)
    if isinstance(error, (ConnectionError, socket.gaierror)):
        return ProviderFailureDecision("network", True)

    normalized_status = _coerce_status_code(status_code)
    if normalized_status is None and error is not None and not isinstance(error, str):
        for name in ("status_code", "status", "http_status", "code"):
            normalized_status = _coerce_status_code(getattr(error, name, None))
            if normalized_status is not None:
                break

    message = str(error or "").casefold()
    if normalized_status is None:
        status_match = re.search(
            r"\b(?:http(?:\s+status)?|status(?:_code)?|status code|code)\s*[:=]?\s*([1-5]\d{2})\b",
            message,
        )
        if status_match is not None:
            normalized_status = _coerce_status_code(status_match.group(1))

    if any(marker in message for marker in ("cancelled", "canceled", "cancel requested", "aborted by user")):
        return ProviderFailureDecision("cancelled", False)
    if normalized_status in {401, 403} or any(
        marker in message for marker in ("unauthorized", "forbidden", "authentication", "invalid api key", "api key invalid")
    ):
        return ProviderFailureDecision("auth", False)
    if normalized_status == 402 or any(
        marker in message
        for marker in (
            "insufficient credit",
            "credits exhausted",
            "billing",
            "payment required",
            "quota exhausted",
            "quota was exhausted",
            "quota has been exhausted",
        )
    ):
        return ProviderFailureDecision("credit", False)
    if any(
        marker in message
        for marker in (
            "schema",
            "privacy",
            "redaction",
            "malformed response",
            "response validation",
            "invalid json response",
        )
    ):
        return ProviderFailureDecision("schema", False)
    if normalized_status == 408 or normalized_status == 504 or any(
        marker in message for marker in ("timeout", "timed out", "deadline exceeded")
    ):
        return ProviderFailureDecision("timeout", True)
    if normalized_status == 429 or any(
        marker in message for marker in ("rate limit", "rate-limit", "too many requests")
    ):
        return ProviderFailureDecision("rate_limit", True)
    if normalized_status is not None and normalized_status >= 500:
        return ProviderFailureDecision("server_error", True)
    if normalized_status in {400, 404, 405, 409, 413, 415, 422} or any(
        marker in message
        for marker in (
            "invalid request",
            "bad request",
            "unsupported parameter",
            "unknown parameter",
            "context length",
            "payload too large",
        )
    ):
        return ProviderFailureDecision("invalid_request", False)
    if any(
        marker in message
        for marker in (
            "connection refused",
            "connection reset",
            "connection aborted",
            "host unreachable",
            "network unreachable",
            "name resolution",
            "dns failure",
            "socket closed",
        )
    ):
        return ProviderFailureDecision("network", True)
    return ProviderFailureDecision("unknown", False)


def _coerce_status_code(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 100 <= number <= 599 else None


class RepeatedFailureGuard:
    """Suppress only a consecutive run of the same normalized failure."""

    def __init__(self, threshold: int = REPEATED_FAILURE_THRESHOLD) -> None:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 2:
            raise ValueError("threshold must be an integer of at least two")
        self.threshold = threshold
        self._signature: tuple[str, str, str] | None = None
        self._count = 0
        self._lock = threading.RLock()

    def record_failure(self, tool: str, arguments: Any, failure_class: str) -> bool:
        normalized_tool = str(tool or "").strip().casefold()
        normalized_failure_class = str(failure_class or "").strip().casefold()
        if not normalized_tool or not normalized_failure_class:
            raise ValueError("tool and failure_class must not be empty")
        argument_digest = _normalized_argument_digest(arguments)
        signature = (normalized_tool, argument_digest, normalized_failure_class)
        with self._lock:
            if signature == self._signature:
                self._count = min(self.threshold, self._count + 1)
            else:
                self._signature = signature
                self._count = 1
            return self._count >= self.threshold

    def record_success(self) -> None:
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._signature = None
            self._count = 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tool, argument_digest, failure_class = self._signature or ("", "", "")
            return {
                "tool": tool,
                "argumentDigest": argument_digest,
                "failureClass": failure_class,
                "consecutive": self._count,
                "suppressed": self._count >= self.threshold,
                "threshold": self.threshold,
            }


def _normalized_argument_digest(arguments: Any) -> str:
    normalized = _normalize_argument_value(arguments, parse_json_string=True)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"vrcforge.background.failure.v1\0" + encoded).hexdigest()


def _normalize_argument_value(value: Any, *, parse_json_string: bool = False) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$number": "nan"}
        if math.isinf(value):
            return {"$number": "infinity" if value > 0 else "-infinity"}
        return 0.0 if value == 0 else value
    if isinstance(value, str):
        if parse_json_string:
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    return _normalize_argument_value(json.loads(stripped))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        return value
    if isinstance(value, bytes):
        return {"$bytesSha256": hashlib.sha256(value).hexdigest(), "$length": len(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_argument_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_argument_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = [_normalize_argument_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    return {
        "$type": f"{type(value).__module__}.{type(value).__qualname__}",
        "$value": str(value),
    }


_USAGE_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "inputTokens": ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens"),
        "outputTokens": ("outputTokens", "output_tokens", "completionTokens", "completion_tokens"),
        "totalTokens": ("totalTokens", "total_tokens"),
        "cachedTokens": (
            "cachedTokens",
            "cached_tokens",
            "cacheReadTokens",
            "cache_read_tokens",
            "cacheReadInputTokens",
            "cache_read_input_tokens",
        ),
    }
)

_PRICING_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "input": ("inputPerMillion", "input_per_million"),
        "output": ("outputPerMillion", "output_per_million"),
        "cached": ("cachedInputPerMillion", "cached_input_per_million", "cachedPerMillion", "cached_per_million"),
    }
)


def aggregate_bounded_usage(
    usage_records: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    pricing: Mapping[str, Any] | None = None,
    max_records: int = MAX_USAGE_RECORDS,
    max_tokens: int = MAX_USAGE_TOKENS,
) -> dict[str, Any]:
    """Aggregate explicit provider usage fields without estimating omissions.

    Pricing, when supplied, is expressed per million tokens using
    ``inputPerMillion``, ``outputPerMillion``, and optionally
    ``cachedInputPerMillion``.  Cached tokens are treated as a reported subset
    of input only when both counts and the cached rate are explicit.
    """

    if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
        raise ValueError("max_records must be a positive integer")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer")

    if isinstance(usage_records, Mapping):
        records: Iterable[Mapping[str, Any]] = (usage_records,)
    else:
        records = usage_records

    totals = {field: 0 for field in _USAGE_ALIASES}
    seen: set[str] = set()
    bounded = False
    for index, record in enumerate(records):
        if index >= max_records:
            bounded = True
            break
        if not isinstance(record, Mapping):
            continue
        source = record.get("usage") if isinstance(record.get("usage"), Mapping) else record
        for field, aliases in _USAGE_ALIASES.items():
            value = _first_bounded_usage_int(source, aliases)
            if value is None:
                continue
            seen.add(field)
            if value > max_tokens or totals[field] > max_tokens - value:
                totals[field] = max_tokens
                bounded = True
            else:
                totals[field] += value

    result: dict[str, Any] = {field: totals[field] for field in _USAGE_ALIASES if field in seen}
    if bounded:
        result["bounded"] = True

    if not pricing:
        result["costUnavailableReason"] = "pricing_not_configured"
        return result
    if bounded:
        result["costUnavailableReason"] = "usage_bounded"
        return result

    cost, unavailable_reason = _explicit_usage_cost(result, pricing)
    if unavailable_reason:
        result["costUnavailableReason"] = unavailable_reason
    else:
        result["cost"] = cost
        currency = str(pricing.get("currency") or "").strip()
        if currency:
            result["currency"] = currency[:16]
    return result


def _first_bounded_usage_int(source: Mapping[str, Any], aliases: tuple[str, ...]) -> int | None:
    for key in aliases:
        if key not in source:
            continue
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _explicit_usage_cost(
    usage: Mapping[str, Any],
    pricing: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    if "inputTokens" not in usage or "outputTokens" not in usage:
        return None, "usage_incomplete"

    input_rate, input_state = _explicit_price(pricing, _PRICING_ALIASES["input"])
    output_rate, output_state = _explicit_price(pricing, _PRICING_ALIASES["output"])
    if input_state == "invalid" or output_state == "invalid":
        return None, "pricing_invalid"
    if input_rate is None or output_rate is None:
        return None, "pricing_incomplete"

    input_tokens = int(usage["inputTokens"])
    output_tokens = int(usage["outputTokens"])
    cached_tokens = int(usage.get("cachedTokens", 0))
    if cached_tokens > input_tokens:
        return None, "usage_inconsistent"

    cached_rate = Decimal(0)
    if cached_tokens:
        cached_rate, cached_state = _explicit_price(pricing, _PRICING_ALIASES["cached"])
        if cached_state == "invalid":
            return None, "pricing_invalid"
        if cached_rate is None:
            return None, "pricing_incomplete"

    million = Decimal(1_000_000)
    cost = (
        Decimal(input_tokens - cached_tokens) * input_rate
        + Decimal(cached_tokens) * cached_rate
        + Decimal(output_tokens) * output_rate
    ) / million
    return float(cost.quantize(Decimal("0.000000000001"))), None


def _explicit_price(
    pricing: Mapping[str, Any],
    aliases: tuple[str, ...],
) -> tuple[Decimal | None, str]:
    for key in aliases:
        if key not in pricing:
            continue
        value = pricing.get(key)
        if isinstance(value, bool):
            return None, "invalid"
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None, "invalid"
        if not number.is_finite() or number < 0:
            return None, "invalid"
        return number, "configured"
    return None, "missing"


__all__ = [
    "BACKGROUND_CONCURRENCY_LIMIT",
    "MAX_WAKE_STAGGER_SECONDS",
    "PHASE_TIMEOUT_SECONDS",
    "PROVIDER_PREFLIGHT_CACHE_SECONDS",
    "PROVIDER_RETRY_BACKOFF_SECONDS",
    "QUESTION_REMINDER_SECONDS",
    "REPEATED_FAILURE_THRESHOLD",
    "TOTAL_CONCURRENCY_LIMIT",
    "TOTAL_PROVIDER_ATTEMPTS",
    "ProviderFailureDecision",
    "ProviderPreflightCache",
    "ProviderPreflightResult",
    "RepeatedFailureGuard",
    "RuntimeLaneBudget",
    "aggregate_bounded_usage",
    "classify_provider_failure",
    "classify_runtime_plan_outcome",
    "classify_runtime_step_failure",
    "deterministic_wake_stagger_seconds",
    "retry_backoff_seconds",
]
