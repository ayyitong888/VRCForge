from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path

from agent_approval_transactions import AgentApprovalTransactionService, ApprovalGoalPorts
from agent_gateway import AgentGateway


REPO_ROOT = Path(__file__).parents[1]
AGENT_GATEWAY_MAX_BYTES = 506_403
AGENT_GATEWAY_MAX_LF_LINES = 10_954


def _gateway(root: Path) -> AgentGateway:
    return AgentGateway(root / "config.json", root / "audit")


def _class_definition(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def test_approval_transaction_service_owns_no_second_state_or_runtime_resource() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        service = gateway.approval_transactions
        state = service._ports.state

        assert isinstance(service, AgentApprovalTransactionService)
        assert not hasattr(service, "_host")
        assert "__getattr__" not in AgentApprovalTransactionService.__dict__
        assert service._runtime_run_append.__self__ is gateway.runtime_runs
        assert isinstance(service._goal, ApprovalGoalPorts)
        assert service._goal.deny_approval.__self__ is gateway.goal
        assert service._goal.attach_terminal_resolution.__self__ is gateway.goal
        assert service._goal.delivery_for_approval.__self__ is gateway.goal
        assert service._goal.reconcile_missing_approvals.__self__ is gateway.goal
        assert state.approvals is gateway._approvals
        assert state.write_handlers is gateway._write_handlers
        assert state.in_flight_apply_writes is gateway._in_flight_apply_writes
        assert state.shared_state_lock is gateway._lock
        assert state.checkpoint_storage_lock is gateway._checkpoint_storage_lock
        assert service._ports.skills.write_lock is gateway.skills.write_lock


def test_pending_approval_does_not_expire_without_a_user_decision() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        gateway._approvals["patched"] = {
            "id": "patched",
            "status": "pending",
            "createdAt": "2026-08-07T00:00:00+00:00",
            "expiresAt": "2026-08-07T00:01:00+00:00",
        }
        approvals = gateway.approval_transactions.list_approvals(include_expired=True)
        assert approvals[0]["id"] == "patched"
        assert approvals[0]["status"] == "pending"
        assert "expiresAt" not in approvals[0]


def test_new_pending_approval_has_no_timeout() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        gateway.approval_transactions.register_write_handler(
            "vrcforge_fixture_write",
            "Write a fixture.",
            "medium",
            lambda _arguments: {"ok": True},
        )

        requested = gateway.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_fixture_write",
                "arguments": {"projectRoot": str(Path(temp_dir) / "Project")},
            }
        )

        assert requested["status"] == "pending"
        assert requested["approval"]["status"] == "pending"
        assert "expiresAt" not in requested["approval"]


def test_pending_approval_survives_gateway_restart_until_user_decides() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        first = _gateway(root)
        first.approval_transactions.register_write_handler(
            "vrcforge_fixture_write",
            "Write a fixture.",
            "medium",
            lambda _arguments: {"ok": True},
        )
        requested = first.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_fixture_write",
                "arguments": {
                    "projectRoot": str(root / "Project"),
                    "value": "exact persisted proposal",
                },
            }
        )
        approval_id = requested["approval"]["id"]

        reopened = _gateway(root)
        restored = reopened.approval_transactions.list_approvals(
            include_expired=False
        )

        assert [item["id"] for item in restored] == [approval_id]
        assert restored[0]["status"] == "pending"
        assert restored[0]["targetTool"] == "vrcforge_fixture_write"
        assert restored[0]["projectRoot"] == str(root / "Project")
        assert "expiresAt" not in restored[0]

        rejected = reopened.approval_transactions.reject(approval_id)
        assert rejected["ok"] is True
        assert rejected["approval"]["status"] == "rejected"

        after_decision = _gateway(root)
        assert after_decision.approval_transactions.list_approvals(
            include_expired=False
        ) == []


def test_corrupt_or_terminal_pending_snapshot_never_restores_execution_state() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        audit = root / "audit"
        audit.mkdir(parents=True)
        (audit / "pending-approvals.json").write_text(
            json.dumps(
                {
                    "schema": "vrcforge.pending-approvals.v1",
                    "approvals": [
                        {
                            "id": "appr_tampered",
                            "createdAt": "2026-08-11T00:00:00+00:00",
                            "targetTool": "vrcforge_fixture_write",
                            "status": "pending",
                            "approvedAt": "2026-08-11T00:00:01+00:00",
                            "arguments": {"value": "must-not-restore"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        reopened = _gateway(root)

        assert reopened.approval_transactions.list_approvals(
            include_expired=False
        ) == []
        audit_events = [
            json.loads(line)
            for line in reopened.audit_log_path.read_text(encoding="utf-8").splitlines()
        ]
        assert audit_events[-1]["event"] == "pending_approval_snapshot_invalid"


def test_approval_transaction_hooks_remain_late_bound_after_construction() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))

        def observer(_stage: str, _payload: dict[str, object]) -> None:
            return None

        gateway.approval_transactions.apply_lifecycle_observer = observer
        gateway.approval_transactions.scoped_approval_reviewer = lambda _approval: "manual"

        assert gateway.approval_transactions.apply_lifecycle_observer is observer
        assert gateway.approval_transactions.scoped_approval_reviewer is not None


def test_approval_transaction_owner_retires_gateway_facades_and_impl_names() -> None:
    gateway_class = _class_definition(REPO_ROOT / "agent_gateway.py", "AgentGateway")
    service_class = _class_definition(
        REPO_ROOT / "agent_approval_transactions.py",
        "AgentApprovalTransactionService",
    )
    gateway_methods = {
        node.name: node
        for node in gateway_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    owner_methods = {
        node.name: node
        for node in service_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    owned_methods = {
        "_observe_apply_lifecycle", "register_write_handler", "authenticate_approval",
        "auto_approval_enabled", "permission_audit_context", "_auto_approval_block_reason",
        "permission_state", "update_permission_state", "_execute_write_request",
        "create_apply_request", "_auto_execute_approval", "_matching_project_category_allow_rule",
        "_scoped_rule_execute_approval", "apply_approved", "list_approvals", "approve",
        "approve_with_project_category_rule", "reject", "recent_audit_logs", "_call_write_handler",
        "_create_pre_write_checkpoint", "_create_pre_write_checkpoint_locked",
        "has_in_flight_project_write", "try_acquire_background_project_read",
        "release_background_project_read", "_apply_recovery_blocks_writes",
        "_start_apply_recovery", "_finish_apply_recovery",
        "_resolve_apply_recoveries_for_checkpoint", "visible_write_targets",
        "_write_handler_rollback_policy", "_inject_user_constraints_for_apply",
        "_write_auto_manual_approval_reason", "_new_approval", "_approval_project_root",
        "_ensure_approval_scope", "_set_approval_status", "request_approval_revision",
        "_refresh_approval_expiry", "_load_approval_from_audit",
        "_reconcile_unrecoverable_linked_approval",
    }

    assert owned_methods <= owner_methods.keys()
    assert owned_methods.isdisjoint(gateway_methods)
    assert all(not name.startswith("_impl_") for name in owner_methods)


def test_agent_gateway_facade_respects_approval_transaction_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()

    assert len(source) <= AGENT_GATEWAY_MAX_BYTES
    assert source.count(b"\n") <= AGENT_GATEWAY_MAX_LF_LINES
