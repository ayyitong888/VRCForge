from __future__ import annotations

import dashboard_server
from provider_configuration_service import ProviderApiConfig


def test_dashboard_general_auto_review_reuses_key_with_a_distinct_model(monkeypatch) -> None:
    active = ProviderApiConfig(
        provider="openrouter",
        api_key="user-key",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-pro",
        api_type="auto",
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", lambda: active)
    monkeypatch.setattr(
        dashboard_server.PROVIDER_MODEL_CATALOG,
        "fetch_provider_models",
        lambda config: [
            {"id": config.model},
            {"id": "google/gemini-2.5-flash"},
        ],
    )

    def probe(config, prompt, *, structured):
        observed.update(config=config, prompt=prompt, structured=structured)
        return '{"decision":"allow_auto"}'

    monkeypatch.setattr(dashboard_server.PROVIDER_TESTS, "probe_text", probe)
    decision = dashboard_server._review_general_auto_approval(
        {
            "targetTool": "vrcforge_write_file",
            "riskLevel": "medium",
            "arguments": {
                "path": "C:/General/notes.txt",
                "content": "PRIVATE_CONTENT",
                "overwrite": False,
            },
        }
    )

    reviewer = observed["config"]
    assert decision == "allow_auto"
    assert reviewer.api_key == active.api_key
    assert reviewer.model == "google/gemini-2.5-flash"
    assert reviewer.model != active.model
    assert reviewer.api_type == "auto"
    assert observed["structured"] is True
    assert "PRIVATE_CONTENT" not in str(observed["prompt"])


def test_dashboard_general_auto_review_fails_closed_without_distinct_model(monkeypatch) -> None:
    active = ProviderApiConfig(
        provider="openrouter",
        api_key="user-key",
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash",
        api_type="auto",
    )
    monkeypatch.setattr(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", lambda: active)
    monkeypatch.setattr(
        dashboard_server.PROVIDER_MODEL_CATALOG,
        "fetch_provider_models",
        lambda _config: [{"id": active.model}],
    )
    assert dashboard_server._review_general_auto_approval(
        {"targetTool": "vrcforge_write_file", "arguments": {}}
    ) == "manual"


def test_general_manual_approvals_offer_once_reject_and_remembered_category() -> None:
    handlers = dashboard_server.AGENT_GATEWAY.approval_transactions._ports.state.write_handlers
    for target in (
        "vrcforge_edit_file",
        "vrcforge_write_file",
        "vrcforge_delete_path",
        "vrcforge_move_path",
        "vrcforge_apply_patch",
    ):
        handler = handlers[target]
        assert handler.allow_future_category is True
        assert handler.approval_category.startswith("general-file-")
        assert dashboard_server.AGENT_GATEWAY._write_handler_allows_future_category(
            handler,
            {"targetTool": target, "riskLevel": "medium"},
        ) is True
