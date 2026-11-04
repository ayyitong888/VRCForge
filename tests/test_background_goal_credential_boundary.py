import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_gateway import AgentGateway, redact_background_goal_persistence, redact_runtime_tool_result
from credential_redaction import redact_credential_text


@pytest.mark.parametrize("key", ["password", "client_secret", "credential", "Client-Secret", "credentials"])
def test_structured_background_credentials_are_removed(key):
    value = {key: "PERSISTENCE_LEAK_SENTINEL", "tokenCount": 42, "credentialCount": 3}
    result = redact_background_goal_persistence(value)
    assert "PERSISTENCE_LEAK_SENTINEL" not in json.dumps(result)
    assert result["tokenCount"] == 42 and result["credentialCount"] == 3
    assert redact_runtime_tool_result(value) == value


@pytest.mark.parametrize("text", [
    'password="alpha beta gamma"',
    "client_secret='alpha beta gamma'",
    '{"credential": "alpha beta gamma", "tokenCount": 42}',
    r'password="alpha \"beta\" gamma"',
    r'{\"apiKey\":\"alpha beta gamma\",\"tokenCount\":42}',
    r'prefix {\"password\":\"alpha \\\"beta\\\" gamma\"} suffix',
    '"ordinary prose" ' + r'{\"password\":\"alpha beta gamma\"}',
    json.dumps(json.dumps({"password": "alpha beta gamma", "tokenCount": 42})),
    json.dumps("first line\r\npassword=alpha beta gamma\r\n"),
])
def test_quoted_credentials_are_completely_removed(text):
    for redact in (redact_credential_text, redact_background_goal_persistence):
        result = redact(text)
        assert all(piece not in result for piece in ("alpha", "beta", "gamma")), result


def test_ordinary_metrics_and_identifiers_are_not_credentials():
    value = 'tokenCount=42 credentialLength=3 passwordLimit=10 thread-sk-identifier-long-enough'
    assert redact_credential_text(value) == value


@pytest.mark.parametrize("key", ["control_token", "controltoken", "approvalToken", "providerPassword", "tokenValue"])
def test_text_and_structured_fields_share_credential_classification(key):
    secret = "ASSIGNMENT_SECRET_SENTINEL"
    for text in (json.dumps({key: secret}), f'{key}="{secret}"', f"https://example.test/?{key}={secret}&tokenCount=42"):
        assert secret not in redact_credential_text(text)
    assert secret not in json.dumps(redact_background_goal_persistence({key: secret}))


@pytest.mark.parametrize("text", [
    'foo=bar password="alpha beta gamma" tokenCount=42',
    'foo="bar" control_token=alpha beta gamma; tokenCount=42',
    '{"foo":{"password":"alpha beta gamma"},"tokenCount":42}',
    'foo="password=alpha beta gamma"; tokenCount=42',
    'api key="alpha beta gamma"; tokenCount=42',
    '{"credentials":{"first":"alpha","nested":{"second":"beta gamma"}},"tokenCount":42}',
])
def test_nonsecret_assignment_cannot_hide_following_secret(text):
    result = redact_credential_text(text)
    assert all(piece not in result for piece in ("alpha", "beta", "gamma")), result
    assert "42" in result


def test_actual_goal_delivery_persists_only_scrubbed_response(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    goal = gateway.goal.create_agent_goal({"title": "credential boundary", "chatId": "chat-test",
                                          "wakeAt": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})["goal"]
    delivery = gateway.goal.wake_agent_goal(goal["goalId"])["delivery"]
    gateway.goal.begin_agent_goal_delivery(delivery["deliveryId"], {"clientTurnId": delivery["clientTurnId"]})
    response = {"ok": True, "turnId": "done", "plan": {"nextStep": "done", "reply": 'password="alpha beta gamma"'},
                "steps": [{"result": {"password": "PERSISTENCE_LEAK_SENTINEL", "client_secret": "CLIENT_SECRET_SENTINEL", "credential": "CREDENTIAL_SENTINEL"}}]}
    gateway.goal.complete_agent_goal_delivery(delivery["deliveryId"], response)
    files = list(tmp_path.rglob("*.json")) + list(tmp_path.rglob("*.jsonl"))
    assert any("agent-goal" in str(path) for path in files)
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "done" in persisted
    for secret in ("PERSISTENCE_LEAK_SENTINEL", "CLIENT_SECRET_SENTINEL", "CREDENTIAL_SENTINEL", "alpha", "beta", "gamma"):
        assert secret not in persisted
    assert redact_runtime_tool_result(response) == response
