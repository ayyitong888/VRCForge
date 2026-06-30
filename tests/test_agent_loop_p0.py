"""Focused P0 regression tests for the VRCForge agent runtime loop.

These cover the post-1.1.0 P0 fix: the runtime turn is now a bounded agentic
loop (observe -> plan -> act -> feed result back), with deterministic
single-model auto-resolution for "add an object to the model" write intents,
and an honest "not connected / cannot plan" terminal instead of a fake
"做了做了" reply when no provider/skill can produce an actionable plan.

The sandbox has no Unity Editor and no MCP bridge, so the supervised write is
exercised by mocking `call_tool` (which, on a real machine, creates the
checkpoint + approval record). The loop itself, the scan-first single-model
resolution, the supervised-write proposal, and the honest fallback are all real.
Live "add object -> checkpoint -> rollback" proof must run on a machine with
Unity + the MCP bridge (codex / user machine).
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server


class AgentLoopP0Tests(unittest.TestCase):
    def test_single_model_autoresolve_scans_then_proposes_supervised_write(self) -> None:
        """One project, one model, 'add a new object' -> scan, auto-pick the only
        model, then propose a supervised write (approval pending). No reverse
        question, no empty reply."""
        gateway = dashboard_server.AGENT_GATEWAY

        def fake_skill(tool, params, agent_name=None):
            if tool == "vrcforge_list_avatars":
                return {
                    "tool": tool,
                    "status": "executed",
                    "result": {"avatars": [{"avatarPath": "Assets/Milltina/Milltina.prefab"}]},
                }
            return {"tool": tool, "status": "executed", "result": {}}

        def fake_call_tool(name, params, agent_name=None):
            # Mirrors a real supervised write: handler returns a pending approval
            # (the checkpoint + approval record are created server-side).
            return {
                "ok": True,
                "tool": name,
                "result": {"status": "pending_approval", "approval_id": "appr-p0-1"},
            }

        with patch.object(gateway, "_execute_runtime_skill", side_effect=fake_skill), patch.object(
            gateway, "call_tool", side_effect=fake_call_tool
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={"message": "往模型里加个 new object"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])

        # Multi-step loop: step 0 scans, step 1 proposes the write.
        steps = payload.get("steps") or []
        self.assertEqual(len(steps), 2, f"expected scan+write, got {steps}")
        self.assertEqual(steps[0]["kind"], "skill")
        self.assertEqual(steps[0]["tool"], "vrcforge_list_avatars")
        self.assertEqual(steps[1]["kind"], "write")
        self.assertEqual(steps[1]["tool"], "vrcforge_unity_mcp_write")

        # The write is PROPOSED, not auto-applied: it stops on the approval.
        self.assertIn("write", payload)
        self.assertEqual(payload["write"]["status"], "approval_pending")
        self.assertEqual(payload["write"]["tool"], "vrcforge_unity_mcp_write")
        self.assertEqual(payload["approval_id"], "appr-p0-1")

        # Top-level plan is flagged multi-step.
        self.assertTrue(payload["plan"].get("multiStep"))
        self.assertEqual(payload["plan"].get("stepCount"), 2)

    def test_multiple_models_asks_user_to_choose_without_writing(self) -> None:
        """Two models, ambiguous target -> scan, then ask which one. No write
        proposed (we never guess a target)."""
        gateway = dashboard_server.AGENT_GATEWAY

        def fake_skill(tool, params, agent_name=None):
            if tool == "vrcforge_list_avatars":
                return {
                    "tool": tool,
                    "status": "executed",
                    "result": {
                        "avatars": [
                            {"avatarPath": "Assets/A/A.prefab"},
                            {"avatarPath": "Assets/B/B.prefab"},
                        ]
                    },
                }
            return {"tool": tool, "status": "executed", "result": {}}

        with patch.object(gateway, "_execute_runtime_skill", side_effect=fake_skill):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={"message": "往模型里加个 new object"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("write", payload)
        steps = payload.get("steps") or []
        # Only the scan ran; the turn ends asking the user to choose.
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["tool"], "vrcforge_list_avatars")
        self.assertEqual(payload["plan"].get("nextStep"), "done")
        self.assertIn("多个模型", payload["plan"].get("reply", ""))

    def test_unplanned_message_without_provider_is_honest_not_fake(self) -> None:
        """No deterministic match + model planner unavailable -> honest 'can't
        plan / not connected', NOT a fake 'done' reply (A5)."""
        gateway = dashboard_server.AGENT_GATEWAY

        def raising_plan_fn(prompt):
            raise RuntimeError("no provider connected")

        with patch.object(gateway, "llm_plan_fn", raising_plan_fn):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={"message": "随便陪我聊聊今天的天气吧"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        plan = payload["plan"]
        self.assertEqual(plan["planner"], "deterministic-local")
        self.assertFalse(plan.get("providerConnected", True))
        self.assertTrue(plan.get("deterministicTerminal"))
        self.assertEqual(plan.get("nextStep"), "done")
        self.assertIn("没法自动规划", plan.get("reply", ""))
        # Honest terminal does not fabricate tool work.
        self.assertNotIn("write", payload)
        self.assertNotIn("skill", payload)
        self.assertEqual(payload.get("steps", []), [])


if __name__ == "__main__":
    unittest.main()
