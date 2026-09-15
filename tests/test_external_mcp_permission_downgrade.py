from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError


@pytest.mark.parametrize("mode", ["approval", "auto"])
@pytest.mark.parametrize("risk", ["high", "critical"])
def test_prepared_automatic_write_cannot_keep_full_permission_after_downgrade(
    tmp_path: Path, mode: str, risk: str,
) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "roslyn_full_auto"
    gateway.save_config(config)
    executed = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_permission_probe", "Check permission before applying.", risk,
        lambda arguments: executed.append(arguments) or {"ok": True},
    )
    prepared = gateway.approval_transactions.prepare_external_mcp_write(
        "vrcforge_permission_probe", {"value": 1},
    )
    assert prepared["requiresUserConfirmation"] is False
    gateway.approval_transactions.update_permission_state(mode)
    reloaded = AgentGateway(tmp_path / "config.json", tmp_path / "reload-audit")
    assert reloaded.approval_transactions.permission_state()["executionMode"] == mode

    with pytest.raises(AgentGatewayError, match="[Pp]ermission"):
        gateway.approval_transactions.execute_prepared_external_mcp_write(prepared)
    assert executed == []
