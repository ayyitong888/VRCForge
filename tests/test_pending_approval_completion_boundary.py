from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import dashboard_server
from approved_unity_execution import current_approved_unity_execution
@pytest.fixture
def loop_harness():
    from tests.test_agent_loop_p0 import AgentLoopP0Tests

    harness = AgentLoopP0Tests("runTest")
    harness.setUp()
    try:
        yield harness
    finally:
        harness.tearDown()


def test_real_pending_approval_outlives_an_older_shell_failure(loop_harness) -> None:
    gateway = loop_harness.gateway
    project = loop_harness._unity_project()
    plans = iter(
        [
            {
                "planner": "llm",
                "action": "shell",
                "shellNeeded": True,
                "shellCommand": "Get-ChildItem",
                "shellParams": {"cwd": str(project)},
                "continueLoop": True,
                "nextStep": "call_shell",
            },
            {
                "planner": "llm",
                "writeNeeded": True,
                "writeTool": "vrcforge_create_gameobject",
                "writeParams": {
                    "name": "PendingBoundaryProbe",
                    "parentPath": "",
                    "projectPath": str(project),
                    "executionTarget": loop_harness._fixture_execution_target(project),
                },
                "continueLoop": False,
                "nextStep": "call_tool",
            },
        ]
    )
    denied = {
        "ok": False,
        "status": "rejected",
        "error": "scope denied",
        "errorDetails": {
            "error": {
                "type": "permission",
                "code": "unity_project_shell_scope",
                "retryable": False,
            }
        },
    }

    with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plans), patch.object(
        gateway.shell, "execute", return_value=denied
    ), patch("dashboard_server.invoke_unity_mcp") as invoke_unity:
        result = gateway.runtime_message(
            {
                "message": "Inspect the project, then create PendingBoundaryProbe.",
                "projectPath": str(project),
                "session_id": "pending-boundary-regression",
            }
        )

    assert result["plan"]["nextStep"] == "needs_user_action"
    approval_id = result["approval_id"]
    approval = gateway._approvals[approval_id]
    assert approval["status"] == "pending"
    assert result["approvalId"] == approval_id
    assert result["plan"]["completionGate"]["status"] == "needs_user_action"
    assert any(
        action.get("status") == "failed"
        and action.get("kind") == "shell"
        for action in result["task"]["actions"]
    )
    assert any(
        step.get("tool") == "vrcforge_shell_process"
        or step.get("tool") == "vrcforge_execute_shell"
        or step.get("status") == "rejected"
        for step in result["steps"]
    )
    assert invoke_unity.call_count == 0

    applied_result = dashboard_server.McpResult(
        exit_code=0,
        stdout="ok",
        stderr="",
        payload={
            "data": {
                "ok": True,
                "gameObjectPath": "PendingBoundaryProbe",
                "persistedReadback": True,
                "sceneSaved": True,
            }
        },
    )

    def invoke_with_bound_execution(_settings, tool_name, arguments, **_kwargs):
        plan = current_approved_unity_execution()
        assert plan is not None
        claim = plan.claim(tool_name, arguments, project)
        claim.complete()
        return applied_result

    with patch("dashboard_server.load_dashboard_settings", return_value=SimpleNamespace()), patch(
        "dashboard_server.invoke_unity_mcp", side_effect=invoke_with_bound_execution
    ) as resumed_invoke:
        gateway.approval_transactions.approve(approval_id)
        applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
        continuation = gateway.resume_runtime_task_after_approval(approval, applied)

    assert applied["status"] == "applied"
    assert continuation["resumedApprovalId"] == approval_id
    assert resumed_invoke.call_count == 1
    assert continuation["plan"]["nextStep"] != "done"
    assert any(
        action.get("status") == "failed" and action.get("kind") == "shell"
        for action in continuation["task"]["actions"]
    )

    with patch.object(
        gateway.runtime_planner,
        "plan_agent_turn",
        return_value={
            "planner": "llm",
            "reply": "Do not replay the approved write.",
            "continueLoop": False,
            "nextStep": "done",
        },
    ):
        duplicate = gateway.resume_runtime_task_after_approval(approval, applied)
    assert resumed_invoke.call_count == 1
    assert duplicate["plan"]["nextStep"] != "done"
