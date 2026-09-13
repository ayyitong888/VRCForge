"""Provider usage survives actual Gateway question/answer continuation boundaries."""
import json
import threading

from agent_gateway import AgentGateway
from tests.test_dashboard_server import bind_test_runtime_planner


def test_three_provider_responses_across_two_questions_are_counted_once(tmp_path):
    delivered = threading.Event()
    results = []

    def completed(payload):
        results.append(payload)
        delivered.set()

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit", runtime_turn_completed=completed)
    gateway.register_tool(
        "vrcforge_ask_user",
        "When to use: ask a required question. When NOT to use: no missing input.",
        "plan/preview",
        gateway.create_runtime_question,
    )
    requests = []

    def scripted(prompt):
        requests.append(prompt)
        turn = len(requests)
        plan = (
            {"action": "skill", "skill_tool": "vrcforge_ask_user", "skill_params": {"question": f"Required choice {turn}?"}, "summary": "wait for required input"}
            if turn < 3 else {"action": "reply", "reply": "finished"}
        )
        return {"text": json.dumps(plan), "usage": {"exact": True, "inputTokens": 10 * turn, "outputTokens": turn, "totalTokens": 11 * turn}}

    bind_test_runtime_planner(gateway, scripted)
    try:
        first = gateway.runtime_message({"message": "Ask two distinct required questions, then finish.", "sessionId": "cost-session", "clientTurnId": "cost-turn", "projectRoot": str(tmp_path)})
        assert first["plan"]["nextStep"] == "needs_user_action"
        for turn in range(2):
            questions = gateway.questions.list(session_id="cost-session", project_root=str(tmp_path))["questions"]
            question = next(q for q in questions if q["status"] == "pending")
            delivered.clear()
            gateway.questions.answer(question["questionId"], {"sessionId": "cost-session", "projectRoot": str(tmp_path), "answer": "yes"})
            assert delivered.wait(5), "actual Gateway continuation did not finish"
            assert results[-1]["task"]["taskId"] == first["task"]["taskId"]
            if turn == 0:
                assert results[-1]["contextUsage"]["totalTokens"] == 33
        assert len(requests) == 3
        usage = results[-1]["contextUsage"]
        assert usage["requestCount"] == 3
        assert usage["inputTokens"] == 60
        assert usage["outputTokens"] == 6
        assert usage["totalTokens"] == 66
        assert usage["exact"] is True
        assert usage["taskTotalAvailable"] is True
        assert usage["scope"] == "task_total_context_usage"
    finally:
        assert gateway.shutdown_runtime_continuations(5)["ok"]
