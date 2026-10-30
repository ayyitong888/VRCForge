from pathlib import Path
from copy import deepcopy
import pytest

@pytest.fixture
def wardrobe_case(tmp_path, monkeypatch):
    import dashboard_server as server
    root = tmp_path / "project"
    (root / "Assets").mkdir(parents=True)
    for name in ("Params.asset", "FX.controller", "Menu.asset", "Nested.asset", "Main.unity"):
        (root / "Assets" / name).write_text(name)
        (root / "Assets" / (name + ".meta")).write_text("guid: " + "a" * 32)
    target = {
        "schema": "vrcforge.execution_target.v1", "scope": "avatar", "ambiguous": False,
        "resolutionCandidateCount": 1,
        "project": {"root": str(root), "projectId": "project-id"},
        "editor": {"unityPid": 1, "processStartTime": "1", "coreInstanceId": "core"},
        "scene": {"assetPath": "Assets/Main.unity", "guid": "scene", "revision": "1", "digest": "a" * 64},
        "avatar": {"globalObjectId": "avatar-id", "exactHierarchyPath": "Avatar"},
    }
    paths = {
        "ensure_expression_parameter": ["Assets/Params.asset"],
        "ensure_animator_state": ["Assets/FX.controller", "Assets/Generated/Animations/Default.anim"],
        "ensure_expression_menu_control": ["Assets/Menu.asset", "Assets/Nested.asset", "Assets/Generated/Menus/Wardrobe.asset"],
    }
    def preview(action):
        def read(params, *, preview):
            assert preview is True
            return {"ok": True, "preview": True, "plan": {
                "action": action, "avatarPath": "Avatar", "checkpointAssetPaths": paths[action],
            }}
        return read
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda query: {"ok": True, "targets": [target]})
    for action in paths:
        monkeypatch.setattr(server, action + "_sync", preview(action))
    inspected = []
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda root, **kw: inspected.append(kw) or {"ok": True})
    arguments = {
        "_checkpointTargetTool": "vrcforge_create_wardrobe", "projectPath": str(root),
        "avatarPath": "Avatar", "parameterName": "Outfit", "executionTarget": target,
    }
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_create_wardrobe"]
    arguments, _ = handler.request_preparer(arguments, None)
    return server, root, arguments, target, paths, inspected


def test_create_wardrobe_authoritative_preflight_accepts_complete_core_footprints(wardrobe_case):
    server, root, arguments, target, paths, inspected = wardrobe_case
    result = server.prepare_authoritative_unity_checkpoint_sync(root, arguments)
    assert result.get("ok") is True, result
    footprint = result["assetFootprint"]
    assert "Assets/Nested.asset" in footprint["archiveFiles"]
    assert "Assets/Generated/Animations/Default.anim" in footprint["archiveAbsentFiles"]
    assert "Assets/Generated/Menus/Wardrobe.asset.meta" in footprint["archiveAbsentFiles"]
    assert "Assets/Generated.meta" in footprint["archiveAbsentFiles"]
    assert "Assets/Generated/Animations" in footprint["archiveAbsentDirectories"]
    assert inspected[0]["checkpoint_files"] == sorted(footprint["archiveFiles"] + footprint["archiveAbsentFiles"])


@pytest.mark.parametrize("change", ["output", "avatar", "missing_scope"])
def test_changed_primitive_preview_blocks_before_checkpoint(wardrobe_case, monkeypatch, change):
    server, root, arguments, target, paths, inspected = wardrobe_case
    if change == "output":
        paths["ensure_animator_state"].append("Assets/Unexpected.anim")
    else:
        def changed(params, *, preview):
            return {"ok": True, "preview": True, "plan": {
                "action": "ensure_expression_parameter", "avatarPath": "Other" if change == "avatar" else "Avatar",
                **({"checkpointAssetPaths": ["Assets/Params.asset"]} if change == "avatar" else {}),
            }}
        monkeypatch.setattr(server, "ensure_expression_parameter_sync", changed)
    result = server.prepare_authoritative_unity_checkpoint_sync(root, arguments)
    assert result["ok"] is False and inspected == []


@pytest.mark.parametrize("path", ["Assets/../Escape.asset", "Packages/pkg.asset", "Assets/Other.unity", "Assets/Editor/Script.cs"])
def test_unproven_or_outside_footprints_are_rejected(wardrobe_case, path):
    server, root, arguments, target, paths, inspected = wardrobe_case
    from prepared_unity_execution import PREPARED_UNITY_EXECUTION_KEY
    raw = deepcopy(arguments); raw.pop(PREPARED_UNITY_EXECUTION_KEY)
    paths["ensure_expression_parameter"][:] = [path]
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_create_wardrobe"]
    sealed, _ = handler.request_preparer(raw, None)
    result = server.prepare_authoritative_unity_checkpoint_sync(root, sealed)
    assert result["ok"] is False and inspected == []


@pytest.mark.parametrize("section,key,value", [("scene", "guid", "other-scene"), ("avatar", "globalObjectId", "other"), ("editor", "coreInstanceId", "other")])
def test_fresh_identity_drift_blocks_before_checkpoint(wardrobe_case, monkeypatch, section, key, value):
    server, root, arguments, target, paths, inspected = wardrobe_case
    live = deepcopy(target); live[section][key] = value
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda query: {"ok": True, "targets": [live]})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, arguments)
    assert result["ok"] is False and inspected == []


def test_wardrobe_checkpoint_ignores_scene_content_snapshot(wardrobe_case, monkeypatch):
    server, root, arguments, target, paths, inspected = wardrobe_case
    target["scene"].pop("digest")
    target["scene"].pop("revision")
    live = deepcopy(target)
    live["scene"].update(digest="current-content", revision="new")
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda query: {"ok": True, "targets": [live]})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, arguments)
    assert result["ok"] is True, result
    assert len(inspected) == 1


def test_unsealed_wardrobe_plan_is_rejected(wardrobe_case):
    server, root, arguments, target, paths, inspected = wardrobe_case
    arguments.pop("_vrcforge_prepared_unity_execution")
    result = server.prepare_authoritative_unity_checkpoint_sync(root, arguments)
    assert result["ok"] is False and inspected == []


def test_sealed_expected_paths_reach_exact_primitive_requests(wardrobe_case):
    server, root, arguments, target, paths, inspected = wardrobe_case
    from wardrobe_outfit_workflow_service import build_create_wardrobe_core_calls
    builders = {"vrc_ensure_expression_parameter": server.build_ensure_expression_parameter_request,
                "vrc_ensure_animator_state": server.build_ensure_animator_state_request,
                "vrc_ensure_expression_menu_control": server.build_ensure_expression_menu_control_request}
    for tool, request in build_create_wardrobe_core_calls(arguments, False):
        assert builders[tool](request, False) == request


def test_checkpoint_archive_covers_existing_graph_and_records_new_outputs(wardrobe_case, tmp_path):
    import zipfile
    from agent_gateway import AgentGateway
    server, root, arguments, target, paths, inspected = wardrobe_case
    (root / "Assets/Unrelated.asset").write_text("unrelated")
    result = server.prepare_authoritative_unity_checkpoint_sync(root, arguments)
    footprint = result["assetFootprint"]
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    record = {"id": "wardrobe-checkpoint", "projectRoot": str(root), "assetFootprint": footprint,
              **{key: value for key, value in footprint.items() if key != "schema"}}
    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(root, record)
    assert checkpoint["ok"] is True, checkpoint
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert set(archive.namelist()) == set(footprint["archiveFiles"])
        assert "Assets/Nested.asset" in archive.namelist()
        assert "Assets/Unrelated.asset" not in archive.namelist()
    assert "Assets/Generated/Animations/Default.anim" in checkpoint["archiveAbsentFiles"]


def test_add_binding_has_same_prepared_asset_and_scene_guards(wardrobe_case, monkeypatch):
    from types import SimpleNamespace
    from prepared_unity_execution import build_prepared_execution_plan
    from wardrobe_outfit_workflow_service import build_add_wardrobe_outfit_request
    server, root, arguments, target, paths, inspected = wardrobe_case
    raw = deepcopy(arguments); raw.pop("_vrcforge_prepared_unity_execution")
    raw.update(_checkpointTargetTool="vrcforge_add_wardrobe_outfit", outfitName="City", objectPaths=["Outfit"], value=1)
    plan = {"action": "add_wardrobe_outfit", "avatarPath": "Avatar", "value": 1,
            "checkpointAssetPaths": ["Assets/FX.controller", "Assets/Menu.asset", "Assets/Nested.asset", "Assets/Main.unity", "Assets/Generated/City.anim"]}
    monkeypatch.setattr(server, "WARDROBE_OUTFIT_WORKFLOWS", SimpleNamespace(preview_add_wardrobe_outfit=lambda query: {"ok": True, "preview": True, "plan": deepcopy(plan)}))
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_add_wardrobe_outfit"]
    assert handler.request_preparer is not None
    sealed, _ = handler.request_preparer(raw, None)
    assert build_add_wardrobe_outfit_request(sealed, False) == build_prepared_execution_plan(sealed)[0][1]
    result = server.prepare_authoritative_unity_checkpoint_sync(root, sealed)
    assert result["ok"] is True, result
    assert "Assets/Main.unity" in result["assetFootprint"]["archiveFiles"]
    assert "Assets/Generated/City.anim.meta" in result["assetFootprint"]["archiveAbsentFiles"]
    inspected.clear(); plan["checkpointAssetPaths"].append("Assets/Other.anim")
    assert server.prepare_authoritative_unity_checkpoint_sync(root, sealed)["ok"] is False
    assert inspected == []
