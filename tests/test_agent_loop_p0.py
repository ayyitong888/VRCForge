"""Focused P0 regression tests for the VRCForge agent runtime loop.

These cover the post-1.1.0 P0 path: the runtime turn is a bounded agentic loop
(observe -> plan -> act -> feed result back), with deterministic single-model
auto-resolution for "add an object to the model" write intents, real approval
creation, approved dispatch to the static GameObject primitive, and an honest
"not connected / cannot plan" terminal instead of a fake success reply.

The tests do not require a live Unity Editor: the final Unity MCP invocation is
mocked, while the agent loop, approval request, checkpoint boundary, write
handler dispatch, and fallback behavior are exercised through the real backend
path.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server


class AgentLoopP0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = dashboard_server.AGENT_GATEWAY
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (self.gateway.config_path, self.gateway.audit_dir)
        self.original_prepare = self.gateway.checkpoint_prepare_handler
        self.gateway.configure_paths(root / "agent_gateway.json", root / "agent_gateway")
        config = self.gateway.ensure_config()
        config.enabled = True
        config.allow_write_requests = True
        config.execution_mode = "approval"
        self.gateway.save_config(config)
        self.gateway.checkpoint_prepare_handler = lambda _root: {"ok": True}

    def tearDown(self) -> None:
        self.gateway.checkpoint_prepare_handler = self.original_prepare
        self.gateway.configure_paths(*self.original_paths)
        self.temp_dir.cleanup()

    def _unity_project(self) -> Path:
        project = Path(self.temp_dir.name) / "UnityProject"
        (project / "Assets").mkdir(parents=True, exist_ok=True)
        (project / "Packages").mkdir(exist_ok=True)
        (project / "ProjectSettings").mkdir(exist_ok=True)
        (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 2022.3",
            encoding="utf-8",
        )
        return project

    def test_single_model_autoresolve_creates_real_approval_and_dispatches_static_write(self) -> None:
        gateway = self.gateway
        project = self._unity_project()

        def fake_skill(tool, params, agent_name=None):
            if tool == "vrcforge_list_avatars":
                return {
                    "tool": tool,
                    "status": "executed",
                    "result": {"avatars": [{"avatarPath": "Milltina"}]},
                }
            return {"tool": tool, "status": "executed", "result": {}}

        with patch.object(gateway, "_execute_runtime_skill", side_effect=fake_skill):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "add a new object to the model",
                        "projectPath": str(project),
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])

        steps = payload.get("steps") or []
        self.assertEqual(len(steps), 2, f"expected scan+write, got {steps}")
        self.assertEqual(steps[0]["kind"], "skill")
        self.assertEqual(steps[0]["tool"], "vrcforge_list_avatars")
        self.assertEqual(steps[1]["kind"], "write")
        self.assertEqual(steps[1]["tool"], "vrcforge_create_gameobject")

        self.assertIn("write", payload)
        self.assertEqual(payload["write"]["status"], "approval_pending")
        self.assertEqual(payload["write"]["tool"], "vrcforge_create_gameobject")
        approval_id = payload["approval_id"]
        approval = gateway._approvals[approval_id]
        self.assertEqual(approval["targetTool"], "vrcforge_create_gameobject")
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["arguments"]["name"], "GameObject")
        self.assertEqual(approval["arguments"]["parentPath"], "Milltina")
        self.assertEqual(approval["arguments"]["projectPath"], str(project))

        self.assertTrue(payload["plan"].get("multiStep"))
        self.assertEqual(payload["plan"].get("stepCount"), 2)

        with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
            "dashboard_server.invoke_unity_mcp",
            return_value=dashboard_server.McpResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                payload={"data": {"ok": True, "gameObjectPath": "Milltina/GameObject"}},
            ),
        ) as mock_invoke:
            gateway.approve(approval_id)
            applied = gateway.apply_approved({"approval_id": approval_id})

        self.assertTrue(applied["ok"])
        self.assertEqual(applied["status"], "applied")
        _settings, tool_name, arguments = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_create_gameobject")
        self.assertEqual(arguments["name"], "GameObject")
        self.assertEqual(arguments["parentPath"], "Milltina")
        self.assertFalse(arguments["preview"])

    def test_multiple_models_asks_user_to_choose_without_writing(self) -> None:
        gateway = self.gateway

        def fake_skill(tool, params, agent_name=None):
            if tool == "vrcforge_list_avatars":
                return {
                    "tool": tool,
                    "status": "executed",
                    "result": {
                        "avatars": [
                            {"avatarPath": "AvatarA"},
                            {"avatarPath": "AvatarB"},
                        ]
                    },
                }
            return {"tool": tool, "status": "executed", "result": {}}

        with patch.object(gateway, "_execute_runtime_skill", side_effect=fake_skill):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={"message": "add a new object to the model"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("write", payload)
        steps = payload.get("steps") or []
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["tool"], "vrcforge_list_avatars")
        self.assertEqual(payload["plan"].get("nextStep"), "done")
        self.assertIn("Multiple avatars", payload["plan"].get("summary", ""))

    def test_unplanned_message_without_provider_is_honest_not_fake(self) -> None:
        gateway = self.gateway

        def raising_plan_fn(prompt):
            raise RuntimeError("no provider connected")

        with patch.object(gateway, "llm_plan_fn", raising_plan_fn):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={"message": "just chat with me about the weather"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        plan = payload["plan"]
        self.assertEqual(plan["planner"], "deterministic-local")
        self.assertFalse(plan.get("providerConnected", True))
        self.assertTrue(plan.get("deterministicTerminal"))
        self.assertEqual(plan.get("nextStep"), "done")
        self.assertNotIn("write", payload)
        self.assertNotIn("skill", payload)
        self.assertEqual(payload.get("steps", []), [])


if __name__ == "__main__":
    unittest.main()
