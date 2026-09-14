import pytest
from test_external_mcp_apply_recovery import _gateway, _project, _register, _prepared
from agent_gateway import AgentGatewayError


def test_prior_recovery_gate_returns_discoverable_guidance_without_current_mutation(tmp_path):
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    service = gateway.approval_transactions
    recovery = service._start_apply_recovery(
        {"id": "prior", "targetTool": "vrcforge_refresh_unity_asset_database"},
        {"projectRoot": str(project)},
        {"ok": True, "id": "prior-checkpoint", "projectRoot": str(project)},
    )
    _register(service, "vrcforge_test_blocked_write", checkpoint=False)
    prepared = _prepared(service, "vrcforge_test_blocked_write", project)
    with pytest.raises(AgentGatewayError) as raised:
        service.execute_prepared_external_mcp_write(prepared)
    result = gateway._external_mcp_no_write_error("vrcforge_test_blocked_write", "transaction_start", raised.value)
    assert result["blockingRecoveries"] == [{"recoveryId": recovery["id"], "checkpointId": "prior-checkpoint", "targetTool": "vrcforge_refresh_unity_asset_database", "status": "applying"}]
    assert result["recoveryDiscovery"] == {"tool": "vrcforge_list_interrupted_apply_recoveries", "arguments": {"projectRoot": str(project), "includeResolved": False}}
    assert "vrcforge_preview_interrupted_apply_recovery" in result["nextAction"]
    assert result["outcome"]["nextAction"] == result["nextAction"]
    from external_mcp_result_projection import project_result
    compact = project_result({**result, "operationResource": "vrcforge://operation/test/receipt"}, mode="compact", resource_readable=True)
    assert compact["nextAction"] == result["nextAction"]
    assert compact["blockingRecoveries"] == result["blockingRecoveries"]
    assert compact["recoveryDiscovery"] == result["recoveryDiscovery"]
    assert result["errorDetails"]["mutationStarted"] is False
    assert result["errorDetails"]["commitState"] == "not_started"
    assert result["errorDetails"]["safeToRetry"] is False
    assert service._ports.checkpoint.active_apply_recoveries()[0]["id"] == recovery["id"]


def test_unrelated_transaction_error_does_not_invent_recovery(tmp_path):
    gateway = _gateway(tmp_path)
    result = gateway._external_mcp_no_write_error("vrcforge_test", "transaction_start", AgentGatewayError("Other write active", status_code=409))
    assert "blockingRecoveries" not in result
    assert "recoveryDiscovery" not in result
