"""Exercise the real approved-write transaction around the wardrobe service."""
from __future__ import annotations

from pathlib import Path

from agent_gateway import AgentGateway
from wardrobe_outfit_workflow_service import (
    CreateWardrobeApprovedWritePorts,
    CreateWardrobeApprovedWriteService,
    build_create_wardrobe_core_calls,
    build_create_wardrobe_request,
)


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    return gateway


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (root / marker).mkdir(parents=True)
    return root


def _receipt(step: str, *, ok: bool = True, started: bool = True) -> dict:
    return {
        "schema": f"vrcforge.wardrobe.{step}.receipt.v1",
        "ok": ok,
        "mutationStarted": started,
        "mutationApplied": started,
        "committed": started,
        "commitState": "committed" if started else "not_started",
        "verified": ok and started,
        "persistedReadback": ok and started,
        "readback": {"step": step, "value": "persisted"} if ok and started else {},
    }


def _service(gateway: AgentGateway, outcomes: list[dict]) -> CreateWardrobeApprovedWriteService:
    logs: list[dict] = []
    wardrobe = CreateWardrobeApprovedWriteService(
        CreateWardrobeApprovedWritePorts(
            build_request=build_create_wardrobe_request,
            build_calls=build_create_wardrobe_core_calls,
            ensure_parameter=lambda _args: outcomes[0],
            ensure_animator=lambda _args: outcomes[1],
            ensure_menu=lambda _args: outcomes[2],
            log=lambda level, scope, message, data=None: logs.append(
                {"level": level, "scope": scope, "message": message, "data": data}
            ),
        )
    )
    gateway.approval_transactions.register_write_handler(
        "vrcforge_create_wardrobe",
        "Create a wardrobe fixture.",
        "high",
        wardrobe.execute,
        requires_approved_execution_context=True,
        pre_write_checkpoint_required=True,
        approved_execution_plan_builder=lambda _args: [("vrc_test_wardrobe", {})],
    )
    return wardrobe


def _execute(monkeypatch, gateway: AgentGateway, wardrobe: CreateWardrobeApprovedWriteService, project: Path) -> dict:
    service = gateway.approval_transactions
    # Keep the transaction service real while routing its handler call directly
    # to the real wardrobe service; Unity/Core execution is outside this test.
    monkeypatch.setattr(
        type(service),
        "_call_external_mcp_write_handler",
        lambda _self, _handler, _tool, _operation, arguments, _digest, _plan: wardrobe.execute(arguments),
    )
    prepared = service.prepare_external_mcp_write(
        "vrcforge_create_wardrobe",
        {"projectRoot": str(project), "parameterName": "Clothes"},
    )
    return service.execute_prepared_external_mcp_write(prepared)


def _checkpoint(monkeypatch, gateway: AgentGateway, project: Path) -> None:
    monkeypatch.setattr(
        type(gateway.approval_transactions),
        "_create_pre_write_checkpoint",
        lambda *_args: {"ok": True, "status": "ready", "id": "ckpt-wardrobe", "projectRoot": str(project)},
    )


def test_wardrobe_three_verified_step_receipts_are_applied(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    wardrobe = _service(gateway, [_receipt("parameter"), _receipt("animator"), _receipt("menu")])
    _checkpoint(monkeypatch, gateway, project)

    result = _execute(monkeypatch, gateway, wardrobe, project)

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["commitState"] == "committed"
    assert result["readbackState"] == "verified"
    assert result["recovery"]["status"] == "applied"
    assert result["result"]["verified"] is True
    assert result["result"]["persistedReadback"] is True
    assert len(result["result"]["readback"]["steps"]) == 3


def test_wardrobe_second_step_failure_preserves_partial_facts_and_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    wardrobe = _service(gateway, [_receipt("parameter"), _receipt("animator", ok=False), _receipt("menu")])
    _checkpoint(monkeypatch, gateway, project)

    result = _execute(monkeypatch, gateway, wardrobe, project)

    assert result["ok"] is False
    assert result["recovery"]["status"] == "needs_recovery"
    assert result["result"]["steps"][0]["result"]["committed"] is True
    assert result["result"]["steps"][1]["result"]["ok"] is False
    assert result["writeFailure"]["mutationStarted"] is True


def test_wardrobe_first_explicit_no_write_closes_recovery(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    wardrobe = _service(gateway, [_receipt("parameter", ok=False, started=False), _receipt("animator"), _receipt("menu")])
    _checkpoint(monkeypatch, gateway, project)

    result = _execute(monkeypatch, gateway, wardrobe, project)

    assert result["ok"] is False
    assert result["recovery"]["status"] == "not_applied"
    assert result["writeFailure"]["mutationStarted"] is False
    assert result["writeFailure"]["commitState"] == "not_started"
    assert result["result"]["steps"][-1]["tool"] == "vrc_ensure_expression_parameter"
