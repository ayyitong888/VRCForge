from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from agent_approval_transactions import AgentApprovalTransactionService
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
        service = gateway._approval_transactions

        assert isinstance(service, AgentApprovalTransactionService)
        assert service._host is gateway
        assert AgentApprovalTransactionService.__slots__ == ("_host",)
        assert service._approvals is gateway._approvals
        assert service._write_handlers is gateway._write_handlers
        assert service._in_flight_apply_writes is gateway._in_flight_apply_writes
        assert service._lock is gateway._lock
        assert service._checkpoint_storage_lock is gateway._checkpoint_storage_lock


def test_approval_transaction_internal_calls_preserve_facade_monkeypatches() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        gateway._approvals["patched"] = {
            "id": "patched",
            "status": "pending",
            "createdAt": "2026-08-07T00:00:00+00:00",
        }
        sentinel = {
            "id": "patched",
            "status": "expired",
            "createdAt": "2026-08-07T00:00:00+00:00",
        }
        gateway._refresh_approval_expiry = lambda _approval: sentinel  # type: ignore[method-assign]

        assert gateway.list_approvals(include_expired=True) == [sentinel]


def test_approval_transaction_hooks_remain_late_bound_after_construction() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))

        def observer(_stage: str, _payload: dict[str, object]) -> None:
            return None

        gateway.apply_lifecycle_observer_fn = observer
        gateway.scoped_approval_reviewer_fn = lambda _approval: "manual"

        assert gateway._approval_transactions.apply_lifecycle_observer_fn is observer
        assert gateway._approval_transactions.scoped_approval_reviewer_fn is gateway.scoped_approval_reviewer_fn


def test_approval_transaction_facade_is_delegate_only_and_keeps_domain_boundaries() -> None:
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
    implementation_methods = {
        node.name.removeprefix("_impl_"): node
        for node in service_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_impl_")
    }

    assert len(implementation_methods) == 41
    assert {
        "list_checkpoints",
        "restore_checkpoint",
        "_validated_memory_evidence_for_applied_write",
        "_write_handler_allows_future_category",
        "execute_shell_payload",
        "_plan_write_intent",
    }.isdisjoint(implementation_methods)

    for method_name, implementation in implementation_methods.items():
        facade = gateway_methods[method_name]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(
            implementation.args,
            include_attributes=False,
        )
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        call = statement.value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr == f"_impl_{method_name}"


def test_agent_gateway_facade_respects_approval_transaction_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()

    assert len(source) <= AGENT_GATEWAY_MAX_BYTES
    assert source.count(b"\n") <= AGENT_GATEWAY_MAX_LF_LINES
