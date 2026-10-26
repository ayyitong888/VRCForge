from __future__ import annotations

import unittest
from unittest.mock import patch

import tests.test_agent_loop_p0 as loop_fixture
from runtime_planner_service import RuntimePlannerService


class NonblockingQuestionProviderFailureTests(unittest.TestCase):
    """Regression envelope for a non-blocking question followed by provider loss."""

    def setUp(self) -> None:
        self._fixture = loop_fixture.AgentLoopP0Tests("runTest")
        self._fixture.setUp()
        self.gateway = self._fixture.gateway

    def tearDown(self) -> None:
        self._fixture.tearDown()

    def test_original_overload_without_http_status_keeps_capacity_diagnosis(self) -> None:
        error = RuntimeError("Our servers are currently overloaded. Please try again later.")
        error.body = {
            "type": "service_unavailable_error",
            "code": "server_is_overloaded",
            "headers": {"x-retry-metadata": "NO_MORE_RETRY"},
            "message": str(error),
            "param": None,
        }
        self.assertEqual(RuntimePlannerService._planner_failure_code(error), "provider_capacity")
        self.assertTrue(RuntimePlannerService._provider_no_more_retry(error))

    def _run(self, *, blocking: bool) -> dict:
        self._fixture._use_deepseek_plan_config()
        session_id = "nonblocking-question-provider-failure"
        question_plan = {
            "planner": "llm", "skillNeeded": True,
            "skillTool": "vrcforge_ask_user",
            "skillParams": {
                "question": "Need an optional source decision.",
                "blocking": blocking,
            },
            "continueLoop": not blocking, "nextStep": "call_skill",
        }
        read_plan = {
            "planner": "llm", "skillNeeded": True,
            "skillTool": "vrcforge_scan_materials", "skillParams": {},
            "continueLoop": True, "nextStep": "call_skill",
        }
        error = RuntimeError("Our servers are currently overloaded. Please try again later.")
        error.body = {
            "type": "service_unavailable_error", "code": "server_is_overloaded",
            "headers": {"x-retry-metadata": "NO_MORE_RETRY"}, "message": str(error),
            "param": None,
        }
        failure_plan = self.gateway.runtime_planner._planner_failure_plan(
            cause_code=RuntimePlannerService._planner_failure_code(error),
            phase="post_tool", planner_label="", provider_error=error,
        )
        plans = iter([question_plan] if blocking else [question_plan, read_plan, failure_plan])

        def fake_skill(_owner, tool, params, agent_name=None, owner_id=""):
            if tool == "vrcforge_ask_user":
                created = self.gateway.create_runtime_question(params)
                return {
                    "ok": True,
                    "tool": tool,
                    "status": created.get("status", "pending"),
                    "result": {"question": created["question"]},
                    "outcome": created.get("outcome", {"status": created.get("status", "pending")}),
                }
            return {
                "ok": True,
                "tool": tool,
                "status": "executed",
                "result": {"ok": True, "materialCount": 1},
                "outcome": {
                    "status": "ok",
                    "summary": "independent read retained",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch.object(self.gateway.runtime_planner, "plan_agent_turn", side_effect=plans), patch.object(
            type(self.gateway.runtime_skills), "execute", autospec=True, side_effect=fake_skill
        ):
            return self.gateway.runtime_message({
                "message": "Inspect independently, then continue the requested work.",
                "provider": "deepseek", "providerLabel": "DeepSeek",
                "model": "deepseek-chat", "session_id": session_id,
                "clientTurnId": "nonblocking-question-provider-failure-turn",
                "projectPath": "C:/Unity/FixtureProject",
            })

    def test_nonblocking_question_does_not_replace_post_tool_provider_failure(self) -> None:
        payload = self._run(blocking=False)
        plan = payload["plan"]
        self.assertEqual(plan.get("nextStep"), "planner_failed", "terminal planner failure was masked")
        self.assertTrue(plan.get("plannerFailed"), "terminal planner failure flag was masked")
        self.assertEqual(plan.get("plannerFailure", {}).get("code"), "provider_capacity", "provider failure reason changed")
        self.assertEqual(plan.get("plannerFailure", {}).get("phase"), "post_tool", "provider failure phase changed")
        self.assertEqual(plan.get("plannerFailure", {}).get("providerError", {}).get("body", {}).get("code"), "server_is_overloaded", "provider body reason changed")
        self.assertNotEqual(plan.get("completionGate", {}).get("reason"), "question_pending", "non-blocking question replaced terminal failure")
        self.assertEqual(payload["steps"][1]["tool"], "vrcforge_scan_materials")
        self.assertEqual(payload["steps"][1]["outcome"]["status"], "ok")
        question = self.gateway.questions.list(session_id=payload["session_id"])["questions"][0]
        self.assertFalse(question["blocking"])
        self.assertEqual(question["status"], "pending")
        completed = [event for event in self.gateway.runtime_runs.read_events() if event.get("event") == "runtime_turn_completed"]
        self.assertEqual(completed[-1]["status"], "failed")

    def test_blocking_question_keeps_question_pending_boundary(self) -> None:
        payload = self._run(blocking=True)
        plan = payload["plan"]
        self.assertEqual(plan.get("nextStep"), "needs_user_action", "blocking question boundary changed")
        self.assertEqual(plan.get("completionGate", {}).get("reason"), "question_pending", "blocking question gate changed")
        question = self.gateway.questions.list(session_id=payload["session_id"])["questions"][0]
        self.assertTrue(question["blocking"])
        self.assertEqual(question["status"], "pending")
        completed = [event for event in self.gateway.runtime_runs.read_events() if event.get("event") == "runtime_turn_completed"]
        self.assertEqual(completed[-1]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
