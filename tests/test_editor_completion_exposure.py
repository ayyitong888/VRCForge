"""Executable proposed-contract regression. No product edits or Unity calls."""
from pathlib import Path
import sys

import pytest

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
        assert kwargs["refresh_scene_snapshot"] is False
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


def test_play_mode_finalize_refreshes_project_target_after_editor_instance_changes(monkeypatch):
    import dashboard_server as ds

    original = {
        "scope": "avatar",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "old-core"},
        "scene": {"assetPath": "Assets/Main.unity"},
    }
    fresh = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "new-core"},
    }
    observed = []

    monkeypatch.setattr(ds, "list_execution_targets_sync", lambda params: {"ok": True, "targets": [fresh]})
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda *args: None)
    monkeypatch.setattr(ds, "capture_scene_view_status_direct", lambda *args, **kwargs: {"ok": True, "isPlayMode": True})

    from contextlib import contextmanager

    @contextmanager
    def readback(arguments):
        observed.append(arguments["executionTarget"])
        yield {"verified": True}

    monkeypatch.setattr(ds, "bound_editor_readback", readback)
    receipt = ds.set_play_mode_finalize(
        {"projectPath": "D:/Fixture", "executionTarget": original, "isPlaying": True}, {},
        {"ok": True, "mutationStarted": True},
    )

    assert receipt["isPlayMode"] is True
    assert observed == [fresh]


def test_play_mode_finalize_refreshes_project_scope_target_on_exit(monkeypatch):
    import dashboard_server as ds

    original = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "old-core"},
    }
    fresh = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "new-core"},
    }
    observed = []

    monkeypatch.setattr(ds, "list_execution_targets_sync", lambda params: {
        "ok": True, "targets": [fresh], "candidateCount": 1,
        "totalCandidateCount": 1, "hasMore": False, "complete": True,
    })
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda *args: None)
    monkeypatch.setattr(ds, "capture_scene_view_status_direct", lambda *args, **kwargs: {"ok": True, "isPlayMode": False})

    from contextlib import contextmanager

    @contextmanager
    def readback(arguments):
        observed.append(arguments["executionTarget"])
        yield {"verified": True}

    monkeypatch.setattr(ds, "bound_editor_readback", readback)
    receipt = ds.set_play_mode_finalize(
        {"projectPath": "D:/Fixture", "executionTarget": original, "isPlaying": False}, {},
        {"ok": True, "mutationStarted": True},
    )

    assert receipt["isPlayMode"] is False
    assert observed == [fresh]


@pytest.mark.parametrize("metadata", [
    {"hasMore": True},
    {"complete": False},
    {"totalCandidateCount": 2},
    {"ambiguous": True},
])
def test_play_mode_target_refresh_rejects_incomplete_or_ambiguous_discovery(monkeypatch, metadata):
    import editor_state_completion as domain

    original = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "old-core"},
    }
    fresh = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "new-core"},
    }
    result = domain.fresh_play_mode_readback_arguments(
        {"projectPath": "D:/Fixture", "executionTarget": original},
        lambda _params: {"ok": True, "targets": [fresh], **metadata},
    )
    assert result["executionTarget"] is original


@pytest.mark.parametrize("field", ["unityPid", "processStartTime", "projectRoot"])
def test_play_mode_target_refresh_rejects_editor_identity_mismatch(field):
    import editor_state_completion as domain

    original = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 12, "processStartTime": "start-1", "coreInstanceId": "old-core"},
    }
    fresh = {
        "scope": "project",
        "project": {"root": "D:/Fixture", "projectId": "project-1"},
        "editor": {"unityPid": 99, "processStartTime": "start-1", "coreInstanceId": "new-core"},
    }
    if field == "processStartTime":
        fresh["editor"][field] = "start-2"
    elif field == "projectRoot":
        fresh["project"]["root"] = "D:/Other"
    result = domain.fresh_play_mode_readback_arguments(
        {"projectPath": "D:/Fixture", "executionTarget": original},
        lambda _params: {"ok": True, "targets": [fresh]},
    )
    assert result["executionTarget"] is original


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
