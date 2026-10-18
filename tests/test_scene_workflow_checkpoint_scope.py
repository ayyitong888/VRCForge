"""Exact saved-scene checkpoint scope for proven scene-only Core workflows."""
from copy import deepcopy

import pytest

from unity_execution_plans_scene import build_scene_execution_plan
from unity_execution_plans_workflows import build_workflow_execution_plan
from unity_scene_workflow_checkpoint import prepare_scene_workflow_checkpoint
from prepared_unity_execution import install_prepared_calls


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
    (root / "Assets/Main.unity.meta").write_text("guid: "+"a"*32, encoding="utf-8")
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


def _installation(root):
    target = _target(root)
    owner = {"globalObjectId":"avatar-id", "gameObjectPath":"Avatar",
             "scenePath":"Assets/Main.unity", "children":[]}
    asset = {"assetPath":"Assets/Outfit.prefab", "guid":"b"*32, "dependencyHash":"c"*32}
    nonce = "d"*64
    calls = [
        ("vrc_instantiate_prefab", {
            "projectPath":str(root), "assetPath":asset["assetPath"], "guid":asset["guid"],
            "parentPath":"Avatar", "name":"Outfit", "worldPositionStays":True,
            "expectedPrefabGuid":asset["guid"], "expectedAssetDependencyHash":asset["dependencyHash"],
            "expectedScenePath":"Assets/Main.unity", "expectedParentGlobalObjectId":"avatar-id",
            "expectedResultPath":"Avatar/Outfit", "preview":False,
            "approvedObjectReceiptNonce":nonce, "approvedContinuationTools":["vrc_setup_outfit"],
        }),
        ("vrc_setup_outfit", {"projectPath":str(root), "avatarPath":"Avatar", "outfitPath":"Avatar/Outfit",
            "confirmSetup":True, "saveScene":True, "approvedObjectReceiptNonce":nonce}),
    ]
    evidence = {"schema":"vrcforge.prepared-add-outfit.v1", "projectIdentity":{"projectPath":str(root)},
        "readFacts":{"asset":asset,"avatar":deepcopy(owner),"parent":deepcopy(owner),"outfitPath":"Avatar/Outfit"},
        "manageWardrobe":False, "setupOutfit":True, "unpackPrefab":False, "outfitName":"Outfit"}
    return target, calls, evidence


def _run_installation(root, monkeypatch, *, mutate=None, drift=False):
    import dashboard_server as dashboard
    target, calls, evidence = _installation(root)
    if mutate is not None:
        mutate(calls,evidence)
    args = install_prepared_calls({"executionTarget":target,"_checkpointTargetTool":"vrcforge_add_outfit"},calls,evidence)
    fresh = deepcopy(target)
    if drift:
        fresh["scene"]["digest"] = "e"*64
    observed = []
    monkeypatch.setattr(dashboard,"list_execution_targets_sync",lambda _query:{"ok":True,"targets":[fresh]})
    monkeypatch.setattr(dashboard,"prepare_unity_checkpoint_sync",lambda _root,**kw:observed.append(kw) or {"ok":True})
    return dashboard.prepare_authoritative_unity_checkpoint_sync(root,args),observed


def test_sealed_install_then_setup_prepares_only_exact_scene(scene_case,monkeypatch):
    result,observed = _run_installation(scene_case,monkeypatch)
    assert result["ok"] is True,result
    assert result["archiveAssetPaths"] == ["Assets/Main.unity"]
    assert observed == [{"checkpoint_files":["Assets/Main.unity"]}]


@pytest.mark.parametrize("mutate",[
    lambda calls,evidence:evidence.update(manageWardrobe=True),
    lambda calls,evidence:evidence.update(unpackPrefab=True),
    lambda calls,evidence:calls.append(("vrc_add_wardrobe_outfit",{})),
    lambda calls,evidence:calls[1][1].update(outfitPath="Other/Outfit"),
    lambda calls,evidence:calls[1][1].update(saveScene=False),
    lambda calls,evidence:calls[1][1].update(approvedObjectReceiptNonce="e"*64),
    lambda calls,evidence:calls[0][1].update(expectedScenePath="Assets/Other.unity"),
    lambda calls,evidence:evidence["readFacts"]["avatar"].update(globalObjectId="other-avatar"),
    lambda calls,evidence:evidence["readFacts"]["parent"].update(globalObjectId="other-parent"),
])
def test_installation_checkpoint_rejects_asset_workflows_or_identity_drift(scene_case,monkeypatch,mutate):
    result,observed = _run_installation(scene_case,monkeypatch,mutate=mutate)
    assert result["ok"] is False and result["mutationStarted"] is False
    assert observed == []


def test_installation_checkpoint_revalidates_scene_after_preparation(scene_case,monkeypatch):
    result,observed = _run_installation(scene_case,monkeypatch,drift=True)
    assert result["ok"] is False and result["mutationStarted"] is False
    assert observed == []


def test_installation_checkpoint_archive_does_not_fall_back_to_whole_project(scene_case):
    import zipfile
    from agent_gateway import AgentGateway
    from unity_scene_workflow_checkpoint import prepare_add_outfit_checkpoint
    for folder in ("Packages","ProjectSettings"):
        (scene_case/folder).mkdir()
    (scene_case/"Assets/Main.unity.meta").write_text("guid: "+"a"*32)
    (scene_case/"Assets/Unrelated.txt").write_text("keep unrelated file")
    target,calls,evidence = _installation(scene_case)
    sealed = install_prepared_calls({"projectPath":str(scene_case),"executionTarget":target},calls,evidence)
    gateway = AgentGateway(scene_case.parent/"app/config.json",scene_case.parent/"app/audit")
    service = gateway.approval_transactions
    service.register_write_handler(
        "vrcforge_add_outfit","Fixture scene-only installation","high",lambda _args:{"ok":True},
        request_preparer=lambda _args,_preview:(sealed,{}),
        checkpoint_prepare_handler=lambda root,args:prepare_add_outfit_checkpoint(
            root,args,discover=lambda _query:{"ok":True,"targets":[target]},
            prepare=lambda _root,**_kw:{"ok":True}),
    )
    created = service.create_apply_request({"target_tool":"vrcforge_add_outfit","arguments":{"projectPath":str(scene_case)}})
    approval_id = created["approval"]["id"]
    service.approve(approval_id)
    applied = service.apply_approved({"approval_id":approval_id})
    assert applied["ok"] is True,applied
    checkpoint = applied["checkpoint"]
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert sorted(archive.namelist()) == ["Assets/Main.unity","Assets/Main.unity.meta"]


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


@pytest.mark.parametrize("active", [True, False])
def test_exact_object_visibility_prepares_authoritative_scene_scope(scene_case, monkeypatch, active):
    import dashboard_server as dashboard
    target = _target(scene_case, "object")
    observed = []
    monkeypatch.setattr(dashboard, "list_execution_targets_sync", lambda query: observed.append(query) or {"ok": True, "targets": [target]})
    monkeypatch.setattr(dashboard, "prepare_unity_checkpoint_sync", lambda root, **kw: observed.append(kw) or {"ok": True})
    result = dashboard.prepare_authoritative_unity_checkpoint_sync(scene_case, {
        "executionTarget": target, "_checkpointTargetTool": "vrcforge_set_gameobject_active",
        "gameObjectPath": "Avatar/Outfit", "active": active,
    })
    assert result.get("ok") is True, result
    assert result["archiveAssetPaths"] == ["Assets/Main.unity"]
    assert observed[-1] == {"checkpoint_files": ["Assets/Main.unity"]}


@pytest.mark.parametrize("scope,path", [("avatar", "Avatar/Outfit"), ("object", "Avatar/Other")])
def test_visibility_rejects_nonexact_object_before_discovery(scene_case, scope, path):
    result, observed = _run(scene_case, "vrcforge_set_gameobject_active", {
        "executionTarget": _target(scene_case, scope), "gameObjectPath": path, "active": False,
    })
    assert result.get("ok") is False and observed == []


def test_visibility_revalidates_exact_owner_before_checkpoint(scene_case):
    target = _target(scene_case, "object")
    changed = deepcopy(target)
    changed["object"]["globalObjectId"] = "different-object"
    result, observed = _run(scene_case, "vrcforge_set_gameobject_active", {
        "executionTarget": target, "gameObjectPath": "Avatar/Outfit", "active": False,
    }, fresh={"ok": True, "targets": [changed]})
    assert result.get("ok") is False
    assert [item[0] for item in observed] == ["discover"]


@pytest.mark.parametrize("core_request", [
    {"gameObjectPath": "Avatar/Outfit", "preview": False},
    {"gameObjectPath": "Avatar/Outfit", "active": "false", "preview": False},
    {"gameObjectPath": "Avatar/Outfit", "active": False, "preview": True},
    {"gameObjectPath": "Avatar/Outfit", "active": False, "preview": False, "extraCall": {}},
])
def test_visibility_rejects_nonexact_core_effect(scene_case, core_request):
    result, observed = _run(scene_case, "vrcforge_set_gameobject_active", {
        "executionTarget": _target(scene_case, "object"),
    }, plan=lambda _tool, _args: [("vrc_set_gameobject_active", core_request)])
    assert result.get("ok") is False and observed == []


def test_visibility_rejects_object_outside_bound_avatar(scene_case):
    target = _target(scene_case, "object")
    target["object"]["exactHierarchyPath"] = "Other/Outfit"
    result, observed = _run(scene_case, "vrcforge_set_gameobject_active", {
        "executionTarget": target, "gameObjectPath": "Other/Outfit", "active": False,
    })
    assert result.get("ok") is False and observed == []
