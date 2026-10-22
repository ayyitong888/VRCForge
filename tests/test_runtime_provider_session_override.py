"""Session choices must reach the real runtime Provider boundary without global writes."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

import dashboard_server as host
from agent_gateway import AgentGateway
from provider_configuration_service import ProviderApiConfig
from runtime_planner_service import RuntimePlannerService
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
from tests.test_provider_configuration_service import _owner


@pytest.fixture(autouse=True)
def offline_model_capacity(monkeypatch):
    monkeypatch.setattr(host.PROVIDER_MODEL_CATALOG, "resolve_context_limit", lambda config: None)


def configured_owner(tmp_path):
    owner = _owner(tmp_path / "providers.json")
    owner.save_api_config(ProviderApiConfig("deepseek", "saved-deepseek-key", "https://old-deepseek.example/v1", "deepseek-old", "chat_completions"))
    current = ProviderApiConfig("openai", "current-openai-key", "https://current-openai.example/v1", "model-a", "chat_completions")
    owner.save_api_config(current)
    return owner, current


def test_session_resolution_is_read_only_and_preserves_unqualified_config(tmp_path):
    owner, current = configured_owner(tmp_path)
    before = owner.config_path.read_bytes()
    assert owner.resolve_session_config() is current
    assert owner.resolve_session_config(provider="openai", model="model-a") is current
    selected = owner.resolve_session_config(model="model-b")
    assert selected == replace(current, model="model-b")
    assert owner.current_api_config() is current
    assert owner.config_path.read_bytes() == before


def test_cross_provider_selection_uses_only_its_saved_key_and_default_endpoint(tmp_path):
    owner, current = configured_owner(tmp_path)
    selected = owner.resolve_session_config(provider="deepseek", model="deepseek-chat")
    assert selected.api_key == "saved-deepseek-key"
    assert selected.base_url == "https://deepseek.example/v1"
    assert selected.provider == "deepseek" and selected.model == "deepseek-chat"
    assert selected.api_key != current.api_key and selected.base_url != current.base_url
    assert owner.current_api_config() is current


def test_session_model_preserves_chatgpt_oauth_account_and_transport(tmp_path):
    owner = _owner(tmp_path / "providers.json")
    current = ProviderApiConfig("chatgpt", "synthetic-oauth-account-id", "https://api.openai.com/v1", "gpt-old", "responses")
    owner.save_api_config(current)
    selected = owner.resolve_session_config(provider="chatgpt", model="gpt-new")
    assert selected == replace(current, model="gpt-new")
    assert selected.native_binding() and selected.native_binding() != current.native_binding()
    assert owner.current_api_config() is current


def test_actual_native_runtime_uses_session_provider_and_rebinds_history(tmp_path, monkeypatch):
    owner, current = configured_owner(tmp_path)
    monkeypatch.setattr(host, "PROVIDER_CONFIGURATION", owner)
    records = []

    def provider_receipt(settings, prompt, **kwargs):
        records.append((settings.llm_provider, settings.llm_model, settings.llm_api_key,
                        settings.llm_base_url, deepcopy(kwargs["native_messages"])))
        message = {"role": "assistant", "content": f"synthetic-response-{len(records)}"}
        return SimpleNamespace(text=message["content"], usage={}, reasoning={}, assistant_message=message, finish_reason="stop")

    monkeypatch.setattr(host, "request_llm_plan_with_metadata", provider_receipt)

    def new_gateway():
        gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
        monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
        binding = host._RuntimePlannerProviderTurnBinding()
        model = host._RuntimePlannerModel(binding)
        gateway.bind_runtime_planner(RuntimePlannerService(
            catalog=_TestRuntimePlannerCatalog(gateway), desktop=_TestRuntimePlannerDesktop(gateway),
            model=model, turn=binding,
        ))
        return gateway, model

    gateway, model = new_gateway()
    before = owner.config_path.read_bytes()
    common = {"sessionId": "session", "projectRoot": "", "_projectContextActive": False, "maxAgenticTurns": 2}
    first = gateway.runtime_message({**common, "clientTurnId": "one", "message": "original-provider-message"})
    assert first["plan"]["nextStep"] == "done"
    second = gateway.runtime_message({**common, "clientTurnId": "two", "provider": "deepseek", "model": "deepseek-chat",
                                      "message": "selected-provider-message", "history": [{"role": "agent", "text": "restored-public-history"}]})
    assert second["plan"]["nextStep"] == "done"
    assert records[0][:4] == ("openai", "model-a", current.api_key, current.base_url)
    assert records[1][:4] == ("deepseek", "deepseek-chat", "saved-deepseek-key", "https://deepseek.example/v1")
    assert any(message.get("content") == "restored-public-history" for message in records[1][4])
    assert not any(message.get("content") == "original-provider-message" for message in records[1][4])
    assert model.shutdown()["ok"]

    restarted, restarted_model = new_gateway()
    third = restarted.runtime_message({**common, "clientTurnId": "three", "provider": "deepseek", "model": "deepseek-chat",
                                       "message": "after-restart"})
    assert third["plan"]["nextStep"] == "done"
    assert records[2][:4] == records[1][:4]
    assert any(message.get("content") == "selected-provider-message" for message in records[2][4])
    assert not any(message.get("content") == "original-provider-message" for message in records[2][4])
    assert restarted_model.shutdown()["ok"]
    assert owner.current_api_config() is current
    assert owner.config_path.read_bytes() == before
