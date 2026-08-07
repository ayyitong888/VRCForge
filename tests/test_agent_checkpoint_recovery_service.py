from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from agent_checkpoint_recovery import AgentCheckpointRecoveryService
from agent_gateway import AgentGateway


REPO_ROOT = Path(__file__).parents[1]
AGENT_GATEWAY_MAX_BYTES = 578_597
AGENT_GATEWAY_MAX_LF_LINES = 12_340


def _gateway(root: Path) -> AgentGateway:
    return AgentGateway(root / "config.json", root / "audit")


def _class_definition(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def test_checkpoint_recovery_service_owns_no_second_runtime_or_lock() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        service = gateway._checkpoint_recovery

        assert isinstance(service, AgentCheckpointRecoveryService)
        assert service._host is gateway
        assert AgentCheckpointRecoveryService.__slots__ == ("_host",)
        assert service._checkpoint_storage_lock is gateway._checkpoint_storage_lock
        assert service._project_chat_checkpoint_lock is gateway._project_chat_checkpoint_lock


def test_checkpoint_recovery_internal_calls_preserve_facade_monkeypatches() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        sentinel = {"ok": True, "checkpoints": [{"id": "patched"}], "count": 1}
        gateway._list_checkpoints_locked = lambda _params: sentinel  # type: ignore[method-assign]

        assert gateway.list_checkpoints() is sentinel


def test_checkpoint_recovery_hooks_remain_late_bound_after_construction() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))

        def prepare(_project_root: Path) -> dict[str, str]:
            return {"status": "prepared"}

        def reload(_project_root: Path, _prepared: dict[str, object]) -> dict[str, str]:
            return {"status": "reloaded"}

        gateway.checkpoint_restore_prepare_handler = prepare
        gateway.checkpoint_restore_handler = reload

        assert gateway._checkpoint_recovery.checkpoint_restore_prepare_handler is prepare
        assert gateway._checkpoint_recovery.checkpoint_restore_handler is reload


def test_checkpoint_recovery_facade_is_delegate_only_and_keeps_transaction_boundary() -> None:
    gateway_class = _class_definition(REPO_ROOT / "agent_gateway.py", "AgentGateway")
    service_class = _class_definition(
        REPO_ROOT / "agent_checkpoint_recovery.py",
        "AgentCheckpointRecoveryService",
    )
    gateway_methods = {
        node.name: node
        for node in gateway_class.body
        if isinstance(node, ast.FunctionDef)
    }
    implementation_methods = {
        node.name.removeprefix("_impl_"): node
        for node in service_class.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }

    assert len(implementation_methods) == 87
    assert {
        "_call_write_handler",
        "_create_pre_write_checkpoint",
        "_create_pre_write_checkpoint_locked",
        "_start_apply_recovery",
        "_finish_apply_recovery",
        "_resolve_apply_recoveries_for_checkpoint",
        "has_in_flight_project_write",
    }.isdisjoint(implementation_methods)

    for method_name in implementation_methods:
        facade = gateway_methods[method_name]
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        call = statement.value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr == f"_impl_{method_name}"


def test_agent_gateway_facade_respects_the_monotonic_1_5_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()

    assert len(source) <= AGENT_GATEWAY_MAX_BYTES
    assert source.count(b"\n") <= AGENT_GATEWAY_MAX_LF_LINES
