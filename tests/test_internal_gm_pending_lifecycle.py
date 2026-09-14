import json
from pathlib import Path

import pytest

from agent_approval_transactions import AgentApprovalTransactionService
from agent_gateway import AgentGateway


@pytest.mark.parametrize("already_connected", [False, True])
def test_internal_approved_gm_entry_remains_pending_until_connected(tmp_path, monkeypatch, already_connected):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.approval_transactions
    raw = json.loads((Path(__file__).parent / "fixtures" / "gesture_manager_pending_receipt.json").read_text())
    if already_connected:
        raw.update(isPlayMode=True, moduleConnected=True, enterPlayModePending=False,
                   commitState="runtime_connected", readbackVerified=True)
    finalized = []
    runtime_events = []
    monkeypatch.setattr(AgentApprovalTransactionService, "_runtime_run_append", lambda _self, event: runtime_events.append(event))
    service.register_write_handler(
        "vrcforge_gesture_manager_enter_play_mode", "Enter Gesture Manager", "low",
        lambda _arguments: dict(raw),
        verification_finalize_handler=lambda _arguments, _baseline, result: (finalized.append(True) or result),
    )
    # Exercise the real approval/handler/completion path without accessing Unity.
    monkeypatch.setattr(
        AgentApprovalTransactionService, "_create_pre_write_checkpoint",
        lambda *_args: {"ok": True, "id": "fixture-checkpoint", "projectRoot": str(tmp_path)},
    )
    arguments = {"projectRoot": str(tmp_path), "avatarPath": "Avatar",
                 "executionTarget": {"scope": "avatar", "namespace": "fixture-avatar"}}
    request = service.create_apply_request({
        "target_tool": "vrcforge_gesture_manager_enter_play_mode",
        "arguments": arguments,
    })
    approval_id = request["approval"]["id"]
    service.approve(approval_id)
    result = service.apply_approved({"approval_id": approval_id})

    active = gateway.checkpoint_recovery._active_apply_recoveries()
    if already_connected:
        assert finalized == [True]
        assert result["status"] == "applied"
        assert result["outcome"]["status"] == "ok"
        assert active == []
        return
    assert finalized == []
    assert result["status"] == "pending"
    assert result["outcome"]["status"] == "pending"
    assert result["outcome"]["success"] is False
    assert result["outcome"]["enterPlayModePending"] is True
    assert result["outcome"]["isPlayMode"] is False
    assert result["outcome"]["moduleConnected"] is False
    assert result["commitState"] == "enter_play_mode_requested"
    assert result["readbackState"] == "pending"
    assert result["approval"]["completionOutcome"]["status"] == "pending"
    assert runtime_events[-1]["status"] == "pending"
    assert runtime_events[-1]["completionStatus"] == "pending"
    assert len(active) == 1
    assert active[0]["status"] == "applying"
    assert active[0]["resolution"] == "write_pending"
    assert active[0]["blockingWrites"] is True
    connected = {"ok": True, "isPlayMode": True,
                 "managers": [{"avatarPath": "Avatar", "moduleConnected": True}]}
    assert service.reconcile_gesture_manager_pending_recovery(
        arguments, {**connected, "ok": False}) == []
    assert service.reconcile_gesture_manager_pending_recovery(
        {**arguments, "avatarPath": "Other"}, connected) == []
    resolved = service.reconcile_gesture_manager_pending_recovery(arguments, connected)
    assert len(resolved) == 1
    assert resolved[0]["resolution"] == "write_completed_readback"
    assert gateway.checkpoint_recovery._active_apply_recoveries() == []
