from __future__ import annotations

import hashlib
import secrets
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
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
    with pytest.raises(Exception, match="identity or content drifted"):
        _approved().execute(prepared)


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
            return _pending_import(arguments)
        if tool == "vrc_import_unitypackage":
            assert kwargs["execution_context"] == {"lane": "app_unitypackage_import_poll"}
            return _completed_import(start_arguments)
        return dashboard_server.McpResult(0, "", "", {"ok": True})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved().execute(prepared)
    assert result["ok"] is True
    assert [call for call in calls if "expectedSha256" in call[1] or call[0] == "vrc_refresh_asset_database"] == build_prepared_execution_plan(prepared)
    assert [call for call in calls if set(call[1]) == {"jobId"}] == [("vrc_import_unitypackage", {"jobId": JOB_ID})]


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
    with pytest.raises(Exception, match="identity drifted"):
        _approved().execute(prepared)


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
    assert calls == [build_prepared_execution_plan(prepared)[0]]


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
        raise dashboard_server.UnityMcpError("poll transport lost")

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = _approved(poll_seconds=0.0).execute(prepared)
    assert result["ok"] is False
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert [call for call in calls if "expectedSha256" in call[1]] == [build_prepared_execution_plan(prepared)[0]]
    assert all(call[0] != "vrc_refresh_asset_database" for call in calls)


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
    assert result["error"] == "refresh failed"


def test_preparer_rejects_reserved_and_handler_is_bound() -> None:
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER.prepare(
            {PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: None},
            None,
        )
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_import_outfit_package"]  # noqa: SLF001
    assert handler.request_preparer == dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER.prepare
    assert handler.handler == dashboard_server.PREPARED_OUTFIT_IMPORT_APPROVED_WRITE.execute
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan


def test_unitypackage_core_source_holds_read_handle_and_echoes_bound_identity() -> None:
    source = (dashboard_server.ROOT_DIR / "Assets" / "VRCForge" / "Editor" / "OutfitPackageImporter.cs").read_text(encoding="utf-8")
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
    assert "if (!MatchesExpectedPackageEvent(job, packageName))" in source
    assert source.count("MatchesExpectedPackageEvent(job, packageName)") == 2
    assert "job.importEventPackageName = packageName ??" in source
    assert "string.Equals(job.importEventPackageName, packageName ??" in source
    assert "ActiveJobForEvent(packageName)" in source
    assert "app_unitypackage_import_poll" in (dashboard_server.ROOT_DIR / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs").read_text(encoding="utf-8")
