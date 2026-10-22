"""Replay a nested MA Sync request through its registered scene checkpoint lane."""
from copy import deepcopy
from dataclasses import replace
import zipfile

import pytest

from test_agent_checkpoint_recovery_service import _gateway
from test_scene_workflow_checkpoint_scope import _target
from unity_execution_plans_workflows import build_workflow_execution_plan


TOOL = "vrcforge_add_modular_avatar_component"


@pytest.fixture
def case(tmp_path, monkeypatch):
    import dashboard_server as server

    root = tmp_path / "project"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (root / folder).mkdir(parents=True)
    for name in ("Assets/Main.unity", "Assets/Main.unity.meta", "Assets/Unrelated.mat"):
        (root / name).write_text(name, encoding="utf-8")
    target = _target(root, "object")
    # Same typed payload as the real rejected Sync action; only scene/object identities
    # are replaced with a disposable fixture, keeping author shape names and structure.
    request = {
        "projectPath": str(root), "executionTarget": target,
        "avatarPath": "Avatar", "gameObjectPath": "Avatar/Outfit",
        "componentType": "nadena.dev.modular_avatar.core.ModularAvatarBlendshapeSync",
        "fields": {"Bindings": [
            {"ReferenceMesh": {"referencePath": "Body"}, "Blendshape": name, "LocalBlendshape": name}
            for name in ("(Wear bra)Breasts_Big", "(Wear bra)Breasts_Cow")
        ]},
        "saveScene": True,
    }
    observed = []
    monkeypatch.setattr(server, "list_execution_targets_sync",
                        lambda query: observed.append(("discover", deepcopy(query))) or
                        {"ok": True, "targets": [deepcopy(target)]})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync",
                        lambda project, **kwargs: observed.append(("prepare", project, kwargs)) or {"ok": True})
    return server, root, target, request, observed


@pytest.mark.parametrize("save_scene", [True, False])
def test_nested_blendshape_sync_uses_registered_exact_scene_scope(case, save_scene):
    server, root, target, request, observed = case
    request["saveScene"] = save_scene
    original = deepcopy(request)
    core_calls = build_workflow_execution_plan(TOOL, request)
    prepared = server.AGENT_GATEWAY._write_handlers[TOOL].checkpoint_prepare_handler(
        root, {**request, "_checkpointTargetTool": TOOL})
    assert prepared["ok"] is True, prepared
    assert prepared["archiveAssetPaths"] == ["Assets/Main.unity"]
    assert observed[-1] == ("prepare", root, {"checkpoint_files": ["Assets/Main.unity"]})
    assert observed[0][1]["objectGlobalObjectId"] == target["object"]["globalObjectId"]
    assert core_calls == [("vrc_add_modular_avatar_component", {
        "gameObjectPath": "Avatar/Outfit", "avatarPath": "Avatar",
        "componentType": request["componentType"], "fields": request["fields"],
        "preview": False, "saveScene": save_scene,
    })]
    assert request == original


def test_sync_checkpoint_archive_and_restore_exclude_unrelated_assets(case, tmp_path):
    server, root, _target_value, request, _observed = case
    handler = server.AGENT_GATEWAY._write_handlers[TOOL]
    gateway = _gateway(tmp_path / "app")
    service = gateway.approval_transactions
    service.register_write_handler(TOOL, "component", "medium", lambda _: None,
                                   checkpoint_prepare_handler=handler.checkpoint_prepare_handler)
    service._ports = replace(service._ports, run_git=lambda *a, **k:
                             {"ok": False, "returncode": 1, "stdout": "", "error": "not git"})
    checkpoint = service._create_pre_write_checkpoint(
        {"id": "approval", "targetTool": TOOL},
        {**request, "archiveFiles": ["Assets/Unrelated.mat"]})
    assert checkpoint["ok"] is True, checkpoint
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert set(archive.namelist()) == {"Assets/Main.unity", "Assets/Main.unity.meta"}
    (root / "Assets/Main.unity").write_text("changed", encoding="utf-8")
    (root / "Assets/Unrelated.mat").write_text("preserve", encoding="utf-8")
    assert gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)["ok"] is True
    assert (root / "Assets/Main.unity").read_text() == "Assets/Main.unity"
    assert (root / "Assets/Unrelated.mat").read_text() == "preserve"


@pytest.mark.parametrize("kind", ["object", "scene", "editor", "ambiguous"])
def test_sync_checkpoint_rejects_fresh_identity_drift(case, monkeypatch, kind):
    server, root, target, request, observed = case
    fresh = deepcopy(target)
    if kind == "object":
        fresh["object"]["globalObjectId"] = "different-object"
    elif kind == "scene":
        fresh["scene"]["digest"] = "b" * 64
    elif kind == "editor":
        fresh["editor"]["coreInstanceId"] = "different-editor"
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda _:
                        {"ok": True, "targets": [fresh, fresh] if kind == "ambiguous" else [fresh]})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**request, "_checkpointTargetTool": TOOL})
    assert result["ok"] is False and "archiveAssetPaths" not in result
    assert not any(call[0] == "prepare" for call in observed)


@pytest.mark.parametrize("kind", ["wrong_object_path", "outside_avatar", "missing_target", "invalid_scene", "unsaved"])
def test_sync_checkpoint_rejects_unproven_saved_scene_owner(case, kind):
    server, root, target, request, observed = case
    if kind == "wrong_object_path":
        request["gameObjectPath"] = "Avatar/AnotherOutfit"
    elif kind == "outside_avatar":
        target["object"]["exactHierarchyPath"] = request["gameObjectPath"] = "OtherAvatar/Outfit"
    elif kind == "missing_target":
        request.pop("executionTarget")
    elif kind == "invalid_scene":
        target["scene"]["assetPath"] = "Assets/../Outside.unity"
    else:
        target["scene"]["assetPath"] = ""
        target["scene"]["absolutePath"] = ""
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**request, "_checkpointTargetTool": TOOL})
    assert result["ok"] is False and "archiveAssetPaths" not in result
    assert not any(call[0] == "prepare" for call in observed)


@pytest.mark.parametrize("change", [None, "source", "shape", "empty"])
def test_nested_sync_binding_verifier_compares_independent_property_projection(case, change):
    from wardrobe_outfit_workflow_service import finalize_add_modular_avatar_component_verification

    _server, _root, _target_value, request, _observed = case
    bindings = deepcopy(request["fields"]["Bindings"])
    if change == "source":
        bindings[0]["ReferenceMesh"]["referencePath"] = "WrongBody"
    elif change == "shape":
        bindings[0]["Blendshape"] = "WrongShape"
    elif change == "empty":
        bindings = []
    calls = []
    result = finalize_add_modular_avatar_component_verification(
        request,
        {"ok": True, "addedComponent": True, "before": {"count": 0}, "after": {"count": 1}},
        lambda _: {"ok": True, "present": True, "count": 1, "type": request["componentType"],
                   "gameObjectPath": request["gameObjectPath"], "sceneDirty": False, "references": []},
        lambda query: calls.append(query) or {"ok": True, "propertyValue": bindings,
                                              "valueTruncated": False, "valueIncomplete": False},
    )
    assert calls[0]["propertyPath"] == "Bindings"
    assert calls[0]["componentIndex"] == 0
    assert (result.get("verified") is True) is (change is None)
    if change is not None:
        assert result["ok"] is False
