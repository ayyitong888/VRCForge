from __future__ import annotations

import dashboard_server
from provider_configuration_service import ProviderApiConfig
from types import SimpleNamespace


def test_dashboard_auto_review_uses_bound_provider_and_tool_free_request(monkeypatch) -> None:
    active = ProviderApiConfig(
        provider="openrouter",
        api_key="user-key",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-pro",
        api_type="auto",
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(dashboard_server._RUNTIME_PLANNER_TURN, "current_config", lambda: active)

    def review(prompt):
        observed["prompt"] = prompt
        return SimpleNamespace(text='{"decision":"allow_auto"}')

    monkeypatch.setattr(dashboard_server._RUNTIME_PLANNER_MODEL, "review", review)
    decision = dashboard_server._review_auto_approval(
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

    assert decision == "allow_auto"
    assert "PRIVATE_CONTENT" not in str(observed["prompt"])
    assert "No external capabilities" in str(observed["prompt"])


def test_dashboard_auto_review_fails_closed_when_reviewer_errors(monkeypatch) -> None:
    active = ProviderApiConfig(
        provider="openrouter",
        api_key="user-key",
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash",
        api_type="auto",
    )
    monkeypatch.setattr(dashboard_server._RUNTIME_PLANNER_TURN, "current_config", lambda: active)

    def fail_review(_prompt):
        raise RuntimeError("reviewer unavailable")

    monkeypatch.setattr(dashboard_server._RUNTIME_PLANNER_MODEL, "review", fail_review)
    assert dashboard_server._review_auto_approval(
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
