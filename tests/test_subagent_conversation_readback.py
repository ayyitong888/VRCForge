"""HTTP replay of a read-only child conversation from its durable runtime ledger."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import dashboard_server as server
from agent_runtime_run_ledger import AgentRuntimeRunLedger
from background_goal_runtime import RuntimeLaneBudget
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from tests.test_native_runtime_gateway import call
from tests.test_sub_agent_runtime_integration import _child_gateway, _finish


def _await_task(service, task_id):
    thread = service._registry._threads[task_id]
    thread.join(5)
    assert not thread.is_alive(), f"child task {task_id} did not finish"
    task = service.get_task(task_id)["task"]
    assert task["status"] == "completed", task.get("error")
    return task


def _read_timeline(client, session_id, client_turn_id):
    response = client.get("/api/app/agent/runs/timeline", params={
        "sessionId": session_id,
        "clientTurnId": client_turn_id,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_http_child_conversation_readback_survives_resume_and_ledger_rebuild(tmp_path, monkeypatch):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("child-read", "vrcforge_read_text_file", {
            "path": "child-note.txt",
            "rawArgumentSentinel": "RAW_TOOL_ARGUMENT_SENTINEL",
        }),
        _finish,
        {"role": "assistant", "content": "The saved read supports the follow-up."},
    ])
    with gateway.runtime_planner.bind_turn({"model": "fixture-child-model"}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    gateway._runtime_session_state.begin_native_turn(
        "parent-session",
        binding=binding,
        turn_id="parent-turn",
        message="PRIVATE_PARENT_CONTEXT_SENTINEL",
    )
    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "children",
        gateway=gateway,
        lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles,
        build_handlers=build_sub_agent_role_handlers,
        task_finished=lambda _event: None,
    ))
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    try:
        with TestClient(server.app) as client:
            created_response = client.post("/api/app/sub-agents", json={
                "role": "llm_readonly",
                "task": "Read the selected note.",
                "parentChatId": "parent-chat",
                "parentSessionId": "parent-session",
                "projectPath": str(tmp_path),
                "model": "fixture-child-model",
                "systemPrompt": "Use the child history and return a concise finding.",
                "maxSteps": 4,
            })
            assert created_response.status_code == 200, created_response.text
            first_id = created_response.json()["task"]["id"]
            first_task = _await_task(service, first_id)
            child_session_id = first_task["childSessionId"]
            assert child_session_id != "parent-session"
            assert first_task["resumable"] is True

            first_timeline = _read_timeline(client, child_session_id, first_id)
            first_events = first_timeline["events"]
            first_kinds = [event["kind"] for event in first_events]
            assert "tool_call" in first_kinds
            assert "tool_result" in first_kinds
            assert "assistant" in first_kinds
            assert first_events.index(next(event for event in first_events if event["kind"] == "tool_call")) < \
                first_events.index(next(event for event in first_events if event["kind"] == "tool_result")) < \
                first_events.index(next(event for event in first_events if event["kind"] == "assistant"))
            assert first_timeline["run"]["sessionId"] == child_session_id
            assert first_timeline["run"]["clientTurnId"] == first_id

            calls_before_open = len(model.requests)
            tools_before_open = len(invoked)
            assert _read_timeline(client, child_session_id, first_id) == first_timeline
            assert len(model.requests) == calls_before_open
            assert len(invoked) == tools_before_open == 1

            resumed_response = client.post(f"/api/app/sub-agents/{first_id}/resume", json={
                "task": "Continue using the saved findings.",
            })
            assert resumed_response.status_code == 200, resumed_response.text
            resumed_id = resumed_response.json()["task"]["id"]
            resumed_task = _await_task(service, resumed_id)
            assert resumed_id != first_id
            assert resumed_task["resumeOf"] == first_id
            assert resumed_task["childSessionId"] == child_session_id

            resumed_request = json.dumps(model.requests[-1]["messages"])
            assert "Read the selected note." in resumed_request
            assert "Continue using the saved findings." in resumed_request
            assert "fixture-body" in resumed_request
            assert "PRIVATE_PARENT_CONTEXT_SENTINEL" not in json.dumps(model.requests)

            # The public route reads the same durable file through a fresh ledger instance.
            reopened_ledger = AgentRuntimeRunLedger(gateway.runtime_runs._ports)
            monkeypatch.setattr(server, "AGENT_GATEWAY", SimpleNamespace(runtime_runs=reopened_ledger))
            resumed_timeline = _read_timeline(client, child_session_id, resumed_id)
            assert resumed_timeline["run"]["sessionId"] == child_session_id
            assert resumed_timeline["run"]["clientTurnId"] == resumed_id
            assert any(event["kind"] == "assistant" for event in resumed_timeline["events"])
            assert _read_timeline(client, child_session_id, first_id) == first_timeline

            wrong_owner = _read_timeline(client, "parent-session", first_id)
            assert wrong_owner["run"] is None
            assert wrong_owner["events"] == []

            public_json = json.dumps({"first": first_timeline, "resumed": resumed_timeline})
            for private_value in (
                "PRIVATE_PARENT_CONTEXT_SENTINEL",
                "RAW_TOOL_ARGUMENT_SENTINEL",
                "private-fixture-replay",
                "reasoning_content",
                "arguments",
            ):
                assert private_value not in public_json
            assert len(model.requests) == calls_before_open + 1
            assert len(invoked) == tools_before_open
    finally:
        service.shutdown()
