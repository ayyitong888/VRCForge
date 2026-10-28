"""Replay corrective tool failures through the real transaction and MCP projections."""
from copy import deepcopy

import pytest
from agent_gateway import AgentGateway
from external_mcp_result_projection import project_result
from test_external_mcp_apply_recovery import _gateway, _project, _register, _prepared


def failure():
    return {"ok": False, "code": "component_property_value_invalid",
            "error": "m_objects.Array.data[0].Material requires a Material asset reference.",
            "mutationStarted": False, "committed": False, "commitState": "not_started",
            "checkpointRecoveryRequired": False, "retryable": False,
            "nextAction": "Read the serialized field type and supply a Material asset reference.",
            "diagnostics": {"handlerException": {"type": "System.ArgumentException",
                "message": "Expected UnityEngine.Material, received a string."}}}


def test_corrective_action_survives_failed_external_transaction(monkeypatch, tmp_path):
    gateway = _gateway(tmp_path)
    service = gateway.approval_transactions
    project = _project(tmp_path)
    name = "vrcforge_test_corrective_write"
    _register(service, name)
    prepared = _prepared(service, name, project)
    raw = failure()
    original = deepcopy(raw)
    calls = []
    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint",
        lambda *_a, **_kw: {"ok": True, "id": "fixture-checkpoint", "projectRoot": str(project)})
    def handler(*_a, **_kw):
        calls.append(name)
        return raw
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", handler)
    applied = service.execute_prepared_external_mcp_write(prepared)
    assert applied["ok"] is False
    assert applied["result"] == original
    assert applied["nextAction"] == raw["nextAction"]
    receipt = gateway._external_mcp_write_result(name, applied)
    receipt["operationResource"] = "vrcforge://operations/fixture-corrective-write"
    compact = project_result(receipt, mode="compact", resource_readable=True)
    assert compact["outcome"]["nextAction"] == raw["nextAction"]
    assert compact["outcome"]["error"]["code"] == raw["code"]
    assert compact["outcome"]["diagnostics"] == raw["diagnostics"]
    assert compact["mutationStarted"] is False
    assert calls == [name]
    assert service._ports.checkpoint.active_apply_recoveries() == []
    assert raw == original


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_empty_wrapper_action_does_not_erase_domain_correction(empty):
    raw = failure()
    result = AgentGateway._external_mcp_write_result(AgentGateway.__new__(AgentGateway),
        "vrcforge_test_corrective_write", {"ok": False, "status": "failed",
            "nextAction": empty, "result": raw})
    assert result["outcome"]["nextAction"] == raw["nextAction"]
