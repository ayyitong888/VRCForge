from __future__ import annotations

from pathlib import Path

import dashboard_server
from prepared_unity_execution import install_prepared_calls


def _arguments(project: Path, risk_facts: dict) -> dict:
    evidence = {
        "kind": "unitypackage_import",
        "projectIdentity": {"projectPath": str(project)},
        "plan": {"kind": "unitypackage_import", "riskFacts": risk_facts},
        "queue": [],
    }
    return install_prepared_calls(
        {"projectPath": str(project)},
        [("vrc_import_unitypackage", {"projectPath": str(project)})],
        evidence,
    )


def _facts(*, code_bearing: bool) -> dict:
    return {
        "codePayloadCount": 1 if code_bearing else 0,
        "codeBearingPackage": code_bearing,
        "fullProjectCheckpointRequired": code_bearing,
        "checkpointScope": "full_project" if code_bearing else "exact_assets",
        "recommendedRiskLevel": "high" if code_bearing else "medium",
        "mediumEligible": not code_bearing,
        "reasonCodes": ["code_bearing_package_requires_full_project_checkpoint"] if code_bearing else [],
    }


def test_code_bearing_dispatch_accepts_only_full_project_receipt(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    calls: list[dict] = []

    def helper(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"ok": True, "archiveScopeReason": "code_bearing_package_full_project"}

    monkeypatch.setattr("unity_import_checkpoint.prepare_import_asset_checkpoint", helper)
    result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        project,
        {**_arguments(project, _facts(code_bearing=True)), "_checkpointTargetTool": "vrcforge_import_outfit_package"},
    )

    assert result["ok"] is True
    assert len(calls) == 1


def test_code_bearing_dispatch_rejects_exact_scope_receipt_without_fallback(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    calls: list[dict] = []

    def helper(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"ok": True, "archiveScopeReason": "sealed_import_complete_asset_footprint"}

    monkeypatch.setattr("unity_import_checkpoint.prepare_import_asset_checkpoint", helper)
    result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        project,
        {**_arguments(project, _facts(code_bearing=True)), "_checkpointTargetTool": "vrcforge_import_outfit_package"},
    )

    assert result["ok"] is False
    assert result["code"] == "checkpoint_scope_unresolved"
    assert "full-project scope receipt" in result["error"]
    assert len(calls) == 1


def test_plain_dispatch_keeps_exact_scope_receipt(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "Project"
    project.mkdir()

    monkeypatch.setattr(
        "unity_import_checkpoint.prepare_import_asset_checkpoint",
        lambda *args, **kwargs: {
            "ok": True,
            "archiveScopeReason": "sealed_import_complete_asset_footprint",
        },
    )
    result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        project,
        {**_arguments(project, _facts(code_bearing=False)), "_checkpointTargetTool": "vrcforge_import_outfit_package"},
    )

    assert result == {
        "ok": True,
        "archiveScopeReason": "sealed_import_complete_asset_footprint",
    }


def test_code_bearing_dispatch_rejects_missing_scope_contract_before_helper(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    called = False

    def helper(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True, "archiveScopeReason": "code_bearing_package_full_project"}

    monkeypatch.setattr("unity_import_checkpoint.prepare_import_asset_checkpoint", helper)
    facts = {"codePayloadCount": 1, "codeBearingPackage": True}
    result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        project,
        {**_arguments(project, facts), "_checkpointTargetTool": "vrcforge_import_outfit_package"},
    )

    assert result["ok"] is False
    assert result["code"] == "checkpoint_scope_unresolved"
    assert called is False


def test_legacy_asmdef_only_plan_cannot_be_upgraded_by_archive_scan(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    called = False

    def helper(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True, "archiveScopeReason": "code_bearing_package_full_project"}

    monkeypatch.setattr("unity_import_checkpoint.prepare_import_asset_checkpoint", helper)
    # This models a plan produced before .asmdef/.asmref were included in the
    # planner's code-bearing family: the sealed facts still say zero code.
    legacy_facts = {
        "codePayloadCount": 0,
        "mediumEligible": True,
        "recommendedRiskLevel": "medium",
        "reasonCodes": [],
    }
    result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        project,
        {**_arguments(project, legacy_facts), "_checkpointTargetTool": "vrcforge_import_outfit_package"},
    )

    assert result["ok"] is False
    assert result["code"] == "checkpoint_scope_unresolved"
    assert "predates code-bearing" in result["error"]
    assert called is True
