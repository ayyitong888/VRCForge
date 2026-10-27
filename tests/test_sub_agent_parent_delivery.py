"""Exercise a real parent runtime, durable read-only child, and parent continuation."""
from __future__ import annotations

import json
import threading
from copy import deepcopy

import dashboard_server as server
from background_goal_runtime import RuntimeLaneBudget
from runtime_planner_service import PlannerModelResult
from sub_agent_collaboration_service import (
    SubAgentCollaborationPorts,
    SubAgentCollaborationService,
)
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from tests.test_native_runtime_gateway import call, setup_gateway


class ParentChildFixtureModel:
    """Route deterministic fixture replies through the real native runtime."""

    def __init__(self, parent_returned: threading.Event) -> None:
        self.parent_returned = parent_returned
        self.requests: list[dict] = []
        self.parent_initial_count = 0
        self.child_count = 0

    @staticmethod
    def _messages(request):
        return request["messages"] if isinstance(request, dict) else request.messages

    @staticmethod
    def _first_user_text(messages):
        message = next((item for item in messages if item.get("role") == "user"), {})
        return str(message.get("content") or "")

    @staticmethod
    def _instructions(request):
        return str(request.get("instructions", "") if isinstance(request, dict)
                   else getattr(request, "instructions", ""))

    def plan_native(self, request):
        snapshot = deepcopy(request)
        self.requests.append(snapshot)
        messages = self._messages(request)
        wire = json.dumps(messages, ensure_ascii=False)
        child_turn = self._first_user_text(messages).startswith("Inspect both scan fixture files")
        if child_turn:
            assert self.parent_returned.wait(5), "parent runtime did not yield before child inference"
            index = self.child_count
            self.child_count += 1
            if index == 0:
                receipt = call("child-read-1", "vrcforge_read_text_file", {"path": "scan-a.txt"})
            elif index == 1:
                receipt = call("child-read-2", "vrcforge_read_text_file", {"path": "scan-b.txt"})
            elif index == 2:
                evidence = []
                for message in messages:
                    if message.get("role") == "tool":
                        for observation in json.loads(message["content"]).get("observations", []):
                            if observation.get("actionId") and observation.get("outcome", {}).get("status") == "ok":
                                evidence.append(observation["actionId"])
                receipt = call("child-final", "vrcforge_runtime_action", {
                    "action": "reply",
                    "reply": "SPECIALIST_REPORT: both independent scans support the same finding.",
                    "completion_claim": {"satisfied": True, "evidence_action_ids": evidence},
                })
            else:
                raise AssertionError("child exceeded its expected two reads and final response")
        else:
            has_child_observation = "SPECIALIST_REPORT:" in wire
            if has_child_observation:
                receipt = {"role": "assistant", "content": "Parent final: SPECIALIST_REPORT confirms the two scans agree."}
            elif self.parent_initial_count == 0:
                self.parent_initial_count += 1
                receipt = call("parent-delegate", "vrcforge_delegate_subagent", {
                    "role": "llm_readonly",
                    "task": "Inspect both scan fixture files and summarize their shared finding.",
                    "parentChatId": "parent-chat-fixture",
                    "projectPath": "",
                    "model": "fixture-child-model",
                    "systemPrompt": "Child-only instructions: compare both scan results.",
                    "maxSteps": 4,
                    "selectedMaterials": [{"content": {"text": "selected context survives"}}],
                })
            else:
                receipt = {"role": "assistant", "content": "Delegation is running; I will wait for its result."}
        return PlannerModelResult(
            "",
            assistant_message=receipt,
            finish_reason="tool_calls" if receipt.get("tool_calls") else "stop",
        )


def _serialized_message_bytes(request) -> int:
    messages = request["messages"] if isinstance(request, dict) else request.messages
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_parent_runtime_receives_child_summary_while_full_scan_stays_in_sidecar(tmp_path, monkeypatch):
    gateway, _original_model, invoked = setup_gateway(tmp_path, [])
    large_scan = "RAW_CHILD_SCAN_SENTINEL_" + ("detailed scan row; " * 12000)
    gateway.register_tool(
        "vrcforge_read_text_file",
        "When to use: read fixture content. When NOT to use: write files.",
        "plan/preview",
        lambda params: invoked.append(dict(params)) or {"text": large_scan, "path": params.get("path")},
    )

    parent_returned = threading.Event()
    model = ParentChildFixtureModel(parent_returned)
    gateway.runtime_planner._model = model
    continuation_results = []

    def resume_parent(event):
        result = gateway.resume_runtime_task_after_sub_agent(event)
        continuation_results.append(result)
        return result

    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "sub-agents",
        gateway=gateway,
        lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles,
        build_handlers=build_sub_agent_role_handlers,
        task_finished=resume_parent,
    ))
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    gateway.register_tool(
        "vrcforge_delegate_subagent",
        "When to use: delegate one bounded read-only review. When NOT to use: delegate writes.",
        "plan/preview",
        server.delegate_sub_agent_runtime,
    )

    try:
        parent_result = gateway.runtime_message({
            "message": "Please use the read-only specialist and return its finding.",
            "sessionId": "fixture-parent-session",
            "clientTurnId": "fixture-parent-turn",
            "projectRoot": str(tmp_path),
            "model": "fixture-parent-model",
            "maxAgenticTurns": 5,
        })
        parent_returned.set()

        tasks = service.list_tasks(include_events=True)["tasks"]
        assert len(tasks) == 1, tasks
        task_id = tasks[0]["id"]
        worker = service._registry._threads[task_id]
        worker.join(8)
        assert not worker.is_alive(), "child or parent continuation did not finish"

        task = service.get_task(task_id, include_events=True)["task"]
        assert task["status"] == "completed", task.get("error")
        assert task["parentContinuationStatus"] == "delivered"
        assert task["childSessionId"] != "fixture-parent-session"
        assert [item["path"] for item in invoked] == ["scan-a.txt", "scan-b.txt"], (task, model.requests)

        sidecar_path = tmp_path / "sub-agents" / "results" / f"{task_id}.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert "RAW_CHILD_SCAN_SENTINEL_" in json.dumps(sidecar, ensure_ascii=False)
        assert len(json.dumps(sidecar, ensure_ascii=False).encode("utf-8")) > 100_000

        parent_requests = [request for request in model.requests
                           if model._first_user_text(model._messages(request)).startswith("Please use the read-only specialist")]
        child_requests = [request for request in model.requests
                          if model._first_user_text(model._messages(request)).startswith("Inspect both scan fixture files")]
        assert len(parent_requests) >= 2
        assert len(child_requests) == 3
        parent_initial_bytes = _serialized_message_bytes(parent_requests[0])
        parent_final_bytes = _serialized_message_bytes(parent_requests[-1])
        parent_added_bytes = parent_final_bytes - parent_initial_bytes
        assert parent_added_bytes > 0
        assert parent_added_bytes < len(large_scan.encode("utf-8"))
        assert "RAW_CHILD_SCAN_SENTINEL_" not in json.dumps(parent_requests, ensure_ascii=False)
        assert "SPECIALIST_REPORT:" in json.dumps(parent_requests[-1], ensure_ascii=False)

        parent_instructions = json.dumps([model._instructions(item) for item in parent_requests], ensure_ascii=False)
        child_instructions = json.dumps([model._instructions(item) for item in child_requests], ensure_ascii=False)
        assert "Child-only instructions" not in parent_instructions
        assert "Child-only instructions" in child_instructions
        assert continuation_results and continuation_results[0]
        assert "Parent final: SPECIALIST_REPORT confirms the two scans agree." in json.dumps(
            continuation_results[0], ensure_ascii=False
        )
    finally:
        parent_returned.set()
        service.shutdown(timeout_seconds=8)
