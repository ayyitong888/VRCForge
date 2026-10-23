"""Exact host receipts resolve import uncertainty without claiming workflow success."""
from types import SimpleNamespace
import copy
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from approved_unity_execution import freeze_approved_unity_execution_plan


def test_status_query_reconciles_the_exact_host_import_receipt(monkeypatch):
    import dashboard_server as server

    calls = []
    status = {"ok": True, "pending": False, "status": "completed", "jobId": "a" * 32}
    service = SimpleNamespace(reconcile_unitypackage_import_recovery=lambda args, result: calls.append((args, result)) or [{"id": "recovery"}])
    monkeypatch.setattr(server, "AGENT_GATEWAY", SimpleNamespace(approval_transactions=service))
    monkeypatch.setattr(server, "load_dashboard_settings", lambda value: value)
    monkeypatch.setattr(server, "build_agent_connection_request", lambda value: value)
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda *args, **kw: status)
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda value: value)
    request = {"projectPath": "project", "jobId": "a" * 32}
    result = server.get_unitypackage_import_status_sync(request)
    assert calls == [(request, status)]
    assert result["reconciledRecoveries"] == [{"id": "recovery"}]


@pytest.fixture
def receipt(tmp_path):
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    package = tmp_path / "Original.unitypackage"
    guid = "1" * 32
    content = b"original prefab bytes"
    meta = f"fileFormatVersion: 2\nguid: {guid}\n".encode()
    with tarfile.open(package, "w:gz") as archive:
        for name, data in {f"{guid}/pathname": b"Assets/Model.prefab", f"{guid}/asset": content, f"{guid}/asset.meta": meta}.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    (project / "Assets/Model.prefab").write_bytes(content)
    (project / "Assets/Model.prefab.meta").write_bytes(meta)
    call = {"projectPath": str(project), "unityPackagePath": str(package), "expectedSha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            "expectedSize": package.stat().st_size, "expectedAssetPaths": ["Assets/Model.prefab"], "interactive": False}
    plan = freeze_approved_unity_execution_plan([("vrc_import_unitypackage", call),
        ("vrc_refresh_asset_database", {"projectPath": str(project), "resolvePackages": False, "packageResolveTimeoutSeconds": 120})])
    context = {"sessionId": "session", "turnId": "turn", "actionId": "action", "tool": "vrcforge_import_outfit_package"}
    approval = {"id": "approval", "status": "failed", "targetTool": context["tool"], "projectRoot": str(project),
                "taskContext": context, "approvedUnityExecutionPlan": plan, "approvedAt": "2026-10-23T08:18:00+00:00",
                "failedAt": "2026-10-23T08:18:10+00:00", "resultSummary": {"jobId": "a" * 32},
                "arguments": {"packagePath": "intentionally truncated..."}}
    recovery = {"id": "recovery", "status": "needs_recovery", "resolution": "write_failed_after_checkpoint", "approvalId": "approval", "operationId": "approval",
                "targetTool": context["tool"], "projectRoot": str(project)}
    status = {**call, "jobId": "a" * 32, "ok": True, "status": "completed", "pending": False, "committed": True,
              "commitState": "complete", "checkpointRecoveryRequired": False, "createdUtc": "2026-10-23T08:18:01+00:00",
              "completedUtc": "2026-10-23T08:18:11+00:00", "expectedAssets": [{"assetPath": "Assets/Model.prefab", "guid": guid, "assetType": "GameObject"}]}
    result = {"jobId": "a" * 32, "failureLayer": "unity_core_job_polling", "unityImports": [],
              "unityJobState": {"jobId": "a" * 32, "startedForThisJob": True}}
    event = {"event": "runtime_turn_completed", "sessionId": "session", "turnId": "turn", "steps": [
        {"actionId": "action", "tool": context["tool"], "status": "failed", "result": result}]}
    return SimpleNamespace(project=project, package=package, approval=approval, recovery=recovery, status=status, event=event, root=tmp_path)


def verify(fixture):
    from unity_import_recovery import verify_interrupted_package_import

    audit = fixture.root / "approvals.jsonl"
    runtime = fixture.root / "runtime-runs.jsonl"
    audit.write_text("\n".join(json.dumps({"event": event, "approval": fixture.approval})
                               for event in ("approval_approved", "approval_applying", "approval_failed")), encoding="utf8")
    runtime.write_text(json.dumps(fixture.event), encoding="utf8")
    return verify_interrupted_package_import(fixture.recovery, fixture.status, audit_path=audit, runtime_path=runtime)


def test_exact_receipt_and_persisted_content_prove_only_partial_import(receipt):
    proof = verify(receipt)
    assert proof["verified"] is True
    assert proof["workflowComplete"] is False
    assert proof["originalApprovalStatus"] == "failed"
    assert proof["sourceCleanupPerformed"] is False
    assert receipt.approval["status"] == "failed"
    assert receipt.package.exists()


@pytest.mark.parametrize("failure", ["project", "job", "operation", "session", "action", "tool", "duplicate", "pending", "commit", "plan_hash", "multi_import", "content", "guid", "source", "job_time", "restore_attempt", "missing_job_witness"])
def test_incomplete_or_conflicting_evidence_keeps_recovery_blocked(receipt, failure):
    if failure == "project": receipt.status["projectPath"] = str(receipt.root / "Other")
    elif failure == "job": receipt.event["steps"][0]["result"]["jobId"] = "b" * 32
    elif failure == "operation": receipt.recovery["operationId"] = "other"
    elif failure == "session": receipt.event["sessionId"] = "other"
    elif failure == "action": receipt.event["steps"][0]["actionId"] = "other"
    elif failure == "tool": receipt.event["steps"][0]["tool"] = "other"
    elif failure == "duplicate": receipt.event["steps"].append(copy.deepcopy(receipt.event["steps"][0]))
    elif failure == "pending": receipt.status["pending"] = True
    elif failure == "commit": receipt.status["commitState"] = "unknown"
    elif failure == "plan_hash": receipt.approval["approvedUnityExecutionPlan"]["planDigest"] = "b" * 64
    elif failure == "multi_import":
        calls = receipt.approval["approvedUnityExecutionPlan"]["calls"]
        receipt.approval["approvedUnityExecutionPlan"] = freeze_approved_unity_execution_plan([calls[0], *calls])
    elif failure == "content": (receipt.project / "Assets/Model.prefab").write_bytes(b"different")
    elif failure == "guid": (receipt.project / "Assets/Model.prefab.meta").write_text("guid: " + "2" * 32)
    elif failure == "source": receipt.package.write_bytes(b"different")
    elif failure == "job_time": receipt.status["createdUtc"] = "2026-10-23T08:17:00+00:00"
    elif failure == "restore_attempt": receipt.recovery["restoreAttemptCount"] = 1
    elif failure == "missing_job_witness": receipt.approval["resultSummary"].pop("jobId")
    with pytest.raises((ValueError, RuntimeError)):
        verify(receipt)
    assert receipt.recovery["status"] == "needs_recovery"


@pytest.mark.parametrize("gate", ["success", "project_locked", "in_flight", "identity_drift", "content_drift"])
def test_production_recovery_finish_keeps_original_approval_failed_and_enforces_gates(receipt, monkeypatch, gate):
    import agent_approval_transactions as transactions
    from agent_gateway import AgentGateway
    from execution_target import ExecutionTargetError

    verify(receipt)  # Populate only the minimal host audit and runtime receipt.
    gateway = AgentGateway(receipt.root / "config.json", receipt.root)
    service = gateway.approval_transactions
    service._ports.state.approvals["approval"] = copy.deepcopy(receipt.approval)
    gateway.checkpoint_recovery._append_apply_recovery_entry(receipt.recovery)
    monkeypatch.setattr(transactions, "validate_runtime_execution_target", lambda *args, **kw: {})
    if gate == "project_locked":
        key, acquired = service._try_acquire_project_write(receipt.project)
        assert acquired
    elif gate == "in_flight":
        service._ports.state.in_flight_apply_writes["other"] = {"projectRoot": str(receipt.project)}
    elif gate == "identity_drift":
        def reject(*args, **kw): raise ExecutionTargetError("identity_drift", "different Core")
        monkeypatch.setattr(transactions, "validate_runtime_execution_target", reject)
    elif gate == "content_drift":
        (receipt.project / "Assets/Model.prefab").write_bytes(b"different")
    try:
        resolved = service.reconcile_unitypackage_import_recovery(
            {"projectPath": str(receipt.project), "jobId": "a" * 32}, receipt.status)
    finally:
        if gate == "project_locked": service._release_project_write(key)
    assert service._ports.state.approvals["approval"]["status"] == "failed"
    assert receipt.package.exists()
    if gate == "success":
        assert resolved[0]["status"] == "resolved"
        assert resolved[0]["resolution"] == "verified_partial_import"
        assert resolved[0]["resultSummary"]["workflowComplete"] is False
        assert gateway.checkpoint_recovery._active_apply_recoveries() == []
    else:
        assert resolved == []
        assert gateway.checkpoint_recovery._active_apply_recoveries()[0]["status"] == "needs_recovery"


def test_future_polling_failure_persists_job_witness_in_recovery(tmp_path):
    from agent_gateway import AgentGateway
    from tests.test_agent_gateway_apply_lifecycle import approved_write, create_project

    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    result = approved_write(gateway, project, handler=lambda arguments: {
        "ok": False, "jobId": "a" * 32, "status": "transport_error",
        "failureLayer": "unity_core_job_polling", "mutationStarted": True,
        "committed": True, "commitState": "unknown", "checkpointRecoveryRequired": True,
        "error": "Import job polling transport failed.",
    })
    assert result["ok"] is False
    recovery = gateway.checkpoint_recovery._active_apply_recoveries()[0]
    assert recovery["resultSummary"]["jobId"] == "a" * 32
    assert recovery["operationId"] == result["approval"]["id"]
    assert recovery["status"] == "needs_recovery"
