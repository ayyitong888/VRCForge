"""Executable proposed-contract regression. No product edits or Unity calls."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from test_mcp_write_transaction_contract import _gateway
from editor_state_completion import verified_editor_receipt


def execute(tmp_path):
    gateway = _gateway(tmp_path)
    name = "vrcforge_contract_editor_transition"
    initial = {"ok": True, "transitionScheduled": True, "verificationRequired": True,
               "mutationStarted": True, "committed": False, "commitState": "pending"}
    final = verified_editor_receipt({"isPlayMode": False, "requested": False}, initial)
    gateway.approval_transactions.register_write_handler(
        name, "Editor transition", "high", lambda args: dict(initial),
        verification_finalize_handler=lambda args, baseline, result: dict(final),
        pre_write_checkpoint_required=False,
    )
    gateway.register_external_mcp_unity_tool(name, "avatar")
    proposal = gateway.call_external_mcp_tool(name, {})
    result = gateway.call_external_mcp_tool(name, {
        "confirmation": {**proposal["confirmation"], "decision": "approve"}
    })
    assert result["ok"] is True and result["readbackState"] == "verified"
    assert result["commitState"] == "runtime_state_verified"
    assert result["result"] == initial  # Preserve original scheduling facts.
    return gateway, result, final


def test_verified_editor_finalizer_facts_are_public(tmp_path):
    _, result, final = execute(tmp_path)
    assert result.get("completionVerification") == final
    assert final["readback"]["isPlayMode"] is False
    assert final["transitionScheduled"] is False
    assert final["verificationRequired"] is False


def test_operation_resource_retains_same_finalizer_facts(tmp_path):
    gateway, result, final = execute(tmp_path)
    gateway.publish_mcp_tool_result_resource("vrcforge_contract_editor_transition", {}, result, source_mode="external_agent")
    resource = gateway.read_mcp_resource(result["operationResource"])["structuredContent"]
    captured = resource["data"]["result"]
    assert captured.get("completionVerification") == final


def test_successful_bound_read_produces_identity_evidence(monkeypatch):
    import editor_state_completion as domain
    target = {"scope": "project", "editor": {"unityPid": 12}}
    calls = []
    def validate(*args, **kwargs):
        calls.append(1)
        return dict(target)
    monkeypatch.setattr(domain, "validate_runtime_execution_target", validate)
    monkeypatch.setattr(domain, "execution_target_digest", lambda value: "same-identity-digest")
    with domain.bound_editor_readback({"projectPath": "D:/Fixture", "executionTarget": target}) as evidence:
        pass
    assert len(calls) == 2  # Current code DOES perform both validations.
    assert evidence is not None, "Successful pre/post identity checks are not retained for the public receipt"
    assert evidence["verified"] is True
    assert evidence["beforeExecutionTargetDigest"] == evidence["afterExecutionTargetDigest"] == "same-identity-digest"


def test_real_exit_finalizer_carries_observed_state_and_validated_identity(monkeypatch):
    import dashboard_server as ds
    import editor_state_completion as domain
    target = {"scope": "project", "editor": {"unityPid": 12, "coreInstanceId": "core-12"}}
    calls = []
    def validate(*args, **kwargs):
        calls.append(1)
        return dict(target)
    monkeypatch.setattr(domain, "validate_runtime_execution_target", validate)
    monkeypatch.setattr(domain, "execution_target_digest", lambda value: "matching-identity")
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda *args: None)
    monkeypatch.setattr(ds, "capture_scene_view_status_direct", lambda *args, **kwargs: {"ok": True, "isPlayMode": False})
    receipt = ds.set_play_mode_finalize(
        {"projectPath": "D:/Fixture", "executionTarget": target, "isPlaying": False}, {},
        {"ok": True, "mutationStarted": True, "transitionScheduled": True, "verificationRequired": True, "committed": False},
    )
    assert len(calls) == 2
    assert receipt["readback"] == {"isPlayMode": False, "requested": False}
    assert receipt["identityVerification"]["verified"] is True
    assert receipt["identityVerification"]["editor"]["unityPid"] == 12
    assert receipt["identityVerification"]["beforeExecutionTargetDigest"] == receipt["identityVerification"]["afterExecutionTargetDigest"] == "matching-identity"
    assert receipt["transitionScheduled"] is False and receipt["verificationRequired"] is False


def test_unrelated_finalizer_schema_is_not_given_editor_completion_projection(tmp_path):
    gateway = _gateway(tmp_path)
    name = "vrcforge_contract_other_completion"
    gateway.approval_transactions.register_write_handler(
        name, "Other completion", "high", lambda args: {"ok": True},
        verification_finalize_handler=lambda *args: {"schema": "vrcforge.other.v1", "ok": True, "verified": True, "readback": {}, "committed": True},
        pre_write_checkpoint_required=False,
    )
    gateway.register_external_mcp_unity_tool(name, "avatar")
    proposal = gateway.call_external_mcp_tool(name, {})
    result = gateway.call_external_mcp_tool(name, {"confirmation": {**proposal["confirmation"], "decision": "approve"}})
    assert "completionVerification" not in result
