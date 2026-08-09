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
from approved_unity_execution import current_approved_unity_execution
from agent_task_loop import AgentTaskLoop, approval_completion, approval_task_context
from runtime_planner_service import detect_avatar_write_intent


class AgentLoopP0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = dashboard_server.AGENT_GATEWAY
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (self.gateway.config_path, self.gateway.audit_dir)
        self.original_prepare = self.gateway.approval_transactions.checkpoint_prepare_handler
        self.original_create_checkpoint_prepare = self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].checkpoint_prepare_handler
        self.gateway.configure_paths(root / "agent_gateway.json", root / "agent_gateway")
        config = self.gateway.ensure_config()
        config.enabled = True
        config.allow_write_requests = True
        config.execution_mode = "approval"
        self.gateway.save_config(config)
        self.gateway.approval_transactions.checkpoint_prepare_handler = lambda _root: {"ok": True}
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].checkpoint_prepare_handler = lambda _root, _arguments: {"ok": True}

    def tearDown(self) -> None:
        self.gateway.approval_transactions.checkpoint_prepare_handler = self.original_prepare
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].checkpoint_prepare_handler = self.original_create_checkpoint_prepare
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

        with patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=lambda _owner, tool, params, agent_name=None, owner_id="": fake_skill(tool, params, agent_name),
        ):
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
        self.assertEqual(len(steps), 3, f"expected scan+execution-phase+write, got {steps}")
        self.assertEqual(steps[0]["kind"], "skill")
        self.assertEqual(steps[0]["tool"], "vrcforge_list_avatars")
        self.assertEqual(steps[1]["kind"], "phase")
        self.assertEqual(steps[1]["status"], "entered_execution")
        self.assertEqual(steps[2]["kind"], "write")
        self.assertEqual(steps[2]["tool"], "vrcforge_create_gameobject")

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
        self.assertEqual(payload["plan"].get("stepCount"), 3)

        applied_result = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "gameObjectPath": "Milltina/GameObject",
                    "persistedReadback": True,
                    "sceneSaved": True,
                }
            },
        )

        def invoke_with_bound_execution(_settings, tool_name, arguments, **_kwargs):
            plan = current_approved_unity_execution()
            self.assertIsNotNone(plan)
            claim = plan.claim(tool_name, arguments, project)
            claim.complete()
            return applied_result

        with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
            "dashboard_server.invoke_unity_mcp",
            side_effect=invoke_with_bound_execution,
        ) as mock_invoke:
            gateway.approval_transactions.approve(approval_id)
            applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

        self.assertTrue(applied["ok"])
        self.assertEqual(applied["status"], "applied")
        _settings, tool_name, arguments = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_create_gameobject")
        self.assertEqual(arguments["name"], "GameObject")
        self.assertEqual(arguments["parentPath"], "Milltina")
        self.assertFalse(arguments["preview"])

    def test_explicit_scene_root_create_bypasses_avatar_scan_and_uses_supervised_write(self) -> None:
        gateway = self.gateway
        project = self._unity_project()

        with patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=AssertionError("an explicit scene-root target must not scan avatars"),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "create an object named VRCForgeMCP2AppAcceptance at the scene root",
                        "projectPath": str(project),
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        steps = payload.get("steps") or []
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["kind"], "phase")
        self.assertEqual(steps[0]["status"], "entered_execution")
        self.assertEqual(steps[1]["kind"], "write")
        self.assertEqual(steps[1]["tool"], "vrcforge_create_gameobject")
        self.assertEqual(payload["plan"].get("resolvedTarget"), "scene_root")

        approval_id = payload["approval_id"]
        approval = gateway._approvals[approval_id]
        self.assertEqual(approval["targetTool"], "vrcforge_create_gameobject")
        self.assertEqual(approval["arguments"]["name"], "VRCForgeMCP2AppAcceptance")
        self.assertEqual(approval["arguments"]["parentPath"], "")
        self.assertEqual(approval["arguments"]["projectPath"], str(project))

        applied_result = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "gameObjectPath": "VRCForgeMCP2AppAcceptance",
                    "persistedReadback": True,
                    "sceneSaved": True,
                }
            },
        )

        def invoke_with_bound_execution(_settings, tool_name, arguments, **_kwargs):
            plan = current_approved_unity_execution()
            self.assertIsNotNone(plan)
            claim = plan.claim(tool_name, arguments, project)
            claim.complete()
            return applied_result

        with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
            "dashboard_server.invoke_unity_mcp",
            side_effect=invoke_with_bound_execution,
        ) as mock_invoke:
            gateway.approval_transactions.approve(approval_id)
            applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

        self.assertTrue(applied["ok"])
        self.assertEqual(applied["status"], "applied")
        _settings, tool_name, arguments = mock_invoke.call_args.args
        self.assertEqual(tool_name, "vrc_create_gameobject")
        self.assertEqual(arguments["name"], "VRCForgeMCP2AppAcceptance")
        self.assertEqual(arguments["parentPath"], "")
        self.assertFalse(arguments["preview"])

    def test_approved_write_resumes_the_same_task_without_replaying_the_write(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        applied_result = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "gameObjectPath": "ApprovalLoopProbe",
                    "persistedReadback": True,
                    "sceneSaved": True,
                }
            },
        )

        def invoke_with_bound_execution(_settings, tool_name, arguments, **_kwargs):
            plan = current_approved_unity_execution()
            self.assertIsNotNone(plan)
            claim = plan.claim(tool_name, arguments, project)
            claim.complete()
            return applied_result

        with TestClient(dashboard_server.app) as client:
            initial = client.post(
                "/api/app/agent/message",
                json={
                    "message": "create an object named ApprovalLoopProbe at the scene root",
                    "projectPath": str(project),
                    "session_id": "approval-loop-session",
                    "clientTurnId": "approval-loop-client",
                },
            ).json()
            approval_id = initial["approvalId"]
            approval = gateway._approvals[approval_id]
            self.assertEqual(approval["taskContext"]["taskId"], initial["task"]["taskId"])
            self.assertEqual(approval["taskContext"]["sessionId"], "approval-loop-session")
            created_audits = [
                item
                for item in gateway.approval_transactions.recent_audit_logs(50)
                if item.get("event") == "approval_requested"
                and (item.get("approval") or {}).get("id") == approval_id
            ]
            self.assertEqual(
                created_audits[-1]["approval"]["taskContext"]["taskId"],
                initial["task"]["taskId"],
            )

            with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                "dashboard_server.invoke_unity_mcp",
                side_effect=invoke_with_bound_execution,
            ) as mock_invoke:
                decided = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project), "globalOnly": False},
                )

        self.assertEqual(decided.status_code, 200)
        payload = decided.json()
        self.assertEqual(payload["execution"]["status"], "applied")
        continuation = payload["continuation"]
        self.assertEqual(continuation["resumedApprovalId"], approval_id)
        self.assertEqual(continuation["sessionId"], "approval-loop-session")
        self.assertEqual(continuation["task"]["taskId"], initial["task"]["taskId"])
        self.assertEqual(continuation["plan"]["nextStep"], "done", continuation)
        mock_invoke.assert_called_once()

    def test_approved_unity_shell_resumes_the_same_task_without_replaying_the_command(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        initial = gateway.runtime_message(
            {
                "message": "write the task loop probe file",
                "shell_command": "Set-Content Assets/task-loop.txt ok",
                "projectRoot": str(project),
                "cwd": str(project),
                "workspaceRoot": str(project),
                "session_id": "shell-approval-loop-session",
                "clientTurnId": "shell-approval-loop-client",
            }
        )

        approval_id = initial["approvalId"]
        approval = gateway._approvals[approval_id]
        task_context = approval["taskContext"]
        self.assertEqual(task_context["taskId"], initial["task"]["taskId"])
        self.assertEqual(task_context["kind"], "shell")
        self.assertEqual(task_context["tool"], "shell")
        self.assertEqual(
            initial["task"]["actions"][0]["actionId"],
            task_context["requestedActionId"],
        )

        gateway.approval_transactions.approve(approval_id)
        with patch.object(
            type(gateway.shell),
            "execute_payload",
            autospec=True,
            return_value={
                "ok": True,
                "status": "executed",
                "result": {"ok": True, "exitCode": 0, "stdout": "ok", "stderr": ""},
            },
        ) as execute_payload:
            applied = gateway.shell.execute_approved({"approval_id": approval_id})
            continuation = gateway.resume_runtime_task_after_approval(approval, applied)

        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["taskCompletion"]["status"], "completed")
        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["resumedApprovalId"], approval_id)
        self.assertEqual(continuation["sessionId"], "shell-approval-loop-session")
        self.assertEqual(continuation["task"]["taskId"], initial["task"]["taskId"])
        self.assertEqual(continuation["task"]["actions"][0]["kind"], "shell")
        self.assertEqual(continuation["plan"]["nextStep"], "done")
        execute_payload.assert_called_once()

    def test_rejected_write_returns_to_the_original_task_as_needs_user_action(self) -> None:
        project = self._unity_project()
        with TestClient(dashboard_server.app) as client:
            initial = client.post(
                "/api/app/agent/message",
                json={
                    "message": "create an object named RejectedProbe at the scene root",
                    "projectPath": str(project),
                    "session_id": "rejected-loop-session",
                },
            ).json()
            approval_id = initial["approvalId"]
            rejected = client.post(
                f"/api/app/agent/approvals/{approval_id}/reject",
                json={"expectedProjectRoot": str(project), "globalOnly": False},
            )

        self.assertEqual(rejected.status_code, 200)
        continuation = rejected.json()["continuation"]
        self.assertEqual(continuation["sessionId"], "rejected-loop-session")
        self.assertEqual(continuation["task"]["taskId"], initial["task"]["taskId"])
        self.assertEqual(continuation["plan"]["nextStep"], "needs_user_action")
        self.assertEqual(
            continuation["plan"]["completionGate"]["reason"],
            "approval_rejected",
        )
        self.assertEqual(self.gateway._approvals[approval_id]["status"], "rejected")

    def test_failed_approved_handler_returns_to_the_original_task_without_replay(self) -> None:
        project = self._unity_project()
        with TestClient(dashboard_server.app) as client:
            initial = client.post(
                "/api/app/agent/message",
                json={
                    "message": "create an object named FailedApprovalProbe at the scene root",
                    "projectPath": str(project),
                    "session_id": "failed-approval-loop-session",
                    "clientTurnId": "failed-approval-loop-client",
                },
            ).json()
            approval_id = initial["approvalId"]
            with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                "dashboard_server.invoke_unity_mcp",
                side_effect=RuntimeError("simulated approved handler failure"),
            ) as invoke:
                decided = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project), "globalOnly": False},
                )

        self.assertEqual(decided.status_code, 200)
        payload = decided.json()
        self.assertEqual(payload["execution"]["status"], "failed")
        self.assertEqual(payload["execution"]["taskCompletion"]["status"], "failed")
        continuation = payload["continuation"]
        self.assertEqual(continuation["sessionId"], "failed-approval-loop-session")
        self.assertEqual(continuation["task"]["taskId"], initial["task"]["taskId"])
        self.assertEqual(continuation["plan"]["nextStep"], "tool_failed")
        invoke.assert_called_once()

    def test_post_approval_planner_failure_preserves_the_verified_write_without_replay(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        loop = AgentTaskLoop(
            "create and then inspect",
            session_id="post-approval-failure-session",
            client_turn_id="post-approval-failure-client",
            project_root=str(project),
            agent_name="desktop-agent",
        )
        arguments = {"name": "Probe", "projectRoot": str(project)}
        context = approval_task_context(
            loop.approval_seed(
                tool_calls_used=1,
                exposure_layer="execution",
                requested_tool="vrcforge_create_gameobject",
                requested_arguments=arguments,
                continue_after_approval=True,
            ),
            tool="vrcforge_create_gameobject",
            arguments=arguments,
        )
        self.assertIsNotNone(context)
        completion = approval_completion(
            context,
            raw_result={"ok": True, "persistedReadback": True, "sceneSaved": True},
            outcome={
                "status": "ok",
                "summary": "created",
                "verification": {"state": "not_required", "checks": []},
            },
        )
        self.assertIsNotNone(completion)
        approval = {
            "id": "approval-planner-failed",
            "agentName": "desktop-agent",
            "arguments": arguments,
            "taskContext": context,
            "taskCompletion": completion,
        }
        execution = {
            "ok": True,
            "status": "applied",
            "approval": approval,
            "taskCompletion": completion,
            "outcome": completion["outcome"],
        }
        planner_failed = {
            "summary": "The model planner failed after the approved write.",
            "reply": "The write was kept, but planning could not continue.",
            "planner": "runtime",
            "continueLoop": False,
            "nextStep": "planner_failed",
        }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            return_value=planner_failed,
        ), patch.object(
            type(gateway.approval_transactions),
            "_execute_write_request",
            autospec=True,
            side_effect=AssertionError("the approved write must not be replayed"),
        ):
            continuation = gateway.resume_runtime_task_after_approval(approval, execution)

        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["plan"]["nextStep"], "planner_failed")
        self.assertEqual(continuation["task"]["status"], "planner_failed")
        self.assertEqual(continuation["resumedApprovalId"], "approval-planner-failed")

    def test_nonterminal_approval_execution_does_not_resume_the_task(self) -> None:
        loop = AgentTaskLoop("create one object", session_id="blocked-approval-session")
        context = approval_task_context(
            loop.approval_seed(
                tool_calls_used=1,
                exposure_layer="execution",
                requested_tool="vrcforge_create_gameobject",
                requested_arguments={"name": "Probe"},
            ),
            tool="vrcforge_create_gameobject",
            arguments={"name": "Probe"},
        )
        approval = {
            "id": "approval-blocked",
            "agentName": "desktop-agent",
            "arguments": {"name": "Probe"},
            "taskContext": context,
        }

        continuation = self.gateway.resume_runtime_task_after_approval(
            approval,
            {"ok": False, "status": "blocked_concurrent_write"},
        )

        self.assertIsNone(continuation)

    def test_blocked_approved_write_can_retry_and_resume_the_same_task_once(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        applied_result = dashboard_server.McpResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            payload={
                "data": {
                    "ok": True,
                    "gameObjectPath": "RetryApprovalProbe",
                    "persistedReadback": True,
                    "sceneSaved": True,
                }
            },
        )

        def invoke_with_bound_execution(_settings, tool_name, arguments, **_kwargs):
            plan = current_approved_unity_execution()
            self.assertIsNotNone(plan)
            claim = plan.claim(tool_name, arguments, project)
            claim.complete()
            return applied_result

        with TestClient(dashboard_server.app) as client:
            initial = client.post(
                "/api/app/agent/message",
                json={
                    "message": "create an object named RetryApprovalProbe at the scene root",
                    "projectPath": str(project),
                    "session_id": "retry-approval-loop-session",
                },
            ).json()
            approval_id = initial["approvalId"]
            with gateway._lock:
                gateway._in_flight_apply_writes["other-write"] = {
                    "approvalId": "other-write",
                    "targetTool": "fixture",
                }
            try:
                blocked = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project), "globalOnly": False},
                )
            finally:
                with gateway._lock:
                    gateway._in_flight_apply_writes.pop("other-write", None)
            blocked_approval_status = gateway._approvals[approval_id]["status"]

            with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
                "dashboard_server.invoke_unity_mcp",
                side_effect=invoke_with_bound_execution,
            ) as invoke:
                retried = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project), "globalOnly": False},
                )

        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(blocked.json()["execution"]["status"], "blocked_concurrent_write")
        self.assertNotIn("continuation", blocked.json())
        self.assertEqual(blocked_approval_status, "approved")
        self.assertEqual(gateway._approvals[approval_id]["status"], "applied")
        self.assertEqual(retried.status_code, 200)
        continuation = retried.json()["continuation"]
        self.assertEqual(continuation["sessionId"], "retry-approval-loop-session")
        self.assertEqual(continuation["task"]["taskId"], initial["task"]["taskId"])
        self.assertEqual(continuation["plan"]["nextStep"], "done")
        invoke.assert_called_once()

    def test_blocked_approved_write_can_be_rejected_before_retry(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        with TestClient(dashboard_server.app) as client:
            initial = client.post(
                "/api/app/agent/message",
                json={
                    "message": "create an object named CancelledRetryProbe at the scene root",
                    "projectPath": str(project),
                    "session_id": "cancel-retry-approval-session",
                },
            ).json()
            approval_id = initial["approvalId"]
            with gateway._lock:
                gateway._in_flight_apply_writes["other-write"] = {
                    "approvalId": "other-write",
                    "targetTool": "fixture",
                }
            try:
                blocked = client.post(
                    f"/api/app/agent/approvals/{approval_id}/approve",
                    json={"expectedProjectRoot": str(project), "globalOnly": False},
                )
            finally:
                with gateway._lock:
                    gateway._in_flight_apply_writes.pop("other-write", None)
            rejected = client.post(
                f"/api/app/agent/approvals/{approval_id}/reject",
                json={"expectedProjectRoot": str(project), "globalOnly": False},
            )

        self.assertEqual(blocked.json()["execution"]["status"], "blocked_concurrent_write")
        self.assertEqual(rejected.status_code, 200)
        self.assertTrue(rejected.json()["ok"])
        self.assertEqual(gateway._approvals[approval_id]["status"], "rejected")
        self.assertEqual(rejected.json()["continuation"]["sessionId"], "cancel-retry-approval-session")
        self.assertEqual(rejected.json()["continuation"]["plan"]["nextStep"], "needs_user_action")
        cannot_apply = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
        self.assertFalse(cannot_apply["ok"])
        self.assertEqual(cannot_apply["status"], "rejected")

    def test_scene_root_write_intent_requires_an_unambiguous_scene_root_phrase(self) -> None:
        english = detect_avatar_write_intent("create an object named RootProbe at the scene root")
        chinese = detect_avatar_write_intent("在活动场景根节点创建一个名为根节点探针的对象")
        project_root = detect_avatar_write_intent("create an object in the project root")

        self.assertEqual(english["targetMode"], "scene_root")
        self.assertEqual(chinese["targetMode"], "scene_root")
        self.assertEqual(project_root["targetMode"], "")
        self.assertIsNone(detect_avatar_write_intent("inspect the scene root"))

    def test_scene_root_and_avatar_target_conflict_fails_closed(self) -> None:
        plan = self.gateway.runtime_planner.plan_agent_turn(
            "create an object at the scene root",
            {"avatarPath": "AvatarRoot"},
            {},
            loop_state=[],
        )

        self.assertIsNotNone(plan)
        self.assertTrue(plan["deterministicTerminal"])
        self.assertEqual(plan["nextStep"], "done")
        self.assertFalse(plan["writeNeeded"])
        self.assertFalse(plan["skillNeeded"])

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

        with patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=lambda _owner, tool, params, agent_name=None, owner_id="": fake_skill(tool, params, agent_name),
        ):
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

    def test_unplanned_message_provider_failure_is_typed_not_fake_disconnect(self) -> None:
        gateway = self.gateway

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=RuntimeError("no provider connected"),
        ):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={"message": "just chat with me about the weather"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        plan = payload["plan"]
        self.assertEqual(plan["planner"], "llm")
        self.assertTrue(plan.get("plannerFailed"))
        self.assertEqual(plan.get("plannerFailure", {}).get("phase"), "initial")
        self.assertEqual(plan.get("plannerFailure", {}).get("code"), "provider_request_failed")
        self.assertTrue(plan.get("deterministicTerminal"))
        self.assertEqual(plan.get("nextStep"), "planner_failed")
        self.assertNotIn("还没接上可用的模型 Provider", plan.get("reply", ""))
        self.assertNotIn("write", payload)
        self.assertNotIn("skill", payload)
        self.assertEqual(payload.get("steps", []), [])

    def test_projectless_runtime_shell_auto_yields_to_a_controllable_session(self) -> None:
        gateway = self.gateway
        calls: list[dict] = []

        def execute_shell(params, agent_name="desktop-agent", *, task_context=None):
            calls.append({**params, "agentName": agent_name})
            return {
                "ok": True,
                "status": "running",
                "sessionId": "shell-fixture",
                "session": {"sessionId": "shell-fixture", "status": "running"},
                "classification": {"risk": "low", "protectionScope": "host"},
            }

        with patch.object(gateway.shell, "execute", side_effect=execute_shell):
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "run a long host task",
                        "shell_command": "python worker.py",
                        "cwd": str(Path.cwd()),
                        "sessionId": "temporary-shell-chat",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["yieldMs"], 10_000)
        self.assertEqual(calls[0]["timeout"], 30 * 60)
        self.assertEqual(calls[0]["projectRoot"], "")
        self.assertEqual(response.json()["shell"]["sessionId"], "shell-fixture")

    def test_post_tool_provider_failure_preserves_skill_result_and_marks_run_failed(self) -> None:
        gateway = self.gateway
        llm_results = [
            SimpleNamespace(
                text='{"action":"skill","skill_tool":"vrcforge_scan_materials","skill_params":{}}',
                usage={},
                reasoning={},
            ),
            RuntimeError("upstream stream ended token=secret"),
        ]
        skill_calls: list[str] = []

        def fake_llm(*_args, **_kwargs):
            value = llm_results.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        def fake_skill(_owner, tool, params, agent_name=None, owner_id=""):
            skill_calls.append(tool)
            return {
                "tool": tool,
                "status": "executed",
                "result": {"materialCount": 3},
            }

        with patch("dashboard_server.request_llm_plan_with_metadata", side_effect=fake_llm):
            with patch.object(
                type(gateway.runtime_skills),
                "execute",
                autospec=True,
                side_effect=fake_skill,
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/agent/message",
                        json={
                            "message": "帮我整理列出未被使用的贴图",
                            "provider": "deepseek",
                            "providerLabel": "DeepSeek",
                            "model": "fixture-model",
                            "sessionId": "planner-failure-session",
                            "clientTurnId": "planner-failure-turn",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(skill_calls, ["vrcforge_scan_materials"])
        self.assertEqual(len(payload.get("steps") or []), 1)
        self.assertEqual(payload["skill"]["result"]["materialCount"], 3)
        plan = payload["plan"]
        self.assertEqual(plan.get("nextStep"), "planner_failed")
        self.assertEqual(plan.get("plannerFailure", {}).get("phase"), "post_tool")
        self.assertEqual(plan.get("plannerFailure", {}).get("code"), "provider_connection_failed")
        self.assertTrue(plan.get("providerConnected"))
        self.assertIn("结果也已保留", plan.get("reply", ""))
        self.assertNotIn("没配置", plan.get("reply", ""))
        self.assertNotIn("secret", str(payload))

        completed = [
            event
            for event in gateway.runtime_runs.read_events()
            if event.get("event") == "runtime_turn_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["status"], "failed")

    def test_model_cannot_claim_completion_after_canonical_tool_failure(self) -> None:
        gateway = self.gateway
        llm_results = iter(
            [
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"vrcforge_scan_materials","skill_params":{}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"reply","reply":"完成了。","summary":"done"}',
                    usage={},
                    reasoning={},
                ),
            ]
        )

        def fake_skill(_owner, tool, params, agent_name=None, owner_id=""):
            return {
                "ok": False,
                "tool": tool,
                "status": "failed",
                "result": {"ok": False, "error": "material scan failed"},
                "outcome": {
                    "schema": "vrcforge.tool_result.v1",
                    "status": "failed",
                    "summary": "material scan failed",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=lambda *_args, **_kwargs: next(llm_results),
        ):
            with patch.object(
                type(gateway.runtime_skills),
                "execute",
                autospec=True,
                side_effect=fake_skill,
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/agent/message",
                        json={
                            "message": "检查材质并确认完成",
                            "provider": "deepseek",
                            "providerLabel": "DeepSeek",
                            "model": "fixture-model",
                            "sessionId": "failed-completion-session",
                            "clientTurnId": "failed-completion-turn",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan"]["nextStep"], "tool_failed")
        self.assertEqual(payload["plan"]["reply"], "material scan failed")
        self.assertNotIn("完成了", payload["plan"]["reply"])
        completed = [
            event
            for event in gateway.runtime_runs.read_events()
            if event.get("event") == "runtime_turn_completed"
        ]
        self.assertEqual(completed[-1]["status"], "failed")

    def test_model_cannot_claim_completion_after_shell_failure(self) -> None:
        gateway = self.gateway
        llm_results = iter(
            [
                SimpleNamespace(
                    text='{"action":"shell","shell_command":"python missing.py"}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"reply","reply":"done","summary":"done"}',
                    usage={},
                    reasoning={},
                ),
            ]
        )

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=lambda *_args, **_kwargs: next(llm_results),
        ):
            with patch.object(
                gateway.shell,
                "execute",
                return_value={
                    "ok": False,
                    "status": "failed",
                    "result": {"ok": False, "error": "shell failed"},
                },
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/agent/message",
                        json={
                            "message": "执行这个主机检查",
                            "provider": "deepseek",
                            "providerLabel": "DeepSeek",
                            "model": "fixture-model",
                            "sessionId": "failed-shell-session",
                            "clientTurnId": "failed-shell-turn",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan"]["nextStep"], "tool_failed")
        self.assertEqual(payload["plan"]["reply"], "shell failed")
        self.assertEqual(payload["shell"]["outcome"]["status"], "failed")
        completed = [
            event
            for event in gateway.runtime_runs.read_events()
            if event.get("event") == "runtime_turn_completed"
        ]
        self.assertEqual(completed[-1]["status"], "failed")

    def test_unrelated_success_cannot_clear_an_unresolved_tool_failure(self) -> None:
        gateway = self.gateway
        llm_results = iter(
            [
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"vrcforge_scan_materials","skill_params":{}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"vrcforge_health","skill_params":{}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"reply","reply":"done","summary":"done"}',
                    usage={},
                    reasoning={},
                ),
            ]
        )

        def fake_skill(_owner, tool, params, agent_name=None, owner_id=""):
            if tool == "vrcforge_scan_materials":
                return {
                    "ok": False,
                    "tool": tool,
                    "status": "failed",
                    "result": {"ok": False, "error": "material scan failed"},
                    "outcome": {
                        "status": "failed",
                        "summary": "material scan failed",
                        "verification": {"state": "not_required", "checks": []},
                    },
                }
            return {
                "ok": True,
                "tool": tool,
                "status": "executed",
                "result": {"ok": True},
                "outcome": {
                    "status": "ok",
                    "summary": "runtime healthy",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=lambda *_args, **_kwargs: next(llm_results),
        ):
            with patch.object(
                type(gateway.runtime_skills),
                "execute",
                autospec=True,
                side_effect=fake_skill,
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/agent/message",
                        json={
                            "message": "完成这个复杂检查",
                            "provider": "deepseek",
                            "providerLabel": "DeepSeek",
                            "model": "fixture-model",
                            "sessionId": "unrelated-success-session",
                            "clientTurnId": "unrelated-success-turn",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([step["tool"] for step in payload["steps"]], [
            "vrcforge_scan_materials",
            "vrcforge_health",
        ])
        self.assertEqual(payload["plan"]["nextStep"], "tool_failed")
        self.assertEqual(payload["plan"]["reply"], "material scan failed")
        completed = [
            event
            for event in gateway.runtime_runs.read_events()
            if event.get("event") == "runtime_turn_completed"
        ]
        self.assertEqual(completed[-1]["status"], "failed")

    def test_retrying_one_failed_action_cannot_erase_another_unresolved_failure(self) -> None:
        gateway = self.gateway
        llm_results = iter(
            [
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"vrcforge_scan_materials","skill_params":{"avatarPath":"A"}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"vrcforge_health","skill_params":{"scope":"B"}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"vrcforge_health","skill_params":{"scope":"B"}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"reply","reply":"done","summary":"done"}',
                    usage={},
                    reasoning={},
                ),
            ]
        )
        health_attempts = 0

        def fake_skill(_owner, tool, params, agent_name=None, owner_id=""):
            nonlocal health_attempts
            if tool == "vrcforge_scan_materials":
                summary = "material scan failed"
                status = "failed"
            else:
                health_attempts += 1
                summary = "health failed" if health_attempts == 1 else "health recovered"
                status = "failed" if health_attempts == 1 else "ok"
            return {
                "ok": status == "ok",
                "tool": tool,
                "status": "executed" if status == "ok" else "failed",
                "result": {"ok": status == "ok"},
                "outcome": {
                    "status": status,
                    "summary": summary,
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=lambda *_args, **_kwargs: next(llm_results),
        ):
            with patch.object(
                type(gateway.runtime_skills),
                "execute",
                autospec=True,
                side_effect=fake_skill,
            ):
                with TestClient(dashboard_server.app) as client:
                    response = client.post(
                        "/api/app/agent/message",
                        json={
                            "message": "完成多项检查",
                            "provider": "deepseek",
                            "providerLabel": "DeepSeek",
                            "model": "fixture-model",
                            "sessionId": "multiple-failure-session",
                            "clientTurnId": "multiple-failure-turn",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan"]["nextStep"], "tool_failed")
        self.assertEqual(payload["plan"]["reply"], "material scan failed")
        completed = [
            event
            for event in gateway.runtime_runs.read_events()
            if event.get("event") == "runtime_turn_completed"
        ]
        self.assertEqual(completed[-1]["status"], "failed")

    def test_provider_model_followup_replies_without_tooling(self) -> None:
        gateway = self.gateway
        with patch.object(type(gateway.runtime_skills), "execute", autospec=True) as execute_skill:
            with TestClient(dashboard_server.app) as client:
                response = client.post(
                    "/api/app/agent/message",
                    json={
                        "message": "追问：只回答上一条用了哪个供应商和模型。",
                        "history": [
                            {"role": "user", "text": "hello"},
                            {"role": "agent", "text": "hi"},
                        ],
                        "provider": "deepseek",
                        "providerLabel": "DeepSeek",
                        "model": "deepseek-v4-pro",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        plan = payload["plan"]
        self.assertTrue(plan.get("deterministicTerminal"))
        self.assertEqual(plan.get("nextStep"), "done")
        self.assertIn("DeepSeek", plan.get("reply", ""))
        self.assertIn("deepseek-v4-pro", plan.get("reply", ""))
        self.assertNotIn("write", payload)
        self.assertNotIn("skill", payload)
        self.assertEqual(payload.get("steps", []), [])
        execute_skill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
