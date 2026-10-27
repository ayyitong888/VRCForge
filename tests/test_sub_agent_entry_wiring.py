"""Exercise the existing HTTP and parent-tool entry points for LLM children."""
from fastapi.testclient import TestClient

import dashboard_server as server
from background_goal_runtime import RuntimeLaneBudget
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService
from sub_agent_delegate import build_sub_agent_roles, build_sub_agent_role_handlers
from tests.test_sub_agent_runtime_integration import _child_gateway, _finish
from tests.test_native_runtime_gateway import call


def test_http_entry_runs_durable_child_with_selected_model_and_materials(tmp_path, monkeypatch):
    gateway, model, _ = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}), _finish])
    with gateway.runtime_planner.bind_turn({"model": "fixture-child-model"}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    gateway.runtime_sessions.begin_native_turn("parent-session", binding=binding,
        turn_id="parent-turn", message="PARENT ENTRY PRIVATE SENTINEL")
    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "children", gateway=gateway, lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles, build_handlers=build_sub_agent_role_handlers,
        task_finished=lambda _: None))
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    try:
        with TestClient(server.app) as client:
            response = client.post("/api/app/sub-agents", json={
                "role": "llm_readonly", "task": "Review the selected note.",
                "parentChatId": "parent-chat", "parentSessionId": "parent-session",
                "projectPath": str(tmp_path), "model": "fixture-child-model",
                "systemPrompt": "Summarize the selected evidence.", "maxSteps": 3,
                "selectedMaterials": [{"content": {"text": "selected note sentinel"}}],
                "params": {"childSessionId": "parent-session"},
            })
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            service._registry._threads[task_id].join(3)
            assert not service._registry._threads[task_id].is_alive()
            task = service.get_task(task_id)["task"]
            assert task["status"] == "completed", task.get("error")
            assert task["model"] == "fixture-child-model"
            assert task["maxSteps"] == 3
            assert task["childSessionId"] != "parent-session"
            bound = gateway.runtime_planner._turn.requests[-1]
            assert bound["session_id"] == task["childSessionId"]
            assert "PARENT ENTRY PRIVATE SENTINEL" not in str(model.requests)
            assert "Summarize the selected evidence." in model.requests[0]["instructions"]
            assert "selected note sentinel" in str(model.requests[0]["messages"])
            assert (tmp_path / "children" / "results" / (task_id + ".json")).exists()
    finally:
        service.shutdown()


def test_parent_delegate_entry_forwards_only_declared_llm_options(monkeypatch):
    class Capture:
        def create_task(self, **kwargs):
            self.arguments = kwargs
            return {"task": {"id": "child-task"}}
    capture = Capture()
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", capture)
    server.delegate_sub_agent_runtime({
        "role": "llm_readonly", "task": "Review.", "model": "fixture-model",
        "systemPrompt": "Check evidence.", "maxSteps": 2,
        "selectedMaterials": [{"text": "selected"}], "parentSessionId": "parent-session",
    })
    assert capture.arguments["params"] == {
        "model": "fixture-model", "systemPrompt": "Check evidence.", "maxSteps": 2,
        "selectedMaterials": [{"text": "selected"}],
    }
