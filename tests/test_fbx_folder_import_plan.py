"""A single prepared FBX must reach the existing sealed loose-import lane."""
from pathlib import Path

import pytest

from outfit_import_planner import build_outfit_import_plan
from test_outfit_import_planner import make_project


MODEL = "Milltina_FullRun_20261021_FT.fbx"
TARGET = "Assets/VRCForgeGenerated/FullRun_20261021/Models"


def fixture(tmp_path: Path, names=(MODEL,)):
    project = tmp_path / "Milltina"
    make_project(project)
    source = tmp_path / "Output"
    source.mkdir()
    for name in names:
        (source / name).write_bytes(b"model-contract-fixture-not-a-valid-fbx")
    return project, source


def test_single_fbx_folder_has_no_invented_prefab_selection(tmp_path):
    project, source = fixture(tmp_path)
    result = build_outfit_import_plan(source, project_path=project, target_folder=TARGET)
    plan = result["plan"]
    assert result["ok"] and plan["readyToApply"]
    assert plan["kind"] == "loose_prefab_copy"
    assert plan["selectedPrefab"] == plan["source"]["selectedPrefab"] == ""
    assert plan["expectedAssetPaths"] == [f"{TARGET}/{MODEL}"]
    assert plan["requiresApproval"] and plan["requiresCheckpoint"]
    assert not (project / TARGET).exists()


@pytest.mark.parametrize("names", [("one.fbx", "two.fbx"), ("one.obj",), ("one.blend",), ("unknown.bin",)])
def test_new_fbx_entry_does_not_guess_multiple_models_or_expand_types(tmp_path, names):
    project, source = fixture(tmp_path, names)
    assert not build_outfit_import_plan(source, project_path=project)["plan"]["readyToApply"]


@pytest.mark.parametrize("options", [
    {"selected_prefab": "missing.prefab"},
    {"selected_unitypackage": "missing.unitypackage"},
    {"dependency_mode": "selected_only"},
])
def test_new_fbx_entry_respects_existing_explicit_selection_modes(tmp_path, options):
    project, source = fixture(tmp_path)
    assert not build_outfit_import_plan(source, project_path=project, **options)["plan"]["readyToApply"]


def test_single_fbx_file_still_requires_folder_context(tmp_path):
    project, source = fixture(tmp_path)
    assert not build_outfit_import_plan(source / MODEL, project_path=project)["plan"]["readyToApply"]


def test_truncated_model_folder_cannot_become_a_single_model_selection(tmp_path):
    project, source = fixture(tmp_path, ("one.fbx", "two.fbx"))
    result = build_outfit_import_plan(source, project_path=project, max_entries=1)
    assert not result["plan"]["readyToApply"]


def test_registered_fbx_preparer_reaches_exact_approval_checkpoint_files(tmp_path, monkeypatch):
    import dashboard_server as server
    from agent_gateway import AgentGateway
    from prepared_unity_execution import build_prepared_execution_plan, prepared_evidence

    project, source = fixture(tmp_path)
    sentinel = project / "Assets/Unrelated.shader"
    sentinel.write_bytes(b"unsaved-editor-state-has-separate-ownership")
    production = server.AGENT_GATEWAY._write_handlers["vrcforge_import_outfit_package"]
    assert ".fbx" in server.OUTFIT_IMPORT_ALLOWED_SUFFIXES
    # Use the actual registered preparer, planner and checkpoint owner. Only the
    # Unity transport is replaced: it records what the approval asks to inspect.
    arguments, _ = production.request_preparer({
        "projectPath": str(project), "packagePath": str(source), "targetFolder": TARGET,
        "archiveFiles": ["Assets/Unrelated.shader"],
    }, None)
    evidence = prepared_evidence(arguments)
    assert evidence["kind"] == "loose_prefab_copy"
    assert evidence["plan"]["selectedPrefab"] == ""
    assert [name for name, _ in build_prepared_execution_plan(arguments)] == ["vrc_refresh_asset_database"]
    seen = []

    def inspect(root, *, checkpoint_files=None):
        assert root == project
        seen.append(checkpoint_files)
        return {"ok": True, "mutationStarted": False, "saved": False}

    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", inspect)
    gateway = AgentGateway(tmp_path / "app/config.json", tmp_path / "app/audit")
    service = gateway.approval_transactions
    service.register_write_handler(
        production.name, production.description, production.risk_level, production.handler,
        checkpoint_prepare_handler=production.checkpoint_prepare_handler,
    )
    checkpoint = service._create_pre_write_checkpoint(
        {"id": "fbx-contract-replay", "targetTool": production.name}, arguments,
    )
    expected = sorted([
        "Assets/VRCForgeGenerated.meta", "Assets/VRCForgeGenerated/FullRun_20261021.meta",
        f"{TARGET}.meta", f"{TARGET}/{MODEL}", f"{TARGET}/{MODEL}.meta",
    ])
    assert checkpoint["ok"], checkpoint
    assert seen == [expected]
    assert checkpoint["archiveAbsentFiles"] == expected
    assert checkpoint["archiveFiles"] == []
    assert checkpoint["archiveScopeReason"] == "sealed_import_complete_asset_footprint"
    assert not (project / TARGET).exists()
    assert sentinel.read_bytes() == b"unsaved-editor-state-has-separate-ownership"

    (source / MODEL).write_bytes(b"source-drift")
    before = len(seen)
    rejected = service._create_pre_write_checkpoint(
        {"id": "fbx-source-drift", "targetTool": production.name}, arguments,
    )
    assert not rejected["ok"] and "drifted" in rejected["error"]
    assert len(seen) == before


@pytest.mark.parametrize("names,limit,expected", [
    ((MODEL,), 5000, "loose_prefab_assets"),
    (("one.fbx", "two.fbx"), 5000, "manual_review"),
    (("one.fbx", "two.fbx"), 1, "manual_review"),
    (("one.blend",), 5000, "manual_review"),
])
def test_inspection_summary_uses_the_same_model_fact(tmp_path, names, limit, expected):
    from outfit_package_inspector import inspect_outfit_package
    _, source = fixture(tmp_path, names)
    inspected = inspect_outfit_package(source, max_entries=limit)
    assert inspected["summary"]["importPlanKind"] == expected
