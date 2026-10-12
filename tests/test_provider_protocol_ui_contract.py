from __future__ import annotations

from pathlib import Path


def test_provider_ui_separates_auto_negotiation_from_real_models() -> None:
    settings = Path("src/components/settings/provider-settings.tsx").read_text(encoding="utf-8")
    hook = Path("src/hooks/use-provider-settings.ts").read_text(encoding="utf-8")
    api = Path("src/lib/api/app.ts").read_text(encoding="utf-8")

    assert "onProtocolAutoNegotiationChange(event.target.checked)" in settings
    assert 'id: "deepseek-auto"' not in settings
    assert "protocolAutoNegotiationHint" in settings
    assert "deepseekAutoRoute" not in settings
    assert "supportedApiTypeOptions" in settings
    assert "payload.attempts?.map" in hook
    auto_handler = hook.split("function handleProtocolAutoNegotiationChange", 1)[1].split("function handleVisionProviderChange", 1)[0]
    assert "setApiModel(" not in auto_handler
    assert "setApiType(" in auto_handler
    model_handler = hook.split("function handleApiModelChange", 1)[1].split("function handleProtocolAutoNegotiationChange", 1)[0]
    assert 'setApiType("auto")' not in model_handler
    assert "recommendedModel?: string" in api
    assert "attempts?: Array" in api


def test_every_locale_explains_protocol_only_negotiation() -> None:
    for locale in ("en-US", "zh-CN", "zh-TW", "ja-JP"):
        source = Path(f"src/locales/{locale}.json").read_text(encoding="utf-8")
        assert '"protocolAutoNegotiation"' in source
        assert '"protocolAutoNegotiationHint"' in source
        assert '"deepseekAutoRoute"' not in source
        assert '"apiTypeHint"' in source
        assert "Pro Responses" not in source
        assert "Pro Anthropic" not in source


def test_deepseek_ga_models_expose_all_official_text_protocols() -> None:
    settings = Path("src/lib/provider-protocol-options.ts").read_text(encoding="utf-8")
    assert 'model.trim() === "deepseek-v4-flash" || model.trim() === "deepseek-v4-pro"' in settings
    assert '["auto", "responses", "messages", "chat_completions"]' in settings
