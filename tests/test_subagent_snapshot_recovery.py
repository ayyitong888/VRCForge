from threading import Event

from fastapi.testclient import TestClient

import dashboard_server as server
from background_goal_runtime import RuntimeLaneBudget
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService
from sub_agent_delegate import build_sub_agent_roles


def test_manual_worker_publishes_terminal_state_after_initial_running_response(tmp_path, monkeypatch):
    release = Event()
    started = Event()

    def run_manual_worker(*_):
        started.set()
        assert release.wait(5)
        return {"ok": True, "summaryText": "completed manual worker"}

    events = []
    monkeypatch.setattr(server.EVENT_BUS, "broadcast_from_sync", lambda event, payload: events.append((event, payload)))
    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path, gateway=None, lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles, build_handlers=lambda _: {"llm_readonly": run_manual_worker},
        task_finished=server._sub_agent_task_finished,
        worker_terminal=server._publish_sub_agent_worker_terminal,
    ))
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    client = TestClient(server.app)
    try:
        initial = client.post("/api/app/sub-agents", json={
            "role": "llm_readonly", "task": "Review the note.", "parentChatId": "manual-chat",
        }).json()["task"]
        assert started.wait(5)
        assert client.get(f"/api/app/sub-agents/{initial['id']}").json()["task"]["status"] == "running"
        release.set()
        service._registry._threads[initial["id"]].join(5)
        task = client.get(f"/api/app/sub-agents/{initial['id']}").json()["task"]
        assert task["status"] == "completed", task
        assert any(event == "subAgentTasks" for event, _ in events), events
    finally:
        release.set()
        service.shutdown()
