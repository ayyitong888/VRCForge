from __future__ import annotations

from pathlib import Path

import pytest
from agent_gateway import AgentGateway
from approved_unity_execution import (
    create_approved_unity_execution_plan,
    current_approved_unity_execution,
)
from agent_approval_transactions import _requires_pre_write_checkpoint


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True)
    return project


def _service(tmp_path: Path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    return gateway.approval_transactions


def test_external_runtime_policy_skips_checkpoint_only_for_play_exit(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    service = _service(tmp_path)
    service.register_write_handler(
        "vrcforge_runtime_policy_test",
        "runtime policy test",
        "low",
        lambda _args: {"ok": True},
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_runtime_policy_test", dict(args))],
        checkpoint_prepare_handler=lambda _approval, _args: {"ok": True},
        pre_write_checkpoint_required=lambda args: args.get("isPlaying") is not False,
    )
    checkpoint_calls: list[dict] = []
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda _self, approval, _args: checkpoint_calls.append(dict(approval))
        or {"ok": True, "status": "ready", "id": "checkpoint", "projectRoot": str(project)},
    )
    monkeypatch.setattr(
        type(service),
        "_call_external_mcp_write_handler",
        lambda *_args: {
            "ok": True,
            "status": "applied",
            "mutationStarted": True,
            "mutationApplied": True,
            "committed": True,
            "commitState": "committed",
            "verified": True,
            "readback": {"verified": True},
        },
    )

    exit_prepared = service.prepare_external_mcp_write(
        "vrcforge_runtime_policy_test",
        {"projectRoot": str(project), "isPlaying": False},
    )
    assert service.execute_prepared_external_mcp_write(exit_prepared)["ok"] is True
    assert checkpoint_calls == []

    enter_prepared = service.prepare_external_mcp_write(
        "vrcforge_runtime_policy_test",
        {"projectRoot": str(project), "isPlaying": True},
    )
    assert service.execute_prepared_external_mcp_write(enter_prepared)["ok"] is True
    assert len(checkpoint_calls) == 1


def test_internal_approved_runtime_policy_uses_same_predicate(tmp_path) -> None:
    project = _project(tmp_path)
    service = _service(tmp_path)
    service.register_write_handler(
        "vrcforge_internal_runtime_policy_test",
        "internal runtime policy test",
        "low",
        lambda _args: {"ok": True},
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_runtime_policy_test", dict(args))],
        pre_write_checkpoint_required=lambda args: args.get("isPlaying") is not False,
    )
    handler = service._ports.state.write_handlers["vrcforge_internal_runtime_policy_test"]
    assert _requires_pre_write_checkpoint(handler, {"isPlaying": False}) is False
    assert _requires_pre_write_checkpoint(handler, {"isPlaying": True}) is True
    handler.pre_write_checkpoint_required = lambda _args: "yes"
    assert _requires_pre_write_checkpoint(handler, {"isPlaying": False}) is True


def test_internal_approved_play_exit_runs_without_checkpoint_but_enter_does_not(tmp_path) -> None:
    project = _project(tmp_path)
    service = _service(tmp_path)
    calls: list[dict] = []

    def handler(arguments: dict) -> dict:
        plan = current_approved_unity_execution()
        assert plan is not None
        claim = plan.claim("vrc_runtime_policy_test", arguments, project)
        claim.complete()
        calls.append(arguments)
        return {"ok": True, "verified": True, "mutationStarted": True, "committed": True}

    service.register_write_handler(
        "vrcforge_internal_runtime_apply",
        "internal runtime apply",
        "low",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_runtime_policy_test", dict(args))],
        pre_write_checkpoint_required=lambda args: args.get("isPlaying") is not False,
    )
    monkeypatch = pytest.MonkeyPatch()
    checkpoint_calls: list[dict] = []
    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda _self, approval, _args: checkpoint_calls.append(dict(approval)) or None,
    )
    try:
        request = service.create_apply_request({"target_tool": "vrcforge_internal_runtime_apply", "arguments": {"projectRoot": str(project), "isPlaying": False}})
        service.approve(request["approval"]["id"])
        result = service.apply_approved({"approval_id": request["approval"]["id"]})
        assert result["ok"] is True, result.get("error") or result
        assert checkpoint_calls == []
        request = service.create_apply_request({"target_tool": "vrcforge_internal_runtime_apply", "arguments": {"projectRoot": str(project), "isPlaying": True}})
        service.approve(request["approval"]["id"])
        result = service.apply_approved({"approval_id": request["approval"]["id"]})
        assert result["ok"] is False
        assert len(checkpoint_calls) == 1
    finally:
        monkeypatch.undo()
    registered_handler = service._ports.state.write_handlers["vrcforge_internal_runtime_apply"]
    registered_handler.pre_write_checkpoint_required = lambda _args: (_ for _ in ()).throw(RuntimeError("bad policy"))
    assert _requires_pre_write_checkpoint(registered_handler, {"isPlaying": False}) is True


def test_internal_runtime_rejects_missing_project_and_plan_drift(tmp_path) -> None:
    project = _project(tmp_path)
    service = _service(tmp_path)
    calls: list[dict] = []

    def handler(arguments: dict) -> dict:
        plan = current_approved_unity_execution()
        assert plan is not None
        plan.claim("vrc_runtime_policy_test", arguments, project).complete()
        calls.append(arguments)
        return {"ok": True, "verified": True}

    service.register_write_handler(
        "vrcforge_internal_runtime_reject", "internal runtime reject", "low", handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_runtime_policy_test", dict(args))],
        pre_write_checkpoint_required=False,
    )
    missing = service.create_apply_request({"target_tool": "vrcforge_internal_runtime_reject", "arguments": {"isPlaying": False}})
    service.approve(missing["approval"]["id"])
    missing_result = service.apply_approved({"approval_id": missing["approval"]["id"]})
    assert missing_result["ok"] is False
    assert calls == []

    drift = service.create_apply_request({"target_tool": "vrcforge_internal_runtime_reject", "arguments": {"projectRoot": str(project), "isPlaying": False}})
    service.approve(drift["approval"]["id"])
    service._ports.state.write_handlers["vrcforge_internal_runtime_reject"].approved_execution_plan_builder = lambda args: [("different_tool", dict(args))]
    drift_result = service.apply_approved({"approval_id": drift["approval"]["id"]})
    assert drift_result["ok"] is False
    assert calls == []


def test_approved_context_checkpoint_required_defaults_true_and_is_strict_bool(tmp_path) -> None:
    base = {
        "lane": "approved_write",
        "approvalId": "approval",
        "targetTool": "runtime",
        "projectRoot": str(_project(tmp_path)),
        "issuedAtUnixMs": 1,
        "expiresAtUnixMs": 2,
    }
    with pytest.raises(ValueError, match="context is invalid"):
        create_approved_unity_execution_plan(base, [("tool", {})])
    accepted = {**base, "checkpointRequired": False}
    create_approved_unity_execution_plan(accepted, [("tool", {})])
    with pytest.raises(ValueError, match="context is invalid"):
        create_approved_unity_execution_plan({**accepted, "checkpointRequired": "false"}, [("tool", {})])
