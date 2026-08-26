from __future__ import annotations

import hashlib
import secrets
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
import vrcforge_runtime_paths as runtime_paths
from prepared_archive_imports import (
    cleanup_owned_zip_materialization,
    execute_zip_member_materialization,
    prepare_zip_member_materialization,
)
from prepared_file_imports import (
    capture_directory,
    capture_regular_file,
    verify_directory,
    verify_regular_file,
)
from prepared_loose_outfit_import import (
    execute_loose_outfit_import,
    prepare_loose_outfit_import,
)
from prepared_outfit_import_workflow_service import (
    PreparedOutfitImportApprovedWritePorts,
    PreparedOutfitImportApprovedWriteService,
    PreparedOutfitImportPreparer,
    PreparedOutfitImportPreparerPorts,
    classify_prepared_outfit_import_risk,
    prepared_outfit_import_manual_confirmation_reason,
)
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, build_prepared_execution_plan


JOB_ID = "a" * 32


def _pending_import(arguments: dict, job_id: str = JOB_ID) -> dashboard_server.McpResult:
    return dashboard_server.McpResult(0, "", "", {
        "ok": True,
        "pending": True,
        "status": "pending",
        "jobId": job_id,
        "mutationStarted": True,
        "projectPath": arguments["projectPath"],
        "unityPackagePath": arguments["unityPackagePath"],
        "expectedSha256": arguments["expectedSha256"],
        "expectedSize": arguments["expectedSize"],
        "expectedAssetPaths": arguments["expectedAssetPaths"],
        "startedForThisJob": False,
        "restoredAfterDomainReload": True,
        "expectedAssetCount": len(arguments["expectedAssetPaths"]),
        "readbackFailurePath": (
            arguments["expectedAssetPaths"][0]
            if arguments["expectedAssetPaths"]
            else None
        ),
        "readbackFailureCode": "unitypackage_restored_readback_pending",
        "readbackFailureReason": "asset readback is still pending",
    })


def _completed_import(arguments: dict, job_id: str = JOB_ID) -> dashboard_server.McpResult:
    return dashboard_server.McpResult(0, "", "", {
        "ok": True,
        "pending": False,
        "status": "completed",
        "jobId": job_id,
        "mutationStarted": True,
        "committed": True,
        "commitState": "complete",
        "checkpointRecoveryRequired": False,
        "projectPath": arguments["projectPath"],
        "unityPackagePath": arguments["unityPackagePath"],
        "expectedSha256": arguments["expectedSha256"],
        "expectedSize": arguments["expectedSize"],
        "expectedAssetPaths": arguments["expectedAssetPaths"],
        "expectedAssets": [
            {"assetPath": path, "guid": "a" * 32, "assetType": "UnityEngine.GameObject"}
            for path in arguments["expectedAssetPaths"]
        ],
    })


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    return root


def _plan(project: Path, package: Path) -> dict:
    return {"plan": {"readyToApply": True, "kind": "unitypackage_import", "projectPath": str(project), "source": {"actualPackagePath": str(package)}, "expectedAssetPaths": ["Assets/Outfits/Dress.prefab"]}}


def _medium_risk_facts() -> dict:
    return {
        "schema": "vrcforge.outfit_import_risk_facts.v1",
        "pathnameEvidenceComplete": True,
        "unsafePathnameCount": 0,
        "codePayloadCount": 1,
        "automaticCodeExecutionPlanned": False,
        "projectSettingsPathCount": 0,
        "packagesPathCount": 0,
        "unsupportedProjectPathCount": 0,
        "targetAbsenceChecked": True,
        "existingTargetPathCount": 0,
        "deleteRequested": False,
        "importQueueCount": 1,
        "mediumEligible": True,
        "recommendedRiskLevel": "medium",
        "reasonCodes": [],
    }


def _preparer(tmp_path: Path, plan: dict) -> PreparedOutfitImportPreparer:
    return PreparedOutfitImportPreparer(
        PreparedOutfitImportPreparerPorts(
            plan_outfit_import=lambda _arguments: plan,
            plan_error_type=RuntimeError,
            map_plan_error=lambda exc: exc,
            resolve_project_root=dashboard_server._resolve_unity_project_root_for_import,
            capture_project_identity=dashboard_server._prepared_import_project_identity,
            capture_regular_file=lambda path, label: capture_regular_file(path, label=label),
            capture_directory=lambda path, label: capture_directory(path, label=label),
            prepare_loose_import=prepare_loose_outfit_import,
            prepare_zip_member=prepare_zip_member_materialization,
            normalize_archive_name=dashboard_server.normalize_archive_name,
            digest=dashboard_server.shader_evidence_sha256,
            ensure_dict=dashboard_server.ensure_dict_payload,
            nonce_hex=secrets.token_hex,
            temp_parent=tmp_path / "isolated-import-temp",
            allowed_loose_suffixes=frozenset(dashboard_server.OUTFIT_IMPORT_ALLOWED_SUFFIXES),
        )
    )


def _prepare(tmp_path: Path, plan: dict, arguments: dict) -> tuple[dict, object]:
    return _preparer(tmp_path, plan).prepare(arguments, None)


def test_preparer_reports_blocking_dependency_reason(tmp_path: Path) -> None:
    plan = {
        "plan": {
            "readyToApply": False,
            "dependencyPreflight": {
                "entries": [
                    {
                        "status": "missing",
                        "blockingBeforeImport": True,
                        "message": "Poiyomi is required by the selected prefab.",
                    }
                ]
            },
        }
    }

    with pytest.raises(RuntimeError, match="Poiyomi is required by the selected prefab"):
        _prepare(tmp_path, plan, {})


def _approved(
    *, timeout_seconds: float = 180.0, poll_seconds: float = 0.5
) -> PreparedOutfitImportApprovedWriteService:
    return PreparedOutfitImportApprovedWriteService(
        PreparedOutfitImportApprovedWritePorts(
            digest=dashboard_server.shader_evidence_sha256,
            verify_project_identity=dashboard_server._verify_prepared_import_project_identity,
            require_evidence=dashboard_server._require_prepared_import_evidence,
            execute_loose_import=execute_loose_outfit_import,
            execute_zip_member=execute_zip_member_materialization,
            cleanup_zip_member=cleanup_owned_zip_materialization,
            verify_regular_file=lambda identity, digest, label: verify_regular_file(
                identity, digest, label=label
            ),
            verify_directory=lambda identity, label: verify_directory(identity, label=label),
            load_settings=lambda arguments: dashboard_server.load_dashboard_settings(
                dashboard_server.build_agent_connection_request(arguments)
            ),
            start_import=lambda settings, arguments: dashboard_server.ensure_dict_payload(
                dashboard_server.extract_tool_result_payload(
                    dashboard_server.invoke_unity_mcp(
                        settings, "vrc_import_unitypackage", arguments
                    )
                ),
                "prepared unitypackage import",
            ),
            poll_import=lambda settings, job_id: dashboard_server.ensure_dict_payload(
                dashboard_server.extract_tool_result_payload(
                    dashboard_server.invoke_unity_mcp(
                        settings,
                        "vrc_import_unitypackage",
                        {"jobId": job_id},
                        execution_context={"lane": "app_unitypackage_import_poll"},
                    )
                ),
                "prepared unitypackage import job",
            ),
            refresh_assets=lambda settings, arguments: dashboard_server.ensure_dict_payload(
                dashboard_server.extract_tool_result_payload(
                    dashboard_server.invoke_unity_mcp(
                        settings, "vrc_refresh_asset_database", arguments
                    )
                ),
                "prepared outfit import refresh",
            ),
            monotonic=time.monotonic,
            sleep=time.sleep,
            timeout_seconds=lambda: timeout_seconds,
            poll_seconds=lambda: poll_seconds,
            log=dashboard_server.emit_log,
            map_error=lambda exc: dashboard_server.to_http_exception(exc),
            handled_errors=(RuntimeError, dashboard_server.UnityMcpError, ValueError),
        )
    )


def test_preparer_seals_direct_unitypackage_and_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    calls = build_prepared_execution_plan(prepared)
    assert calls[0] == (
        "vrc_import_unitypackage",
        {
            "projectPath": str(project.resolve()),
            "unityPackagePath": str(package.resolve()),
            "expectedSha256": hashlib.sha256(b"package").hexdigest(),
            "expectedSize": len(b"package"),
            "expectedAssetPaths": ["Assets/Outfits/Dress.prefab"],
            "interactive": False,
        },
    )
    assert calls[1][0] == "vrc_refresh_asset_database"


def test_prepared_outfit_risk_uses_frozen_effects_not_script_presence(tmp_path: Path) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    plan = _plan(project, package)
    plan["plan"]["riskFacts"] = _medium_risk_facts()

    prepared, _ = _prepare(
        tmp_path,
        plan,
        {"packagePath": str(package), "projectPath": str(project)},
    )

    assert classify_prepared_outfit_import_risk(prepared) == "medium"
    plan["plan"]["riskFacts"] = {**_medium_risk_facts(), "existingTargetPathCount": 1}
    high_prepared, _ = _prepare(
        tmp_path,
        plan,
        {"packagePath": str(package), "projectPath": str(project)},
    )
    assert classify_prepared_outfit_import_risk(high_prepared) == "high"


def test_prepared_outfit_confirmation_names_overwrite_risk_and_not_source_path(
    tmp_path: Path,
) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    plan = _plan(project, package)
    plan["plan"]["riskFacts"] = {
        **_medium_risk_facts(),
        "existingTargetPathCount": 3,
        "mediumEligible": False,
        "recommendedRiskLevel": "high",
        "reasonCodes": ["existing_asset_overwrite"],
    }
    prepared, preview = _prepare(
        tmp_path,
        plan,
        {"packagePath": str(package), "projectPath": str(project)},
    )

    reason = prepared_outfit_import_manual_confirmation_reason(prepared, preview)

    assert "overwrite 3 existing Unity asset paths" in reason
    assert "outside the selected project" not in reason
    generic_reason = dashboard_server.AGENT_GATEWAY.approval_transactions._write_auto_manual_approval_reason(  # noqa: SLF001
        "vrcforge_import_outfit_package",
        prepared,
        preview,
    )
    assert generic_reason == ""


def test_medium_import_fails_before_mutation_if_target_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    plan = _plan(project, package)
    plan["plan"]["riskFacts"] = _medium_risk_facts()
    prepared, _ = _prepare(
        tmp_path,
        plan,
        {"packagePath": str(package), "projectPath": str(project)},
    )
    target = project / "Assets" / "Outfits" / "Dress.prefab"
    target.parent.mkdir(parents=True)
    target.write_text("appeared after preparation", encoding="utf-8")
    invoked = []
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda *_args, **_kwargs: invoked.append(True),
    )

    result = _approved().execute(prepared)

    assert result["ok"] is False
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert "target now exists" in result["error"]
    assert invoked == []


def test_prepared_import_rejects_source_drift_before_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    package.write_bytes(b"changed")
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    result = _approved().execute(prepared)
    assert result["ok"] is False
    assert result["failureLayer"] == "prepared_source_verification"
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert result["checkpointRecoveryRequired"] is False
    assert "identity or content drifted" in result["error"]


def test_prepared_import_executes_exact_calls_and_verifies_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    calls: list[tuple[str, dict]] = []
    start_arguments: dict = {}

    def invoke(_settings, tool, arguments, **kwargs):
        calls.append((tool, arguments))
        if tool == "vrc_import_unitypackage" and "expectedSha256" in arguments:
            start_arguments.update(arguments)
            pending = _pending_import(arguments)
            pending.payload["projectPath"] = pending.payload["projectPath"].replace("\\", "/")
            return pending
        if tool == "vrc_import_unitypackage":
            assert kwargs["execution_context"] == {"lane": "app_unitypackage_import_poll"}
            completed = _completed_import(start_arguments)
            completed.payload["projectPath"] = completed.payload["projectPath"].replace("\\", "/")
            return completed
        return dashboard_server.McpResult(0, "", "", {"ok": True})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved().execute(prepared)
    assert result["ok"] is True
    assert [call for call in calls if "expectedSha256" in call[1] or call[0] == "vrc_refresh_asset_database"] == build_prepared_execution_plan(prepared)
    assert [call for call in calls if set(call[1]) == {"jobId"}] == [("vrc_import_unitypackage", {"jobId": JOB_ID})]


def test_job_receipt_accepts_windows_project_path_separator_difference() -> None:
    sha256 = hashlib.sha256(b"package").hexdigest()
    payload = {
        "jobId": JOB_ID,
        "projectPath": "E:/unity/Projects/Sapphy",
        "unityPackagePath": "E:/packages/Sapphy.unitypackage",
        "expectedSha256": sha256,
        "expectedSize": len(b"package"),
        "expectedAssetPaths": ["Assets/Voidcat/Sapphy.prefab"],
    }

    assert PreparedOutfitImportApprovedWriteService._job_receipt(
        payload,
        {"projectPath": r"E:\unity\Projects\Sapphy"},
        {
            "path": r"E:\packages\Sapphy.unitypackage",
            "sha256": sha256,
            "size": len(b"package"),
        },
        ["Assets/Voidcat/Sapphy.prefab"],
    ) == JOB_ID


def test_job_receipt_rejects_different_project_after_separator_normalization() -> None:
    sha256 = hashlib.sha256(b"package").hexdigest()
    payload = {
        "jobId": JOB_ID,
        "projectPath": "E:/unity/Projects/Other",
        "unityPackagePath": "E:/packages/Sapphy.unitypackage",
        "expectedSha256": sha256,
        "expectedSize": len(b"package"),
        "expectedAssetPaths": [],
    }

    with pytest.raises(RuntimeError, match="receipt project/path"):
        PreparedOutfitImportApprovedWriteService._job_receipt(
            payload,
            {"projectPath": r"E:\unity\Projects\Sapphy"},
            {
                "path": r"E:\packages\Sapphy.unitypackage",
                "sha256": sha256,
                "size": len(b"package"),
            },
            [],
        )


def test_prepared_import_rejects_wrong_sha_or_size_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda _settings, tool, arguments, **_kwargs: dashboard_server.McpResult(0, "", "", {**_pending_import(arguments).payload, "expectedSha256": "0" * 64}) if tool == "vrc_import_unitypackage" else dashboard_server.McpResult(0, "", "", {"ok": True}))
    result = _approved().execute(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert result["failureLayer"] == "unity_core_receipt_validation"
    assert "receipt project/path" in result["error"]


def test_malformed_expected_size_after_core_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )

    def invoke(_settings, tool, arguments, **_kwargs):
        if tool == "vrc_import_unitypackage":
            return dashboard_server.McpResult(
                0,
                "",
                "",
                {**_pending_import(arguments).payload, "expectedSize": None},
            )
        raise AssertionError("refresh must not run")

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved().execute(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert "receipt project/path" in result["error"]


def test_prepared_import_rejects_project_directory_identity_drift_before_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    (project / "Packages").rmdir()
    (project / "Packages").mkdir()
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    result = _approved().execute(prepared)
    assert result["ok"] is False
    assert result["failureLayer"] == "project_identity_verification"
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert result["checkpointRecoveryRequired"] is False
    assert "identity drifted" in result["error"]


def test_prepared_import_second_core_uncertainty_requires_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    support, target = tmp_path / "Support.unitypackage", tmp_path / "Dress.unitypackage"
    support.write_bytes(b"support")
    target.write_bytes(b"target")
    plan = _plan(project, target)
    plan["plan"]["kind"] = "unitypackage_import_sequence"
    plan["plan"]["source"]["importQueue"] = [
        {"actualPackagePath": str(support), "role": "support", "order": 1},
        {"actualPackagePath": str(target), "role": "target", "order": 2},
    ]
    prepared, _ = _prepare(
        tmp_path,
        plan,
        {"packagePath": str(target), "projectPath": str(project)},
    )
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    call_count = 0

    starts: list[dict] = []

    def invoke(_settings, tool, arguments, **_kwargs):
        nonlocal call_count
        if tool != "vrc_import_unitypackage":
            return dashboard_server.McpResult(0, "", "", {"ok": True})
        if set(arguments) == {"jobId"}:
            return _completed_import(starts[-1], arguments["jobId"])
        call_count += 1
        if call_count == 2:
            raise dashboard_server.UnityMcpError("lost receipt")
        starts.append(dict(arguments))
        return _pending_import(arguments)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved().execute(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert len(result["unityImports"]) == 1


def test_prepared_import_timeout_never_retries_write_or_runs_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    calls: list[tuple[str, dict]] = []

    def invoke(_settings, tool, arguments, **_kwargs):
        calls.append((tool, arguments))
        assert tool == "vrc_import_unitypackage" and "expectedSha256" in arguments
        return _pending_import(arguments)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved(timeout_seconds=0.0).execute(prepared)
    assert result["ok"] is False
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert result["failureLayer"] == "unity_core_job_polling"
    assert result["unityJobState"] == {
        "jobId": JOB_ID,
        "status": "pending",
        "mutationStarted": True,
        "startedForThisJob": False,
        "restoredAfterDomainReload": True,
        "expectedAssetCount": 1,
        "readbackFailurePath": "Assets/Outfits/Dress.prefab",
        "readbackFailureCode": "unitypackage_restored_readback_pending",
        "readbackFailureReason": "asset readback is still pending",
    }
    assert calls == [build_prepared_execution_plan(prepared)[0]]


def test_prepared_import_nonterminal_failure_preserves_unity_job_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )

    def invoke(_settings, tool, arguments, **_kwargs):
        assert tool == "vrc_import_unitypackage"
        if "expectedSha256" in arguments:
            return _pending_import(arguments)
        return dashboard_server.McpResult(0, "", "", {
            "ok": False,
            "pending": False,
            "status": "readback_pending",
            "jobId": JOB_ID,
            "mutationStarted": True,
            "startedForThisJob": False,
            "restoredAfterDomainReload": True,
            "expectedAssetCount": 1,
            "readbackFailurePath": "Assets/Outfits/Dress.prefab",
            "readbackFailureCode": "unitypackage_restored_readback_pending",
            "readbackFailureReason": "asset type missing",
            "reason": "asset readback is still pending",
        })

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved(poll_seconds=0.0).execute(prepared)
    assert result["ok"] is False
    assert result["failureLayer"] == "unity_core_job_polling"
    assert result["unityJobState"] == {
        "jobId": JOB_ID,
        "status": "readback_pending",
        "mutationStarted": True,
        "startedForThisJob": False,
        "restoredAfterDomainReload": True,
        "expectedAssetCount": 1,
        "readbackFailurePath": "Assets/Outfits/Dress.prefab",
        "readbackFailureCode": "unitypackage_restored_readback_pending",
        "readbackFailureReason": "asset type missing",
    }


def test_prepared_import_poll_transport_error_never_retries_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    calls: list[tuple[str, dict]] = []

    def invoke(_settings, tool, arguments, **_kwargs):
        calls.append((tool, arguments))
        if "expectedSha256" in arguments:
            return _pending_import(arguments)
        raise dashboard_server.UnityMcpError(
            "poll transport lost",
            cause_code="unity_core_connection_failed",
            retryable=True,
            core_tool="vrc_import_unitypackage",
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved(poll_seconds=0.0).execute(prepared)
    assert result["ok"] is False
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert result["failureLayer"] == "unity_core_job_polling"
    assert result["status"] == "transport_error"
    assert result["jobId"] == JOB_ID
    assert result["errorCode"] == "unity_core_connection_failed"
    assert result["requestMayHaveCommitted"] is True
    assert result["safeToRetry"] is False
    assert result["unityJobState"] == {
        "jobId": JOB_ID,
        "status": "pending",
        "mutationStarted": True,
        "startedForThisJob": False,
        "restoredAfterDomainReload": True,
        "expectedAssetCount": 1,
        "readbackFailurePath": "Assets/Outfits/Dress.prefab",
        "readbackFailureCode": "unitypackage_restored_readback_pending",
        "readbackFailureReason": "asset readback is still pending",
    }
    assert [call for call in calls if "expectedSha256" in call[1]] == [build_prepared_execution_plan(prepared)[0]]
    assert all(call[0] != "vrc_refresh_asset_database" for call in calls)


def test_prepared_import_waits_for_core_descriptor_after_domain_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )
    starts: list[dict] = []
    polls = 0

    def invoke(_settings, tool, arguments, **_kwargs):
        nonlocal polls
        if tool == "vrc_refresh_asset_database":
            return dashboard_server.McpResult(0, "", "", {"ok": True})
        if "expectedSha256" in arguments:
            starts.append(dict(arguments))
            return _pending_import(arguments)
        polls += 1
        if polls <= 2:
            raise dashboard_server.UnityMcpError(
                "Core descriptor is temporarily absent during domain reload.",
                cause_code="unity_core_starting",
                retryable=True,
                core_tool="vrc_import_unitypackage",
            )
        return _completed_import(starts[0], arguments["jobId"])

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved(poll_seconds=0.0).execute(prepared)

    assert result["ok"] is True
    assert len(starts) == 1
    assert polls == 3


def test_unitypackage_core_restores_active_job_across_domain_reload() -> None:
    source = (
        runtime_paths.ROOT_DIR
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "OutfitPackageImporter.cs"
    ).read_text(encoding="utf-8")
    assert 'ActiveJobSessionKey = "VRCForge.UnityPackageImport.ActiveJob"' in source
    assert "RestorePersistedActiveJob();" in source
    assert "SessionState.SetString(ActiveJobSessionKey, job.jobId);" in source
    assert "persisted.ToObject<ImportJob>()" in source
    assert "SessionState.EraseString(ActiveJobSessionKey);" in source
    assert "job.restoredAfterDomainReload = true;" in source
    assert "TryCompletePendingReadback(activeJob);" in source
    restored_readback = source.split("private static void TryCompletePendingReadback", 1)[1].split(
        "private static void RecordPendingReadbackFailure", 1
    )[0]
    assert "!job.startedForThisJob" not in restored_readback
    assert "!job.mutationStarted" in restored_readback
    assert "job.expectedAssetPaths.Count == 0" in source
    assert "EditorApplication.isCompiling" in source
    assert "EditorApplication.isUpdating" in source
    assert '"restored_expected_asset_readback"' in source
    assert '"pending_expected_asset_readback"' in source
    assert 'job.status = "readback_pending"' in source
    assert 'job.status != "readback_pending"' in source
    assert "TimeSpan.FromMilliseconds(500)" in source
    assert "readbackFailurePath" in source
    assert "readbackFailureReason" in source


def test_unitypackage_event_binding_uses_the_actual_unity_event_name() -> None:
    source = (
        runtime_paths.ROOT_DIR
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "OutfitPackageImporter.cs"
    ).read_text(encoding="utf-8")
    started = source.split("private static void OnImportStarted", 1)[1].split(
        "private static void OnImportCompleted", 1
    )[0]
    terminal = source.split("private static ImportJob ActiveJobForEvent", 1)[1].split(
        "private static void CompleteFailedJob", 1
    )[0]
    assert "MatchesExpectedPackageEvent(job, packageName)" not in started
    assert "MatchesExpectedPackageEvent(job, packageName)" not in terminal
    assert "job.mutationStarted" in started
    assert "string.Equals(importInvocationJobId, job.jobId, StringComparison.Ordinal)" in started
    assert "job.importEventPackageName = packageName" in started
    assert "job.startedForThisJob" in terminal
    assert "job.importEventPackageName" in terminal


def test_unitypackage_timeout_preserves_structured_readback_diagnostics() -> None:
    payload = {
        "ok": True,
        "pending": True,
        "status": "readback_pending",
        "jobId": "a" * 32,
        "mutationStarted": True,
        "startedForThisJob": False,
        "restoredAfterDomainReload": True,
        "expectedAssetCount": 165,
        "readbackFailurePath": "Assets/MANUKA/Prefab/MANUKA_lilToon.prefab",
        "readbackFailureCode": "unitypackage_restored_readback_pending",
        "readbackFailureReason": "asset type missing",
    }
    result = _approved(timeout_seconds=0.0)._wait_for_job(  # noqa: SLF001
        SimpleNamespace(), payload
    )

    assert result["failureLayer"] == "unity_core_job_polling"
    assert result["unityJobState"] == {
        "jobId": "a" * 32,
        "status": "readback_pending",
        "mutationStarted": True,
        "startedForThisJob": False,
        "restoredAfterDomainReload": True,
        "expectedAssetCount": 165,
        "readbackFailurePath": "Assets/MANUKA/Prefab/MANUKA_lilToon.prefab",
        "readbackFailureCode": "unitypackage_restored_readback_pending",
        "readbackFailureReason": "asset type missing",
    }


def test_prepared_import_rejects_unsafe_expected_asset_before_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    plan = _plan(project, package)
    plan["plan"]["expectedAssetPaths"] = ["Assets/../outside.prefab"]
    with pytest.raises(RuntimeError, match="expected asset path"):
        _prepare(
            tmp_path,
            plan,
            {"packagePath": str(package), "projectPath": str(project)},
        )


def test_nested_zip_and_loose_branches_are_prepared_and_execute_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("x.unitypackage", b"nested-package")
    zip_plan = {"plan": {"readyToApply": True, "kind": "unitypackage_import", "projectPath": str(project), "source": {"path": str(bundle), "importQueue": [{"sourceType": "zip", "path": "x.unitypackage", "role": "target", "order": 1}]}}}
    prepared, _ = _prepare(
        tmp_path,
        zip_plan,
        {"packagePath": str(bundle), "projectPath": str(project)},
    )
    nested_call = build_prepared_execution_plan(prepared)[0]
    assert nested_call[0] == "vrc_import_unitypackage"
    assert nested_call[1]["expectedSha256"] == hashlib.sha256(b"nested-package").hexdigest()
    temporary_package = Path(nested_call[1]["unityPackagePath"])
    assert not temporary_package.exists()

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))

    start_arguments: dict = {}

    def invoke(_settings, tool, arguments, **_kwargs):
        if tool == "vrc_import_unitypackage" and "expectedSha256" in arguments:
            assert Path(arguments["unityPackagePath"]).read_bytes() == b"nested-package"
            start_arguments.update(arguments)
            return _pending_import(arguments)
        if tool == "vrc_import_unitypackage":
            return _completed_import(start_arguments)
        return dashboard_server.McpResult(0, "", "", {"ok": True})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    assert _approved().execute(prepared)["ok"] is True
    assert not temporary_package.exists()

    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "Dress.prefab").write_bytes(b"prefab")
    loose_plan = {"plan": {"readyToApply": True, "kind": "loose_prefab_copy", "projectPath": str(project), "targetFolder": "Assets/VRCForge/ImportedOutfits", "source": {"path": str(loose)}}}
    prepared, _ = _prepare(
        tmp_path,
        loose_plan,
        {"packagePath": str(loose), "projectPath": str(project)},
    )
    assert build_prepared_execution_plan(prepared)[0][0] == "vrc_refresh_asset_database"
    result = _approved().execute(prepared)
    assert result["ok"] is True
    assert result["importedPrefabCandidates"] == ["Assets/VRCForge/ImportedOutfits/Dress.prefab"]
    assert (project / "Assets" / "VRCForge" / "ImportedOutfits" / "Dress.prefab").read_bytes() == b"prefab"


def test_loose_post_copy_identity_failure_returns_partial_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "Dress.prefab").write_bytes(b"prefab")
    plan = {
        "plan": {
            "readyToApply": True,
            "kind": "loose_prefab_copy",
            "projectPath": str(project),
            "targetFolder": "Assets/VRCForge/ImportedOutfits",
            "source": {"path": str(loose)},
        }
    }
    prepared, _ = _prepare(
        tmp_path,
        plan,
        {"packagePath": str(loose), "projectPath": str(project)},
    )
    original_verify = dashboard_server._verify_prepared_import_project_identity
    verify_calls = 0

    def fail_second(identity):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise RuntimeError("post-copy identity drift")
        return original_verify(identity)

    monkeypatch.setattr(
        dashboard_server,
        "_verify_prepared_import_project_identity",
        fail_second,
    )
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("refresh must not run")
        ),
    )

    result = _approved().execute(prepared)

    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "partial"
    assert result["checkpointRecoveryRequired"] is True
    assert "post-copy identity drift" in result["error"]
    assert (
        project / "Assets/VRCForge/ImportedOutfits/Dress.prefab"
    ).read_bytes() == b"prefab"


def test_completed_import_refresh_failure_has_explicit_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    prepared, _ = _prepare(
        tmp_path,
        _plan(project, package),
        {"packagePath": str(package), "projectPath": str(project)},
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )

    def invoke(_settings, tool, arguments, **_kwargs):
        if tool == "vrc_import_unitypackage":
            return _completed_import(arguments)
        return dashboard_server.McpResult(
            0, "", "", {"ok": False, "error": "refresh failed"}
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved().execute(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "partial"
    assert result["checkpointRecoveryRequired"] is True
    assert result["failureLayer"] == "unity_asset_database_refresh"
    assert result["error"] == "refresh failed"


def test_preparer_rejects_reserved_and_handler_is_bound() -> None:
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER.prepare(
            {PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: None},
            None,
        )
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_import_outfit_package"]  # noqa: SLF001
    assert handler.risk_level == "medium"
    assert handler.risk_level_resolver is classify_prepared_outfit_import_risk
    assert handler.manual_approval_resolver is prepared_outfit_import_manual_confirmation_reason
    assert handler.request_preparer == dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER.prepare
    assert handler.handler == dashboard_server.PREPARED_OUTFIT_IMPORT_APPROVED_WRITE.execute
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan
    assert handler.verification_profile == "unity_asset_write_console"
    assert handler.verification_prepare_handler is dashboard_server.prepare_persisted_scene_console_verification
    assert handler.verification_finalize_handler is dashboard_server.finalize_persisted_scene_console_verification


def test_unitypackage_core_source_holds_read_handle_and_echoes_bound_identity() -> None:
    source = (runtime_paths.ROOT_DIR / "Assets" / "VRCForge" / "Editor" / "OutfitPackageImporter.cs").read_text(encoding="utf-8")
    assert "expectedSha256" in source and "expectedSize" in source
    assert "new FileStream(packagePath, FileMode.Open, FileAccess.Read, FileShare.Read)" in source
    assert "SHA256.Create()" in source
    assert "AssetDatabase.ImportPackage(packagePath" in source
    assert "expectedAssetPaths" in source
    assert "AssetDatabase.GetMainAssetTypeAtPath(assetPath)" in source
    assert "AssetDatabase.AssetPathToGUID(assetPath)" in source
    assert "OnImportCompleted" in source
    assert "ReadExpectedAssets(job.expectedAssetPaths)" in source
    assert "!job.startedForThisJob" in source
    assert "expectedEventPackageName = Path.GetFileNameWithoutExtension(packagePath)" in source
    assert "MatchesExpectedPackageEvent" not in source
    assert "importInvocationJobId = job.jobId" in source
    assert "importInvocationJobId = \"\"" in source
    assert "job.mutationStarted" in source
    assert "job.importEventPackageName = packageName" in source
    assert "string.Equals(job.importEventPackageName, packageName ??" in source
    assert "ActiveJobForEvent(packageName)" in source
    assert "app_unitypackage_import_poll" in (runtime_paths.ROOT_DIR / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
