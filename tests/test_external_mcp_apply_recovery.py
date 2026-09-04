from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    return gateway


def _project(tmp_path: Path, name: str = "Project") -> Path:
    root = tmp_path / name
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (root / marker).mkdir(parents=True)
    return root


def _register(service, name: str, *, checkpoint: bool = True, prepare=None) -> None:
    service.register_write_handler(
        name,
        "test external write",
        "medium",
        lambda _args: {"ok": True},
        requires_approved_execution_context=checkpoint,
        pre_write_checkpoint_required=checkpoint,
        approved_execution_plan_builder=lambda _args: [("vrc_test_write", {})]
        if checkpoint
        else None,
        request_preparer=prepare,
    )


def _prepared(service, name: str, project: Path, **arguments):
    return service.prepare_external_mcp_write(
        name,
        {"projectRoot": str(project), **arguments},
    )


def _receipt(*, verified: bool = True) -> dict:
    return {
        "schema": "vrcforge.test_write_receipt.v1",
        "mutationStarted": True,
        "mutationApplied": True,
        "committed": True,
        "commitState": "committed",
        "verified": verified,
        "readback": {"verified": verified},
    }


def test_external_exception_records_needs_recovery_with_operation_and_target(
    monkeypatch, tmp_path: Path
) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_exception"
    _register(service, name)
    prepared = _prepared(service, name, project)
    calls = {"checkpoint": 0, "handler": 0}
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda *_args: calls.__setitem__("checkpoint", calls["checkpoint"] + 1)
        or {"ok": True, "id": "tmp-checkpoint", "projectRoot": str(project)},
    )
    monkeypatch.setattr(
        type(service),
        "_call_external_mcp_write_handler",
        lambda *_args: calls.__setitem__("handler", calls["handler"] + 1)
        or (_ for _ in ()).throw(RuntimeError("transport unknown")),
    )
    result = service.execute_prepared_external_mcp_write(prepared)
    active = service._ports.checkpoint.active_apply_recoveries()
    assert result["ok"] is False
    assert result["writeFailure"]["commitState"] == "unknown"
    assert len(active) == 1
    assert active[0]["status"] == "needs_recovery"
    assert active[0]["operationId"] == result["operationId"]
    assert calls == {"checkpoint": 1, "handler": 1}


def test_external_structured_no_write_failure_closes_recovery_safely(
    monkeypatch, tmp_path: Path
) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_structured_failure"
    _register(service, name)
    prepared = _prepared(service, name, project)
    calls = {"checkpoint": 0, "handler": 0}
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda *_args: calls.__setitem__("checkpoint", calls["checkpoint"] + 1)
        or {"ok": True, "id": "tmp-checkpoint", "projectRoot": str(project)},
    )
    monkeypatch.setattr(
        type(service),
        "_call_external_mcp_write_handler",
        lambda *_args: calls.__setitem__("handler", calls["handler"] + 1)
        or {"ok": False, "error": "rejected", "mutationStarted": False,
            "committed": False, "commitState": "not_started",
            "checkpointRecoveryRequired": False},
    )
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result["ok"] is False
    assert result["writeFailure"]["commitState"] == "not_started"
    assert result["recovery"]["status"] == "not_applied"
    assert service._ports.checkpoint.active_apply_recoveries() == []
    assert calls == {"checkpoint": 1, "handler": 1}


def test_external_verified_success_closes_recovery_and_preserves_execution_target(
    monkeypatch, tmp_path: Path
) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_success"
    _register(service, name)
    prepared = _prepared(service, name, project)
    operation_id = "external-op-verified"
    prepared["_externalOperationId"] = operation_id
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda *_args: {"ok": True, "id": "tmp-checkpoint", "projectRoot": str(project)},
    )
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", lambda *_args: _receipt())
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result["ok"] is True
    assert result["recovery"]["status"] == "applied"
    assert result["recovery"]["operationId"] == operation_id
    assert service._ports.checkpoint.active_apply_recoveries() == []
    target = {"schema": "vrcforge.execution_target.v1", "scope": "avatar"}
    saved = service._start_apply_recovery(
        {"id": "target-op", "operationId": "target-op", "targetTool": name, "executionTarget": target},
        {"projectRoot": str(project)},
        {"ok": True, "id": "target-checkpoint", "projectRoot": str(project)},
    )
    assert saved["operationId"] == "target-op"
    assert saved["executionTarget"] == target


def test_external_checkpoint_to_recovery_is_one_storage_critical_section(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_atomic_recovery_start"
    _register(service, name)
    prepared = _prepared(service, name, project)
    delete_attempted = threading.Event()
    delete_acquired = threading.Event()

    def fake_checkpoint(*_args):
        def manual_delete() -> None:
            delete_attempted.set()
            with service._ports.state.checkpoint_storage_lock:
                delete_acquired.set()

        threading.Thread(target=manual_delete, daemon=True).start()
        assert delete_attempted.wait(1)
        return {"ok": True, "id": "atomic-checkpoint", "projectRoot": str(project)}

    original_start = service._start_apply_recovery

    def checked_start(_self, *args, **kwargs):
        assert not delete_acquired.is_set()
        return original_start(*args, **kwargs)

    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", fake_checkpoint)
    monkeypatch.setattr(type(service), "_start_apply_recovery", checked_start)
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", lambda *_args: _receipt())
    assert service.execute_prepared_external_mcp_write(prepared)["ok"] is True
    assert delete_acquired.wait(1)


def test_external_unverified_success_requires_recovery(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_unverified"
    _register(service, name)
    prepared = _prepared(service, name, project)
    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint",
                        lambda *_args: {"ok": True, "id": "tmp-checkpoint", "projectRoot": str(project)})
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler",
                        lambda *_args: {"ok": True, "status": "applied"})
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result["ok"] is False
    assert result["recovery"]["status"] == "needs_recovery"
    assert len(service._ports.checkpoint.active_apply_recoveries()) == 1


def test_external_preview_has_no_checkpoint_or_handler(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_preview"
    _register(service, name, checkpoint=False, prepare=lambda args, _preview: (dict(args), {"preview": True}))
    prepared = _prepared(service, name, project, preview=True)
    calls = {"checkpoint": 0, "handler": 0}
    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", lambda *_args: calls.__setitem__("checkpoint", 1))
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", lambda *_args: calls.__setitem__("handler", 1))
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result["status"] == "preview"
    assert calls == {"checkpoint": 0, "handler": 0}
    assert service._ports.checkpoint.active_apply_recoveries() == []


def test_external_before_handler_failure_closes_checkpoint_recovery(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    name = "vrcforge_test_external_before_handler"
    service.register_write_handler(
        name,
        "test before-handler failure",
        "medium",
        lambda _args: pytest.fail("handler must not run"),
        requires_approved_execution_context=True,
        pre_write_checkpoint_required=True,
        approved_execution_plan_builder=lambda _args: [("vrc_test_write", {})],
        verification_prepare_handler=lambda _args: (_ for _ in ()).throw(RuntimeError("baseline failed")),
    )
    prepared = _prepared(service, name, project)
    calls = {"checkpoint": 0}
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda *_args: calls.__setitem__("checkpoint", 1)
        or {"ok": True, "id": "tmp-checkpoint", "projectRoot": str(project)},
    )
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result["ok"] is False
    assert result["recovery"]["status"] == "not_applied"
    assert calls == {"checkpoint": 1}
    assert service._ports.checkpoint.active_apply_recoveries() == []


@pytest.mark.parametrize("scope", ["same", "global", "other"])
def test_external_active_recovery_project_gate_and_exempt_rule(monkeypatch, tmp_path: Path, scope: str) -> None:
    gateway = _gateway(tmp_path)
    project_a = _project(tmp_path, "ProjectA")
    project_b = _project(tmp_path, "ProjectB")
    service = gateway.approval_transactions
    active_project = project_a if scope == "same" else ("" if scope == "global" else project_b)
    service._start_apply_recovery(
        {"id": "prior-op", "operationId": "prior-op", "targetTool": "prior"},
        {"projectRoot": str(active_project) if active_project else ""},
        {"ok": True, "id": "prior-checkpoint", "projectRoot": str(active_project) if active_project else ""},
    )
    name = "vrcforge_test_external_gate"
    _register(service, name, checkpoint=False)
    prepared = _prepared(service, name, project_a)
    calls = {"handler": 0, "checkpoint": 0}
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", lambda *_args: calls.__setitem__("handler", 1) or _receipt())
    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", lambda *_args: calls.__setitem__("checkpoint", 1))
    if scope in {"same", "global"}:
        with pytest.raises(AgentGatewayError, match="previous write did not finish"):
            service.execute_prepared_external_mcp_write(prepared)
        assert calls == {"handler": 0, "checkpoint": 0}
    else:
        assert service.execute_prepared_external_mcp_write(prepared)["ok"] is True
        assert calls["handler"] == 1


def test_external_recovery_tool_remains_exempt(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    service._start_apply_recovery(
        {"id": "prior-op", "targetTool": "prior"},
        {"projectRoot": str(project)},
        {"ok": True, "id": "prior-checkpoint", "projectRoot": str(project)},
    )
    name = "vrcforge_restore_checkpoint"
    _register(service, name, checkpoint=False)
    prepared = _prepared(service, name, project)
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", lambda *_args: _receipt())
    assert service.execute_prepared_external_mcp_write(prepared)["ok"] is True
