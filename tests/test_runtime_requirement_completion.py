import json

from context_compaction import MODEL_HANDOFF_PREFIX
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
from tests.test_runtime_planner_service import FakeTurn
from tests.test_todo_requirement_links import seed_graph


QUOTE = "把胸部再调大"


def _claim(call_id, request):
    message = finish(request)
    call_record = message["tool_calls"][0]
    call_record["id"] = call_id
    payload = json.loads(call_record["function"]["arguments"])
    payload["reply"] = "WHOLE_TASK_DONE"
    call_record["function"]["arguments"] = json.dumps(payload)
    return message


def test_native_completion_claim_returns_user_quote_and_stays_unverified(tmp_path):
    def retry(request):
        feedback = "\n".join(str(row.get("content")) for row in request["messages"] if row["role"] == "tool")
        assert "uncoveredRequirementIds" in feedback
        assert QUOTE in feedback
        assert "Add an actionable TODO" in feedback
        return _claim("second-claim", request)

    gateway, model, invoked = setup_gateway(tmp_path, [
        call("check", "vrcforge_read_text_file", {"path": "first.txt"}),
        lambda request: _claim("first-claim", request), retry,
    ])
    seed_graph(gateway, "native-session", str(tmp_path), ("fit", "goal", QUOTE))

    result = run(gateway, tmp_path)

    assert len(model.requests) == 3
    assert len(invoked) == 1
    plan = result["plan"]
    assert plan["nextStep"] == "completion_unverified"
    assert plan["completionGate"]["uncoveredRequirementIds"] == ["fit"]
    assert plan["completionGate"]["uncoveredRequirements"] == [{"requirementId": "fit", "userQuote": QUOTE}]
    assert QUOTE in plan["reply"] and "WHOLE_TASK_DONE" not in plan["reply"]
    assert "taskCompletion" not in plan


def test_native_scope_checks_current_linked_todo_before_certifying(tmp_path):
    tool = "vrcforge_progress_create"
    gateway, model, _ = setup_gateway(tmp_path, [lambda request: _claim("first-claim", request),
        call("link", tool, {"title": "Adjust the figure", "requirementIds": ["fit"]}),
        call("verify", "vrcforge_read_text_file", {"path": "verification.txt"}), finish,
    ])
    gateway.register_tool(tool, "When to use: add a TODO. When NOT to use: edit Unity.",
                          "plan/preview", gateway.create_agent_progress)
    seed_graph(gateway, "native-session", str(tmp_path), ("fit", "goal", QUOTE))

    result = run(gateway, tmp_path)

    assert len(model.requests) == 4
    progress, = gateway.list_agent_progress(session_id="native-session", project_root=str(tmp_path))["items"]
    assert progress["requirementIds"] == ["fit"]
    assert result["plan"]["taskCompletion"]["status"] == "completed"


def test_uncovered_requirement_does_not_gate_incidental_dialogue(tmp_path):
    gateway, model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Conversation answer."}])
    seed_graph(gateway, "native-session", str(tmp_path), ("fit", "goal", QUOTE))

    result = run(gateway, tmp_path, message="What does the term mean?")

    assert len(model.requests) == 1
    assert result["plan"]["reply"] == "Conversation answer."
    assert result["plan"]["nextStep"] == "done"
    assert "taskCompletion" not in result["plan"]


def test_legacy_completion_claim_uses_the_current_handoff_graph(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    graph = seed_graph(gateway, "native-session", "", ("fit", "goal", QUOTE))
    prompts = []

    class LegacyModel:
        def plan(self, prompt):
            prompts.append(prompt)
            return PlannerModelResult(json.dumps({
                "action": "reply", "reply": "WHOLE_TASK_DONE", "completion_claim": {"satisfied": True},
            }))

    gateway.runtime_planner._model = LegacyModel()
    gateway.runtime_planner._turn = FakeTurn(PlannerTurnMetadata())
    result = run(gateway, tmp_path, projectRoot="", _projectContextActive=False, history=[
        {"role": "agent", "text": MODEL_HANDOFF_PREFIX + json.dumps(graph)},
    ])

    assert len(prompts) == 2
    assert "Add an actionable TODO" in prompts[1]
    assert QUOTE in prompts[1]
    assert result["plan"]["completionGate"]["uncoveredRequirementIds"] == ["fit"]
    assert "taskCompletion" not in result["plan"]
