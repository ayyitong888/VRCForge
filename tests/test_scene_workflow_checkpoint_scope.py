"""Exact saved-scene checkpoint scope for proven scene-only Core workflows."""
from copy import deepcopy

import pytest

from unity_execution_plans_scene import build_scene_execution_plan
from unity_execution_plans_workflows import build_workflow_execution_plan
from unity_scene_workflow_checkpoint import prepare_scene_workflow_checkpoint


def _target(root, scope="avatar"):
    target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": scope,
        "project": {"root": str(root), "projectId": "project-id"},
        "editor": {"unityPid": 42, "processStartTime": "1.25", "coreInstanceId": "core"},
        "scene": {
            "assetPath": "Assets/Main.unity",
            "absolutePath": str((root / "Assets/Main.unity").resolve()),
            "guid": "scene-guid",
            "revision": "1",
            "digest": "a" * 64,
        },
        "avatar": {"globalObjectId": "avatar-id", "exactHierarchyPath": "Avatar"},
        "ambiguous": False,
        "resolutionCandidateCount": 1,
        "namespace": "vrcforge://projects/project-id/scenes/scene-guid/avatars/avatar-id",
    }
    if scope in {"object", "component"}:
        target["object"] = {
            "globalObjectId": "object-id",
            "exactHierarchyPath": "Avatar/Outfit",
        }
    if scope == "component":
        target["component"] = {
            "globalObjectId": "component-id",
            "type": "UnityEngine.Transform",
        }
    return target


@pytest.fixture
def scene_case(tmp_path):
    root = tmp_path / "project"
    (root / "Assets").mkdir(parents=True)
    (root / "Assets/Main.unity").write_text("scene", encoding="utf-8")
    return root


def _plan(tool, arguments):
    if tool == "vrcforge_setup_outfit":
        return build_workflow_execution_plan(tool, arguments)
    return build_scene_execution_plan(tool, arguments)


def _run(root, tool, arguments, *, fresh=None, plan=_plan, prepared=None):
    target = arguments["executionTarget"]
    observed = []

    def discover(query):
        observed.append(("discover", query))
        return fresh if fresh is not None else {"ok": True, "targets": [deepcopy(target)]}

    def prepare(project_root, checkpoint_asset_paths=None, *, checkpoint_files):
        observed.append(("prepare", project_root, checkpoint_files))
        return deepcopy(prepared) if prepared is not None else {"ok": True, "mode": "read_only"}

    result = prepare_scene_workflow_checkpoint(
        root,
        arguments,
        checkpoint_target=tool,
        build_plan=plan,
        discover=discover,
        prepare=prepare,
    )
    return result, observed


@pytest.mark.parametrize(
    "tool,scope,values,expected_query",
    [
        (
            "vrcforge_setup_outfit",
            "avatar",
            {"avatarPath": "Avatar", "outfitPath": "Avatar/Outfit", "saveScene": True},
            {"avatarGlobalObjectId": "avatar-id"},
        ),
        (
            "vrcforge_create_gameobject",
            "avatar",
            {"name": "Child", "parentPath": "Avatar"},
            {"avatarGlobalObjectId": "avatar-id"},
        ),
        (
            "vrcforge_instantiate_prefab",
            "object",
            {"assetPath": "Assets/Item.prefab", "parentPath": "Avatar/Outfit"},
            {"avatarGlobalObjectId": "avatar-id", "objectGlobalObjectId": "object-id"},
        ),
        (
            "vrcforge_unpack_prefab",
            "component",
            {"gameObjectPath": "Avatar/Outfit"},
            {
                "avatarGlobalObjectId": "avatar-id",
                "objectGlobalObjectId": "object-id",
                "componentGlobalObjectId": "component-id",
                "componentType": "UnityEngine.Transform",
            },
        ),
    ],
)
def test_scene_only_workflow_prepares_exact_revalidated_scene(
    scene_case, tool, scope, values, expected_query
):
    target = _target(scene_case, scope)
    arguments = {
        "projectPath": str(scene_case),
        "executionTarget": target,
        "checkpointScope": {"archiveAssetPaths": ["Assets/Forged.asset"]},
        "archiveFiles": ["Assets/Forged.asset"],
        **values,
    }
    before = deepcopy(arguments)
    result, observed = _run(scene_case, tool, arguments)

    assert result["ok"] is True, result
    assert result["archiveAssetPaths"] == ["Assets/Main.unity"]
    assert result["archiveScopeReason"] == "revalidated_scene_only_workflow_owner"
    assert observed[0] == (
        "discover",
        {"projectPath": str(scene_case), "scope": scope, **expected_query},
    )
    assert observed[1] == ("prepare", scene_case, ["Assets/Main.unity"])
    assert arguments == before


@pytest.mark.parametrize(
    "tool,scope,values",
    [
        ("vrcforge_setup_outfit", "avatar", {"avatarPath": "Other", "outfitPath": "Other/Outfit"}),
        ("vrcforge_setup_outfit", "avatar", {"avatarPath": "Avatar", "outfitPath": "Other/Outfit"}),
        ("vrcforge_setup_outfit", "avatar", {"avatarPath": "Avatar", "outfitPath": "Avatar/Outfit", "saveScene": False}),
        ("vrcforge_create_gameobject", "scene", {"name": "Child", "parentPath": ""}),
        ("vrcforge_create_gameobject", "avatar", {"name": "Child", "parentPath": "Other"}),
        ("vrcforge_instantiate_prefab", "avatar", {"assetPath": "Assets/Item.prefab", "parentPath": ""}),
        ("vrcforge_instantiate_prefab", "object", {"assetPath": "Assets/Item.prefab", "parentPath": "Avatar/Other"}),
        ("vrcforge_unpack_prefab", "avatar", {"gameObjectPath": "Avatar/Outfit"}),
        ("vrcforge_unpack_prefab", "object", {"gameObjectPath": "Avatar/Other"}),
    ],
)
def test_unbound_or_ambiguous_selectors_fail_before_discovery(scene_case, tool, scope, values):
    arguments = {"executionTarget": _target(scene_case, scope), **values}
    result, observed = _run(scene_case, tool, arguments)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert observed == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda target, root: target["project"].update(root=str(root / "Other")),
        lambda target, root: target["editor"].update(coreInstanceId=""),
        lambda target, root: target["scene"].update(assetPath="Assets/../Outside.unity"),
        lambda target, root: target["scene"].update(absolutePath=str(root / "Outside.unity")),
        lambda target, root: target["scene"].update(guid=""),
        lambda target, root: target["scene"].update(digest="wrong"),
        lambda target, root: target.update(ambiguous=True),
    ],
)
def test_incomplete_or_outside_identity_fails_closed(scene_case, mutate):
    target = _target(scene_case)
    mutate(target, scene_case)
    result, observed = _run(
        scene_case,
        "vrcforge_create_gameobject",
        {"executionTarget": target, "name": "Child", "parentPath": "Avatar"},
    )
    assert result["ok"] is False and "archiveAssetPaths" not in result
    assert observed == []


@pytest.mark.parametrize("drift", ["editor", "scene", "avatar", "owner"])
def test_fresh_identity_or_scene_owner_drift_blocks_checkpoint(scene_case, drift):
    target = _target(scene_case, "object")
    fresh_target = deepcopy(target)
    if drift == "editor":
        fresh_target["editor"]["coreInstanceId"] = "other-core"
    elif drift == "scene":
        fresh_target["scene"]["digest"] = "b" * 64
    elif drift == "avatar":
        fresh_target["avatar"]["globalObjectId"] = "other-avatar"
    else:
        fresh_target["scene"]["assetPath"] = "Assets/Other.unity"
    result, observed = _run(
        scene_case,
        "vrcforge_unpack_prefab",
        {"executionTarget": target, "gameObjectPath": "Avatar/Outfit"},
        fresh={"ok": True, "targets": [fresh_target]},
    )
    assert result["ok"] is False and "archiveAssetPaths" not in result
    assert [item[0] for item in observed] == ["discover"]


@pytest.mark.parametrize(
    "plan",
    [
        lambda _tool, _arguments: [],
        lambda _tool, _arguments: [("vrc_create_gameobject", {}), ("vrc_create_gameobject", {})],
        lambda _tool, _arguments: [("vrc_delete_gameobject", {})],
    ],
)
def test_unsupported_or_non_exact_plan_fails_closed(scene_case, plan):
    result, observed = _run(
        scene_case,
        "vrcforge_create_gameobject",
        {"executionTarget": _target(scene_case), "name": "Child", "parentPath": "Avatar"},
        plan=plan,
    )
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert observed == []


def test_core_discovery_failure_preserves_reason_and_never_prepares(scene_case):
    target = _target(scene_case)
    result, observed = _run(
        scene_case,
        "vrcforge_create_gameobject",
        {"executionTarget": target, "name": "Child", "parentPath": "Avatar"},
        fresh={"ok": False, "code": "core_busy", "error": "Core is compiling."},
    )
    assert result["code"] == "core_busy" and result["error"] == "Core is compiling."
    assert result["blocking"] is True and result["mutationStarted"] is False
    assert [item[0] for item in observed] == ["discover"]


def test_prepare_failure_is_returned_without_archive_offer(scene_case):
    result, observed = _run(
        scene_case,
        "vrcforge_create_gameobject",
        {"executionTarget": _target(scene_case), "name": "Child", "parentPath": "Avatar"},
        prepared={"ok": False, "code": "checkpoint_failed", "error": "save failed"},
    )
    assert result == {"ok": False, "code": "checkpoint_failed", "error": "save failed"}
    assert [item[0] for item in observed] == ["discover", "prepare"]
