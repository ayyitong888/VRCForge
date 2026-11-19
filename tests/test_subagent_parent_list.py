from threading import Event

from fastapi.testclient import TestClient

import dashboard_server as server
from sub_agent_collaboration_service import SubAgentCollaborationService
from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


def test_parent_scope_filters_before_paging_and_keeps_global_list(tmp_path, monkeypatch):
    release = Event()
    started = Event()

    def run(task, _):
        if task["task"] == "Long-running A":
            started.set()
            assert release.wait(15)
        return {"ok": True, "summaryText": task["task"]}

    registry = SubAgentTaskRegistry(
        artifact_dir=tmp_path, roles=[SubAgentRole(id="fixture", title="Fixture", description="Test worker")],
        handlers={"fixture": run},
    )
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", SubAgentCollaborationService.from_registry_for_testing(registry))
    client = TestClient(server.app)

    def post(chat, message):
        response = client.post("/api/app/sub-agents", json={
            "role": "fixture", "task": message, "parentChatId": chat,
        })
        assert response.status_code == 200, response.text
        return response.json()["task"]["id"]

    try:
        running = post("chat-A", "Long-running A")
        assert started.wait(5)
        pending = post("chat-A", "Pending review A")
        registry._threads[pending].join(5)
        for index in range(50):
            newer = post("chat-B", f"Completed B {index}")
            registry._threads[newer].join(5)
            assert not registry._threads[newer].is_alive()

        global_tasks = client.get("/api/app/sub-agents").json()["tasks"]
        assert len(global_tasks) == 50
        assert {task["parentChatId"] for task in global_tasks} == {"chat-B"}
        assert client.get(f"/api/app/sub-agents/{running}").json()["task"]["status"] == "running"
        for limit in (50, 1):
            scoped = client.get("/api/app/sub-agents", params={"parentChatId": "chat-A", "limit": limit}).json()
            assert {task["id"]: task["status"] for task in scoped["tasks"]} == {
                running: "running", pending: "completed",
            }
    finally:
        release.set()
        registry.shutdown()


def test_parent_model_comes_from_the_delegating_turn(tmp_path):
    registry = SubAgentTaskRegistry(
        artifact_dir=tmp_path,
        roles=[SubAgentRole(id="fixture", title="Fixture", description="File check")],
        handlers={"fixture": lambda task, _: {"ok": True, "summaryText": task["task"]}},
    )
    seed = {
        "sessionId": "parent-session",
        "parentChatId": "parent-chat",
        "turnId": "parent-turn",
        "clientTurnId": "parent-client-turn",
        "model": "delegating-model",
    }
    try:
        matched = registry.create_task(
            role="fixture", display_name="Fixture", task="Check file names", parent_chat_id="parent-chat",
            parent_session_id="parent-session", params={"_taskSeed": seed, "model": "role-override"},
        )
        mismatched = registry.create_task(
            role="fixture", display_name="Fixture", task="Check file names", parent_chat_id="different-chat",
            parent_session_id="parent-session", params={"_taskSeed": seed},
        )
        assert registry.get_task(matched["task"]["id"])["task"]["parentModel"] == "delegating-model"
        assert "parentModel" not in registry.get_task(mismatched["task"]["id"])["task"]
        public = registry.list_tasks(parent_chat_id="parent-chat")["tasks"][0]
        assert public["parentModel"] == "delegating-model"
        assert public["model"] == "role-override"
    finally:
        registry.shutdown()
