from agent_task_loop import apply_declared_verification
from agent_tool_result_contract import normalize_agent_tool_result, completion_gate_plan
from agent_gateway import visual_audit_correction_candidate


def test_read_validation_failure_is_not_a_human_request():
    result = normalize_agent_tool_result({"ok": True, "readbackVerified": False}, fallback_summary="read", write=False)
    assert result["status"] == "failed"
    assert result["verification"]["state"] == "failed"
    assert result["error"]["code"] == "verification_failed"


def test_human_request_and_write_uncertainty_remain_blocking():
    for value, write in [
        ({"ok": True, "readbackVerified": False}, True),
        ({"status": "needs_user_action", "readbackVerified": False}, False),
        ({"status": "pending_approval", "readbackVerified": False}, False),
    ]:
        assert normalize_agent_tool_result(value, fallback_summary="pending", write=write)["status"] == "needs_user_action"


def test_failed_shell_check_does_not_authorize_repeat_side_effects():
    result = apply_declared_verification("shell", {"exitCode": 1}, {"status": "ok"})
    assert result["status"] == "needs_user_action"
    assert (result.get("error") or {}).get("retryable") is not True


def test_visual_check_stays_failed_and_requires_real_capture_proof():
    raw = {"visualVerified": False, "coverageComplete": True, "captureEvidenceVerified": True,
           "results": [{"angle": "left", "status": "clipping"}]}
    outcome = apply_declared_verification("vrcforge_vision_audit_multi", raw, {"status": "ok"})
    assert outcome["status"] == "failed"
    assert outcome["verification"]["state"] == "failed"
    payload = {"result": raw, "outcome": outcome}
    assert visual_audit_correction_candidate("vrcforge_vision_audit_multi", payload, previous_audits=[raw])
    assert not visual_audit_correction_candidate("vrcforge_vision_audit_multi", payload, previous_audits=[raw, raw])
    raw["captureEvidenceVerified"] = False
    assert not visual_audit_correction_candidate("vrcforge_vision_audit_multi", payload, previous_audits=[])


def test_terminal_verification_failure_keeps_structured_reason():
    outcome = {"status": "failed", "error": {"code": "verification_failed"}, "summary": "still clipping"}
    gate = completion_gate_plan({"nextStep": "done"}, outcome)
    assert gate["nextStep"] == "tool_failed"
    assert gate["completionGate"] == {"status": "failed", "reason": "verification_failed"}
