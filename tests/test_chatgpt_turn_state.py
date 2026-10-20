"""Offline turn-scoped routing through the production per-request wrapper."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from agent_runtime_native_turn import NativeRuntimeTurn
from chatgpt_integration.adapter import ChatGPTResponsesAdapter
from native_request_composition import NativePlannerRequest, copy_native_request
from tests.test_provider_protocol_clients import _native_chat_settings
from vrchat_blendshape_agent import request_llm_plan_with_metadata


def owner(session="session", turn="turn"):
    return NativeRuntimeTurn(None, None, session, turn, "", "task", [], None, None, None)


def wire(monkeypatch, returned_headers):
    sent, clients, events = [], [], []
    headers = iter(returned_headers)

    def create_client(self):
        def handle(request):
            sent.append({"headers": dict(request.headers), "body": json.loads(request.content)})
            event = {"type": "response.completed", "response": {
                "id": "resp_fixture", "status": "completed", "output": [{
                    "type": "message", "id": "msg_fixture", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                }], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }}
            return httpx.Response(200, headers={"content-type": "text/event-stream", **next(headers)},
                                  content="data: " + json.dumps(event) + "\n\n")
        client = OpenAI(api_key="offline", http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        clients.append(client)
        return client

    monkeypatch.setattr(ChatGPTResponsesAdapter, "_client", create_client)

    def request(state, session="session", account="account"):
        settings = replace(_native_chat_settings("responses"), llm_provider="chatgpt",
                           llm_api_key=account, llm_base_url="https://api.openai.com/v1")
        return request_llm_plan_with_metadata(
            settings, "", session_id=session, native_turn_state=state,
            native_messages=[{"role": "user", "content": "task"}], native_tools=[],
            stream_activity_callback=events.append,
        )
    return request, sent, clients, events


def test_real_wrapper_recreates_adapters_but_replays_first_header(monkeypatch):
    request, sent, clients, events = wire(monkeypatch, [
        {"x-codex-turn-state": "first-token"}, {"x-codex-turn-state": "later-token"}, {},
    ])
    state = {}
    for _ in range(3):
        request(state)
    assert "x-codex-turn-state" not in sent[0]["headers"]
    assert [r["headers"].get("x-codex-turn-state") for r in sent[1:]] == ["first-token"] * 2
    assert len({id(client) for client in clients}) == 3
    assert all(client.is_closed() for client in clients)
    assert "first-token" not in json.dumps(events)
    assert all("first-token" not in json.dumps(r["body"]) for r in sent)


def test_new_turn_and_interleaved_sessions_do_not_share_headers(monkeypatch):
    request, sent, _, _ = wire(monkeypatch, [
        {"x-codex-turn-state": "A"}, {"x-codex-turn-state": "B"}, {}, {}, {},
    ])
    a, b, next_turn = owner(), owner("other"), owner(turn="next")
    request(a.provider_turn_state)
    request(b.provider_turn_state, session="other")
    request(a.provider_turn_state)
    request(b.provider_turn_state, session="other")
    request(next_turn.provider_turn_state)
    assert [r["headers"].get("x-codex-turn-state") for r in sent] == [None, None, "A", "B", None]


@pytest.mark.parametrize("change", [{"account": "other"}, {"session": "other"}])
def test_changed_owner_binding_discards_previous_token(monkeypatch, change):
    request, sent, _, _ = wire(monkeypatch, [{"x-codex-turn-state": "A"}, {}, {}])
    state = {}
    request(state)
    request(state, **change)
    request(state)
    assert all("x-codex-turn-state" not in r["headers"] for r in sent)


def test_missing_header_can_be_learned_later(monkeypatch):
    request, sent, _, _ = wire(monkeypatch, [{}, {"x-codex-turn-state": "A"}, {}])
    state = {}
    for _ in range(3):
        request(state)
    assert [r["headers"].get("x-codex-turn-state") for r in sent] == [None, None, "A"]


def test_request_copy_retains_private_state_without_serializing():
    state = {"token": "private-token"}
    request = NativePlannerRequest({"messages": [], "tools": []}, provider_turn_state=state)
    copied = copy_native_request(request, {"messages": [{"role": "user", "content": "task"}]})
    assert copied.get("_providerTurnState") is state
    assert "private-token" not in json.dumps(copied)
    assert "private-token" not in repr(copied)


def test_concurrent_turn_owners_remain_independent(monkeypatch):
    request, _, _, _ = wire(monkeypatch, [
        {"x-codex-turn-state": "A"}, {"x-codex-turn-state": "B"}, {}, {},
    ])
    a, b = owner(), owner("other")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request, a.provider_turn_state),
                   pool.submit(request, b.provider_turn_state, "other")]
        for future in futures:
            future.result()
    assert {a.provider_turn_state["token"], b.provider_turn_state["token"]} == {"A", "B"}
    assert a.provider_turn_state is not b.provider_turn_state
    assert a.provider_turn_state["binding"] != b.provider_turn_state["binding"]


def test_real_planner_attaches_owner_without_persisting_it():
    from tests.test_native_context_compaction import fixture, plan
    state, planner, model, turn = fixture(prior=False)
    turn.provider_turn_state["token"] = "owner-private-token"
    plan(planner, turn)
    assert model.requests[-1].get("_providerTurnState") is turn.provider_turn_state
    assert "owner-private-token" not in json.dumps(model.requests[-1])
    assert "owner-private-token" not in json.dumps(turn.snapshot())


def test_dashboard_provider_worker_passes_same_state_to_real_wrapper(monkeypatch):
    import dashboard_server as dashboard
    from tests.test_dashboard_server import _test_runtime_provider_config
    request, sent, clients, _ = wire(monkeypatch, [{"x-codex-turn-state": "A"}, {}])
    config = replace(_test_runtime_provider_config(), provider="chatgpt", api_key="account",
                     base_url="https://api.openai.com/v1", api_type="responses")
    model = dashboard._RuntimePlannerModel(SimpleNamespace(current_config=lambda: config))
    sessions = dashboard.AGENT_GATEWAY.runtime_sessions
    previous_context = sessions.stream_context()
    sessions.set_stream_context({"sessionId": "session", "clientTurnId": "offline-header-turn"})
    state = owner().provider_turn_state
    native = NativePlannerRequest({"instructions": "", "messages": [{"role": "user", "content": "task"}],
                                   "tools": []}, provider_turn_state=state)
    try:
        for _ in range(2):
            model.plan_native(native)
    finally:
        sessions.set_stream_context(previous_context)
        assert model.shutdown()["ok"]
    assert [r["headers"].get("x-codex-turn-state") for r in sent] == [None, "A"]
    assert all(client.is_closed() for client in clients)
