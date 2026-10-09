"""Offline regression for conversation-scoped OAuth cache routing."""

from copy import deepcopy
from uuid import uuid4

from chatgpt_integration.adapter import ChatGPTResponsesAdapter, CHATGPT_RESPONSES_BASE_URL
from tests.test_chatgpt_responses_adapter import FakeClient, completed


def capture(session_id, account_id="fixture-account", token="fixture-token", messages=None):
    sent = []
    client = FakeClient([])
    client.responses.create = lambda **payload: sent.append(deepcopy(payload)) or iter([
        completed(output_text="ok", output=[{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "ok"}],
        }])
    ])
    adapter = ChatGPTResponsesAdapter(
        api_key=account_id, base_url=CHATGPT_RESPONSES_BASE_URL,
        session_id=session_id, access_token_provider=lambda _: token,
        client_factory=lambda **kwargs: client,
    )
    adapter.send_native_request(model="fixture-model", messages=messages or [
        {"role": "user", "content": "hello"}], tools=[])
    assert client.closed == 1
    return sent[0]


def test_headers_survive_new_adapter_followup_and_token_refresh():
    session = str(uuid4())
    first = capture(session)
    followup = capture(session, token="refreshed-token", messages=[
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "continue"},
    ])
    assert first["extra_headers"] == followup["extra_headers"]
    headers = first["extra_headers"]
    assert set(headers) == {"session-id", "thread-id", "x-client-request-id"}
    assert len(set(headers.values())) == 1
    assert session not in headers["session-id"]
    assert "fixture-account" not in headers["session-id"]


def test_accounts_and_conversations_have_distinct_routing():
    session = str(uuid4())
    first = capture(session)["extra_headers"]
    assert first != capture(str(uuid4()))["extra_headers"]
    assert first != capture(session, account_id="other-account")["extra_headers"]


def test_headers_do_not_change_request_body_or_history():
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": "hello"}]
    before = deepcopy(messages)
    original = capture("", messages=messages)
    routed = capture(str(uuid4()), messages=messages)
    assert "extra_headers" not in original
    routed.pop("extra_headers")
    assert original == routed
    assert messages == before


def test_runtime_wrapper_forwards_session_to_oauth(monkeypatch):
    from dataclasses import replace
    from tests.test_provider_protocol_clients import _native_chat_settings
    from vrchat_blendshape_agent import request_llm_plan_with_metadata
    captured = []
    def stream(self, payload, **kwargs):
        captured.append(self._session_headers)
        return {"status": "completed", "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}]}]}
    monkeypatch.setattr(ChatGPTResponsesAdapter, "_stream_response", stream)
    settings = replace(_native_chat_settings("responses"), llm_provider="chatgpt",
                       llm_api_key="fixture-account", llm_base_url=CHATGPT_RESPONSES_BASE_URL)
    session = str(uuid4())
    for _ in range(2):
        request_llm_plan_with_metadata(settings, "", session_id=session,
            native_messages=[{"role": "user", "content": "hello"}], native_tools=[])
    assert captured[0] == captured[1]
    assert captured[0]["session-id"]
