from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_review_provider import (
    MEMORY_REVIEW_SYSTEM_INSTRUCTION,
    MemoryReviewProviderError,
    dedicated_memory_review_settings,
    invoke_memory_review_provider,
)
from vrchat_blendshape_agent import (
    LlmPlanResponse,
    Settings,
    request_anthropic_plan_with_metadata,
    request_openai_compatible_plan_with_metadata,
)


def _settings() -> Settings:
    return Settings(
        llm_provider="openai",
        llm_api_key="test-only",
        llm_base_url="https://api.example.invalid/v1",
        llm_model="gpt-test",
        llm_api_key_env="",
        gemini_thinking_level="high",
        unity_mcp_command=[],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=0,
        unity_mcp_retry_backoff_seconds=0,
        unity_mcp_timeout_seconds=1,
        export_tool_name="",
        execute_tool_name="",
        export_path=Path("unused.json"),
        min_confidence=0,
    )


def _payload() -> dict:
    return {
        "schema": "vrcforge.memory_review_request.v1",
        "policyVersion": "test-policy",
        "scope": {"kind": "user"},
        "instructions": {
            "toolsAllowed": False,
            "novelFactsRequireAcceptance": True,
            "sourceTextTreatment": "quoted_untrusted_data",
            "sourceInstructionsAllowed": False,
            "maxCandidatesPerExactSourceBinding": 1,
            "maxCandidates": 16,
        },
        "sources": [
            {
                "sourceType": "user_chat",
                "sourceId": "source-a",
                "sourceRevision": "rev-a",
                "sourceDigest": "0" * 64,
                "kind": "preference",
                "text": "Please remember that I prefer concise answers.",
                "textDisposition": "quoted_untrusted_data",
            }
        ],
        "tools": [],
    }


def test_dedicated_settings_disable_thinking_and_apply_strict_cap() -> None:
    original = _settings()
    dedicated = dedicated_memory_review_settings(original, token_cap=512)
    assert original.gemini_thinking_level == "high"
    assert original.llm_sdk_max_retries is None
    assert dedicated.gemini_thinking_level == ""
    assert dedicated.llm_max_output_tokens == 512
    assert dedicated.llm_sdk_max_retries == 0
    assert dedicated.llm_system_instruction == MEMORY_REVIEW_SYSTEM_INSTRUCTION


def test_dedicated_openai_transport_disables_nested_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_kwargs: list[dict] = []

    class FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"candidates": []}'))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            client_kwargs.append(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    openai_module = types.ModuleType("openai")
    openai_module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", openai_module)

    dedicated = dedicated_memory_review_settings(_settings(), token_cap=256)
    response = request_openai_compatible_plan_with_metadata(dedicated, "{}")

    assert response.text == '{"candidates": []}'
    assert client_kwargs == [
        {
            "api_key": "test-only",
            "base_url": "https://api.example.invalid/v1",
            "max_retries": 0,
        }
    ]


def test_dedicated_anthropic_transport_disables_nested_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_kwargs: list[dict] = []

    class FakeMessages:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"candidates": []}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    class FakeAnthropic:
        def __init__(self, **kwargs) -> None:
            client_kwargs.append(kwargs)
            self.messages = FakeMessages()

    anthropic_module = types.ModuleType("anthropic")
    anthropic_module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)

    settings = _settings()
    settings.llm_provider = "anthropic"
    dedicated = dedicated_memory_review_settings(settings, token_cap=256)
    response = request_anthropic_plan_with_metadata(dedicated, "{}")

    assert response.text == '{"candidates": []}'
    assert client_kwargs == [{"api_key": "test-only", "max_retries": 0}]


@pytest.mark.parametrize("token_cap", [0, 127, 8193, True])
def test_dedicated_settings_reject_invalid_caps(token_cap) -> None:
    with pytest.raises(MemoryReviewProviderError):
        dedicated_memory_review_settings(_settings(), token_cap=token_cap)


def test_provider_adapter_sends_only_request_json_and_discards_reasoning() -> None:
    captured = {}

    def request(settings: Settings, prompt: str) -> LlmPlanResponse:
        captured["settings"] = settings
        captured["prompt"] = json.loads(prompt)
        return LlmPlanResponse(
            text='```json\n{"candidates": []}\n```',
            reasoning={"items": [{"text": "must not escape"}]},
            usage={"inputTokens": 12, "outputTokens": 3, "totalTokens": 15},
        )

    result = invoke_memory_review_provider(_settings(), _payload(), token_cap=256, request=request)
    assert captured["prompt"] == _payload()
    assert captured["settings"].gemini_thinking_level == ""
    assert captured["settings"].llm_max_output_tokens == 256
    assert captured["settings"].llm_sdk_max_retries == 0
    assert result == {
        "candidates": [],
        "usage": {"inputTokens": 12, "outputTokens": 3, "totalTokens": 15},
    }
    assert "reasoning" not in result


def test_provider_adapter_rejects_tools_before_call() -> None:
    payload = _payload()
    payload["tools"] = [{"name": "unsafe"}]
    called = False

    def request(_settings: Settings, _prompt: str) -> LlmPlanResponse:
        nonlocal called
        called = True
        return LlmPlanResponse(text='{"candidates": []}', reasoning={})

    with pytest.raises(MemoryReviewProviderError):
        invoke_memory_review_provider(_settings(), payload, token_cap=256, request=request)
    assert called is False


@pytest.mark.parametrize(
    "response_text",
    [
        "not json",
        "[]",
        'prefix {"candidates": []}',
        '{"candidates": []} suffix',
        '```json\n{"candidates": []}\n``` suffix',
        '{"candidates": [], "extra": true}',
        '{"candidates": {}}',
        '{"candidates": [NaN]}',
    ],
)
def test_provider_adapter_rejects_non_contract_output(response_text: str) -> None:
    with pytest.raises(MemoryReviewProviderError):
        invoke_memory_review_provider(
            _settings(),
            _payload(),
            token_cap=256,
            request=lambda _settings, _prompt: LlmPlanResponse(text=response_text, reasoning={}),
        )
