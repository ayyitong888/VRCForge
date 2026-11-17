from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest

import agent_gateway as gateway_module
import dashboard_server as host
from tests.test_native_runtime_gateway import call, setup_gateway


@pytest.mark.parametrize("agent_relay", [False, True], ids=["human", "agent-relay"])
def test_reference_text_does_not_expand_relay_filesystem_scope(tmp_path, monkeypatch, agent_relay):
    workspace = tmp_path / "target-workspace"
    workspace.mkdir()
    reference = tmp_path / "reference-workspace"
    reference.mkdir()
    gateway, _, read_fixture = setup_gateway(tmp_path, [
        call("local-read", "vrcforge_read_text_file", {"path": str(workspace / "glossary.txt")}),
        {"role": "assistant", "content": "Glossary checked."},
    ])
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(host, "RUNTIME_QUEUE_PORT", gateway)
    monkeypatch.setattr(host, "TELEGRAM_SERVICE", Mock())
    monkeypatch.setattr(host, "EVENT_BUS", host.DashboardEventBus())
    monkeypatch.setattr(host, "_CROSS_SESSION_CONVERSATION_SERVICE", None)
    parse_scope = Mock(wraps=gateway_module.extract_explicit_local_roots)
    monkeypatch.setattr(gateway_module, "extract_explicit_local_roots", parse_scope)
    message = f"Review the glossary in this workspace. Reference folder: {reference}"
    turn_id = "agent-forward-scope-reference" if agent_relay else "human-scope-reference"
    queue = {}
    if agent_relay:
        entry = gateway.enqueue_runtime_followup({
            "sessionId": "target-chat", "runtimeSessionId": "target-session",
            "clientTurnId": turn_id, "message": message,
        })
        queue = {"followupQueueId": entry["queueId"], "followupLaneId": "target-chat"}
    request = host.AgentRuntimeMessageRequest(
        message=message, session_id="target-session", chatId="target-chat",
        clientTurnId=turn_id, projectPath=str(workspace), projectType="general", **queue,
    )
    result = asyncio.run(host.app_agent_runtime_message(request))
    assert read_fixture, result["plan"]
    actual = {Path(path) for path in read_fixture[0]["_generalAllowedRoots"]}
    expected = {workspace} if agent_relay else {workspace, reference}
    assert actual == expected
    if agent_relay:
        parse_scope.assert_not_called()
    else:
        parse_scope.assert_called_once_with(message)
