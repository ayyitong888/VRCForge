import json
from pathlib import Path

from contextlib import nullcontext
from types import SimpleNamespace

from agent_approval_transactions import AgentApprovalTransactionService
from agent_gateway import AgentGateway
from approved_unity_execution import current_approved_unity_execution


def test_actual_registered_internal_gm_entry_waits_for_verified_connection(tmp_path, monkeypatch):
    import dashboard_server

    name = "vrcforge_gesture_manager_enter_play_mode"
    registered = dashboard_server.AGENT_GATEWAY._write_handlers[name]
    assert registered.requires_approved_execution_context is True
    assert registered.handler is dashboard_server.gesture_manager_enter_play_mode_sync
    assert registered.verification_finalize_handler is dashboard_server.gesture_manager_enter_play_mode_finalize
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.approval_transactions
    service._ports.state.write_handlers[name] = registered
    raw = json.loads((Path(__file__).parent / "fixtures" / "gesture_manager_pending_receipt.json").read_text())
    calls = []

    def invoke(_settings, tool, arguments, **_kwargs):
        plan = current_approved_unity_execution()
        assert plan is not None
        claim = plan.claim(tool, arguments, tmp_path)
        claim.complete()
        calls.append(tool)
        return dashboard_server.McpResult(exit_code=0, stdout="", stderr="", payload={"data": raw})

    def status(_arguments):
        assert current_approved_unity_execution() is None
        calls.append("connected_readback")
        return {"ok": True, "isPlayMode": True, "managers": [
            {"avatarPath": "Avatar", "managerPath": "GM", "moduleConnected": True}]}

    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint",
                        lambda *_args: {"ok": True, "id": "fixture-checkpoint", "projectRoot": str(tmp_path)})
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(dashboard_server, "bound_editor_readback", lambda _arguments: nullcontext({"verified": True}))
    monkeypatch.setattr(dashboard_server, "gesture_manager_status_sync", status)
    request = service.create_apply_request({"target_tool": name,
                                            "arguments": {"projectRoot": str(tmp_path), "avatarPath": "Avatar"}})
    service.approve(request["approval"]["id"])
    result = service.apply_approved({"approval_id": request["approval"]["id"]})
    assert calls == ["vrc_gesture_manager_enter_play_mode", "connected_readback"]
    assert result["status"] == "applied"
    assert result["outcome"]["status"] == "ok"
    assert result["result"]["moduleConnected"] is True
    assert gateway.checkpoint_recovery._active_apply_recoveries() == []
