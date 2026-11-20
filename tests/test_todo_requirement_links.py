import json

import pytest
from fastapi.testclient import TestClient

from agent_handoff_graph import apply_handoff_edits
from agent_runtime_session_state import model_handoff_payload
from context_compaction import MODEL_HANDOFF_PREFIX
from tests.test_native_runtime_gateway import call, setup_gateway


def seed_graph(gateway, session_id, project_root, *requirements):
    evidence = [{"type": "message", "id": "source"}]
    graph = apply_handoff_edits(None, {"nodes": {
        "objective": {"kind": "goal", "text": "Finish the outfit", "evidence": evidence},
        **{node_id: {"kind": kind, "text": text, "userRequirement": True, "evidence": evidence}
           for node_id, kind, text in requirements},
    }}, fold=False)
    with gateway.runtime_planner.bind_turn({}):
        binding = gateway.runtime_planner.native_conversation_binding(project_root)
    state = gateway.runtime_sessions
    state.begin_native_turn(session_id, binding=binding, turn_id="old", message="Finish the outfit")
    state.append_native_assistant(session_id, binding=binding,
                                  message={"role": "assistant", "content": MODEL_HANDOFF_PREFIX + json.dumps(graph)})
    return graph


def test_requirement_ids_round_trip_through_app_and_preserve_scope(tmp_path, monkeypatch):
    import dashboard_server as dashboard

    gateway, _, _ = setup_gateway(tmp_path, [])
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    project = str(tmp_path / "project")
    other_project = str(tmp_path / "different-project")
    seed_graph(gateway, "owner", project, ("fit", "goal", "Adjust the fit"), ("order", "decision", "Check before mount"))
    seed_graph(gateway, "other-owner", project, ("other", "goal", "Other conversation"))
    owner = {"sessionId": "owner", "projectRoot": project}
    other = {"sessionId": "other-owner", "projectRoot": project}
    route = "/api/app/agent/progress"
    with TestClient(dashboard.app) as client:
        legacy = client.post(route, json={**owner, "title": "Old unlinked task"}).json()["progress"]
        assert "requirementIds" not in legacy
        create = client.post(route, json={**owner, "title": "Make the fit adjustment", "requirementIds": ["fit"]})
        assert create.status_code == 200
        item = create.json()["progress"]
        assert item["requirementIds"] == ["fit"]
        path = route + "/" + item["progressId"]
        assert client.post(path, json={**other, "requirementIds": ["other"]}).status_code == 404
        assert client.post(route, json={**other, "title": "Wrong", "requirementIds": ["fit"]}).status_code == 400
        assert client.post(route, json={**owner, "projectRoot": other_project, "title": "Wrong",
                                        "requirementIds": ["fit"]}).status_code == 400
        assert client.post(path, json={**owner, "requirementIds": ["objective"]}).status_code == 400
        assert client.post(path, json={**owner, "requirementIds": ["fit", "fit"]}).status_code == 400
        assert client.post(path, json={**owner, "requirementIds": None}).status_code == 422
        changed = client.post(path, json={**owner, "requirementIds": ["fit", "order"]}).json()
        assert changed["readbackVerified"] and changed["progress"]["requirementIds"] == ["fit", "order"]
        untouched = client.post(path, json={**owner, "status": "in_progress"}).json()["progress"]
        assert untouched["requirementIds"] == ["fit", "order"]
        rejected = client.post(route + "/replace", json={**owner, "items": [
            {"id": "new", "title": "Replace everything", "requirementIds": ["fit"]},
            {"id": "wrong", "title": "Foreign requirement", "requirementIds": ["other"]},
        ]})
        assert rejected.status_code == 400
        assert {row["progressId"] for row in client.get(route, params=owner).json()["items"]} == {
            legacy["progressId"], item["progressId"],
        }
        replaced = client.post(route + "/replace", json={**owner, "items": [
            {"id": item["progressId"], "title": untouched["title"]},
            {"id": "new", "title": "Check before mount", "requirementIds": ["order"]},
        ]}).json()
        assert replaced["readbackVerified"]
        assert [row["requirementIds"] for row in replaced["items"]] == [["fit", "order"], ["order"]]
        clear = client.post(path, json={**owner, "requirementIds": []}).json()
        assert clear["readbackVerified"] and clear["progress"]["requirementIds"] == []


@pytest.mark.parametrize("operation", ["update", "replace"])
def test_reused_todo_title_clears_links_from_an_older_graph(tmp_path, operation):
    from agent_handoff_graph import handoff_view, project_requirement_tasks

    gateway, _, _ = setup_gateway(tmp_path, [])
    owner = {"sessionId": "owner", "projectRoot": str(tmp_path)}
    graph = seed_graph(gateway, "owner", str(tmp_path), ("fit", "goal", "Adjust the fit"))
    graph = apply_handoff_edits(graph, {"nodes": {
        "old-task": {"kind": "task", "text": "Adjust the fit", "todoId": "task",
                     "evidence": [{"type": "todo", "id": "task"}]},
    }, "edges": [["old-task", "belongs_to", "fit"]]}, fold=False)
    initial = gateway.replace_agent_progress({**owner, "items": [{"id": "task", "title": "Adjust the fit"}]})
    assert "requirementIds" not in initial["items"][0]
    assert handoff_view(project_requirement_tasks(graph, initial["items"]), todo_items=initial["items"])["uncoveredRequirementIds"] == []
    if operation == "update":
        changed = gateway.update_agent_progress("task", {**owner, "title": "Check the texture"})
        rows = [changed["progress"]]
    else:
        changed = gateway.replace_agent_progress({**owner, "items": [{"id": "task", "title": "Check the texture"}]})
        rows = changed["items"]
    assert changed["readbackVerified"] and rows[0]["requirementIds"] == []
    projected = project_requirement_tasks(graph, rows)
    assert ["old-task", "belongs_to", "fit"] not in projected["edges"]
    assert handoff_view(projected, todo_items=rows)["uncoveredRequirementIds"] == ["fit"]


def test_native_progress_tool_maps_live_todo_before_next_model_request(tmp_path):
    from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS

    project = str(tmp_path)
    tool = "vrcforge_progress_create"
    gateway, model, _ = setup_gateway(tmp_path, [
        call("create", tool, {"title": "Adjust the fit", "requirementIds": ["fit"]}),
        {"role": "assistant", "content": "I added the fit task."},
    ])
    gateway.register_tool(tool, "When to use: add a TODO. When NOT to use: change the project.",
                          "plan/preview", gateway.create_agent_progress)
    seed_graph(gateway, "session", project, ("fit", "goal", "Adjust the fit"))
    assert "requirementIds" in UNITY_READ_TOOL_INPUT_SCHEMAS[tool]["properties"]
    gateway.runtime_message({"message": "Continue", "sessionId": "session", "clientTurnId": "turn",
                             "projectRoot": project, "maxAgenticTurns": 2})
    before = next(payload for row in model.requests[0]["messages"] if (payload := model_handoff_payload(row)))
    after = next(payload for row in model.requests[1]["messages"] if (payload := model_handoff_payload(row)))
    assert json.loads(before)["uncoveredRequirementIds"] == ["fit"]
    current = json.loads(after)
    assert current["uncoveredRequirementIds"] == []
    todo, = gateway.list_agent_progress(session_id="session", project_root=project)["items"]
    assert todo["requirementIds"] == ["fit"]
    projected, = (node_id for node_id, node in current["nodes"].items() if node.get("todoId") == todo["progressId"])
    assert [projected, "belongs_to", "fit"] in current["edges"]
    assert projected in current["readyTaskIds"]


def test_legacy_progress_tool_maps_decision_on_the_next_plan(tmp_path):
    from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata
    from tests.test_runtime_planner_service import FakeTurn

    gateway, _, _ = setup_gateway(tmp_path, [])
    graph = seed_graph(gateway, "general", "", ("check-first", "decision", "Check before mounting"))
    tool = "vrcforge_progress_replace"
    gateway.register_tool(tool, "When to use: replace TODOs. When NOT to use: change the project.",
                          "plan/preview", gateway.replace_agent_progress)
    gateway.replace_agent_progress({"sessionId": "general", "projectRoot": str(tmp_path),
                                    "items": [{"id": "other-project", "title": "Unrelated project task"}]})

    class Model:
        def __init__(self):
            self.prompts = []

        def plan(self, prompt):
            self.prompts.append(prompt)
            actions = [
                {"action": "skill", "skill_tool": tool, "skill_params": {"items": [
                    {"id": "check", "title": "Check the fit", "requirementIds": ["check-first"]},
                ]}},
                {"action": "reply", "reply": "Added the check."},
            ]
            return PlannerModelResult(json.dumps(actions[len(self.prompts) - 1]))

    model = Model()
    gateway.runtime_planner._model = model
    gateway.runtime_planner._turn = FakeTurn(PlannerTurnMetadata())
    gateway.runtime_message({
        "message": "Continue", "sessionId": "general", "clientTurnId": "turn", "_projectContextActive": False,
        "history": [{"role": "agent", "text": MODEL_HANDOFF_PREFIX + json.dumps(graph)}], "maxAgenticTurns": 2,
    })
    assert len(model.prompts) == 2
    assert '"uncoveredRequirementIds":["check-first"]' in model.prompts[0]
    assert '"uncoveredRequirementIds":[]' in model.prompts[1]
    assert '"todoId":"check"' in model.prompts[1]
    assert '"check-first","constrains","todo-requirement-' in model.prompts[1]
    assert all("Unrelated project task" not in prompt for prompt in model.prompts)
