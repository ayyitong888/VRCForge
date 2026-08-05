from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
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


def test_preparer_seals_direct_unitypackage_and_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
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
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
    package.write_bytes(b"changed")
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    with pytest.raises(Exception, match="identity or content drifted"):
        dashboard_server.import_outfit_package_approved_sync(prepared)


def test_prepared_import_executes_exact_calls_and_verifies_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
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
    result = dashboard_server.import_outfit_package_approved_sync(prepared)
    assert result["ok"] is True
    assert [call for call in calls if "expectedSha256" in call[1] or call[0] == "vrc_refresh_asset_database"] == build_prepared_execution_plan(prepared)
    assert [call for call in calls if set(call[1]) == {"jobId"}] == [("vrc_import_unitypackage", {"jobId": JOB_ID})]


def test_prepared_import_rejects_wrong_sha_or_size_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda _settings, tool, arguments, **_kwargs: dashboard_server.McpResult(0, "", "", {**_pending_import(arguments).payload, "expectedSha256": "0" * 64}) if tool == "vrc_import_unitypackage" else dashboard_server.McpResult(0, "", "", {"ok": True}))
    result = dashboard_server.import_outfit_package_approved_sync(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert "receipt project/path" in result["error"]


def test_prepared_import_rejects_project_directory_identity_drift_before_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
    (project / "Packages").rmdir()
    (project / "Packages").mkdir()
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    with pytest.raises(Exception, match="identity drifted"):
        dashboard_server.import_outfit_package_approved_sync(prepared)


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
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: plan)
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(target), "projectPath": str(project)}, None)
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
    result = dashboard_server.import_outfit_package_approved_sync(prepared)
    assert result["ok"] is False
    assert result["committed"] is True
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert len(result["unityImports"]) == 1


def test_prepared_import_timeout_never_retries_write_or_runs_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "OUTFIT_IMPORT_JOB_TIMEOUT_SECONDS", 0.0)
    calls: list[tuple[str, dict]] = []

    def invoke(_settings, tool, arguments, **_kwargs):
        calls.append((tool, arguments))
        assert tool == "vrc_import_unitypackage" and "expectedSha256" in arguments
        return _pending_import(arguments)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.import_outfit_package_approved_sync(prepared)
    assert result["ok"] is False
    assert result["commitState"] == "unknown"
    assert result["checkpointRecoveryRequired"] is True
    assert calls == [build_prepared_execution_plan(prepared)[0]]


def test_prepared_import_poll_transport_error_never_retries_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, package = _project(tmp_path), tmp_path / "Dress.unitypackage"
    package.write_bytes(b"package")
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: _plan(project, package))
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30))
    monkeypatch.setattr(dashboard_server, "OUTFIT_IMPORT_JOB_POLL_SECONDS", 0.0)
    calls: list[tuple[str, dict]] = []

    def invoke(_settings, tool, arguments, **_kwargs):
        calls.append((tool, arguments))
        if "expectedSha256" in arguments:
            return _pending_import(arguments)
        raise dashboard_server.UnityMcpError("poll transport lost")

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.import_outfit_package_approved_sync(prepared)
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
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: plan)
    with pytest.raises(RuntimeError, match="expected asset path"):
        dashboard_server.prepare_outfit_import_package_request({"packagePath": str(package), "projectPath": str(project)}, None)


def test_nested_zip_and_loose_branches_are_prepared_and_execute_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("x.unitypackage", b"nested-package")
    zip_plan = {"plan": {"readyToApply": True, "kind": "unitypackage_import", "projectPath": str(project), "source": {"path": str(bundle), "importQueue": [{"sourceType": "zip", "path": "x.unitypackage", "role": "target", "order": 1}]}}}
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: zip_plan)
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(bundle), "projectPath": str(project)}, None)
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
    assert dashboard_server.import_outfit_package_approved_sync(prepared)["ok"] is True
    assert not temporary_package.exists()

    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "Dress.prefab").write_bytes(b"prefab")
    loose_plan = {"plan": {"readyToApply": True, "kind": "loose_prefab_copy", "projectPath": str(project), "targetFolder": "Assets/VRCForge/ImportedOutfits", "source": {"path": str(loose)}}}
    monkeypatch.setattr(dashboard_server, "plan_outfit_import_sync", lambda _arguments: loose_plan)
    prepared, _ = dashboard_server.prepare_outfit_import_package_request({"packagePath": str(loose), "projectPath": str(project)}, None)
    assert build_prepared_execution_plan(prepared)[0][0] == "vrc_refresh_asset_database"
    result = dashboard_server.import_outfit_package_approved_sync(prepared)
    assert result["ok"] is True
    assert result["importedPrefabCandidates"] == ["Assets/VRCForge/ImportedOutfits/Dress.prefab"]
    assert (project / "Assets" / "VRCForge" / "ImportedOutfits" / "Dress.prefab").read_bytes() == b"prefab"


def test_preparer_rejects_reserved_and_handler_is_bound() -> None:
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_outfit_import_package_request({PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_import_outfit_package"]  # noqa: SLF001
    assert handler.request_preparer is dashboard_server.prepare_outfit_import_package_request
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
