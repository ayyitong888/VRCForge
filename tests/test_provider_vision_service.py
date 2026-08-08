from __future__ import annotations

import types
from pathlib import Path

import pytest

from provider_vision_service import (
    ProviderVisionPolicyPorts,
    ProviderVisionSdkPorts,
    ProviderVisionSdkRunner,
    ProviderVisionService,
    ProviderVisionStatePorts,
    VisionModelConfig,
    VisionProfileConfig,
    build_vision_analysis_prompt,
    extract_openai_usage,
    split_image_data_url,
)


ROOT = Path(__file__).parents[1]


class _Runner:
    def __init__(self, text: str = "analysis") -> None:
        self.text = text
        self.calls: list[tuple[VisionModelConfig, str, list[dict[str, object]]]] = []

    def run(
        self,
        config: VisionModelConfig,
        prompt: str,
        images: list[dict[str, object]],
    ) -> tuple[str, dict[str, object]]:
        self.calls.append((config, prompt, images))
        return self.text, {"exact": False}


def _policy(*, requires_key: bool = True) -> ProviderVisionPolicyPorts:
    return ProviderVisionPolicyPorts(
        normalize_provider_name=lambda value: value.strip().lower(),
        provider_requires_api_key=lambda _provider: requires_key,
        provider_display_name=lambda provider: {"openai": "OpenAI", "gemini": "Google AI Studio"}.get(
            provider, provider
        ),
        validate_provider_api_key=lambda _key: None,
        resolve_vertex_project_location=lambda _value: ("project", "location"),
        model_rejects_fixed_temperature=lambda _model: False,
    )


def _service(
    main: VisionModelConfig,
    profile: VisionProfileConfig,
    runner: _Runner,
    *,
    requires_key: bool = True,
) -> ProviderVisionService:
    return ProviderVisionService(
        ProviderVisionStatePorts(main_config=lambda: main, profile_config=lambda: profile),
        _policy(requires_key=requires_key),
        runner,
    )


def test_provider_vision_service_selects_vision_capable_main_model() -> None:
    main = VisionModelConfig("openai", "safe-key", "https://provider.example/v1", "gpt-4o")
    runner = _Runner()
    service = _service(main, VisionProfileConfig("", "", "", "", False), runner)
    images = [{"name": "avatar.png", "dataUrl": "data:image/png;base64,YQ=="}]

    result = service.analyze("look", images)

    assert result == {
        "status": "analyzed",
        "text": "analysis",
        "provider": "openai",
        "providerLabel": "OpenAI",
        "model": "gpt-4o",
        "source": "main",
        "usage": {"exact": False},
    }
    assert runner.calls[0][0] is main
    assert runner.calls[0][2] is images
    assert "avatar.png" in runner.calls[0][1]


def test_provider_vision_service_falls_back_only_to_enabled_configured_profile() -> None:
    main = VisionModelConfig("deepseek", "safe-key", "", "deepseek-chat")
    runner = _Runner()
    profile = VisionProfileConfig("gemini", "vision-key", "", "gemini-2.5-flash", True)
    service = _service(main, profile, runner)

    result = service.analyze("检查", [{"name": "view.png"}])

    assert result["source"] == "visionProfile"
    assert result["provider"] == "gemini"
    assert runner.calls[0][0] == VisionModelConfig("gemini", "vision-key", "", "gemini-2.5-flash")
    assert type(runner.calls[0][0]) is VisionModelConfig


@pytest.mark.parametrize(
    ("profile", "reason"),
    [
        (
            VisionProfileConfig("", "", "", "", False),
            "Main model is not vision-capable and no vision profile is configured.",
        ),
        (
            VisionProfileConfig("gemini", "key", "", "gemini-2.5-flash", False),
            "The configured vision profile is disabled in settings.",
        ),
        (
            VisionProfileConfig("gemini", "", "", "gemini-2.5-flash", True),
            "The vision profile (Google AI Studio) has no API key.",
        ),
    ],
)
def test_provider_vision_service_returns_honest_unconfigured_status(
    profile: VisionProfileConfig,
    reason: str,
) -> None:
    runner = _Runner()
    service = _service(
        VisionModelConfig("deepseek", "safe-key", "", "deepseek-chat"),
        profile,
        runner,
    )

    assert service.analyze("look", [{"name": "image"}]) == {
        "status": "unconfigured",
        "reason": reason,
    }
    assert runner.calls == []


def test_provider_vision_service_preserves_conservative_model_policy() -> None:
    service = _service(
        VisionModelConfig("openai", "key", "", "gpt-4o"),
        VisionProfileConfig("", "", "", "", False),
        _Runner(),
    )

    assert service.model_supports_vision("gemini", "anything") is True
    assert service.model_supports_vision("anthropic", "anything") is True
    assert service.model_supports_vision("deepseek", "deepseek-vl") is False
    assert service.model_supports_vision("openai", "gpt-4o") is True
    assert service.model_supports_vision("custom", "qwen3-vl-32b") is True
    assert service.model_supports_vision("custom", "text-only") is False


def test_provider_vision_service_rejects_empty_provider_result() -> None:
    service = _service(
        VisionModelConfig("openai", "key", "", "gpt-4o"),
        VisionProfileConfig("", "", "", "", False),
        _Runner("  "),
    )
    with pytest.raises(RuntimeError, match="OpenAI returned an empty vision analysis"):
        service.analyze("look", [{"name": "image"}])


def test_provider_vision_pure_contracts_are_bounded_and_unchanged() -> None:
    assert split_image_data_url("data:image/png;base64,YQ==") == ("image/png", "YQ==")
    with pytest.raises(RuntimeError, match="not an image"):
        split_image_data_url("data:text/plain;base64,YQ==")
    with pytest.raises(RuntimeError, match="not base64-encoded"):
        split_image_data_url("data:image/png,YQ==")

    prompt = build_vision_analysis_prompt(
        "x" * 2500,
        [{"name": f"image-{index}.png"} for index in range(10)],
    )
    assert "image-7.png" in prompt
    assert "image-8.png" not in prompt
    assert "x" * 2000 in prompt
    assert "x" * 2001 not in prompt

    assert extract_openai_usage(
        types.SimpleNamespace(
            usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8)
        )
    ) == {"exact": True, "inputTokens": 3, "outputTokens": 5, "totalTokens": 8}
    assert extract_openai_usage(types.SimpleNamespace()) == {
        "exact": False,
        "unavailableReason": "provider_usage_missing",
    }


def test_provider_vision_service_has_no_migration_host_or_monkeypatch_seam() -> None:
    source = (ROOT / "provider_vision_service.py").read_text(encoding="utf-8")
    for forbidden in ("_host", "_impl_", "sys.modules", "__getattr__", "dashboard_server import"):
        assert forbidden not in source


def test_provider_vision_sdk_runner_shapes_openai_request_through_typed_fake() -> None:
    calls: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self) -> None:
            def create(**request: object) -> object:
                calls["request"] = request
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="fake vision"))],
                    usage=types.SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
                )

            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    sdk = ProviderVisionSdkPorts(
        google_client=lambda _config, _vertex: pytest.fail("Google SDK port must not run"),
        google_part_from_bytes=lambda _data, _mime: pytest.fail("Google SDK port must not run"),
        anthropic_client=lambda _key: pytest.fail("Anthropic SDK port must not run"),
        openai_client=lambda key, base_url, timeout: (
            calls.update(client=(key, base_url, timeout)) or FakeOpenAI()
        ),
    )
    runner = ProviderVisionSdkRunner(_policy(), sdk)
    config = VisionModelConfig("openai", "safe-key", "https://provider.example/v1", "gpt-4o")

    text, usage = runner.run(
        config,
        "late prompt",
        [{"dataUrl": "data:image/png;base64,YQ=="}],
    )

    assert text == "fake vision"
    assert usage == {"exact": True, "inputTokens": 4, "outputTokens": 6, "totalTokens": 10}
    assert calls["client"] == ("safe-key", "https://provider.example/v1", 60.0)
    assert calls["request"] == {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
                    {"type": "text", "text": "late prompt"},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 1024,
    }


def test_provider_vision_sdk_runner_shapes_vertex_request_through_typed_fakes() -> None:
    calls: dict[str, object] = {}

    class FakeModels:
        @staticmethod
        def generate_content(**request: object) -> object:
            calls["request"] = request
            return types.SimpleNamespace(
                text="vertex vision",
                usage_metadata=types.SimpleNamespace(
                    prompt_token_count=7,
                    candidates_token_count=3,
                    total_token_count=10,
                ),
            )

    sdk = ProviderVisionSdkPorts(
        google_client=lambda config, vertex: (
            calls.update(client=(config, vertex)) or types.SimpleNamespace(models=FakeModels())
        ),
        google_part_from_bytes=lambda data, mime: {"bytes": data, "mime": mime},
        anthropic_client=lambda _key: pytest.fail("Anthropic SDK port must not run"),
        openai_client=lambda _key, _url, _timeout: pytest.fail("OpenAI SDK port must not run"),
    )
    runner = ProviderVisionSdkRunner(_policy(), sdk)
    config = VisionModelConfig("vertexai", "safe-key", "project/location", "gemini-2.5-flash")

    text, usage = runner.run(
        config,
        "vertex prompt",
        [{"dataUrl": "data:image/png;base64,YQ=="}],
    )

    assert text == "vertex vision"
    assert usage == {"exact": True, "inputTokens": 7, "outputTokens": 3, "totalTokens": 10}
    assert calls["client"] == (config, ("project", "location"))
    assert calls["request"] == {
        "model": "gemini-2.5-flash",
        "contents": [{"bytes": b"a", "mime": "image/png"}, "vertex prompt"],
    }


def test_provider_vision_sdk_runner_shapes_anthropic_request_through_typed_fake() -> None:
    calls: dict[str, object] = {}

    class FakeMessages:
        @staticmethod
        def create(**request: object) -> object:
            calls["request"] = request
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text="anthropic vision")],
                usage=types.SimpleNamespace(input_tokens=8, output_tokens=2),
            )

    sdk = ProviderVisionSdkPorts(
        google_client=lambda _config, _vertex: pytest.fail("Google SDK port must not run"),
        google_part_from_bytes=lambda _data, _mime: pytest.fail("Google SDK port must not run"),
        anthropic_client=lambda key: (
            calls.update(client=key) or types.SimpleNamespace(messages=FakeMessages())
        ),
        openai_client=lambda _key, _url, _timeout: pytest.fail("OpenAI SDK port must not run"),
    )
    runner = ProviderVisionSdkRunner(_policy(), sdk)
    config = VisionModelConfig("anthropic", "safe-key", "", "claude-sonnet")

    text, usage = runner.run(
        config,
        "anthropic prompt",
        [{"dataUrl": "data:image/png;base64,YQ=="}],
    )

    assert text == "anthropic vision"
    assert usage == {"exact": True, "inputTokens": 8, "outputTokens": 2, "totalTokens": 10}
    assert calls["client"] == "safe-key"
    assert calls["request"] == {
        "model": "claude-sonnet",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "YQ==",
                        },
                    },
                    {"type": "text", "text": "anthropic prompt"},
                ],
            }
        ],
    }


def test_dashboard_composes_typed_provider_vision_without_root_facades() -> None:
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert "from provider_vision_service import (" in dashboard_source
    assert "PROVIDER_VISION = ProviderVisionService(" in dashboard_source
    assert "AGENT_GATEWAY.vision_analyze_fn = PROVIDER_VISION.analyze" in dashboard_source
    for forbidden in (
        "ProviderVisionIntegrationService",
        "_PROVIDER_VISION_INTEGRATION",
        "def _agent_gateway_vision_analyze(",
        "def provider_model_supports_vision(",
        "def split_image_data_url(",
        "def build_vision_analysis_prompt(",
        "def _extract_openai_usage(",
        "def _run_provider_vision_analysis(",
    ):
        assert forbidden not in dashboard_source
    assert not (ROOT / "provider_vision_integration_service.py").exists()
