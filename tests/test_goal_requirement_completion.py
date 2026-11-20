import pytest
from fastapi.testclient import TestClient

from agent_goal_service import AgentGoalServiceError
from agent_handoff_graph import apply_handoff_edits, serialize_handoff_graph
from context_compaction import MODEL_HANDOFF_PREFIX
from tests.test_native_runtime_gateway import setup_gateway


def test_bound_goal_completion_checks_owned_graph_for_both_entry_points(tmp_path, monkeypatch):
    import dashboard_server as dashboard

    gateway, _, _ = setup_gateway(tmp_path, [])
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    scope = {"chatId": "chat", "sessionId": "session", "projectRoot": str(tmp_path)}
    acceptance = {"kind": "subjective", "tool": "vrcforge_call_user", "result": {"question": "Meets the requested result?"}}
    goal = gateway.goal.create_agent_goal({**scope, "title": "Change the label", "sourceMessageId": "original",
                                           "acceptanceChecks": [acceptance]})["goal"]
    goal_id = goal["goalId"]
    gateway.goal.record_acceptance(goal_id, scope, {"id": "accepted", "tool": acceptance["tool"], "arguments": {},
        "source": "authenticated_question_answer", "result": {**acceptance["result"], "resolution": "pass"}})
    evidence = [{"type": "message", "id": "original"}]
    graph = apply_handoff_edits(None, {"nodes": {
        goal_id: {"kind": "goal", "goalSourceId": "original", "text": "Change the label", "evidence": evidence},
        "label": {"kind": "decision", "userRequirement": True, "text": "Change the label", "evidence": evidence},
    }}, fold=False)
    with gateway.runtime_planner.bind_turn({}):
        binding = gateway.runtime_planner.native_conversation_binding(scope["projectRoot"])
    state = gateway.runtime_sessions
    state.begin_native_turn(scope["sessionId"], binding=binding, turn_id="initial", message="Change the label")
    state.append_native_assistant(scope["sessionId"], binding=binding, message={
        "role": "assistant", "content": MODEL_HANDOFF_PREFIX + serialize_handoff_graph(graph),
    })
    ledger = gateway.goal.log_path.read_bytes()
    with TestClient(dashboard.app) as client:
        response = client.post(f"/api/app/agent/goals/{goal_id}", json={**scope, "status": "completed",
                                "uncoveredRequirementIds": []})
        assert response.status_code == 409 and "label" in response.text
        with pytest.raises(AgentGoalServiceError, match="uncovered requirements") as caught:
            gateway.goal.update_agent_goal_from_agent({**scope, "status": "complete", "reason": "Accepted."})
        assert caught.value.status_code == 409
        assert gateway.goal.log_path.read_bytes() == ledger
        assert gateway.goal.project_goals()[goal_id]["status"] == "active"

        previous_plan = apply_handoff_edits(graph, {"nodes": {
            "deliver-label": {"kind": "task", "todoId": "rename-label", "text": "Set the requested label", "evidence": evidence},
        }, "edges": [["label", "constrains", "deliver-label"]]}, fold=False)
        state.append_native_assistant(scope["sessionId"], binding=binding, message={
            "role": "assistant", "content": MODEL_HANDOFF_PREFIX + serialize_handoff_graph(previous_plan),
        })
        gateway.replace_agent_progress({**scope, "items": [{"id": "rename-label", "title": "Set the requested label"}]})
        gateway.replace_agent_progress({**scope, "items": [{"id": "review-list", "title": "Review the names in the list"}]})
        response = client.post(f"/api/app/agent/goals/{goal_id}", json={**scope, "status": "completed"})
        assert response.status_code == 409 and "label" in response.text
        assert gateway.goal.log_path.read_bytes() == ledger

        unrelated = gateway.goal.create_agent_goal({**scope, "title": "Other scope", "sourceMessageId": "separate",
                                                    "acceptanceChecks": [acceptance]})["goal"]
        response = client.post(f"/api/app/agent/goals/{unrelated['goalId']}", json={**scope, "status": "completed"})
        assert response.status_code == 200 and response.json()["goal"]["status"] == "completed"
