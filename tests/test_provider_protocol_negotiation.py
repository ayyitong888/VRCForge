from __future__ import annotations

from types import SimpleNamespace

import pytest

from provider_protocol_negotiation import (
    DEEPSEEK_AUTO_MODEL,
    is_explicit_protocol_compatibility_error,
    provider_protocol_candidates,
)


def _pairs(provider: str, model: str, api_type: str) -> list[tuple[str, str]]:
    return [
        (candidate.model, candidate.api_type)
        for candidate in provider_protocol_candidates(provider, model, api_type)
    ]


def test_deepseek_automatic_model_negotiates_both_models_in_bounded_order() -> None:
    assert _pairs("deepseek", DEEPSEEK_AUTO_MODEL, "auto") == [
        ("deepseek-v4-pro", "responses"),
        ("deepseek-v4-pro", "messages"),
        ("deepseek-v4-pro", "chat_completions"),
        ("deepseek-v4-flash", "responses"),
        ("deepseek-v4-flash", "messages"),
        ("deepseek-v4-flash", "chat_completions"),
    ]


def test_exact_deepseek_models_never_silently_change_model() -> None:
    assert _pairs("deepseek", "deepseek-v4-flash", "auto") == [
        ("deepseek-v4-flash", "responses"),
        ("deepseek-v4-flash", "messages"),
        ("deepseek-v4-flash", "chat_completions"),
    ]
    assert _pairs("deepseek", "deepseek-v4-pro", "auto") == [
        ("deepseek-v4-pro", "responses"),
        ("deepseek-v4-pro", "messages"),
        ("deepseek-v4-pro", "chat_completions"),
    ]


@pytest.mark.parametrize(
    "api_type",
    ["responses", "chat_completions", "messages", "generate_content"],
)
def test_custom_explicit_protocol_is_a_single_pinned_candidate(api_type: str) -> None:
    candidates = provider_protocol_candidates("custom", "site-model", api_type)
    assert len(candidates) == 1
    assert candidates[0].provider == "custom"
    assert candidates[0].model == "site-model"
    assert candidates[0].api_type == api_type


def test_custom_auto_never_changes_provider_model_or_site_choice() -> None:
    candidates = provider_protocol_candidates("custom", "site-model", "auto")
    assert [item.api_type for item in candidates] == [
        "responses",
        "chat_completions",
        "messages",
        "generate_content",
    ]
    assert {(item.provider, item.model) for item in candidates} == {("custom", "site-model")}


@pytest.mark.parametrize("status", [404, 405, 415, 501])
def test_only_explicit_endpoint_compatibility_statuses_allow_fallback(status: int) -> None:
    error = RuntimeError("provider request failed")
    error.status_code = status  # type: ignore[attr-defined]
    assert is_explicit_protocol_compatibility_error(error) is True


@pytest.mark.parametrize("status", [400, 401, 403, 408, 429, 500, 503])
def test_auth_rate_limit_transient_and_ambiguous_errors_do_not_fallback(status: int) -> None:
    error = RuntimeError("provider request failed")
    error.response = SimpleNamespace(status_code=status)  # type: ignore[attr-defined]
    assert is_explicit_protocol_compatibility_error(error) is False


def test_explicit_responses_not_enabled_error_allows_auto_protocol_fallback() -> None:
    error = RuntimeError(
        "Error code: 400 - {'error': {'message': 'this model is not enabled for the Responses API'}}"
    )
    error.status_code = 400  # type: ignore[attr-defined]

    assert is_explicit_protocol_compatibility_error(error) is True
