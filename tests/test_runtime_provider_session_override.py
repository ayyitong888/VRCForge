"""Session choices must reach the real runtime Provider boundary without global writes."""
import json
from copy import deepcopy
from dataclasses import replace
from vrchat_blendshape_agent import LlmPlanResponse

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


def test_cross_provider_selection_uses_its_saved_key_and_endpoint(tmp_path):
    owner, current = configured_owner(tmp_path)
    selected = owner.resolve_session_config(provider="deepseek", model="deepseek-chat")
    assert selected.api_key == "saved-deepseek-key"
    assert selected.base_url == "https://old-deepseek.example/v1"
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


def test_actual_native_runtime_uses_session_provider_and_retains_project_history(tmp_path, monkeypatch):
    owner, current = configured_owner(tmp_path)
    monkeypatch.setattr(host, "PROVIDER_CONFIGURATION", owner)
    records = []

    def provider_receipt(settings, prompt, **kwargs):
        records.append((settings.llm_provider, settings.llm_model, settings.llm_api_key,
                        settings.llm_base_url, deepcopy(kwargs["native_messages"])))
        message = {"role": "assistant", "content": f"synthetic-response-{len(records)}"}
        return LlmPlanResponse(text=message["content"], usage={}, reasoning={}, assistant_message=message, finish_reason="stop", model=settings.llm_model)

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
    assert first["plan"]["nextStep"] == "done", first["plan"].get("plannerFailure")
    second = gateway.runtime_message({**common, "clientTurnId": "two", "provider": "deepseek", "model": "deepseek-chat",
                                      "message": "selected-provider-message", "history": [{"role": "agent", "text": "restored-public-history"}]})
    assert second["plan"]["nextStep"] == "done"
    assert records[0][:4] == ("openai", "model-a", current.api_key, current.base_url)
    assert records[1][:4] == ("deepseek", "deepseek-chat", "saved-deepseek-key", "https://old-deepseek.example/v1")
    assert any("original-provider-message" in message.get("content", "") for message in records[1][4])
    assert model.shutdown()["ok"]

    restarted, restarted_model = new_gateway()
    third = restarted.runtime_message({**common, "clientTurnId": "three", "provider": "deepseek", "model": "deepseek-chat",
                                       "message": "after-restart"})
    assert third["plan"]["nextStep"] == "done"
    assert records[2][:4] == records[1][:4]
    assert any("selected-provider-message" in message.get("content", "") for message in records[2][4])
    assert any("original-provider-message" in message.get("content", "") for message in records[2][4])
    assert restarted_model.shutdown()["ok"]
    assert owner.current_api_config() is current
    assert owner.config_path.read_bytes() == before

@pytest.mark.parametrize("suspension", ["question", "approval"])
@pytest.mark.parametrize("change_default,override", [(True, False), (False, True), (True, True)])
def test_waiting_callback_selects_first_provider_request(tmp_path, monkeypatch, suspension, change_default, override):
    from threading import Event
    from tests.test_native_runtime_continuations import _write_gateway, _run
    from tests.test_native_runtime_gateway import call

    owner, original = configured_owner(tmp_path)
    monkeypatch.setattr(host, "PROVIDER_CONFIGURATION", owner)
    gateway, _ = _write_gateway(tmp_path, [], [])
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    binding = host._RuntimePlannerProviderTurnBinding()
    model = host._RuntimePlannerModel(binding)
    gateway.runtime_planner._model = model
    gateway.runtime_planner._turn = binding
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.",
                          "plan/preview", gateway.create_runtime_question)
    replies = ([call("question", "vrcforge_ask_user", {"question": "Which one?"})] if suspension == "question"
               else [call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
                     call("write", "fixture_write", {"value": "x"})])
    replies.append({"role": "assistant", "content": "continued"})
    samples = []

    def record(settings, prompt, **kwargs):
        samples.append((settings.llm_provider, settings.llm_model, deepcopy(kwargs["native_messages"])))
        reply = replies.pop(0)
        return LlmPlanResponse(text=reply.get("content") or "", reasoning={}, assistant_message=reply,
                               finish_reason="tool_calls" if reply.get("tool_calls") else "stop", model=settings.llm_model)

    monkeypatch.setattr(host, "request_llm_plan_with_metadata", record)
    finished = Event()
    gateway._runtime_turn_completed = lambda _: finished.set()
    initial_model = "explicit-model" if override else original.model
    first = _run(gateway, tmp_path, provider=original.provider, model=initial_model)
    assert samples[-1][:2] == (original.provider, initial_model)
    old_count = len(samples)
    if change_default:
        selected = owner.resolve_session_config(provider="deepseek", model="deepseek-new")
        owner.save_api_config(selected)
    if suspension == "question":
        question, = gateway.questions.list(session_id="native-continuation-session")["questions"]
        gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session",
                                 "projectRoot": str(tmp_path), "answer": "Second choice"})
        assert finished.wait(5)
    else:
        approval = gateway.approval_transactions.reject(first["approval_id"])["approval"]
        # Request a revised plan. A plain rejection may terminate without a model call.
        gateway.resume_runtime_task_after_approval(approval, {}, revision_requested=True)
    expected = ("deepseek", "deepseek-new") if change_default else (original.provider, initial_model)
    assert [item[:2] for item in samples[old_count:]] == [expected]
    from agent_native_history_store import project_native_binding
    from agent_runtime_session_state import NATIVE_RUNTIME_CONTEXT_PREFIX

    call_id = "write" if suspension == "approval" else "question"
    stored = gateway.runtime_sessions.native_conversation("native-continuation-session",
                                                          binding=project_native_binding(str(tmp_path)))
    original_call, = [c for m in stored["messages"] for c in m.get("tool_calls", []) if c["id"] == call_id]
    receipt, = [m for m in stored["messages"] if m.get("role") == "tool" and m["tool_call_id"] == call_id]
    wire = samples[-1][2]
    if change_default:
        groups = []
        for message in wire:
            text = message.get("content")
            if message.get("role") != "user" or not isinstance(text, str) or not text.startswith(NATIVE_RUNTIME_CONTEXT_PREFIX):
                continue
            context = json.loads(text[len(NATIVE_RUNTIME_CONTEXT_PREFIX):])
            if any(row.get("tool_call_id") == call_id for row in context.get("priorToolEvents", [])):
                assert context["source"] == "runtime"
                groups.append(context["priorToolEvents"])
        wire, = groups
    proposals = [call for message in wire if message["role"] == "assistant"
                 for call in message.get("tool_calls", []) if call["id"] == call_id]
    results = [message for message in wire if message["role"] == "tool" and message["tool_call_id"] == call_id]
    assert proposals == [original_call] and results == [receipt]
    assert json.loads(original_call["function"]["arguments"])["tool"] == (
        "fixture_write" if suspension == "approval" else "vrcforge_ask_user")
    assert json.loads(receipt["content"])["status"] == ("needs_user_action" if suspension == "approval" else "completed")
    assert model.shutdown()["ok"]
