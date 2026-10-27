"""Replay an applied write interrupted before its native response is saved."""
import json

import pytest

from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
from tests.test_native_runtime_gateway import call, run


class ProcessStopped(BaseException):
    pass


def test_applied_write_restores_result_after_restart_without_reexecution(tmp_path, monkeypatch):
    receipt = call("wrist-cuffs", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "restore"})
    receipt["tool_calls"] += call("later-proposal", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "later"})["tool_calls"]
    gateway, _, invoked = _write_gateway_for_mode(tmp_path, [receipt], handler_result={
        "ok": True, "before": False, "after": True, "saved": False, "pending": True,
        "saveAssets": False, "objectPath": "Avatar/WristCuffs",
        "apiKey": "private-fixture-secret",
    })
    _set_mode(gateway, "roslyn_full_auto")
    original = gateway.approval_transactions._runtime_run_append

    def stop_after_applied(entry):
        result = original(entry)
        if entry.get("event") == "approval_applied":
            raise ProcessStopped()
        return result

    monkeypatch.setattr(gateway.approval_transactions, "_runtime_run_append", stop_after_applied)
    with pytest.raises(ProcessStopped):
        run(gateway, tmp_path, message="Restore the preview")
    assert len(invoked) == 1
    assert "private-fixture-secret" not in gateway.audit_log_path.read_text(encoding="utf-8")

    for attempt in range(2):
        resumed, model, executed = _write_gateway_for_mode(tmp_path, [{"role": "assistant", "content": "Restoration recorded."}])
        run(resumed, tmp_path, clientTurnId=f"resume-{attempt}", message="Report the previous result")
        messages = model.requests[0]["messages"]
        replies = [m for m in messages if m.get("tool_call_id") == "wrist-cuffs"]
        assert len(replies) == 1
        restored = json.loads(replies[0]["content"])
        assert restored["observations"][0]["outcome"]["status"] == "ok"
        assert '"after":true' in restored["observations"][0]["observation"]
        assert '"saved":false' in restored["observations"][0]["observation"]
        later = next(m for m in messages if m.get("tool_call_id") == "later-proposal")
        assert json.loads(later["content"])["status"] == "not_executed"
        assert not executed


def test_pending_approval_restart_preserves_native_result_link_without_planner(tmp_path, monkeypatch):
    gateway, _, _ = _write_gateway_for_mode(tmp_path, [
        call("manual-write", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "restore"}),
    ])
    _set_mode(gateway, "approval")
    result = run(gateway, tmp_path, message="Restore the preview")
    approval_id = result["approval_id"]
    restarted, _, invoked = _write_gateway_for_mode(tmp_path, [])
    restarted._runtime_planner = None
    restarted.approval_transactions.approve(approval_id)
    original = restarted.approval_transactions._runtime_run_append

    def stop_after_applied(entry):
        original(entry)
        if entry.get("event") == "approval_applied":
            raise ProcessStopped()

    monkeypatch.setattr(restarted.approval_transactions, "_runtime_run_append", stop_after_applied)
    with pytest.raises(ProcessStopped):
        restarted.approval_transactions.apply_approved({"approvalId": approval_id})
    assert len(invoked) == 1
    resumed, model, executed = _write_gateway_for_mode(tmp_path, [{"role": "assistant", "content": "Recorded."}])
    run(resumed, tmp_path, clientTurnId="manual-resume", message="Report the previous result")
    reply = next(m for m in model.requests[0]["messages"] if m.get("tool_call_id") == "manual-write")
    assert json.loads(reply["content"])["observations"][0]["outcome"]["status"] == "ok"
    assert not executed
