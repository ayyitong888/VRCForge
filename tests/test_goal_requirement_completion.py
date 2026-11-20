import re

import pytest
from fastapi.testclient import TestClient

from agent_goal_service import AgentGoalServiceError
from agent_handoff_graph import apply_handoff_edits, serialize_handoff_graph
from context_compaction import MODEL_HANDOFF_PREFIX
from tests.test_native_runtime_gateway import setup_gateway


@pytest.mark.parametrize("existing", [None, "native-session", ""], ids=["explicit-agent-goal", "desktop-slash-goal", "old-slash-before-first-turn"])
def test_ordinary_goal_completes_with_real_readback_in_same_turn(tmp_path, existing, monkeypatch):
    import dashboard_server as dashboard
    from general_agent_tools import read_text_file
    from tests.test_native_runtime_gateway import call, run

    scope = {"chatId": "owner", "sessionId": "native-session", "projectRoot": str(tmp_path)}
    path = tmp_path / "actual.txt"
    path.write_text("verified", encoding="utf-8")
    read = "vrcforge_read_text_file"
    checks = [{"kind": "readback", "tool": read, "arguments": {"path": str(path)}, "result": {"text": "verified"}}]
    objective = "Read the verification file"
    def register(request):
        if existing is not None:
            observed = next(m["content"] for m in reversed(request["messages"]) if m.get("tool_call_id") == "inspect-goal")
            match = re.search(r"goal_\d{8}_\d{6}_\d{6}_[0-9a-f]{6}", observed)
            assert match, observed
            return call("register", "vrcforge_update_goal", {"status": "register_acceptance", "goalId": match.group(),
                                                            "acceptanceChecks": checks})
        return call("create", "vrcforge_create_goal", {"objective": objective, "acceptanceChecks": checks})

    gateway, model, _ = setup_gateway(tmp_path, [
        *([call("inspect-goal", "vrcforge_get_goal", {})] if existing is not None else []),
        register, call("read-file", read, {"path": str(path)}),
        call("done", "vrcforge_update_goal", {"status": "complete", "reason": "Read back actual.txt"}),
        {"role": "assistant", "content": "Verified."},
    ])
    gateway.register_tool(read, "When to use: read a file. When NOT to use: write.", "read/debug",
                          lambda p: read_text_file(p["path"], allowed_roots=[tmp_path]))
    for name, handler in (("vrcforge_get_goal", gateway.goal.get_current_agent_goal),
                          ("vrcforge_create_goal", gateway.goal.create_agent_goal_from_agent),
                          ("vrcforge_update_goal", gateway.goal.update_agent_goal_from_agent)):
        gateway.register_tool(name, "When to use: manage the explicit Goal. When NOT to use: write assets.", "plan/preview", handler)
    if existing is not None:
        monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
        client = TestClient(dashboard.app)
        try:
            response = client.post("/api/app/agent/goals", json={**scope, "sessionId": existing, "title": objective})
            assert response.status_code == 200
            current = response.json()["goal"]
        finally:
            client.close()
        with pytest.raises(AgentGoalServiceError):
            gateway.goal.update_agent_goal_from_agent({**scope, "status": "complete", "reason": "Probably done"})
        with pytest.raises(AgentGoalServiceError):
            gateway.goal.update_agent_goal_from_agent({**scope, "projectRoot": str(tmp_path.parent), "status": "register_acceptance",
                                                      "goalId": current["goalId"], "acceptanceChecks": checks})
    run(gateway, tmp_path, chatId=scope["chatId"], message=objective, _humanUserMessageSource={
        **scope, "clientTurnId": "native-turn", "message": objective, "source": "app_user_message",
    })
    saved, = gateway.goal.project_goals().values()
    assert saved["status"] == "completed", [m["content"] for m in model.requests[-1]["messages"] if m["role"] == "tool"]
    assert saved["acceptanceReceipts"] == {"0": "read-file"}
    assert saved["sessionId"] == "native-session"
    if existing is None:
        assert saved["sourceMessageId"] == "native-turn"


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
