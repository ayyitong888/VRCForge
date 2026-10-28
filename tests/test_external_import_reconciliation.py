"""External submitted imports reconcile only from host journals and exact terminal readback."""
import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import agent_approval_transactions as transactions
from agent_gateway import AgentGateway
from prepared_outfit_import_workflow_service import _journal_import_progress
from prepared_unity_execution import install_prepared_calls, build_prepared_execution_plan
from unity_import_recovery import import_operation_journal_path
from tests.test_unity_import_recovery import receipt


def execute_interrupted_import(receipt, monkeypatch):
    for marker in ("Packages", "ProjectSettings"):
        (receipt.project / marker).mkdir()
    call = {key: receipt.status[key] for key in ("projectPath", "unityPackagePath", "expectedSha256", "expectedSize", "expectedAssetPaths", "interactive")}
    calls = [("vrc_import_unitypackage", call), ("vrc_refresh_asset_database", {"projectPath":str(receipt.project),"resolvePackages":False,"packageResolveTimeoutSeconds":120})]
    from prepared_file_imports import capture_regular_file
    identity, digest = capture_regular_file(receipt.package, label="Prepared package")
    identity["sha256"] = digest
    args = install_prepared_calls({"projectPath": str(receipt.project)}, calls,
        {"kind": "unitypackage_import", "queue": [{"role": "target", "identity": identity, "materializationIndex": None}],
         "materializations": [], "targetIndex": 0, "expectedAssetPaths": receipt.status["expectedAssetPaths"],
         "projectIdentity": {"projectPath": str(receipt.project)}})
    gateway = AgentGateway(receipt.root / "config.json", receipt.root / "audit")
    config = gateway.ensure_config(); config.enabled = True; config.allow_write_requests = True; gateway.save_config(config)
    service = gateway.approval_transactions
    executions = []
    initial = {**receipt.status, "pending": True, "status": "pending", "commitState": "pending", "startedForThisJob": True}
    failed = {"ok": False, "jobId": receipt.status["jobId"], "status": "transport_error",
              "failureLayer": "unity_core_job_polling", "unityImports": [],
              "mutationStarted": True, "committed": True, "commitState": "unknown",
              "checkpointRecoveryRequired": True, "error": "Read-only job polling could not observe the submitted import.",
              "unityJobState": {"jobId": receipt.status["jobId"], "startedForThisJob": True}}
    def handler(arguments):
        executions.append(copy.deepcopy(arguments))
        _journal_import_progress(activeImportIndex=0, importDispatched=True)
        _journal_import_progress(jobReceipt=initial)
        monkeypatch.setattr(transactions, "utc_now_iso", lambda: "2026-10-23T08:18:10+00:00")
        return copy.deepcopy(failed)
    service.register_write_handler("vrcforge_import_outfit_package", "Import one sealed package", "medium", handler,
        requires_approved_execution_context=True, approved_execution_plan_builder=build_prepared_execution_plan,
        checkpoint_prepare_handler=lambda root,args: {"ok": True})
    monkeypatch.setattr(transactions, "utc_now_iso", lambda: "2026-10-23T08:18:00+00:00")
    monkeypatch.setattr(transactions, "bind_approved_unity_execution", lambda _: nullcontext())
    monkeypatch.setattr(transactions, "create_approved_unity_execution_plan", lambda *a: SimpleNamespace(consumed=False, burn=lambda: None))
    monkeypatch.setattr(type(service), "_scan_mutation", lambda *a: nullcontext({}))
    prepared = service.prepare_external_mcp_write("vrcforge_import_outfit_package", args)
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result["ok"] is False and len(executions) == 1
    recovery = service._ports.checkpoint.active_apply_recoveries()[0]
    assert recovery["status"] == "needs_recovery"
    assert recovery["importOperation"]["schema"] == "vrcforge.import-operation.v1"
    journal_path = import_operation_journal_path(recovery["operationId"], service._ports.audit_log_path())
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["jobReceipt"]["jobId"] == receipt.status["jobId"]
    assert journal["handlerResult"] == failed
    return gateway, result, recovery, journal_path, executions


@pytest.mark.parametrize("drift", [None, "job", "project", "source", "asset", "seal", "plan", "witness_job", "pending", "unknown", "audit", "restore"])
def test_external_import_terminal_readback_reconciles_exact_operation_after_restart(receipt, monkeypatch, drift):
    gateway, result, recovery, journal_path, executions = execute_interrupted_import(receipt, monkeypatch)
    status = copy.deepcopy(receipt.status)
    if drift == "job": status["jobId"] = "b" * 32
    elif drift == "project": status["projectPath"] = str(receipt.root / "Other")
    elif drift == "source": receipt.package.write_bytes(b"changed archive")
    elif drift == "asset": (receipt.project / "Assets/Model.prefab").write_bytes(b"changed asset")
    elif drift in {"seal", "plan", "witness_job"}:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if drift == "seal": journal["preparedSeal"]["sealSha256"] = "b" * 64
        elif drift == "plan": journal["approvedUnityExecutionPlan"]["planDigest"] = "b" * 64
        else: journal["jobReceipt"]["jobId"] = "b" * 32
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
    elif drift == "pending": status.update(status="pending", pending=True)
    elif drift == "unknown": status["commitState"] = "unknown"
    elif drift == "audit":
        audit = gateway.approval_transactions._ports.audit_log_path()
        audit.write_text("\n".join(line for line in audit.read_text(encoding="utf-8").splitlines() if json.loads(line).get("event") != "external_mcp_write_failed")+"\n",encoding="utf-8")
    elif drift == "restore": gateway.checkpoint_recovery._append_apply_recovery_entry({**recovery,"restoreAttemptCount":1})
    cold = AgentGateway(gateway.config_path, receipt.root / "audit")
    monkeypatch.setattr(transactions, "validate_runtime_execution_target", lambda *a, **kw: {})
    resolved = cold.approval_transactions.reconcile_unitypackage_import_recovery(
        {"projectPath": str(receipt.project), "jobId": receipt.status["jobId"]}, status)
    assert len(executions) == 1
    if drift is None:
        assert len(resolved) == 1
        assert resolved[0]["operationId"] == result["operationId"]
        assert resolved[0]["resultSummary"]["workflowComplete"] is False
        assert resolved[0]["resultSummary"]["remainingPostImportSteps"]
        assert not cold.checkpoint_recovery._active_apply_recoveries()
    else:
        assert resolved == []
        assert cold.checkpoint_recovery._active_apply_recoveries()[0]["id"] == recovery["id"]


def test_accepted_job_id_normalization_also_reaches_recovery_reconciliation(receipt, monkeypatch):
    gateway, _, recovery, _, _ = execute_interrupted_import(receipt, monkeypatch)
    monkeypatch.setattr(transactions, "validate_runtime_execution_target", lambda *a, **kw: {})
    resolved = gateway.approval_transactions.reconcile_unitypackage_import_recovery(
        {"projectPath": str(receipt.project), "jobId": "  " + receipt.status["jobId"].upper() + "  "}, receipt.status)
    assert len(resolved) == 1 and resolved[0]["id"] == recovery["id"]
