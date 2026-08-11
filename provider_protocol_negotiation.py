"""Pure provider/model protocol negotiation policy.

The module decides which request protocols may be attempted.  It owns no
credentials, clients, persistence, or network activity.  Callers remain
responsible for enforcing the same configured endpoint and for retrying only
before any provider output has been observed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


DEEPSEEK_AUTO_MODEL = "deepseek-auto"
DEEPSEEK_FLASH_MODEL = "deepseek-v4-flash"
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"


@dataclass(frozen=True, slots=True)
class ProviderProtocolCandidate:
    """One same-provider endpoint candidate, ordered by preference."""

    provider: str
    model: str
    api_type: str


def provider_protocol_candidates(
    provider: str,
    model: str,
    requested_api_type: object,
) -> tuple[ProviderProtocolCandidate, ...]:
    """Return an ordered, bounded candidate list.

    Explicit protocol selections are pins and therefore always return exactly
    one candidate.  Only ``auto`` may return alternatives.  The special
    DeepSeek automatic model is the sole opt-in route that may change models;
    an exact model selection never does.
    """

    provider_id = str(provider).strip().lower()
    model_id = str(model).strip()
    requested = str(requested_api_type or "auto").strip().lower()

    if requested != "auto":
        return (ProviderProtocolCandidate(provider_id, model_id, requested),)

    if provider_id == "deepseek":
        if model_id == DEEPSEEK_AUTO_MODEL:
            return (
                ProviderProtocolCandidate(provider_id, DEEPSEEK_FLASH_MODEL, "responses"),
                ProviderProtocolCandidate(provider_id, DEEPSEEK_PRO_MODEL, "chat_completions"),
                ProviderProtocolCandidate(provider_id, DEEPSEEK_FLASH_MODEL, "chat_completions"),
            )
        if model_id == DEEPSEEK_FLASH_MODEL:
            return (
                ProviderProtocolCandidate(provider_id, model_id, "responses"),
                ProviderProtocolCandidate(provider_id, model_id, "chat_completions"),
            )
        return (ProviderProtocolCandidate(provider_id, model_id, "chat_completions"),)
    if provider_id == "openai":
        return (
            ProviderProtocolCandidate(provider_id, model_id, "responses"),
            ProviderProtocolCandidate(provider_id, model_id, "chat_completions"),
        )
    if provider_id == "custom":
        return tuple(
            ProviderProtocolCandidate(provider_id, model_id, api_type)
            for api_type in ("responses", "chat_completions", "messages", "generate_content")
        )
    if provider_id in {"gemini", "vertexai"}:
        return (ProviderProtocolCandidate(provider_id, model_id, "generate_content"),)
    if provider_id == "anthropic":
        return (ProviderProtocolCandidate(provider_id, model_id, "messages"),)
    return (ProviderProtocolCandidate(provider_id, model_id, "chat_completions"),)


def supported_provider_api_types(provider: str, model: str) -> tuple[str, ...]:
    """Return protocols a user may explicitly pin for this configuration."""

    provider_id = str(provider).strip().lower()
    model_id = str(model).strip()
    if provider_id == "custom":
        return ("responses", "chat_completions", "messages", "generate_content")
    if provider_id == "openai":
        return ("responses", "chat_completions")
    if provider_id == "deepseek":
        if model_id == DEEPSEEK_AUTO_MODEL:
            return ()
        if model_id == DEEPSEEK_FLASH_MODEL:
            return ("responses", "chat_completions")
        return ("chat_completions",)
    if provider_id in {"gemini", "vertexai"}:
        return ("generate_content",)
    if provider_id == "anthropic":
        return ("messages",)
    return ("chat_completions",)


def is_explicit_protocol_compatibility_error(error: BaseException) -> bool:
    """Classify only errors that safely prove a protocol is unavailable.

    Authentication, rate limits, timeouts, server failures, generic validation
    errors, and ambiguous transport failures deliberately return ``False``.
    """

    for current in _error_chain(error):
        status = _status_code(current)
        if status in {404, 405, 415, 501}:
            return True
        if status != 400:
            continue
        text = _bounded_error_text(current)
        if any(marker in text for marker in (
            "unsupported endpoint",
            "unsupported protocol",
            "unknown endpoint",
            "responses api is not supported",
            "messages api is not supported",
            "generatecontent is not supported",
        )):
            return True
    return False


def _error_chain(error: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        current = cause if isinstance(cause, BaseException) else context if isinstance(context, BaseException) else None


def _status_code(error: BaseException) -> int | None:
    for value in (
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _bounded_error_text(error: BaseException) -> str:
    values: list[Any] = [str(error)]
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        values.extend(body.get(key) for key in ("code", "type", "message"))
        nested = body.get("error")
        if isinstance(nested, dict):
            values.extend(nested.get(key) for key in ("code", "type", "message"))
    return " ".join(str(value or "") for value in values)[:2000].lower()
