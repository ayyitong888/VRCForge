"""Operation-owned inputs outlive asynchronous polling and process memory."""
import copy
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server as server
from tests.test_prepared_outfit_import_writes import _project, _prepare, _approved, _pending_import
from tests.test_unity_import_recovery import receipt


@pytest.mark.parametrize("failure", ["poll", "start", "refresh", "readback"])
def test_nested_source_is_retained_until_verified_completion(tmp_path, monkeypatch, failure):
    project = _project(tmp_path)
    source = tmp_path / "bundle.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("source.unitypackage", b"package")
    prepared, _ = _prepare(tmp_path, {"plan": {"readyToApply": True, "kind": "unitypackage_import",
        "projectPath": str(project), "source": {"path": str(source), "importQueue": [
            {"sourceType": "zip", "path": "source.unitypackage", "role": "target", "order": 1}]}}},
        {"projectPath": str(project), "packagePath": str(source)})
    from prepared_unity_execution import build_prepared_execution_plan
    from tests.test_prepared_outfit_import_writes import _completed_import
    call = build_prepared_execution_plan(prepared)[0][1]
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: SimpleNamespace(unity_mcp_timeout_seconds=30))
    def invoke(_settings, tool, arguments, **kw):
        if tool == "vrc_refresh_asset_database":
            return server.McpResult(0, "", "", {"ok": failure != "refresh", "error": "refresh failed"})
        if "expectedSha256" in arguments:
            if failure == "start":
                raise RuntimeError("transport disconnected after dispatch")
            return _pending_import(arguments)
        if failure == "poll":
            raise RuntimeError("poll transport disconnected")
        return _completed_import(call)
    monkeypatch.setattr(server, "invoke_unity_mcp", invoke)
    verify = (lambda *_: {"verified": False}) if failure == "readback" else None
    result = _approved(verify_imported_asset_content=verify).execute(prepared)
    assert result["ok"] is False
    assert result["checkpointRecoveryRequired"] is True
    assert Path(call["unityPackagePath"]).read_bytes() == b"package"


def _install_journal(receipt, *, nested=False):
    from unity_import_recovery import make_import_operation_journal, load_import_operation_journal, import_operation_journal_path
    from prepared_unity_execution import install_prepared_calls
    from tests.test_unity_import_recovery import verify
    from prepared_file_imports import capture_regular_file
    identity, digest = capture_regular_file(receipt.package, label="fixture")
    identity["sha256"] = digest
    materializations, owned = [], []
    if nested:
        from prepared_archive_imports import prepare_zip_member_materialization, execute_zip_member_materialization
        original = receipt.root / "bundle.zip"
        with zipfile.ZipFile(original, "w") as archive:
            archive.writestr("source.unitypackage", receipt.package.read_bytes())
        temp = receipt.root / "materializations"
        temp.mkdir()
        facts = prepare_zip_member_materialization(source=original, temp_parent=temp,
            selected_members=[{"path": "source.unitypackage", "targetName": "prepared-owned.unitypackage"}])
        materializations = [facts]
        owned = [execute_zip_member_materialization(facts)]
        receipt.package = Path(owned[0]["ownedFiles"][0]["path"])
        receipt.status["unityPackagePath"] = str(receipt.package)
        identity = {"path": str(receipt.package), "sha256": digest, "size": receipt.package.stat().st_size}
    calls = [("vrc_import_unitypackage", {key: receipt.status[key] for key in
        ("projectPath", "unityPackagePath", "expectedSha256", "expectedSize", "expectedAssetPaths", "interactive")}),
        ("vrc_refresh_asset_database", {"projectPath": str(receipt.project), "resolvePackages": False, "packageResolveTimeoutSeconds": 120})]
    from approved_unity_execution import freeze_approved_unity_execution_plan
    receipt.approval["approvedUnityExecutionPlan"] = freeze_approved_unity_execution_plan(calls)
    evidence = {"kind": "unitypackage_import", "queue": [{"role": "target", "identity": identity,
        "materializationIndex": 0 if nested else None}], "materializations": materializations, "targetIndex": 0,
        "expectedAssetPaths": receipt.status["expectedAssetPaths"], "projectIdentity": {"projectPath": str(receipt.project)}}
    arguments = install_prepared_calls({}, calls, {"branch": "unitypackage", "payloadHash": digest, "outfitEvidence": evidence})
    tool = "vrcforge_import_outfit_package"
    receipt.approval["targetTool"] = receipt.recovery["targetTool"] = tool
    receipt.approval["taskContext"]["tool"] = receipt.event["steps"][0]["tool"] = tool
    journal = make_import_operation_journal(receipt.recovery, receipt.approval, arguments)
    assert journal is not None
    journal.update({"activeImportIndex": 0, "importDispatched": True,
        "materializations": owned, "jobReceipt": copy.deepcopy(receipt.status),
        "handlerResult": copy.deepcopy(receipt.event["steps"][0]["result"])})
    journal["handlerResult"]["ok"] = False
    path = import_operation_journal_path("approval", receipt.root / "approvals.jsonl")
    path.parent.mkdir()
    path.write_text(json.dumps(journal), encoding="utf8")
    receipt.recovery["importOperation"] = {"schema": journal["schema"], "journalFile": path.name,
        "sealSha256": journal["preparedSeal"]["sealSha256"]}
    assert load_import_operation_journal(receipt.recovery, receipt.root / "approvals.jsonl") == journal
    return journal, path


def test_journal_semantics_include_attachment_wrapper_and_survive_json_restart(receipt):
    from tests.test_unity_import_recovery import verify
    _install_journal(receipt)
    assert verify(receipt)["verified"] is True


@pytest.mark.parametrize("drift", [None, "archive", "temporary", "owner", "seal", "plan", "job", "missing_journal", "unresolved"])
def test_nested_recovery_revalidates_original_seal_and_releases_only_verified_owned_input(receipt, drift):
    from tests.test_unity_import_recovery import verify
    from unity_import_recovery import release_verified_import_sources
    journal, path = _install_journal(receipt, nested=True)
    original = receipt.root / "bundle.zip"
    if drift == "archive":
        original.write_bytes(b"changed container; unchanged materialized member is insufficient")
    elif drift == "temporary":
        content = receipt.package.read_bytes()
        receipt.package.unlink()
        receipt.package.write_bytes(content)
    elif drift == "owner": journal["operationId"] = "other"
    elif drift == "seal": journal["preparedSeal"]["evidence"]["payloadHash"] = "0" * 64
    elif drift == "plan": journal["approvedUnityExecutionPlan"]["planDigest"] = "0" * 64
    elif drift == "job": journal["jobReceipt"]["jobId"] = "b" * 32
    if drift == "missing_journal": path.unlink()
    else: path.write_text(json.dumps(journal), encoding="utf8")
    if drift and drift != "unresolved":
        with pytest.raises((ValueError, OSError, RuntimeError)):
            verify(receipt)
        assert receipt.package.exists()
        return
    proof = verify(receipt)
    if drift == "unresolved":
        with pytest.raises(ValueError, match="not finished"):
            release_verified_import_sources(receipt.recovery, receipt.root / "approvals.jsonl")
        assert receipt.package.exists()
        return
    receipt.recovery.update(status="resolved", resolution="verified_partial_import", resultSummary=proof)
    cleanup = release_verified_import_sources(receipt.recovery, receipt.root / "approvals.jsonl")
    assert cleanup == {"performed": True, "required": False, "errors": []}
    assert not receipt.package.exists()
    assert original.exists()


def test_attachment_recovery_survives_new_gateway_without_live_approval_memory(receipt, monkeypatch):
    import agent_approval_transactions as transactions
    from agent_gateway import AgentGateway
    from tests.test_unity_import_recovery import verify
    _install_journal(receipt, nested=True)
    verify(receipt)
    first = AgentGateway(receipt.root / "config.json", receipt.root)
    first.checkpoint_recovery._append_apply_recovery_entry(receipt.recovery)
    del first
    cold = AgentGateway(receipt.root / "config.json", receipt.root)
    assert not cold.approval_transactions._ports.state.approvals
    monkeypatch.setattr(transactions, "validate_runtime_execution_target", lambda *a, **kw: {})
    result = cold.approval_transactions.reconcile_unitypackage_import_recovery(
        {"projectPath": str(receipt.project), "jobId": receipt.status["jobId"]}, receipt.status)
    assert len(result) == 1
    assert result[0]["resolution"] == "verified_partial_import"
    assert result[0]["resultSummary"]["workflowComplete"] is False
    assert result[0]["sourceCleanup"]["performed"] is True
    assert not receipt.package.exists()
    assert (receipt.root / "bundle.zip").exists()
    assert not cold.checkpoint_recovery._active_apply_recoveries()


@pytest.mark.parametrize("disk_failure", [None, "initial", "owned_receipt"])
def test_host_journals_full_seal_before_dispatch_and_fails_closed_on_storage_error(tmp_path, monkeypatch, disk_failure):
    from contextlib import nullcontext
    import agent_approval_transactions as transactions
    from agent_gateway import AgentGateway
    from approved_unity_execution import freeze_approved_unity_execution_plan
    from prepared_unity_execution import build_prepared_execution_plan, PREPARED_UNITY_EXECUTION_ARGUMENT_KEY
    from unity_import_recovery import load_import_operation_journal, import_operation_journal_path
    from prepared_outfit_import_workflow_service import _journal_import_progress
    project = _project(tmp_path)
    source = tmp_path / "bundle.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("source.unitypackage", b"package")
    arguments, _ = _prepare(tmp_path, {"plan": {"readyToApply": True, "kind": "unitypackage_import",
        "projectPath": str(project), "source": {"path": str(source), "importQueue": [
            {"sourceType": "zip", "path": "source.unitypackage", "role": "target", "order": 1}]}}},
        {"projectPath": str(project), "packagePath": str(source)})
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.approval_transactions
    frozen = freeze_approved_unity_execution_plan(build_prepared_execution_plan(arguments))
    recovery = {"id": "recovery", "approvalId": "approval", "operationId": "approval", "targetTool": "facade",
                "projectRoot": str(project), "status": "applying"}
    core_calls = []
    def invoke(_settings, tool, params, **kw):
        core_calls.append(tool)
        journal = load_import_operation_journal(recovery, service._ports.audit_log_path())
        assert journal["preparedSeal"] == arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]
        assert len(journal["materializations"][0]["ownedFiles"]) == 1
        return _pending_import(params)
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(server, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(transactions, "bind_approved_unity_execution", lambda _: nullcontext())
    burned = []
    monkeypatch.setattr(transactions, "create_approved_unity_execution_plan", lambda *a:
        SimpleNamespace(consumed=False, burn=lambda: burned.append(True)))
    monkeypatch.setattr(type(service), "_scan_mutation", lambda *a: nullcontext({}))
    actual_writer = transactions.atomic_write_json
    def fail(path, value):
        if disk_failure == "initial" or disk_failure == "owned_receipt" and value.get("materializations"):
            raise OSError("journal disk failed")
        return actual_writer(path, value)
    monkeypatch.setattr(transactions, "atomic_write_json", fail)
    handler = SimpleNamespace(requires_approved_execution_context=True, pre_write_checkpoint_required=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
        verification_finalize_handler=None, handler=lambda args: _approved(timeout_seconds=0).execute(args))
    def call():
        return service._call_write_handler(handler, "facade", "approval", {"id": "checkpoint", "ok": True,
            "projectRoot": str(project)}, arguments, "digest", frozen, {}, {}, recovery=recovery)
    if disk_failure:
        with pytest.raises(OSError, match="journal disk"):
            call()
        assert not core_calls
        assert burned == [True]
    else:
        assert call()["checkpointRecoveryRequired"] is True
        journal = load_import_operation_journal(recovery, service._ports.audit_log_path())
        assert journal["handlerResult"]["ok"] is False
        assert journal["jobReceipt"]["jobId"] == "a" * 32
    # A subsequent caller cannot inherit the owner's callback, including when
    # persistence failed. This update must not mutate any journal.
    path = import_operation_journal_path("approval", service._ports.audit_log_path())
    before = path.read_bytes() if path.exists() else None
    _journal_import_progress(untrusted=True)
    assert (path.read_bytes() if path.exists() else None) == before
