"""Provider usage survives actual Gateway question/answer continuation boundaries."""
import json
import threading
import pytest

from agent_gateway import AgentGateway
from agent_task_loop import canonical_action_id
from tests.test_dashboard_server import bind_test_runtime_planner


@pytest.mark.parametrize("cache_writes", [(2, 0, 3), (2, None, 3)])
@pytest.mark.parametrize("cache_read", [None, 0])
def test_three_provider_responses_across_two_questions_are_counted_once(tmp_path, cache_writes, cache_read):
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
            if turn < 3 else {"action": "reply", "reply": "finished", "completion_claim": {
                "satisfied": True,
                "evidence_action_ids": [canonical_action_id("skill", "vrcforge_ask_user", {"question": f"Required choice {i}?"}) for i in (1, 2)],
            }}
        )
        usage = {"exact": True, "inputTokens": 10 * turn, "outputTokens": turn, "totalTokens": 11 * turn}
        if cache_read is not None:
            usage["cacheReadTokens"] = cache_read
        if cache_writes[turn - 1] is not None:
            usage["cacheWriteTokens"] = cache_writes[turn - 1]
        return {"text": json.dumps(plan), "usage": usage}

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
        assert results[-1]["plan"]["nextStep"] == "done"
        assert len(requests) == 3
        usage = results[-1]["contextUsage"]
        assert usage["requestCount"] == 3
        assert usage["priorRequestCount"] == 2
        assert usage["inputTokens"] == 60
        assert usage["outputTokens"] == 6
        assert usage["totalTokens"] == 66
        assert usage["cumulativeInputTokens"] == 60
        assert usage["cumulativeOutputTokens"] == 6
        assert usage["cumulativeTotalTokens"] == 66
        assert usage["exact"] is True
        assert usage["taskTotalAvailable"] is True
        assert usage["scope"] == "task_total_context_usage"
        assert usage["cacheWriteTokens"] == 5
        assert usage["cacheWriteUsageRequestCount"] == sum(value is not None for value in cache_writes)
        assert usage["cacheWriteUsageComplete"] is all(value is not None for value in cache_writes)
        raw_completed = [
            event for event in gateway.runtime_runs.read_events()
            if event.get("event") == "runtime_turn_completed"
        ]
        reconciliation = raw_completed[-1]["usageReconciliation"]
        complete_cache = cache_read is not None and all(value is not None for value in cache_writes)
        assert reconciliation["status"] == ("verified" if complete_cache else "unknown")
        assert reconciliation["parentRequestCount"] == 2
        assert reconciliation["cumulativeRequestCount"] == 3
        if complete_cache:
            assert reconciliation["delta"]["requestCount"] == 1
            assert len(reconciliation["cumulativeSnapshotSha256"]) == 64
        else:
            assert "delta" not in reconciliation
            assert reconciliation["reason"] == ("incomplete_cacheUsageComplete" if cache_read is None else "incomplete_cacheWriteUsageComplete")
        assert reconciliation["chainId"] == first["task"]["taskId"]
        assert len(raw_completed) == 3
        assert raw_completed[0]["usageReconciliation"]["status"] == ("verified" if cache_read is not None else "unknown")
        assert reconciliation["parentTurnId"] == raw_completed[-2]["turnId"]
        assert reconciliation["parentTurnId"] != raw_completed[0]["turnId"]
        persisted_seeds = [
            event["runtimeTaskSeed"]
            for event in gateway.questions._persistence.read()
            if isinstance(event.get("runtimeTaskSeed"), dict)
        ]
        assert len(persisted_seeds) >= 2
        assert persisted_seeds[-1]["turnId"] == raw_completed[0]["turnId"]
        assert persisted_seeds[-1]["usageParentTurnId"] == raw_completed[-2]["turnId"]
        assert persisted_seeds[-1]["usageParentTurnId"] == reconciliation["parentTurnId"]
        assert persisted_seeds[-1]["providerUsageSnapshotSha256"] == reconciliation["parentSnapshotSha256"]
    finally:
        assert gateway.shutdown_runtime_continuations(5)["ok"]
