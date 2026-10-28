from __future__ import annotations

import json
import inspect
import time
from contextlib import nullcontext

import dashboard_server as server
from background_goal_runtime import RuntimeLaneBudget
from fastapi.testclient import TestClient
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata
from sub_agent_collaboration_service import (
    SubAgentCollaborationPorts,
    SubAgentCollaborationService,
    SubAgentExecutionPorts,
)
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from tests.test_native_runtime_gateway import call, setup_gateway


class RoutedTurn:
    def bind(self, request):
        provider = str(request.get("provider") or "fixture-provider")
        model = str(request.get("model") or "fixture-model")
        return nullcontext(PlannerTurnMetadata(
            native_binding=f"{provider}:{model}", provider=provider, model=model,
        ))


def _build_service(gateway, artifact_dir, task_finished):
    roles = build_sub_agent_roles()
    return SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=artifact_dir,
        gateway=SubAgentExecutionPorts(
            execute=gateway.runtime_skills.execute,
            run_sub_agent_llm=gateway.run_sub_agent_llm,
        ),
        lane_budget=RuntimeLaneBudget(),
        build_roles=lambda: roles,
        build_handlers=lambda ports: build_sub_agent_role_handlers(ports, roles),
        task_finished=task_finished,
    ))


def _wait_completed(service, task_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = service.get_task(task_id)["task"]
        if task["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.01)
    raise AssertionError(f"sub-agent task {task_id} did not complete")


def test_dashboard_composition_binds_both_child_execution_capabilities():
    handler = server.SUB_AGENT_COLLABORATION._registry.handlers["llm_readonly"]
    execution_ports = inspect.getclosurevars(handler).nonlocals["gateway"]

    # Read the actual composition root's bound handler closure. Rebuilding a
    # correct fixture service below must not mask a missing production port.
    assert callable(execution_ports.execute)
    assert callable(execution_ports.run_sub_agent_llm)
    assert execution_ports.execute.__self__ is server.AGENT_GATEWAY.runtime_skills
    assert execution_ports.run_sub_agent_llm.__self__ is server.AGENT_GATEWAY


def test_dashboard_resume_post_keeps_production_child_runtime_and_drops_old_seed(tmp_path, monkeypatch):
    gateway, _model, _invoked = setup_gateway(tmp_path, [
        {"role": "assistant", "content": "Initial child report."},
        {"role": "assistant", "content": "Resumed child report."},
    ])
    gateway.runtime_planner._turn = RoutedTurn()
    events = []
    service = _build_service(gateway, tmp_path / "sub-agents", events.append)
    original_service = server.SUB_AGENT_COLLABORATION
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    try:
        source = service.create_task(
            role="llm_readonly", task="Original child task", display_name="Manuka",
            parent_session_id="parent-session",
            params={
                "model": "fixture-child-model",
                "provider": "fixture-provider",
                "_taskSeed": {"sessionId": "parent-session", "clientTurnId": "old-parent-turn"},
            },
        )["task"]
        source_task = _wait_completed(service, source["id"])
        assert source_task["status"] == "completed"
        assert source_task["canResume"] is True
        deadline = time.monotonic() + 2
        while len(events) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(events) == 1
        assert events[0]["taskSeed"]["clientTurnId"] == "old-parent-turn"

        with TestClient(server.app) as client:
            response = client.post(
                f"/api/app/sub-agents/{source['id']}/resume",
                json={"task": "Continue from the saved child findings."},
            )
            assert response.status_code == 200, response.text
            resumed = response.json()["task"]
            resumed_task = _wait_completed(service, resumed["id"])

        assert resumed_task["status"] == "completed"
        assert resumed_task["resumeOf"] == source["id"]
        assert resumed_task["childSessionId"] == source_task["childSessionId"]
        assert "_taskSeed" not in service._registry._tasks[resumed["id"]].params
        deadline = time.monotonic() + 2
        while len(events) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(events) == 1, "HTTP resume must not replay the previous parent continuation seed"
    finally:
        service.shutdown(timeout_seconds=5)
        server.SUB_AGENT_COLLABORATION = original_service


class NativeResumeToolModel:
    def __init__(self, source_task_id):
        self.source_task_id = source_task_id
        self.resume_tool_called = False
        self.requests = []

    @staticmethod
    def _messages(request):
        return request["messages"] if isinstance(request, dict) else request.messages

    def plan(self, _prompt):
        raise AssertionError("native fixture should not use the legacy JSON planner")

    def plan_native(self, request):
        self.requests.append(request)
        messages = self._messages(request)
        wire = json.dumps(messages, ensure_ascii=False)
        if "CHILD_RESUME_TASK_SENTINEL" in wire:
            receipt = {"role": "assistant", "content": "Resumed child report from saved history."}
        elif "PARENT_RESUME_TOOL_SENTINEL" in wire and not self.resume_tool_called:
            self.resume_tool_called = True
            receipt = call("native-resume-call", "vrcforge_resume_subagent", {
                "taskId": self.source_task_id,
                "task": "CHILD_RESUME_TASK_SENTINEL continue the same child.",
            })
        else:
            receipt = {"role": "assistant", "content": "Parent received the resumed child report."}
        return PlannerModelResult(
            "", assistant_message=receipt,
            finish_reason="tool_calls" if receipt.get("tool_calls") else "stop",
        )


def test_native_resume_tool_uses_trusted_parent_seed_and_delivers_continuation(tmp_path, monkeypatch):
    gateway, _initial_model, _invoked = setup_gateway(tmp_path, [
        {"role": "assistant", "content": "Initial child report."},
    ])
    gateway.runtime_planner._turn = RoutedTurn()
    events = []
    continuation_results = []

    def finish_task(event):
        events.append(event)
        continuation_results.append(gateway.resume_runtime_task_after_sub_agent(event))

    service = _build_service(gateway, tmp_path / "sub-agents", finish_task)
    source = service.create_task(
        role="llm_readonly", task="Original child task", display_name="Manuka",
        parent_session_id="parent-session",
        params={"model": "fixture-child-model", "provider": "fixture-provider"},
    )["task"]
    source_task = _wait_completed(service, source["id"])
    assert source_task["status"] == "completed"

    model = NativeResumeToolModel(source["id"])
    gateway.runtime_planner._model = model
    gateway.register_tool(
        "vrcforge_resume_subagent",
        "When to use: resume an idle child with a new task. When NOT to use: retry a new child.",
        "plan/preview",
        server.resume_sub_agent_runtime,
    )
    old_gateway, old_service = server.AGENT_GATEWAY, server.SUB_AGENT_COLLABORATION
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    try:
        parent_result = gateway.runtime_message({
            "message": "PARENT_RESUME_TOOL_SENTINEL resume the child with its new question.",
            "sessionId": "parent-session",
            "clientTurnId": "parent-resume-turn",
            "projectRoot": str(tmp_path),
            "model": "fixture-parent-model",
            "maxAgenticTurns": 4,
        })
        assert parent_result["ok"] is True
        deadline = time.monotonic() + 6
        resumed_task = None
        while time.monotonic() < deadline:
            tasks = service.list_tasks(include_events=True)["tasks"]
            resumed_task = next((item for item in tasks if item.get("resumeOf") == source["id"]), None)
            if resumed_task and resumed_task["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        assert resumed_task is not None, "native resume tool did not create a resumed task"
        assert resumed_task["status"] == "completed", resumed_task
        assert resumed_task["childSessionId"] == source_task["childSessionId"]
        deadline = time.monotonic() + 3
        while not continuation_results and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(events) == 1
        seed = events[0]["taskSeed"]
        assert seed["sessionId"] == "parent-session"
        assert seed["requestedTool"] == "vrcforge_resume_subagent"
        assert seed["requestedArguments"]["taskId"] == source["id"]
        assert continuation_results and continuation_results[0]
        assert continuation_results[0]["sessionId"] == "parent-session"
        assert continuation_results[0]["plan"]["nextStep"] == "done"
        durable = service.get_task(resumed_task["id"])["task"]
        assert durable["parentContinuationStatus"] == "delivered"
    finally:
        service.shutdown(timeout_seconds=6)
        server.AGENT_GATEWAY = old_gateway
        server.SUB_AGENT_COLLABORATION = old_service
