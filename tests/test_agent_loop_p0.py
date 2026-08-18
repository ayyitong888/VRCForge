"""Focused P0 regression tests for the VRCForge agent runtime loop.

These cover the post-1.1.0 P0 path: the runtime turn is a bounded agentic loop
(observe -> plan -> act -> feed result back), with Provider-planned
auto-resolution for "add an object to the model" write intents, real approval
creation, approved dispatch to the static GameObject primitive, and an honest
"not connected / cannot plan" terminal instead of a fake success reply.

The tests do not require a live Unity Editor: the final Unity MCP invocation is
mocked, while the agent loop, approval request, checkpoint boundary, write
handler dispatch, and fallback behavior are exercised through the real backend
path.
"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard_server
from approved_unity_execution import current_approved_unity_execution
from agent_harness_journey import RuntimeJourneyReceiptAuthority
from agent_task_loop import (
    AgentTaskLoop,
    approval_completion,
    approval_task_context,
    canonical_action_id,
)
from provider_configuration_service import ProviderApiConfig
from runtime_planner_service import PlannerModelResult


def provider_plan_fixture(test):
    """Drive focused approval-loop journeys with explicit Provider plans."""

    @wraps(test)
    def wrapped(self, *args, **kwargs):
        with self._provider_plan_fixture():
            return test(self, *args, **kwargs)

    return wrapped


class AgentLoopP0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = dashboard_server.AGENT_GATEWAY
        # TestClient shutdown closes the process-owned Provider planner. Each
        # unittest method models a fresh live App process, so reopen that owner
        # explicitly instead of weakening production shutdown fail-closed.
        dashboard_server._RUNTIME_PLANNER_MODEL.start()
        self.gateway.shell.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (self.gateway.config_path, self.gateway.audit_dir)
        self.original_shell_path_guard = self.gateway.shell._project_path_guard
        guard = dashboard_server.UNITY_PROJECT_PATH_GUARD
        self.original_unity_guard_state = (guard.registered_roots, guard.current_root)
        self.gateway.shell.bind_project_path_guard(
            lambda: dashboard_server.UNITY_PROJECT_PATH_GUARD
        )
        self.original_runtime_continuation_accepting = (
            self.gateway._runtime_continuation_accepting
        )
        self.gateway.start_runtime_continuations()
        self.original_prepare = self.gateway.approval_transactions.checkpoint_prepare_handler
        self.original_create_checkpoint_prepare = self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].checkpoint_prepare_handler
        self.original_create_verification_prepare = self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].verification_prepare_handler
        self.original_create_verification_finalize = self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].verification_finalize_handler
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
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].verification_prepare_handler = lambda _arguments: {"diagnosticIds": []}
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].verification_finalize_handler = (
            lambda _arguments, _baseline, result: {**result, "consoleVerified": True}
        )

    def tearDown(self) -> None:
        self.gateway.approval_transactions.checkpoint_prepare_handler = self.original_prepare
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].checkpoint_prepare_handler = self.original_create_checkpoint_prepare
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].verification_prepare_handler = self.original_create_verification_prepare
        self.gateway._write_handlers[
            "vrcforge_create_gameobject"
        ].verification_finalize_handler = self.original_create_verification_finalize
        self.gateway.configure_paths(*self.original_paths)
        guard = dashboard_server.UNITY_PROJECT_PATH_GUARD
        original_roots, original_current = self.original_unity_guard_state
        guard.replace_roots(original_roots)
        if original_current:
            guard.set_current_root(original_current)
        if self.original_shell_path_guard is not None:
            self.gateway.shell.bind_project_path_guard(self.original_shell_path_guard)
        if not self.original_runtime_continuation_accepting:
            self.gateway.shutdown_runtime_continuations(0)
        self.temp_dir.cleanup()

    def test_duplicate_active_client_turn_is_rejected_before_provider_or_tools(self) -> None:
        session_id = "duplicate-session"
        client_turn_id = "duplicate-client-turn"
        assert self.gateway._runtime_session_state.begin_turn(
            session_id=session_id,
            turn_id="original-runtime-turn",
            client_turn_id=client_turn_id,
        )

        with self.assertRaises(dashboard_server.AgentGatewayError) as duplicate:
            self.gateway.runtime_message(
                {
                    "message": "must not reach the Provider",
                    "sessionId": session_id,
                    "clientTurnId": client_turn_id,
                }
            )

        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertTrue(
            self.gateway._runtime_session_state.submit_steer(
                session_id=session_id,
                target_client_turn_id=client_turn_id,
                input_id="original-owner-still-active",
                message="original owner still active",
            )["accepted"]
        )
        self.gateway._runtime_session_state.finish_turn(
            session_id=session_id,
            turn_id="original-runtime-turn",
            client_turn_id=client_turn_id,
        )

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
        guard = dashboard_server.UNITY_PROJECT_PATH_GUARD
        guard.register_root(project)
        guard.set_current_root(project)
        return project

    @contextmanager
    def _provider_plan_fixture(self):
        planner = self.gateway.runtime_planner

        def provider_plan(message, params, _observe, *args, **kwargs):
            lowered = str(message or "").casefold()
            loop_state = list(kwargs.get("loop_state") or [])
            base = {"planner": "llm", "plannerLabel": "Fixture Provider"}
            if "上一条用了哪个供应商和模型" in str(message or ""):
                provider = params.get("providerLabel") or params.get("provider") or "Provider"
                model = params.get("model") or "model"
                return {
                    **base,
                    "summary": f"{provider} / {model}",
                    "reply": f"The previous response used {provider} / {model}.",
                    "continueLoop": False,
                    "nextStep": "done",
                }
            if "capture front and back views" in lowered:
                return {
                    **base,
                    "summary": "Capture the requested front and back coverage views.",
                    "writeNeeded": True,
                    "writeTool": "vrcforge_capture_multi_screenshot",
                    "writeParams": {"angles": ["front", "back"]},
                    "continueLoop": False,
                    "nextStep": "call_tool",
                }
            if params.get("shell_command") or params.get("shellCommand"):
                command = params.get("shell_command") or params.get("shellCommand")
                project_root = params.get("projectRoot") or params.get("projectPath")
                return {
                    **base,
                    "summary": "Run the requested Shell command.",
                    "shellNeeded": True,
                    "shellCommand": command,
                    "shellParams": {},
                    "toolCapabilities": (
                        ["unity_project_access"] if project_root else []
                    ),
                    "continueLoop": False,
                    "nextStep": "call_shell",
                }
            if "scene root" in lowered and params.get("avatarPath"):
                return {
                    **base,
                    "summary": "The scene-root and avatar targets conflict.",
                    "reply": "Choose either the scene root or the named avatar target before writing.",
                    "writeNeeded": False,
                    "skillNeeded": False,
                    "continueLoop": False,
                    "nextStep": "needs_user_action",
                }
            if "scene root" in lowered:
                known_names = (
                    "VRCForgeMCP2AppAcceptance",
                    "ApprovalLoopProbe",
                    "RejectedProbe",
                    "FailedApprovalProbe",
                    "RetryApprovalProbe",
                    "CancelledRetryProbe",
                )
                name = next((item for item in known_names if item.casefold() in lowered), "GameObject")
                return {
                    **base,
                    "summary": f"Create {name} at the scene root.",
                    "writeNeeded": True,
                    "writeTool": "vrcforge_create_gameobject",
                    "writeParams": {
                        "name": name,
                        "parentPath": "",
                        "projectPath": params.get("projectRoot") or params.get("projectPath") or "",
                    },
                    "resolvedTarget": "scene_root",
                    "continueLoop": False,
                    "nextStep": "call_tool",
                }
            if "add a new object to the model" in lowered:
                avatar_result = next(
                    (
                        item.get("result")
                        for item in reversed(loop_state)
                        if item.get("tool") == "vrcforge_list_avatars"
                        and isinstance(item.get("result"), dict)
                    ),
                    None,
                )
                avatars = list((avatar_result or {}).get("avatars") or [])
                if not avatars:
                    return {
                        **base,
                        "summary": "Inspect the available avatars before choosing a parent.",
                        "skillNeeded": True,
                        "skillTool": "vrcforge_list_avatars",
                        "skillParams": {},
                        "continueLoop": True,
                        "nextStep": "call_skill",
                    }
                if len(avatars) > 1:
                    return {
                        **base,
                        "summary": "Multiple avatars require an explicit target choice.",
                        "reply": "Multiple avatars are available. Choose the target avatar before writing.",
                        "continueLoop": False,
                        "nextStep": "needs_user_action",
                    }
                parent_path = str(avatars[0].get("avatarPath") or "")
                return {
                    **base,
                    "summary": "Create the requested object under the only available avatar.",
                    "writeNeeded": True,
                    "writeTool": "vrcforge_create_gameobject",
                    "writeParams": {
                        "name": "GameObject",
                        "parentPath": parent_path,
                        "projectPath": params.get("projectRoot") or params.get("projectPath") or "",
                    },
                    "continueLoop": False,
                    "nextStep": "call_tool",
                }
            raise AssertionError(f"missing explicit Provider fixture for: {message}")

        with patch.object(planner, "plan_agent_turn", side_effect=provider_plan):
            yield

    @provider_plan_fixture
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

    @provider_plan_fixture
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

    @provider_plan_fixture
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
        self.assertEqual(
            continuation["plan"]["nextStep"],
            "done",
            {
                "gate": continuation["plan"].get("completionGate"),
                "requirements": continuation["task"].get("requirements"),
                "actions": continuation["task"].get("actions"),
                "claim": continuation["plan"].get("completionClaim"),
            },
        )
        self.assertEqual(
            continuation["plan"]["taskCompletion"]["taskId"],
            initial["task"]["taskId"],
        )
        self.assertEqual(
            [step["actionId"] for step in continuation["steps"] if step.get("kind") in {"skill", "write", "shell"}],
            [action["actionId"] for action in continuation["task"]["actions"]],
        )
        self.assertTrue(continuation["steps"][0]["historical"])
        self.assertEqual(
            continuation["steps"][0].get("result"),
            payload["execution"].get("result"),
        )
        mock_invoke.assert_called_once()

    @provider_plan_fixture
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
        self.assertEqual(task_context["tool"], "unity_shell")
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
        self.assertEqual(continuation["plan"]["nextStep"], "done", continuation)
        self.assertEqual(continuation["task"]["status"], "completed")
        execute_payload.assert_called_once()

    @provider_plan_fixture
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

    @provider_plan_fixture
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
            raw_result={
                "ok": True,
                "persistedReadback": True,
                "sceneSaved": True,
                "consoleVerified": True,
            },
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

    def test_background_shell_terminal_event_resumes_the_same_task_exactly_once(self) -> None:
        gateway = self.gateway
        arguments = {"command": "python worker.py", "background": True}
        loop = AgentTaskLoop(
            "run the worker and report its result",
            session_id="shell-resume-session",
            client_turn_id="shell-resume-client",
            agent_name="desktop-agent",
        )
        loop.require_action(kind="shell", tool="shell", arguments=arguments)
        action_id = canonical_action_id("shell", "shell", arguments)
        event = {
            "shellSessionId": "shell-session-terminal-1",
            "status": "finished",
            "exitCode": 0,
            "timedOut": False,
            "cancelled": False,
            "terminationFailed": False,
            "result": {
                "ok": True,
                "exitCode": 0,
                "stdout": "worker-marker-42",
                "stderr": "",
                "stdoutTruncated": False,
                "sessionId": "shell-session-terminal-1",
            },
            "taskSeed": loop.approval_seed(
                tool_calls_used=1,
                exposure_layer="planning",
                requested_kind="shell",
                requested_tool="shell",
                requested_arguments=arguments,
                continue_after_approval=True,
            ),
        }
        terminal_plan = {
            "summary": "The worker finished.",
            "reply": "The worker finished.",
            "planner": "llm",
            "continueLoop": False,
            "nextStep": "done",
            "completionClaim": {
                "satisfied": True,
                "evidenceActionIds": [action_id],
            },
        }

        observed_loop_state: list[dict] = []

        def terminal_reply(_message, _params, _observe, _history=None, *, loop_state=None, **_kwargs):
            observed_loop_state.extend(list(loop_state or []))
            return terminal_plan

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=terminal_reply,
        ) as planner, patch.object(
            type(gateway.shell),
            "execute",
            autospec=True,
            side_effect=AssertionError("the completed Shell command must not be replayed"),
        ):
            first = gateway.resume_runtime_task_after_shell(event)
            second = gateway.resume_runtime_task_after_shell(event)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(first["sessionId"], "shell-resume-session")
        self.assertEqual(first["continuationSource"], "shell_process_finished")
        self.assertEqual(first["plan"]["nextStep"], "done")
        self.assertEqual(first["plan"]["taskCompletion"]["evidenceActionIds"], [action_id])
        self.assertTrue(
            any(
                item.get("result", {}).get("stdoutSummary") == "worker-marker-42"
                for item in observed_loop_state
            )
        )
        planner.assert_called_once()

    def test_loaded_skill_instructions_reenter_planning_without_becoming_completion_evidence(self) -> None:
        gateway = self.gateway
        planner_calls: list[list[dict]] = []

        def plan_next(_message, _params, _observe, _history=None, *, loop_state=None, **_kwargs):
            planner_calls.append(list(loop_state or []))
            if len(planner_calls) == 1:
                return {
                    "summary": "Load the installed workflow instructions.",
                    "reply": "",
                    "planner": "llm",
                    "skillNeeded": True,
                    "skillTool": "fixture-guidance",
                    "skillParams": {},
                    "writeNeeded": False,
                    "shellNeeded": False,
                    "continueLoop": False,
                    "nextStep": "call_skill",
                }
            return {
                "summary": "No real tool action was executed.",
                "reply": "I still need to execute the instructed tool.",
                "planner": "llm",
                "continueLoop": False,
                "nextStep": "done",
                "completionClaim": {"satisfied": True, "evidenceActionIds": []},
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=plan_next,
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            return_value={
                "ok": True,
                "status": "loaded",
                "tool": "fixture-guidance",
                "result": {
                    "name": "fixture-guidance",
                    "instructions": "Call vrcforge_health, then inspect its result.",
                    "allowedTools": ["vrcforge_health"],
                    "disallowedTools": [],
                },
                "outcome": {
                    "status": "ok",
                    "summary": "Skill instructions loaded.",
                    "verification": {"state": "not_required", "checks": []},
                },
            },
        ):
            result = gateway.runtime_message(
                {"message": "use fixture guidance", "session_id": "loaded-skill-session"}
            )

        self.assertEqual(len(planner_calls), 2)
        self.assertEqual(
            planner_calls[1][0]["skillContext"]["instructions"],
            "Call vrcforge_health, then inspect its result.",
        )
        self.assertEqual(result["plan"]["nextStep"], "completion_unverified")
        self.assertEqual(result["plan"]["completionGate"]["reason"], "required_action_missing")
        self.assertNotIn("taskCompletion", result["plan"])

    def test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence(self) -> None:
        gateway = self.gateway
        allowed_action_id = canonical_action_id("skill", "vrcforge_health", {})
        plans = iter(
            [
                {
                    "summary": "Load the installed workflow instructions.",
                    "planner": "llm",
                    "skillNeeded": True,
                    "skillTool": "fixture-guidance",
                    "skillParams": {},
                    "continueLoop": False,
                    "nextStep": "call_skill",
                },
                {
                    "summary": "Try a tool outside the Skill policy.",
                    "planner": "llm",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_unity_status",
                    "skillParams": {},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "summary": "Use the allowed health inspection.",
                    "planner": "llm",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_health",
                    "skillParams": {},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "summary": "The instructed inspection completed.",
                    "reply": "The health inspection completed.",
                    "planner": "llm",
                    "continueLoop": False,
                    "nextStep": "done",
                    "completionClaim": {
                        "satisfied": True,
                        "evidenceActionIds": [allowed_action_id],
                    },
                },
            ]
        )
        executed: list[str] = []

        def execute_skill(_owner, tool, _params, agent_name=None, owner_id=""):
            executed.append(tool)
            if tool == "fixture-guidance":
                return {
                    "ok": True,
                    "status": "loaded",
                    "tool": tool,
                    "result": {
                        "name": tool,
                        "instructions": "Call vrcforge_health and inspect the result.",
                        "allowedTools": ["vrcforge_health"],
                        "disallowedTools": ["vrcforge_unity_status"],
                    },
                    "outcome": {"status": "ok", "summary": "Skill instructions loaded."},
                }
            return {
                "ok": True,
                "status": "executed",
                "tool": tool,
                "result": {"healthy": True},
                "outcome": {"status": "ok", "summary": "Health inspected."},
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {"message": "use fixture guidance", "session_id": "skill-policy-session"}
            )

        self.assertEqual(executed, ["fixture-guidance", "vrcforge_health"])
        self.assertEqual(result["plan"]["nextStep"], "done")
        self.assertEqual(
            result["plan"]["taskCompletion"]["evidenceActionIds"],
            [allowed_action_id],
        )
        self.assertTrue(
            any(
                step.get("tool") == "vrcforge_unity_status" and step.get("status") == "blocked"
                for step in result["steps"]
            )
        )

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

    @provider_plan_fixture
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

    @provider_plan_fixture
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

    @provider_plan_fixture
    def test_scene_root_and_avatar_target_conflict_fails_closed(self) -> None:
        plan = self.gateway.runtime_planner.plan_agent_turn(
            "create an object at the scene root",
            {"avatarPath": "AvatarRoot"},
            {},
            loop_state=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan["nextStep"], "needs_user_action")
        self.assertFalse(plan["writeNeeded"])
        self.assertFalse(plan["skillNeeded"])

    @provider_plan_fixture
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
        self.assertEqual(payload["plan"].get("nextStep"), "needs_user_action")
        self.assertNotIn("taskCompletion", payload["plan"])
        self.assertEqual(
            payload["task"]["actions"][0]["tool"],
            "vrcforge_list_avatars",
        )
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
        self.assertEqual(plan.get("nextStep"), "planner_failed")
        self.assertNotIn("还没接上可用的模型 Provider", plan.get("reply", ""))
        self.assertNotIn("write", payload)
        self.assertNotIn("skill", payload)
        self.assertEqual(payload.get("steps", []), [])

    @provider_plan_fixture
    def test_projectless_runtime_shell_auto_yields_to_a_controllable_session(self) -> None:
        gateway = self.gateway
        calls: list[dict] = []

        def execute_shell(
            params,
            agent_name="desktop-agent",
            *,
            task_context=None,
            unity_project_access=False,
        ):
            calls.append({**params, "agentName": agent_name, "taskContext": task_context})
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
                initial = response.json()
                continuation = gateway.resume_runtime_task_after_shell(
                    {
                        "runtimeSessionId": initial["sessionId"],
                        "turnId": initial["turnId"],
                        "clientTurnId": "",
                        "taskSeed": calls[0]["taskContext"],
                        "shellSessionId": "shell-fixture",
                        "status": "finished",
                        "exitCode": 0,
                        "result": {"ok": True, "exitCode": 0, "stdout": "finished"},
                    }
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["yieldMs"], 10_000)
        self.assertEqual(calls[0]["timeout"], 30 * 60)
        self.assertEqual(calls[0]["projectRoot"], "")
        self.assertEqual(initial["shell"]["sessionId"], "shell-fixture")
        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["plan"]["nextStep"], "done")
        self.assertEqual(
            initial["task"]["requirements"][0]["actionId"],
            continuation["task"]["actions"][-1]["actionId"],
        )

    def test_running_llm_shell_yields_before_any_follow_up_tool(self) -> None:
        gateway = self.gateway
        planner_calls = 0
        skill_calls: list[str] = []
        captured_task_context: list[dict] = []

        def fake_llm(*_args, **_kwargs):
            nonlocal planner_calls
            planner_calls += 1
            if planner_calls == 1:
                return SimpleNamespace(
                    text='{"action":"shell","shell_command":"python worker.py","summary":"run worker"}',
                    usage={},
                    reasoning={},
                )
            return SimpleNamespace(
                text='{"action":"skill","skill_tool":"unity_health","skill_params":{}}',
                usage={},
                reasoning={},
            )

        def execute_shell(
            _params,
            agent_name="desktop-agent",
            *,
            task_context=None,
            unity_project_access=False,
        ):
            captured_task_context.append(dict(task_context or {}))
            return {
                "ok": True,
                "status": "running",
                "sessionId": "shell-llm-running",
                "session": {"sessionId": "shell-llm-running", "status": "running"},
            }

        with patch(
            "dashboard_server.request_llm_plan_with_metadata", side_effect=fake_llm
        ), patch.object(
            gateway.shell, "execute", side_effect=execute_shell
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=lambda _owner, tool, *_args, **_kwargs: skill_calls.append(tool),
        ):
            payload = gateway.runtime_message(
                {
                    "message": "run the worker and then inspect health",
                    "provider": "fixture",
                    "model": "fixture",
                    "session_id": "shell-llm-session",
                }
            )

        self.assertEqual(planner_calls, 1)
        self.assertEqual(skill_calls, [])
        self.assertEqual(payload["plan"]["nextStep"], "waiting_for_tool")
        self.assertEqual(captured_task_context[0]["toolCallsUsed"], 1)
        self.assertEqual(captured_task_context[0]["actions"], [])

    def test_sub_agent_terminal_result_resumes_the_same_task_with_exact_evidence(self) -> None:
        gateway = self.gateway
        planner_calls = 0
        planner_prompts: list[str] = []
        created_params: list[dict] = []
        delegate_arguments = {
            "role": "project_index_review",
            "task": "find relevant prefabs",
            "displayName": "Manuka",
        }
        expected_action_id = canonical_action_id(
            "skill",
            "vrcforge_delegate_subagent",
            delegate_arguments,
        )

        def fake_llm(*_args, **_kwargs):
            nonlocal planner_calls
            planner_calls += 1
            planner_prompts.append(str(_args[1]))
            if planner_calls == 1:
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "delegate_subagent",
                            "skill_params": delegate_arguments,
                            "summary": "delegate the project review",
                        }
                    ),
                    usage={},
                    reasoning={},
                )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "action": "reply",
                        "reply": "The review found three relevant prefabs.",
                        "completion_claim": {
                            "satisfied": True,
                            "evidence_action_ids": [expected_action_id],
                        },
                    }
                ),
                usage={},
                reasoning={},
            )

        def create_task(_owner, **kwargs):
            created_params.append(kwargs)
            return {
                "ok": True,
                "task": {
                    "id": "sub-task-fixture",
                    "status": "queued",
                    "role": kwargs["role"],
                },
            }

        with patch(
            "dashboard_server.request_llm_plan_with_metadata", side_effect=fake_llm
        ), patch.object(
            type(dashboard_server.SUB_AGENT_COLLABORATION),
            "create_task",
            autospec=True,
            side_effect=create_task,
        ):
            initial = gateway.runtime_message(
                {
                    "message": "delegate a specialist to find the relevant prefabs",
                    "provider": "fixture",
                    "model": "fixture",
                    "session_id": "sub-agent-owner-session",
                    "client_turn_id": "sub-agent-owner-turn",
                }
            )
            self.assertEqual(initial["plan"]["nextStep"], "waiting_for_tool")
            seed = created_params[0]["params"]["_taskSeed"]
            continuation = gateway.resume_runtime_task_after_sub_agent(
                {
                    "subAgentTaskId": "sub-task-fixture",
                    "status": "completed",
                    "taskSeed": seed,
                    "summary": "found three relevant prefabs",
                    "result": {
                        "ok": True,
                        "summaryText": "found three relevant prefabs",
                        "plannerEvidence": {
                            "role": "project_index_review",
                            "prefabCandidateCount": 3,
                            "summary": "candidate-marker-73",
                        },
                    },
                }
            )

        self.assertIsNotNone(continuation)
        self.assertEqual(planner_calls, 2)
        self.assertIn("candidate-marker-73", planner_prompts[1])
        self.assertEqual(continuation["continuationSource"], "sub_agent_finished")
        self.assertEqual(continuation["plan"]["nextStep"], "done", continuation)
        self.assertEqual(continuation["task"]["actions"][-1]["actionId"], expected_action_id)
        self.assertEqual(
            continuation["plan"]["taskCompletion"]["evidenceActionIds"],
            [expected_action_id],
        )

    def test_external_delegate_call_cannot_forge_a_parent_task_seed(self) -> None:
        captured: list[dict] = []

        def create_task(_owner, **kwargs):
            captured.append(kwargs)
            return {
                "ok": True,
                "task": {
                    "id": "external-sub-task",
                    "status": "queued",
                    "role": kwargs["role"],
                },
            }

        with patch.object(
            type(dashboard_server.SUB_AGENT_COLLABORATION),
            "create_task",
            autospec=True,
            side_effect=create_task,
        ):
            result = self.gateway.call_tool(
                "vrcforge_delegate_subagent",
                {
                    "role": "project_index_review",
                    "task": "inspect",
                    "_runtimeSessionId": "victim-session",
                    "_runtimeClientTurnId": "victim-turn",
                    "_taskSeed": {
                        "schema": "vrcforge.agent_task_loop.v2",
                        "sessionId": "victim-session",
                    },
                },
                agent_name="external-agent",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(captured[0]["parent_session_id"], "")
        self.assertNotIn("_taskSeed", captured[0]["params"])

    @provider_plan_fixture
    def test_projectless_fast_terminal_shell_uses_one_inline_action_identity(self) -> None:
        gateway = self.gateway

        def execute_shell(
            params,
            agent_name="desktop-agent",
            *,
            task_context=None,
            unity_project_access=False,
        ):
            assert task_context is not None
            return {
                "ok": True,
                "status": "executed",
                "sessionId": "shell-fast-terminal",
                "session": {"sessionId": "shell-fast-terminal", "status": "finished"},
                "classification": {"risk": "low", "protectionScope": "host"},
                "result": {"ok": True, "exitCode": 0, "stdout": "finished marker"},
            }

        with patch.object(gateway.shell, "execute", side_effect=execute_shell):
            original = gateway.runtime_message(
                {
                    "message": "run a long host task",
                    "shell_command": "python worker.py",
                    "cwd": str(Path.cwd()),
                    "session_id": "fast-terminal-chat",
                    "client_turn_id": "client-fast-terminal",
                }
            )

        self.assertEqual(original["plan"]["nextStep"], "completion_unverified")
        self.assertEqual(original["task"]["status"], "completion_unverified")
        requirement = original["task"]["requirements"][0]
        completed = original["task"]["actions"][-1]
        self.assertEqual(requirement["actionId"], completed["actionId"])
        self.assertEqual(requirement["verificationProfile"], "shell_exit_zero")
        self.assertEqual(completed["outcome"]["verification"]["state"], "passed")
        self.assertEqual(
            completed["outcome"]["summary"],
            "Shell command completed successfully.",
        )

    def test_projectless_general_agent_corrects_one_unbound_completion_claim(self) -> None:
        gateway = self.gateway
        planner_states: list[list[dict]] = []

        def plan_next(
            _message,
            _params,
            _observe,
            _history=None,
            *,
            loop_state=None,
            **_kwargs,
        ):
            state = [dict(item) for item in (loop_state or [])]
            planner_states.append(state)
            if len(planner_states) == 1:
                return {
                    "planner": "llm",
                    "summary": "Inspect the requested directory.",
                    "shellNeeded": True,
                    "shellCommand": "Get-ChildItem -LiteralPath .",
                    "shellParams": {},
                    "continueLoop": True,
                    "nextStep": "call_shell",
                }
            if len(planner_states) == 2:
                return {
                    "planner": "llm",
                    "summary": "The directory was inspected.",
                    "reply": "I inspected the directory.",
                    "continueLoop": False,
                    "nextStep": "done",
                }
            completed_action_ids = [
                str(item.get("actionId") or "")
                for item in state
                if str(item.get("actionId") or "")
            ]
            return {
                "planner": "llm",
                "summary": "The directory inspection is complete.",
                "reply": "I inspected the directory and verified the result.",
                "continueLoop": False,
                "nextStep": "done",
                "completionClaim": {
                    "satisfied": True,
                    "evidenceActionIds": completed_action_ids,
                },
            }

        def execute_shell(
            _params,
            agent_name="desktop-agent",
            *,
            task_context=None,
            unity_project_access=False,
        ):
            self.assertIsNotNone(task_context)
            return {
                "ok": True,
                "status": "executed",
                "sessionId": "general-agent-shell",
                "session": {"sessionId": "general-agent-shell", "status": "finished"},
                "classification": {"risk": "low", "protectionScope": "host"},
                "result": {"ok": True, "exitCode": 0, "stdout": "fixture.txt"},
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=plan_next,
        ), patch.object(gateway.shell, "execute", side_effect=execute_shell):
            result = gateway.runtime_message(
                {
                    "message": "Inspect this ordinary local directory.",
                    "cwd": str(Path.cwd()),
                    "session_id": "general-agent-completion-session",
                    "client_turn_id": "general-agent-completion-turn",
                    "_projectContextActive": False,
                }
            )

        self.assertEqual(len(planner_states), 3, result)
        correction = planner_states[2][-1]
        self.assertEqual(correction["tool"], "runtime_completion_gate")
        self.assertEqual(correction["status"], "needs_correction")
        self.assertIn(
            result["task"]["actions"][0]["actionId"],
            correction["outcome"]["summary"],
        )
        self.assertEqual(result["plan"]["nextStep"], "done", result)
        self.assertEqual(result["plan"]["taskCompletion"]["status"], "completed")

    def test_projectless_completion_claim_correction_is_bounded_and_fails_closed(self) -> None:
        gateway = self.gateway
        planner_calls = 0

        def plan_next(
            _message,
            _params,
            _observe,
            _history=None,
            *,
            loop_state=None,
            **_kwargs,
        ):
            nonlocal planner_calls
            planner_calls += 1
            if planner_calls == 1:
                return {
                    "planner": "llm",
                    "summary": "Inspect the requested directory.",
                    "shellNeeded": True,
                    "shellCommand": "Get-ChildItem -LiteralPath .",
                    "shellParams": {},
                    "continueLoop": True,
                    "nextStep": "call_shell",
                }
            return {
                "planner": "llm",
                "summary": "The directory was inspected.",
                "reply": "I inspected the directory.",
                "continueLoop": False,
                "nextStep": "done",
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=plan_next,
        ), patch.object(
            gateway.shell,
            "execute",
            return_value={
                "ok": True,
                "status": "executed",
                "sessionId": "bounded-general-agent-shell",
                "session": {
                    "sessionId": "bounded-general-agent-shell",
                    "status": "finished",
                },
                "classification": {"risk": "low", "protectionScope": "host"},
                "result": {"ok": True, "exitCode": 0, "stdout": "fixture.txt"},
            },
        ):
            result = gateway.runtime_message(
                {
                    "message": "Inspect this ordinary local directory.",
                    "cwd": str(Path.cwd()),
                    "session_id": "bounded-general-agent-completion-session",
                    "client_turn_id": "bounded-general-agent-completion-turn",
                    "_projectContextActive": False,
                }
            )

        self.assertEqual(planner_calls, 3, result)
        self.assertEqual(result["plan"]["nextStep"], "completion_unverified")
        self.assertEqual(
            result["plan"]["completionGate"]["reason"],
            "completion_claim_unbound",
        )

    def test_projectless_general_agent_suppresses_equivalent_directory_replays_and_pivots(self) -> None:
        gateway = self.gateway
        planner_calls = 0
        planner_states: list[list[dict]] = []

        def plan_next(
            _message,
            _params,
            _observe,
            _history=None,
            *,
            loop_state=None,
            **_kwargs,
        ):
            nonlocal planner_calls
            planner_calls += 1
            state = [dict(item) for item in (loop_state or [])]
            planner_states.append(state)
            if planner_calls == 1:
                return {
                    "planner": "llm",
                    "summary": "List the target once.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_list_directory",
                    "skillParams": {"path": r"E:\fixture\app"},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                }
            if planner_calls == 2:
                return {
                    "planner": "llm",
                    "summary": "Repeat the same listing through Shell.",
                    "shellNeeded": True,
                    "shellCommand": 'dir /a "E:\\fixture\\app"',
                    "shellParams": {"cwd": r"E:\fixture"},
                    "continueLoop": True,
                    "nextStep": "call_shell",
                }
            if planner_calls == 3:
                return {
                    "planner": "llm",
                    "summary": "Read the relevant file instead.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_read_text_file",
                    "skillParams": {"path": r"E:\fixture\app\Readme.txt"},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                }
            completed_action_ids = [
                str(item.get("actionId") or "")
                for item in state
                if str(item.get("actionId") or "")
                and str(item.get("status") or "") == "executed"
            ]
            return {
                "planner": "llm",
                "summary": "The bounded evidence is sufficient.",
                "reply": "The application uses the mechanism described in Readme.txt.",
                "continueLoop": False,
                "nextStep": "done",
                "completionClaim": {
                    "satisfied": True,
                    "evidenceActionIds": completed_action_ids,
                },
            }

        def execute_skill(_owner, tool_name, params, _agent_name=None, **_kwargs):
            if tool_name == "vrcforge_list_directory":
                result = {
                    "ok": True,
                    "summary": "Readme.txt",
                    "notice": "Do not repeat the directory listing.",
                }
            else:
                self.assertEqual(tool_name, "vrcforge_read_text_file")
                result = {"ok": True, "summary": "Encryption is initialized by startup.exe."}
            return {
                "ok": True,
                "tool": tool_name,
                "status": "executed",
                "result": result,
                "outcome": {
                    "status": "ok",
                    "summary": result["summary"],
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=plan_next,
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ) as execute, patch.object(
            gateway.shell,
            "execute",
            side_effect=AssertionError("equivalent Shell directory replay must be suppressed"),
        ):
            result = gateway.runtime_message(
                {
                    "message": r"Inspect E:\fixture\app and explain its mechanism.",
                    "session_id": "general-no-progress-session",
                    "client_turn_id": "general-no-progress-turn",
                    "projectType": "general",
                    "_projectContextActive": False,
                }
            )

        self.assertEqual(execute.call_count, 2, result)
        self.assertEqual(planner_calls, 4, result)
        self.assertEqual(planner_states[2][-1]["tool"], "runtime_no_progress")
        self.assertEqual(planner_states[2][-1]["status"], "suppressed")
        self.assertEqual(result["plan"]["nextStep"], "done", result)
        self.assertEqual(result["task"]["status"], "completed", result)

    def test_nonzero_shell_exit_without_error_text_never_gets_success_summary(self) -> None:
        gateway = self.gateway
        plans = iter(
            [
                {
                    "planner": "test",
                    "summary": "Run the failing host command.",
                    "shellNeeded": True,
                    "shellCommand": "exit 7",
                    "shellParams": {},
                    "continueLoop": True,
                    "nextStep": "call_shell",
                },
                {
                    "planner": "test",
                    "summary": "Report the failed command.",
                    "reply": "The command failed.",
                    "continueLoop": False,
                    "nextStep": "done",
                },
            ]
        )

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            gateway.shell,
            "execute",
            return_value={
                "ok": False,
                "status": "executed",
                "result": {"ok": False, "exitCode": 7},
            },
        ):
            result = gateway.runtime_message(
                {
                    "message": "run a failing host command",
                    "session_id": "nonzero-shell-summary-session",
                    "client_turn_id": "nonzero-shell-summary-turn",
                }
            )

        outcome = result["shell"]["outcome"]
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(
            outcome["summary"],
            "Shell command did not complete successfully.",
        )
        self.assertNotEqual(outcome["summary"], "Shell command completed successfully.")
        self.assertEqual(result["plan"]["nextStep"], "tool_failed")

    def test_computer_use_bootstrap_has_exact_task_action_identity(self) -> None:
        gateway = self.gateway
        session_id = "desktop-bootstrap-identity-session"
        client_turn_id = "desktop-bootstrap-identity-turn"
        bootstrap_tool = "vrcforge_agent_desktop_action"
        bootstrap_params = {
            "action": "computer_use",
            "prompt": "Discover applications and windows for this user-started Computer Use turn.",
            "sessionId": session_id,
            "clientTurnId": client_turn_id,
            "params": {
                "operation": "sequence",
                "steps": [
                    {"operation": "list_apps", "limit": 80},
                    {"operation": "list_windows", "limit": 30},
                ],
            },
        }
        bootstrap_action_id = canonical_action_id(
            "skill",
            bootstrap_tool,
            bootstrap_params,
        )
        observed_loop_state: list[dict] = []

        def terminal_reply(
            _message,
            _params,
            _observe,
            _history=None,
            *,
            loop_state=None,
            **_kwargs,
        ):
            observed_loop_state.extend(list(loop_state or []))
            return {
                "planner": "llm",
                "summary": "The desktop bootstrap completed.",
                "reply": "The desktop bootstrap completed.",
                "continueLoop": False,
                "nextStep": "done",
                "completionClaim": {
                    "satisfied": True,
                    "evidenceActionIds": [bootstrap_action_id],
                },
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=terminal_reply,
        ), patch.object(
            gateway.desktop,
            "consume_computer_use_turn_grant",
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            return_value={
                "ok": True,
                "status": "executed",
                "tool": bootstrap_tool,
                "result": {"applications": [], "windows": []},
                "outcome": {
                    "schema": "vrcforge.tool_result.v1",
                    "status": "ok",
                    "summary": "Desktop bootstrap completed.",
                    "data": {},
                    "error": None,
                    "evidence": [],
                    "verification": {"state": "not_required", "checks": []},
                },
            },
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "inspect the desktop",
                    "session_id": session_id,
                    "client_turn_id": client_turn_id,
                    "_computerUseRequested": True,
                }
            )

        execute_skill.assert_called_once()
        self.assertEqual(execute_skill.call_args.args[1], bootstrap_tool)
        self.assertEqual(execute_skill.call_args.args[2], bootstrap_params)
        self.assertEqual(result["steps"][0]["actionId"], bootstrap_action_id)
        self.assertEqual(observed_loop_state[0]["actionId"], bootstrap_action_id)
        self.assertEqual(result["plan"]["nextStep"], "done", result)
        self.assertEqual(
            result["plan"]["taskCompletion"]["evidenceActionIds"],
            [bootstrap_action_id],
        )
        requirement = result["task"]["requirements"][0]
        action = result["task"]["actions"][0]
        self.assertEqual(requirement["actionId"], bootstrap_action_id)
        self.assertEqual(requirement["tool"], bootstrap_tool)
        self.assertEqual(requirement["verificationProfile"], "canonical_tool_result")
        self.assertEqual(action["actionId"], bootstrap_action_id)
        self.assertEqual(action["tool"], bootstrap_tool)
        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["outcome"]["verification"]["state"], "passed")
        self.assertEqual(
            gateway.runtime_sessions.get_session(session_id)["desktopBootstrapToolCalls"],
            1,
        )

    def test_post_tool_provider_failure_preserves_skill_result_and_marks_run_failed(self) -> None:
        gateway = self.gateway
        llm_results = [
            SimpleNamespace(
                text='{"action":"skill","skill_tool":"unity_scan_materials","skill_params":{}}',
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
                            "projectPath": r"C:\Unity\FixtureProject",
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
                    text='{"action":"skill","skill_tool":"unity_scan_materials","skill_params":{}}',
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
                            "projectPath": r"C:\Unity\FixtureProject",
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
                    text='{"action":"skill","skill_tool":"unity_scan_materials","skill_params":{}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"unity_health","skill_params":{}}',
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
                            "projectPath": r"C:\Unity\FixtureProject",
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
                    text='{"action":"skill","skill_tool":"unity_scan_materials","skill_params":{"avatarPath":"A"}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"unity_health","skill_params":{"scope":"B"}}',
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text='{"action":"skill","skill_tool":"unity_health","skill_params":{"scope":"B"}}',
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
                            "projectPath": r"C:\Unity\FixtureProject",
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

    @provider_plan_fixture
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
        self.assertEqual(plan.get("nextStep"), "done")
        self.assertIn("DeepSeek", plan.get("reply", ""))
        self.assertIn("deepseek-v4-pro", plan.get("reply", ""))
        self.assertNotIn("write", payload)
        self.assertNotIn("skill", payload)
        self.assertEqual(payload.get("steps", []), [])
        execute_skill.assert_not_called()

    def test_gateway_correction_supersedes_the_original_unresolved_failure(self) -> None:
        gateway = self.gateway
        bad_arguments = {"avatarPath": "MissingAvatar"}
        good_arguments = {"avatarPath": "AvatarRoot"}
        failed_action_id = canonical_action_id(
            "skill", "vrcforge_scan_materials", bad_arguments
        )
        corrected_action_id = canonical_action_id(
            "skill", "vrcforge_scan_materials", good_arguments
        )
        plans = iter(
            [
                {
                    "planner": "llm",
                    "summary": "Inspect the requested avatar.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": bad_arguments,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Correct the avatar path and retry.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": good_arguments,
                    "correctionForActionId": failed_action_id,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "The corrected scan completed.",
                    "reply": "The corrected scan completed.",
                    "continueLoop": False,
                    "nextStep": "done",
                    "completionClaim": {
                        "satisfied": True,
                        "evidenceActionIds": [corrected_action_id],
                    },
                },
            ]
        )

        def execute_skill(_owner, tool, params, agent_name=None, owner_id=""):
            assert tool == "vrcforge_scan_materials"
            failed = params.get("avatarPath") == "MissingAvatar"
            return {
                "ok": not failed,
                "tool": tool,
                "status": "failed" if failed else "executed",
                "result": {"ok": not failed, "materials": []},
                "outcome": {
                    "status": "failed" if failed else "ok",
                    "summary": "avatar missing" if failed else "materials scanned",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {
                    "message": "scan the selected avatar materials",
                    "session_id": "correction-session",
                    "clientTurnId": "correction-turn",
                }
            )

        self.assertEqual(result["plan"]["nextStep"], "done", result)
        self.assertEqual(
            result["plan"]["taskCompletion"]["evidenceActionIds"],
            [corrected_action_id],
        )
        actions = result["task"]["actions"]
        self.assertEqual(actions[0]["status"], "superseded")
        self.assertEqual(actions[0]["supersededBy"], corrected_action_id)

    def test_llm_schema_failure_is_refed_for_correction_without_handler_call(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        prompts: list[str] = []
        responses = iter(
            [
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "unity_scan_materials",
                            "skill_params": {"avatarPath": 42},
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "reply",
                            "reply": "The invalid tool arguments were not executed.",
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
            ]
        )

        def fake_llm(*args, **_kwargs):
            prompts.append(str(args[1]))
            return next(responses)

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=fake_llm,
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "inspect the selected avatar materials",
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "schema-refeed-session",
                    "client_turn_id": "schema-refeed-turn",
                }
            )

        execute_skill.assert_not_called()
        self.assertEqual(len(prompts), 2)
        self.assertIn("planner_invalid_response", prompts[1])
        self.assertIn("planner_validation_", prompts[1])
        self.assertEqual(result["plan"]["nextStep"], "planner_failed")
        self.assertEqual(
            result["plan"]["plannerFailure"]["code"],
            "planner_invalid_response",
        )
        self.assertEqual(result["steps"][0]["kind"], "planner_validation")
        self.assertEqual(result["steps"][0]["tool"], "unity_scan_materials")
        self.assertEqual(result["steps"][0]["status"], "failed")

    def test_supervised_write_misclassified_as_skill_is_refed_then_requests_approval(self) -> None:
        gateway = self.gateway
        prompts: list[str] = []
        angles = ["front", "side_left", "side_right", "back"]
        responses = iter(
            [
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "enter_execution",
                            "summary": "The task requires supervised capture.",
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "unity_capture_multi_screenshot",
                            "skill_params": {"angles": angles},
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "write",
                            "write_tool": "unity_capture_multi_screenshot",
                            "write_params": {"angles": angles},
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
            ]
        )

        def fake_llm(*args, **_kwargs):
            prompts.append(str(args[1]))
            return next(responses)

        project = self._unity_project()
        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=fake_llm,
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "Capture several views and verify their coverage.",
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "write-kind-correction-session",
                    "client_turn_id": "write-kind-correction-turn",
                }
            )

        execute_skill.assert_not_called()
        self.assertEqual(len(prompts), 3)
        self.assertIn("The selected tool is a supervised write", prompts[2])
        self.assertIn("planner_validation_", prompts[2])
        self.assertEqual(result["plan"]["nextStep"], "needs_user_action", result)
        self.assertEqual(result["write"]["status"], "approval_pending")
        approval_id = result["write"]["approvalId"]
        approval = next(
            item
            for item in gateway.approval_transactions.list_approvals()
            if item["id"] == approval_id
        )
        self.assertEqual(approval["targetTool"], "vrcforge_capture_multi_screenshot")
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(
            [step["kind"] for step in result["steps"]],
            ["phase", "planner_validation", "write"],
        )
        self.assertEqual(result["steps"][1]["status"], "failed")

    @provider_plan_fixture
    def test_provider_multi_angle_capture_enters_execution_and_requests_write_approval(self) -> None:
        gateway = self.gateway
        project = self._unity_project()

        with patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "Capture front and back views for full coverage.",
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "provider-multi-capture-session",
                    "client_turn_id": "provider-multi-capture-turn",
                }
            )

        execute_skill.assert_not_called()
        self.assertEqual(result["plan"]["planner"], "llm")
        self.assertEqual(result["plan"]["nextStep"], "needs_user_action", result)
        self.assertEqual(result["write"]["status"], "approval_pending")
        approval_id = result["write"]["approvalId"]
        approval = next(
            item
            for item in gateway.approval_transactions.list_approvals()
            if item["id"] == approval_id
        )
        self.assertEqual(approval["targetTool"], "vrcforge_capture_multi_screenshot")
        self.assertEqual(approval["status"], "pending")
        self.assertFalse(approval["taskContext"]["continueAfterApproval"])
        self.assertEqual(
            [(step["kind"], step["status"]) for step in result["steps"]],
            [("phase", "entered_execution"), ("write", "approval_pending")],
        )

    def test_approved_multi_capture_runs_bound_visual_audit_without_replaying_write(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        message = "Capture front and back views, then run a visual audit."
        capture_arguments: dict[str, object] = {}
        audit_arguments = {"captureReceipt": "managed-visual-receipt"}
        capture_action_id = canonical_action_id(
            "write",
            "vrcforge_capture_multi_screenshot",
            capture_arguments,
        )
        audit_action_id = canonical_action_id(
            "skill",
            "vrcforge_vision_audit_multi",
            audit_arguments,
        )
        planner_prompts: list[str] = []

        def plan_visual_journey(prompt):
            planner_prompts.append(str(prompt))
            if len(planner_prompts) == 1:
                payload = {
                    "action": "enter_execution",
                    "summary": "Enter execution to request the supervised capture.",
                }
            elif len(planner_prompts) == 2:
                payload = {
                    "action": "write",
                    "write_tool": "unity_capture_multi_screenshot",
                    "write_params": capture_arguments,
                    "summary": "Capture the approved fixed-angle views.",
                }
            elif len(planner_prompts) == 3:
                payload = {
                    "action": "skill",
                    "skill_tool": "vision_audit_multi",
                    "skill_params": audit_arguments,
                    "summary": "Audit the managed fixed-angle capture.",
                }
            else:
                payload = {
                    "action": "reply",
                    "reply": "Every frozen angle passed the visual audit.",
                    "completion_claim": {
                        "satisfied": True,
                        "evidence_action_ids": [
                            capture_action_id,
                            audit_action_id,
                        ],
                    },
                }
            return PlannerModelResult(
                text=json.dumps(payload),
                usage={},
                reasoning={},
                planner_label="OpenAI · gpt-4o",
            )

        with patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_api_config",
            return_value=ProviderApiConfig(
                provider="openai",
                api_key="fixture-key",
                base_url="",
                model="gpt-4o",
            ),
        ), patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_vision_config",
            return_value=SimpleNamespace(
                provider="",
                api_key="",
                base_url="",
                model="",
                enabled=False,
            ),
        ), patch.object(
            dashboard_server._RUNTIME_PLANNER_MODEL,
            "plan",
            side_effect=plan_visual_journey,
        ) as request_model:
            initial = gateway.runtime_message(
                {
                    "message": message,
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "managed-visual-chain-session",
                    "client_turn_id": "managed-visual-chain-turn",
                }
            )

            self.assertIn("approvalId", initial)
            approval_id = initial["approvalId"]
            stored_approval = gateway._approvals[approval_id]
            task_context = stored_approval["taskContext"]
            self.assertEqual(task_context["requestedActionId"], capture_action_id)
            self.assertTrue(task_context["continueAfterApproval"])

            capture_result = {
                "ok": True,
                "captureReceipt": "managed-visual-receipt",
                "captureEvidenceId": "visual-fixture",
                "angles": ["front", "back"],
                "evidence": [
                    {"ref": "visual-fixture", "kind": "managed_visual_capture"}
                ],
            }
            capture_outcome = {
                "status": "ok",
                "summary": "The approved multi-angle capture completed.",
                "evidence": [
                    {"ref": "visual-fixture", "kind": "managed_visual_capture"}
                ],
                "verification": {"state": "not_required", "checks": []},
            }
            completion = approval_completion(
                task_context,
                raw_result=capture_result,
                outcome=capture_outcome,
            )
            self.assertIsNotNone(completion)
            execution = {
                "status": "applied",
                "result": capture_result,
                "taskCompletion": completion,
            }
            terminal_approval = {
                **stored_approval,
                "status": "applied",
            }
            execution["approval"] = terminal_approval
            execution["checkpoint"] = {
                "id": "checkpoint-managed-visual",
                "ok": True,
            }
            approval_count = len(gateway.approval_transactions.list_approvals())

            def execute_visual_audit(
                _owner,
                tool_name,
                params,
                agent_name=None,
                owner_id="",
            ):
                self.assertEqual(tool_name, "vrcforge_vision_audit_multi")
                self.assertEqual(params["captureReceipt"], "managed-visual-receipt")
                self.assertEqual(params["_runtimeSessionId"], "managed-visual-chain-session")
                self.assertIn(
                    capture_action_id,
                    params["_taskSeed"]["managedVisualCaptureActionIds"],
                )
                return {
                    "tool": tool_name,
                    "status": "executed",
                    "result": {
                        "ok": True,
                        "visualVerified": True,
                        "coverageComplete": True,
                        "captureEvidenceVerified": True,
                        "captureEvidenceId": "visual-fixture",
                        "evidence": [
                            {
                                "ref": "visual-fixture",
                                "kind": "managed_visual_capture",
                            }
                        ],
                    },
                    "outcome": {
                        "status": "ok",
                        "summary": "Every frozen angle passed the visual audit.",
                        "evidence": [
                            {
                                "ref": "visual-fixture",
                                "kind": "managed_visual_capture",
                            }
                        ],
                        "verification": {"state": "not_required", "checks": []},
                    },
                }

            with patch.object(
                type(gateway.runtime_skills),
                "execute",
                autospec=True,
                side_effect=execute_visual_audit,
            ) as execute_skill:
                continuation = gateway.resume_runtime_task_after_approval(
                    terminal_approval,
                    execution,
                )

        self.assertEqual(request_model.call_count, 4)
        self.assertIn("captureReceipt=managed-visual-receipt", planner_prompts[2])
        self.assertIn("vrcforge_vision_audit_multi", planner_prompts[3])
        execute_skill.assert_called_once()
        self.assertIsNotNone(continuation)
        self.assertEqual(
            len(gateway.approval_transactions.list_approvals()),
            approval_count,
        )
        self.assertEqual(continuation["task"]["taskId"], initial["task"]["taskId"])
        self.assertEqual(continuation["plan"]["nextStep"], "done", continuation)
        self.assertEqual(continuation["task"]["status"], "completed")
        self.assertEqual(continuation["plan"]["planner"], "llm")
        self.assertEqual(
            continuation["plan"]["taskCompletion"]["taskId"],
            initial["task"]["taskId"],
        )
        self.assertEqual(
            [action["tool"] for action in continuation["task"]["actions"]],
            [
                "vrcforge_capture_multi_screenshot",
                "vrcforge_vision_audit_multi",
            ],
        )
        capture_transaction = continuation["task"]["actions"][0]["writeTransaction"]
        self.assertEqual(capture_transaction["approvalId"], approval_id)
        self.assertEqual(capture_transaction["checkpointId"], "checkpoint-managed-visual")
        self.assertEqual(capture_transaction["actionId"], capture_action_id)
        self.assertEqual(capture_transaction["taskId"], initial["task"]["taskId"])
        self.assertEqual(
            [step["tool"] for step in continuation["steps"]],
            [
                "vrcforge_capture_multi_screenshot",
                "vrcforge_vision_audit_multi",
            ],
        )
        self.assertTrue(continuation["steps"][0]["historical"])
        self.assertEqual(
            [
                item["verificationProfile"]
                for item in continuation["task"]["requirements"]
            ],
            ["canonical_tool_result", "multi_angle_visual"],
        )
        authority = RuntimeJourneyReceiptAuthority(secret=b"v" * 32)
        receipt = authority.issue(continuation)
        journey = authority.verify(receipt)
        self.assertEqual(journey["taskId"], initial["task"]["taskId"])
        self.assertEqual(journey["providerRequestCount"], 4)
        self.assertEqual(journey["resultRefeedCount"], 2)

    def test_permanent_visual_provider_rejection_refeeds_route_and_discards_images(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        message = "Capture front and back views, then run a visual audit."
        planner_prompts: list[str] = []

        def plan_visual_failure(prompt):
            planner_prompts.append(str(prompt))
            if len(planner_prompts) == 1:
                payload = {
                    "action": "enter_execution",
                    "summary": "Enter execution to request the supervised capture.",
                }
            elif len(planner_prompts) == 2:
                payload = {
                    "action": "write",
                    "write_tool": "unity_capture_multi_screenshot",
                    "write_params": {},
                    "summary": "Capture the approved fixed-angle views.",
                }
            else:
                payload = {
                    "action": "skill",
                    "skill_tool": "vision_audit_multi",
                    "skill_params": {
                        "captureReceipt": "managed-rejected-visual-receipt"
                    },
                    "summary": "Audit the managed fixed-angle capture.",
                }
            return PlannerModelResult(
                text=json.dumps(payload),
                planner_label="DeepSeek · deepseek-chat",
            )

        with patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_api_config",
            return_value=ProviderApiConfig(
                provider="deepseek",
                api_key="fixture-key",
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
            ),
        ), patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_vision_config",
            return_value=SimpleNamespace(
                provider="",
                api_key="",
                base_url="",
                model="",
                enabled=False,
            ),
        ), patch.object(
            dashboard_server._RUNTIME_PLANNER_MODEL,
            "plan",
            side_effect=plan_visual_failure,
        ) as request_model:
            initial = gateway.runtime_message(
                {
                    "message": message,
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "managed-visual-rejection-session",
                    "client_turn_id": "managed-visual-rejection-turn",
                }
            )

            approval_id = initial["approvalId"]
            stored_approval = gateway._approvals[approval_id]
            task_context = stored_approval["taskContext"]
            capture_result = {
                "ok": True,
                "captureReceipt": "managed-rejected-visual-receipt",
                "captureEvidenceId": "visual-rejected-fixture",
                "angles": ["front", "back"],
                "evidence": [
                    {"ref": "visual-rejected-fixture", "kind": "managed_visual_capture"}
                ],
            }
            completion = approval_completion(
                task_context,
                raw_result=capture_result,
                outcome={
                    "status": "ok",
                    "summary": "The approved multi-angle capture completed.",
                    "evidence": capture_result["evidence"],
                    "verification": {"state": "not_required", "checks": []},
                },
            )
            self.assertIsNotNone(completion)
            terminal_approval = {**stored_approval, "status": "applied"}
            execution = {
                "status": "applied",
                "result": capture_result,
                "taskCompletion": completion,
                "approval": terminal_approval,
                "checkpoint": {"id": "checkpoint-managed-visual-rejected", "ok": True},
            }
            provider_summary = (
                "DeepSeek · deepseek-chat (source=main, errorType=provider_rejected): "
                "HTTP 400 images are not supported. Original images were discarded."
            )

            def execute_visual_audit(
                _owner,
                tool_name,
                params,
                agent_name=None,
                owner_id="",
            ):
                self.assertEqual(tool_name, "vrcforge_vision_audit_multi")
                self.assertEqual(params["captureReceipt"], "managed-rejected-visual-receipt")
                return {
                    "ok": True,
                    "status": "needs_user_action",
                    "tool": tool_name,
                    "result": {
                        "ok": False,
                        "status": "needs_user_action",
                        "summary": provider_summary,
                        "retryable": False,
                        "retainImages": False,
                    },
                    "outcome": {
                        "status": "needs_user_action",
                        "summary": provider_summary,
                        "error": {
                            "type": "provider_rejected",
                            "code": "provider_rejected",
                            "retryable": False,
                            "provider": "deepseek",
                            "providerLabel": "DeepSeek",
                            "model": "deepseek-chat",
                            "source": "main",
                            "retainImages": False,
                            "disposition": "discarded",
                        },
                        "evidence": [],
                        "verification": {"state": "not_required", "checks": []},
                    },
                }

            with patch.object(
                type(gateway.runtime_skills),
                "execute",
                autospec=True,
                side_effect=execute_visual_audit,
            ) as execute_skill:
                continuation = gateway.resume_runtime_task_after_approval(
                    terminal_approval,
                    execution,
                )

        self.assertEqual(request_model.call_count, 3)
        self.assertIn("captureReceipt=managed-rejected-visual-receipt", planner_prompts[2])
        execute_skill.assert_called_once()
        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["plan"]["nextStep"], "needs_user_action", continuation)
        self.assertEqual(continuation["task"]["status"], "needs_user_action")
        self.assertEqual(continuation["plan"]["reply"], provider_summary)
        self.assertNotIn("Audit one frozen fixed-angle", continuation["plan"]["reply"])
        audit_action = continuation["task"]["actions"][-1]
        self.assertEqual(audit_action["tool"], "vrcforge_vision_audit_multi")
        self.assertEqual(audit_action["status"], "needs_user_action")
        self.assertEqual(audit_action["outcome"]["error"]["provider"], "deepseek")
        self.assertEqual(audit_action["outcome"]["error"]["model"], "deepseek-chat")
        self.assertFalse(audit_action["outcome"]["error"]["retainImages"])
        self.assertEqual(audit_action["outcome"]["error"]["disposition"], "discarded")

    def test_visual_audit_without_provider_capability_creates_no_capture_approval(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        approval_count = len(gateway.approval_transactions.list_approvals())

        with patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_api_config",
            return_value=SimpleNamespace(
                provider="",
                api_key="",
                base_url="",
                model="",
            ),
        ), patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_vision_config",
            return_value=SimpleNamespace(
                provider="",
                api_key="",
                base_url="",
                model="",
                enabled=False,
            ),
        ), patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=AssertionError("missing visual capability must fail before Provider sampling"),
        ) as request_model, patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "Capture front and back views, then run a visual audit.",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "visual-capability-preflight-session",
                    "client_turn_id": "visual-capability-preflight-turn",
                }
            )

        request_model.assert_not_called()
        execute_skill.assert_not_called()
        self.assertEqual(
            len(gateway.approval_transactions.list_approvals()),
            approval_count,
        )
        self.assertNotIn("write", result)
        self.assertEqual(result.get("steps", []), [])
        self.assertEqual(result["plan"]["nextStep"], "planner_failed")
        self.assertEqual(
            result["plan"]["plannerFailure"],
            {
                "code": "provider_not_configured",
                "phase": "initial",
                "retryable": False,
            },
        )
        self.assertFalse(result["plan"]["providerConnected"])

    def test_missing_provider_ignores_caller_shell_argument_and_creates_no_approval(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        approval_count = len(gateway.approval_transactions.list_approvals())

        with patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_api_config",
            return_value=SimpleNamespace(
                provider="",
                api_key="",
                base_url="",
                model="",
            ),
        ):
            result = gateway.runtime_message(
                {
                    "message": "write the supplied command",
                    "shell_command": "Set-Content Assets/must-not-run.txt blocked",
                    "projectRoot": str(project),
                    "cwd": str(project),
                    "workspaceRoot": str(project),
                    "session_id": "missing-provider-shell-session",
                }
            )

        self.assertEqual(result["plan"]["plannerFailure"]["code"], "provider_not_configured")
        self.assertNotIn("shell", result)
        self.assertNotIn("approvalId", result)
        self.assertEqual(
            len(gateway.approval_transactions.list_approvals()),
            approval_count,
        )
        self.assertFalse((project / "Assets" / "must-not-run.txt").exists())

    def test_execution_preflight_revalidates_schema_and_calls_no_handler(self) -> None:
        gateway = self.gateway
        plans = iter(
            [
                {
                    "planner": "llm",
                    "summary": "Inspect materials with invalid arguments.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": {"avatarPath": ["not", "a", "string"]},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Repeat the same invalid arguments.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": {"avatarPath": ["still", "not", "a string"]},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
            ]
        )

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "inspect materials",
                    "session_id": "schema-preflight-session",
                    "client_turn_id": "schema-preflight-turn",
                }
            )

        execute_skill.assert_not_called()
        self.assertEqual(result["plan"]["nextStep"], "planner_failed")
        self.assertEqual(
            result["plan"]["plannerFailure"]["code"],
            "planner_invalid_response",
        )
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["kind"], "planner_validation")
        self.assertEqual(result["steps"][0]["tool"], "vrcforge_scan_materials")

    def test_hidden_write_misreported_as_skill_gets_one_execution_layer_correction(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        provider_results = iter(
            [
                SimpleNamespace(
                    text=(
                        '{"action":"skill","skill_tool":"unity_create_gameobject",'
                        '"skill_params":{"name":"Probe"}}'
                    ),
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text=(
                        '{"action":"write","write_tool":"unity_create_gameobject",'
                        '"write_params":{"name":"Probe"}}'
                    ),
                    usage={},
                    reasoning={},
                ),
            ]
        )
        approval_count = len(gateway.approval_transactions.list_approvals())

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=lambda *_args, **_kwargs: next(provider_results),
        ) as request_model:
            result = gateway.runtime_message(
                {
                    "message": "Apply the requested project change.",
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "wrong-kind-correction-session",
                    "client_turn_id": "wrong-kind-correction-turn",
                }
            )

        self.assertEqual(request_model.call_count, 2)
        self.assertEqual(result["plan"]["nextStep"], "needs_user_action")
        self.assertEqual(result["write"]["status"], "approval_pending")
        self.assertEqual(result["write"]["tool"], "vrcforge_create_gameobject")
        self.assertEqual(
            len(gateway.approval_transactions.list_approvals()),
            approval_count + 1,
        )
        self.assertEqual(
            [(step["kind"], step["status"]) for step in result["steps"]],
            [
                ("planner_validation", "failed"),
                ("phase", "entered_execution"),
                ("write", "approval_pending"),
            ],
        )

    def test_schema_failure_requires_a_real_valid_action_before_exact_completion(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        valid_arguments = {"avatarPath": "Avatar"}
        completed_action_id = canonical_action_id(
            "skill",
            "vrcforge_scan_materials",
            valid_arguments,
        )
        prompts: list[str] = []
        responses = iter(
            [
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "unity_scan_materials",
                            "skill_params": {"avatarPath": 42},
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "skill",
                            "skill_tool": "unity_scan_materials",
                            "skill_params": valid_arguments,
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "reply",
                            "reply": "The valid scan completed.",
                            "completion_claim": {
                                "satisfied": True,
                                "evidence_action_ids": [completed_action_id],
                            },
                        }
                    ),
                    usage={},
                    reasoning={},
                ),
            ]
        )
        executed: list[dict[str, object]] = []

        def fake_llm(*args, **_kwargs):
            prompts.append(str(args[1]))
            return next(responses)

        def execute_skill(_owner, tool, params, agent_name=None, owner_id=""):
            self.assertEqual(tool, "vrcforge_scan_materials")
            executed.append(dict(params))
            return {
                "ok": True,
                "tool": tool,
                "status": "executed",
                "result": {"ok": True, "materials": []},
                "outcome": {
                    "status": "ok",
                    "summary": "materials scanned",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=fake_llm,
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {
                    "message": "inspect the selected avatar materials",
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "schema-valid-correction-session",
                    "client_turn_id": "schema-valid-correction-turn",
                }
            )

        self.assertEqual(executed, [valid_arguments])
        self.assertEqual(len(prompts), 3)
        self.assertIn("planner_invalid_response", prompts[1])
        self.assertIn("materials scanned", prompts[2])
        self.assertEqual(result["plan"]["nextStep"], "done")
        self.assertEqual(
            result["plan"]["taskCompletion"]["evidenceActionIds"],
            [completed_action_id],
        )

    def test_repeated_schema_failure_stops_after_one_correction_without_handler_call(self) -> None:
        gateway = self.gateway
        project = self._unity_project()
        planner_calls = 0

        def invalid_llm(*_args, **_kwargs):
            nonlocal planner_calls
            planner_calls += 1
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "action": "skill",
                        "skill_tool": "unity_scan_materials",
                        "skill_params": {"avatarPath": planner_calls},
                    }
                ),
                usage={},
                reasoning={},
            )

        with patch(
            "dashboard_server.request_llm_plan_with_metadata",
            side_effect=invalid_llm,
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
        ) as execute_skill:
            result = gateway.runtime_message(
                {
                    "message": "inspect materials with bounded schema correction",
                    "provider": "fixture",
                    "model": "fixture",
                    "projectPath": str(project),
                    "projectRoot": str(project),
                    "session_id": "schema-bounded-session",
                    "client_turn_id": "schema-bounded-turn",
                }
            )

        execute_skill.assert_not_called()
        self.assertEqual(planner_calls, 2)
        self.assertEqual(result["plan"]["nextStep"], "planner_failed")
        self.assertEqual(
            result["plan"]["plannerFailure"]["code"],
            "planner_invalid_response",
        )
        self.assertEqual(len(result["steps"]), 2)

    def test_unrelated_diagnostic_success_does_not_clear_the_original_failure(self) -> None:
        gateway = self.gateway
        bad_arguments = {"avatarPath": "MissingAvatar"}
        failed_action_id = canonical_action_id(
            "skill", "vrcforge_scan_materials", bad_arguments
        )
        health_action_id = canonical_action_id("skill", "vrcforge_health", {})
        plans = iter(
            [
                {
                    "planner": "llm",
                    "summary": "Scan materials.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": bad_arguments,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Inspect health without claiming it fixed the scan.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_health",
                    "skillParams": {},
                    "correctionForActionId": failed_action_id,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Health is good.",
                    "reply": "Done.",
                    "continueLoop": False,
                    "nextStep": "done",
                    "completionClaim": {
                        "satisfied": True,
                        "evidenceActionIds": [health_action_id],
                    },
                },
            ]
        )

        def execute_skill(_owner, tool, _params, agent_name=None, owner_id=""):
            if tool == "vrcforge_scan_materials":
                return {
                    "ok": False,
                    "tool": tool,
                    "status": "failed",
                    "result": {"ok": False, "error": "avatar missing"},
                    "outcome": {
                        "status": "failed",
                        "summary": "avatar missing",
                        "verification": {"state": "not_required", "checks": []},
                    },
                }
            self.assertEqual(tool, "vrcforge_health")
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

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {
                    "message": "scan materials and diagnose any failure",
                    "session_id": "cross-tool-correction-session",
                }
            )

        self.assertEqual(result["plan"]["nextStep"], "tool_failed", result)
        actions = result["task"]["actions"]
        self.assertEqual(actions[0]["actionId"], failed_action_id)
        self.assertEqual(actions[0]["status"], "failed")
        self.assertEqual(actions[1]["actionId"], health_action_id)
        self.assertEqual(actions[1]["status"], "completed")
        self.assertEqual(
            result["task"]["requirements"][0]["actionId"],
            failed_action_id,
        )

    def test_runtime_injected_task_seed_does_not_defeat_repeated_failure_suppression(self) -> None:
        gateway = self.gateway
        plan = {
            "planner": "llm",
            "summary": "Delegate the same review.",
            "skillNeeded": True,
            "skillTool": "vrcforge_delegate_subagent",
            "skillParams": {
                "role": "project_index_review",
                "task": "inspect prefabs",
            },
            "continueLoop": True,
            "nextStep": "call_skill",
        }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: dict(plan),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            return_value={
                "ok": False,
                "tool": "vrcforge_delegate_subagent",
                "status": "failed",
                "result": {"ok": False, "error": "delegate unavailable"},
                "outcome": {
                    "status": "failed",
                    "summary": "delegate unavailable",
                    "verification": {"state": "not_required", "checks": []},
                },
            },
        ):
            result = gateway.runtime_message(
                {
                    "message": "delegate the same review",
                    "session_id": "repeat-delegate-session",
                }
            )

        self.assertEqual(result["plan"]["nextStep"], "loop_suppressed", result)
        self.assertEqual(result["plan"]["loopSuppression"]["consecutive"], 3)
        self.assertNotIn("toolCallLimitReached", result["plan"])
        self.assertNotIn(
            "subagent",
            [str(event.get("kind") or "") for event in result.get("timeline") or []],
            "Sub Agent lifecycle must come only from the durable task registry",
        )
        delegate_events = [
            event
            for event in result.get("timeline") or []
            if str((event.get("payload") or {}).get("tool") or "")
            == "vrcforge_delegate_subagent"
        ]
        self.assertEqual(
            [str(event.get("kind") or "") for event in delegate_events],
            ["tool_call", "tool_result", "tool_call", "tool_result", "tool_call", "tool_result"],
            "Delegation remains an ordinary tool call/result even though lifecycle events are registry-owned.",
        )

    def test_provider_needs_user_action_stops_without_implicit_retry(self) -> None:
        gateway = self.gateway
        bad_arguments = {"avatarPath": "MissingAvatar"}
        planner_calls = 0

        def plan(*_args, **_kwargs):
            nonlocal planner_calls
            planner_calls += 1
            return {
                "planner": "llm",
                "summary": "Scan the requested avatar.",
                "skillNeeded": True,
                "skillTool": "vrcforge_scan_materials",
                "skillParams": bad_arguments,
                "continueLoop": True,
                "nextStep": "call_skill",
            }

        def execute_skill(_owner, tool, params, agent_name=None, owner_id=""):
            return {
                "ok": True,
                "tool": tool,
                "status": "needs_user_action",
                "result": {"status": "needs_user_action"},
                "outcome": {
                    "status": "needs_user_action",
                    "summary": "avatar path is ambiguous",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {
                    "message": "scan the selected avatar materials",
                    "session_id": "needs-action-correction-session",
                }
            )

        self.assertEqual(planner_calls, 1)
        self.assertEqual(result["plan"]["nextStep"], "needs_user_action", result)
        self.assertEqual(result["task"]["actions"][0]["status"], "needs_user_action")

    def test_runtime_injected_skill_context_does_not_change_action_identity(self) -> None:
        gateway = self.gateway
        expected_action_id = canonical_action_id("skill", "vrcforge_progress_list", {})
        plans = iter(
            [
                {
                    "planner": "llm",
                    "summary": "List progress.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_progress_list",
                    "skillParams": {},
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Progress listed.",
                    "reply": "Progress listed.",
                    "continueLoop": False,
                    "nextStep": "done",
                    "completionClaim": {
                        "satisfied": True,
                        "evidenceActionIds": [expected_action_id],
                    },
                },
            ]
        )

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            return_value={
                "ok": True,
                "tool": "vrcforge_progress_list",
                "status": "executed",
                "result": {"items": []},
                "outcome": {
                    "status": "ok",
                    "summary": "progress listed",
                    "verification": {"state": "not_required", "checks": []},
                },
            },
        ):
            result = gateway.runtime_message(
                {
                    "message": "list progress",
                    "session_id": "progress-identity-session",
                    "clientTurnId": "progress-identity-turn",
                }
            )

        self.assertEqual(result["plan"]["nextStep"], "done", result)
        self.assertEqual(
            result["plan"]["taskCompletion"]["evidenceActionIds"],
            [expected_action_id],
        )

    def test_runtime_goal_tool_receives_current_chat_scope_not_model_scope(self) -> None:
        gateway = self.gateway
        arguments = {"objective": "Ship the verified Goal slice"}
        model_arguments = {**arguments, "chatId": "spoofed-chat"}
        expected_action_id = canonical_action_id("skill", "vrcforge_create_goal", model_arguments)
        plans = iter(
            [
                {
                    "planner": "llm",
                    "summary": "Create the explicitly requested goal.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_create_goal",
                    "skillParams": model_arguments,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Goal created.",
                    "reply": "Goal created.",
                    "continueLoop": False,
                    "nextStep": "done",
                    "completionClaim": {
                        "satisfied": True,
                        "evidenceActionIds": [expected_action_id],
                    },
                },
            ]
        )
        captured: dict[str, object] = {}

        def execute_skill(_owner, tool, params, agent_name=None, owner_id=""):
            captured.update(params)
            return {
                "ok": True,
                "tool": tool,
                "status": "executed",
                "result": {"goal": {"goalId": "goal-live"}},
                "outcome": {
                    "status": "ok",
                    "summary": "goal created",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch.object(
            gateway.runtime_planner,
            "plan_agent_turn",
            side_effect=lambda *_args, **_kwargs: next(plans),
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {
                    "message": "Create a goal to ship this slice",
                    "session_id": "goal-scope-session",
                    "chatId": "goal-scope-chat",
                    "clientTurnId": "goal-scope-client-turn",
                }
            )

        self.assertEqual(captured["sessionId"], "goal-scope-session")
        self.assertEqual(captured["chatId"], "goal-scope-chat")
        self.assertNotEqual(captured["turnId"], "")
        self.assertEqual(result["plan"]["nextStep"], "done")

    def test_repeated_tool_steps_keep_their_own_results_in_execution_order(self) -> None:
        gateway = self.gateway
        first_arguments = {"avatarPath": "AvatarA"}
        second_arguments = {"avatarPath": "AvatarB"}
        first_action_id = canonical_action_id(
            "skill", "vrcforge_scan_materials", first_arguments
        )
        second_action_id = canonical_action_id(
            "skill", "vrcforge_scan_materials", second_arguments
        )
        plans = iter(
            [
                {
                    "planner": "llm",
                    "summary": "Scan the first avatar.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": first_arguments,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Scan the second avatar.",
                    "skillNeeded": True,
                    "skillTool": "vrcforge_scan_materials",
                    "skillParams": second_arguments,
                    "continueLoop": True,
                    "nextStep": "call_skill",
                },
                {
                    "planner": "llm",
                    "summary": "Both scans completed.",
                    "reply": "Both scans completed.",
                    "continueLoop": False,
                    "nextStep": "done",
                    "completionClaim": {
                        "satisfied": True,
                        "evidenceActionIds": [first_action_id, second_action_id],
                    },
                },
            ]
        )

        def execute_skill(_owner, tool, params, agent_name=None, owner_id=""):
            avatar_path = params["avatarPath"]
            return {
                "ok": True,
                "tool": tool,
                "status": "executed",
                "result": {"avatarPath": avatar_path, "materials": []},
                "outcome": {
                    "status": "ok",
                    "summary": f"scanned {avatar_path}",
                    "verification": {"state": "not_required", "checks": []},
                },
            }

        with patch.object(
            gateway.runtime_planner, "plan_agent_turn", side_effect=lambda *_args, **_kwargs: next(plans)
        ), patch.object(
            type(gateway.runtime_skills),
            "execute",
            autospec=True,
            side_effect=execute_skill,
        ):
            result = gateway.runtime_message(
                {
                    "message": "scan both avatars",
                    "session_id": "timeline-result-order-session",
                }
            )

        tool_steps = [
            step
            for step in result["steps"]
            if step.get("kind") == "skill"
            and step.get("tool") == "vrcforge_scan_materials"
        ]
        self.assertEqual(
            [step.get("result", {}).get("avatarPath") for step in tool_steps],
            ["AvatarA", "AvatarB"],
        )
        self.assertLess(tool_steps[0]["index"], tool_steps[1]["index"])
        timeline = result.get("timeline") or []
        self.assertEqual(
            [event.get("kind") for event in timeline],
            [
                "planner",
                "tool_call",
                "tool_result",
                "planner",
                "tool_call",
                "tool_result",
                "assistant",
            ],
        )
        self.assertEqual(
            [event.get("sequence") for event in timeline],
            list(range(len(timeline))),
        )
        self.assertTrue(all(event.get("timestamp") for event in timeline))
        self.assertNotIn("arguments", json.dumps(timeline))
        self.assertEqual(timeline[-1]["payload"]["summary"], result["plan"]["reply"])


if __name__ == "__main__":
    unittest.main()
