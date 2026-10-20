from __future__ import annotations

from pathlib import Path

import agent_checkpoint_recovery
from agent_gateway import AgentGateway


def _full_auto_gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "roslyn_full_auto"
    config.roslyn_risk_acknowledged = True
    gateway.save_config(config)
    return gateway


def test_blocked_zero_effect_restore_requires_new_manual_approval_in_full_auto(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recovery = {
        "id": "recovery-zero-effect-blocked",
        "checkpointId": "checkpoint-zero-effect-blocked",
        "status": "needs_recovery",
        "zeroEffectAutoLaunchBlocked": True,
    }
    monkeypatch.setattr(
        agent_checkpoint_recovery.AgentCheckpointRecoveryService,
        "_active_apply_recoveries",
        lambda _service: [recovery],
    )
    gateway = _full_auto_gateway(tmp_path)
    service = gateway.approval_transactions
    service.register_write_handler(
        "vrcforge_restore_checkpoint",
        "Restore a checkpoint for an interrupted approved write.",
        "high",
        lambda _arguments: {"ok": True},
    )
    arguments = {
        "checkpointId": recovery["checkpointId"],
        "recoveryId": recovery["id"],
        "confirmRestore": True,
    }

    requested = service.create_apply_request(
        {"target_tool": "vrcforge_restore_checkpoint", "arguments": arguments}
    )
    assert requested["status"] == "pending"
    approval = requested["approval"]
    assert approval["requiresExplicitApproval"] is True
    assert approval["autoApprovalBlocked"] is True
    assert "new invocation" in approval["explicitApprovalReason"]
    assert "_vrcforge_incident_restore_auto" not in approval["arguments"]

    prepared = service.prepare_external_mcp_write(
        "vrcforge_restore_checkpoint", arguments
    )
    assert prepared["requiresUserConfirmation"] is True
    assert "new invocation" in prepared["confirmationReason"]
    assert "_vrcforge_incident_restore_auto" not in prepared["arguments"]
