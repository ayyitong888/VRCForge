from __future__ import annotations

from pathlib import Path


def test_provider_ui_exposes_auto_model_custom_protocols_and_visible_attempts() -> None:
    settings = Path("src/components/settings/provider-settings.tsx").read_text(encoding="utf-8")
    hook = Path("src/hooks/use-provider-settings.ts").read_text(encoding="utf-8")
    api = Path("src/lib/api/app.ts").read_text(encoding="utf-8")

    assert 'id: "deepseek-auto"' in settings
    assert '["auto", "responses", "chat_completions", "messages", "generate_content"]' in settings
    assert "payload.attempts?.map" in hook
    model_handler = hook.split("function handleApiModelChange", 1)[1].split("function handleVisionProviderChange", 1)[0]
    assert 'setApiType("auto")' not in model_handler
    assert "recommendedModel?: string" in api
    assert "attempts?: Array" in api


def test_every_locale_explains_auto_handshake_and_deepseek_route() -> None:
    for locale in ("en-US", "zh-CN", "zh-TW", "ja-JP"):
        source = Path(f"src/locales/{locale}.json").read_text(encoding="utf-8")
        assert '"deepseekAutoModel"' in source
        assert '"apiTypeHint"' in source
        assert "Flash Responses" in source
