import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_approval_transactions import _domain_write_receipt

TARGET = {"rendererPath": "Avatar/bra", "blendshapeName": "Bra_ribbon_x"}


def _raw(*, current_ok=True, core_success=True, payload_error=None, exit_code=0, verified=True, saved=True, pending=False, mismatch=False, duplicate=False):
    applied = {**TARGET, "targetWeight": 100.0, "currentWeight": 100.0}
    applied_adjustments = [{**TARGET, "targetWeight": 100.0}]
    verified_change = {**TARGET, "actualWeight": 100.0, "verified": verified}
    if mismatch:
        verified_change["blendshapeName"] = "Other"
    verified_changes = [verified_change, dict(verified_change)] if duplicate else [verified_change]
    if payload_error is None:
        payload_error = not core_success
    return {
        "ok": current_ok, "executionMode": "live-unity",
        "result": {"exitCode": exit_code, "payload": {"isError": payload_error, "structuredContent": {
            "success": core_success, "isError": not core_success,
            "data": {"saved": saved, "pending": pending, "appliedCount": len(verified_changes), "applied": [applied]}
        }}},
        "appliedAdjustments": applied_adjustments,
        "verifiedChanges": verified_changes,
        "undoDepth": 1,
    }


def test_blendshape_nested_success_becomes_verified_receipt():
    receipt = _domain_write_receipt(_raw())
    assert receipt["verified"] is True
    assert receipt["mutationApplied"] is True
    assert receipt["commitState"] == "committed"
    assert receipt["readback"]["verifiedChanges"][0]["actualWeight"] == 100.0


def test_blendshape_failures_stay_unverified():
    for kwargs in (
        {"current_ok": False}, {"core_success": False}, {"payload_error": True},
        {"exit_code": 1}, {"verified": False},
        {"saved": False}, {"pending": True}, {"mismatch": True}, {"duplicate": True},
    ):
        assert "verified" not in _domain_write_receipt(_raw(**kwargs))


def test_generic_success_without_blendshape_evidence_stays_unverified():
    raw = _raw()
    raw.pop("verifiedChanges")
    assert "verified" not in _domain_write_receipt(raw)


def test_blendshape_actual_weight_must_match_requested_target():
    raw = _raw()
    raw["result"]["payload"]["structuredContent"]["data"]["applied"][0]["currentWeight"] = 0.0
    raw["verifiedChanges"][0]["actualWeight"] = 0.0
    assert "verified" not in _domain_write_receipt(raw)

