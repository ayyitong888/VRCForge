"""Provider request contracts used by the bounded Memory Review lane."""

from pathlib import Path

import pytest

from vrchat_blendshape_agent import (
    ANTHROPIC_RESPONSE_BASE_MAX_TOKENS,
    DEFAULT_LLM_SYSTEM_INSTRUCTION,
    Settings,
    build_anthropic_request_payload,
    build_gemini_generate_config,
    build_openai_compatible_request_payload,
)


def make_settings(provider: str, model: str) -> Settings:
    return Settings(
        llm_provider=provider,
        llm_api_key="test-key",
        llm_base_url="https://provider.example/v1",
        llm_model=model,
        llm_api_key_env="TEST_API_KEY",
        gemini_thinking_level="",
        unity_mcp_command=["unity-mcp"],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=3,
        unity_mcp_retry_backoff_seconds=2.0,
        unity_mcp_timeout_seconds=30,
        export_tool_name="vrc_export_blendshapes",
        execute_tool_name="vrc_apply_blendshapes",
        export_path=Path("Assets/VRCForge/blendshapes_export.json"),
        min_confidence=0.65,
    )


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeThinkingConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeGenerateTypes:
    GenerateContentConfig = _FakeGenerateContentConfig
    ThinkingConfig = _FakeThinkingConfig


def test_openai_compatible_standard_model_uses_bounded_output_and_instruction() -> None:
    settings = make_settings("openai", "gpt-4o")
    settings.llm_system_instruction = "Return one bounded review object."
    settings.llm_max_output_tokens = 321

    payload = build_openai_compatible_request_payload(settings, "redacted evidence")

    assert payload["max_tokens"] == 321
    assert "max_completion_tokens" not in payload
    assert payload["messages"][0] == {
        "role": "system",
        "content": "Return one bounded review object.",
    }
    assert "tools" not in payload


def test_openai_compatible_sampling_restricted_model_uses_completion_limit() -> None:
    settings = make_settings("openai", "o3")
    settings.llm_system_instruction = "Return one bounded review object."
    settings.llm_max_output_tokens = 654

    payload = build_openai_compatible_request_payload(settings, "redacted evidence")

    assert payload["max_completion_tokens"] == 654
    assert "max_tokens" not in payload
    assert "temperature" not in payload
    assert "tools" not in payload


def test_anthropic_uses_bounded_output_and_dedicated_instruction() -> None:
    settings = make_settings("anthropic", "claude-3-5-sonnet")
    settings.llm_system_instruction = "Return one bounded review object."
    settings.llm_max_output_tokens = 777

    payload = build_anthropic_request_payload(settings, "redacted evidence")

    assert payload["max_tokens"] == 777
    assert payload["system"] == "Return one bounded review object."
    assert payload["messages"] == [{"role": "user", "content": "redacted evidence"}]
    assert "tools" not in payload
    assert "thinking" not in payload


@pytest.mark.parametrize("provider", ["gemini", "vertexai"])
def test_generate_content_config_uses_bounded_output_and_instruction(provider: str) -> None:
    settings = make_settings(provider, "gemini-2.5-flash")
    settings.llm_system_instruction = "Return one bounded review object."
    settings.llm_max_output_tokens = 888

    config = build_gemini_generate_config(settings, _FakeGenerateTypes)

    assert config is not None
    assert config.kwargs == {
        "max_output_tokens": 888,
        "system_instruction": "Return one bounded review object.",
    }


def test_optional_overrides_leave_default_provider_payloads_unchanged() -> None:
    openai_settings = make_settings("openai", "gpt-4o")
    openai_payload = build_openai_compatible_request_payload(openai_settings, "hello")
    assert "max_tokens" not in openai_payload
    assert "max_completion_tokens" not in openai_payload
    assert openai_payload["messages"][0]["content"] == DEFAULT_LLM_SYSTEM_INSTRUCTION

    anthropic_settings = make_settings("anthropic", "claude-3-5-sonnet")
    anthropic_payload = build_anthropic_request_payload(anthropic_settings, "hello")
    assert anthropic_payload["max_tokens"] == ANTHROPIC_RESPONSE_BASE_MAX_TOKENS
    assert anthropic_payload["system"] == DEFAULT_LLM_SYSTEM_INSTRUCTION

    for provider in ("gemini", "vertexai"):
        settings = make_settings(provider, "gemini-2.5-flash")
        assert build_gemini_generate_config(settings, _FakeGenerateTypes) is None
