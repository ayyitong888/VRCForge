from pathlib import Path
import copy

import pytest

from prepared_unity_execution import build_prepared_execution_plan, install_prepared_calls
from unity_blendshape_checkpoint import prepare_blendshape_checkpoint


ROOT = Path("test-project").resolve()
AVATAR = {
    "globalObjectId": "avatar-guid",
    "exactHierarchyPath": "Avatar",
}
SCENE = {"assetPath": "Assets/Scenes/Avatar.unity", "guid": "scene-guid", "digest": "scene-digest"}
PROJECT = {"root": str(ROOT), "projectId": "project-id"}
TARGET = {
    "schema": "vrcforge.execution_target.v1", "scope": "avatar",
    "project": PROJECT, "avatar": AVATAR, "scene": SCENE,
    "editor": {"unityPid": 10, "processStartTime": "start", "coreInstanceId": "core"},
}
RENDERER = {
    **TARGET, "scope": "component",
    "object": {"globalObjectId": "object-guid", "exactHierarchyPath": "Avatar/Body"},
    "component": {"globalObjectId": "component-guid", "type": "UnityEngine.SkinnedMeshRenderer"},
}
ARGUMENTS = {
    "projectPath": str(ROOT), "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": "opaque"},
}
CALLS = [("vrc_apply_blendshapes", {
    "avatarPath": "Avatar", "saveAssets": True,
    "adjustments": [{"rendererPath": "Avatar/Body", "blendshapeName": "JawWide", "targetWeight": 18}],
})]
ARGUMENTS = install_prepared_calls(ARGUMENTS, CALLS, {"avatarPath": "Avatar"})
OWNER_SCOPE = {
    "schema": "vrcforge.component_property_checkpoint.v1", "ownerKind": "scene",
    "scenePath": SCENE["assetPath"], "sceneGuid": SCENE["guid"], "sceneDigest": SCENE["digest"],
    "objectGlobalObjectId": "object-guid", "componentGlobalObjectId": "component-guid",
    "componentType": "UnityEngine.SkinnedMeshRenderer", "componentIndex": 0,
    "propertyPath": "enabled", "archiveAssetPaths": [SCENE["assetPath"]],
}


def run(*, arguments=ARGUMENTS, **overrides):
    captured = {"owner": [], "prepare": []}
    def discover(query):
        assert query["avatarGlobalObjectId"] == AVATAR["globalObjectId"]
        return {"ok": True, "complete": True, "hasMore": False,
                "totalCandidateCount": 1, "candidateSetHash": "set-hash", "targets": [RENDERER]}
    def inspect(query):
        captured["owner"].append(query)
        return {"ok": True, "checkpointScope": OWNER_SCOPE}
    def prepare(root, *, checkpoint_files):
        captured["prepare"].append((root, checkpoint_files))
        return {"ok": True, "checkpointId": "checkpoint"}
    kwargs = dict(
        build_plan=build_prepared_execution_plan,
        resolve_reference=lambda ref, **kw: TARGET,
        discover=discover, inspect_owner=inspect, prepare=prepare,
        required_scope="project",
    )
    kwargs.update(overrides)
    result = prepare_blendshape_checkpoint(ROOT, arguments, **kwargs)
    return result, captured


def test_blendshape_checkpoint_proves_each_renderer_owner_and_archives_only_scene():
    result, captured = run()
    assert result["ok"] is True
    assert result["archiveAssetPaths"] == [SCENE["assetPath"]]
    assert captured["prepare"] == [(ROOT, [SCENE["assetPath"]])]
    assert captured["owner"][0]["gameObjectPath"] == "Avatar/Body"
    assert captured["owner"][0]["componentType"] == "UnityEngine.SkinnedMeshRenderer"
    assert captured["owner"][0]["componentIndex"] == 0
    assert captured["owner"][0]["propertyPath"] == "enabled"
    assert captured["owner"][0]["executionTarget"]["scope"] == "component"
    assert captured["owner"][0]["executionTarget"]["component"]["globalObjectId"] == "component-guid"


def saved_scene_identities():
    bound = copy.deepcopy(TARGET)
    bound["scene"].update(digest="a" * 64, revision="before-save")
    fresh = copy.deepcopy(RENDERER)
    fresh["scene"] = {**bound["scene"], "revision": "after-save"}
    return bound, fresh


def run_saved_scene(bound, fresh):
    return run(
        resolve_reference=lambda *_args, **_kwargs: bound,
        discover=lambda query: {"ok": True, "complete": True, "hasMore": False,
                               "totalCandidateCount": 1, "candidateSetHash": "set-hash", "targets": [fresh]},
        inspect_owner=lambda query: {"ok": True, "checkpointScope": {**OWNER_SCOPE, "sceneDigest": "a" * 64}},
    )


def test_blendshape_checkpoint_accepts_saved_scene_revision_change_with_same_content():
    bound, fresh = saved_scene_identities()
    original = copy.deepcopy(bound)
    result, captured = run_saved_scene(bound, fresh)
    assert result["ok"] is True
    assert captured["prepare"] == [(ROOT, [SCENE["assetPath"]])]
    assert bound == original, "The frozen target must not be refreshed or rewritten."


@pytest.mark.parametrize("section,key,value", [
    ("scene", "digest", "b" * 64), ("scene", "guid", "other-guid"),
    ("scene", "assetPath", "Assets/Other.unity"),
    ("avatar", "globalObjectId", "other-avatar"), ("avatar", "exactHierarchyPath", "OtherAvatar"),
    ("project", "projectId", "other-project"), ("project", "root", str(ROOT / "Other")),
    ("editor", "unityPid", 999), ("editor", "processStartTime", "other-start"),
    ("editor", "coreInstanceId", "other-core"),
    ("object", "exactHierarchyPath", "OtherAvatar/Body"),
])
def test_blendshape_checkpoint_still_rejects_content_or_identity_change(section, key, value):
    bound, fresh = saved_scene_identities()
    fresh[section][key] = value
    result, captured = run_saved_scene(bound, fresh)
    assert result["ok"] is False
    assert captured["prepare"] == []


@pytest.mark.parametrize("digest", [None, "", "not-sha256", "a" * 63, "A" * 64])
def test_scene_revision_exception_requires_a_complete_valid_content_hash(digest):
    bound, fresh = saved_scene_identities()
    bound["scene"]["digest"] = fresh["scene"]["digest"] = digest
    result, captured = run_saved_scene(bound, fresh)
    assert result["ok"] is False and captured["prepare"] == []


def test_scene_revision_exception_does_not_ignore_additional_scene_fields():
    bound, fresh = saved_scene_identities()
    fresh["scene"]["unexpectedIdentityField"] = "changed"
    result, captured = run_saved_scene(bound, fresh)
    assert result["ok"] is False and captured["prepare"] == []


def test_shared_scene_identity_comparison_rejects_malformed_sections():
    from execution_target import scene_identity_matches
    assert scene_identity_matches({}, None) is False
    assert scene_identity_matches([], {}) is False


def test_blendshape_checkpoint_rejects_owner_or_identity_drift_before_prepare():
    bad_scope = {**OWNER_SCOPE, "sceneGuid": "different-scene"}
    def inspect(_query):
        return {"ok": True, "checkpointScope": bad_scope}
    result, captured = run(inspect_owner=inspect)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert captured["prepare"] == []


def test_blendshape_checkpoint_accepts_avatar_root_renderer_with_exact_component_identity():
    root_renderer = {
        **RENDERER,
        "object": {"globalObjectId": "object-guid", "exactHierarchyPath": "Avatar"},
        "component": {"globalObjectId": "component-guid", "type": "UnityEngine.SkinnedMeshRenderer",
                      "exactHierarchyPath": "Avatar"},
    }
    calls = [("vrc_apply_blendshapes", {
        "avatarPath": "Avatar", "saveAssets": True,
        "adjustments": [{"rendererPath": "Avatar", "blendshapeName": "JawWide", "targetWeight": 18}],
    })]
    arguments = install_prepared_calls(
        {key: value for key, value in ARGUMENTS.items() if key != "_vrcforge_prepared_unity_execution"},
        calls, {"avatarPath": "Avatar"},
    )
    result, captured = run(
        arguments=arguments,
        discover=lambda _query: {"ok": True, "complete": True, "hasMore": False,
                                 "totalCandidateCount": 1, "candidateSetHash": "root-set", "targets": [root_renderer]},
    )
    assert result["ok"] is True
    assert captured["owner"][0]["gameObjectPath"] == "Avatar"


def test_blendshape_checkpoint_rejects_incomplete_or_ambiguous_renderer_discovery():
    def incomplete(_query):
        return {"ok": True, "complete": False, "hasMore": True,
                "totalCandidateCount": 2, "candidateSetHash": "set-hash", "targets": [RENDERER]}
    result, captured = run(discover=incomplete)
    assert result["ok"] is False and captured["prepare"] == []
    def ambiguous(_query):
        return {"ok": True, "complete": True, "hasMore": False,
                "totalCandidateCount": 2, "candidateSetHash": "set-hash", "targets": [RENDERER, RENDERER]}
    result, captured = run(discover=ambiguous)
    assert result["ok"] is False and captured["prepare"] == []


def test_blendshape_checkpoint_preserves_authoritative_read_failures():
    def owner_failure(_query):
        return {"ok": False, "code": "managed_write_required", "failureLayer": "core", "error": "Read-only inspection blocked."}
    result, captured = run(inspect_owner=owner_failure)
    assert result["code"] == "managed_write_required"
    assert result["failureLayer"] == "core" and result["blocking"] is True
    assert captured["prepare"] == []
    def discovery_failure(_query):
        return {"ok": False, "code": "editor_not_ready", "error": "Core is reconnecting."}
    result, captured = run(discover=discovery_failure)
    assert result["code"] == "editor_not_ready" and captured["prepare"] == []


def test_blendshape_checkpoint_rejects_path_scope_or_seal_mismatch():
    bad_calls = [("vrc_apply_blendshapes", {**CALLS[0][1], "avatarPath": "OtherAvatar"})]
    bad_arguments = install_prepared_calls(
        {key: value for key, value in ARGUMENTS.items() if key != "_vrcforge_prepared_unity_execution"},
        bad_calls, {"avatarPath": "OtherAvatar"},
    )
    result, captured = run(arguments=bad_arguments)
    assert result["ok"] is False and captured["prepare"] == []
    result, captured = run(resolve_reference=lambda *_args, **_kwargs: {**TARGET, "avatar": {}})
    assert result["ok"] is False and captured["prepare"] == []


def test_dashboard_checkpoint_materializes_public_ref_and_uses_private_component_owner(monkeypatch):
    import dashboard_server

    captured = {}
    def resolve(reference, **kwargs):
        captured["resolve"] = (reference, kwargs)
        return TARGET
    def discover(query):
        captured["discover"] = query
        return {"ok": True, "complete": True, "hasMore": False,
                "totalCandidateCount": 1, "candidateSetHash": "set-hash", "targets": [RENDERER]}
    def inspect_owner(query):
        captured["owner"] = query
        return {"ok": True, "checkpointScope": OWNER_SCOPE}
    def prepare(root, **kwargs):
        captured["prepare"] = (root, kwargs)
        return {"ok": True}

    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "resolve_execution_target_reference", resolve)
    monkeypatch.setattr(dashboard_server, "list_execution_targets_sync", discover)
    monkeypatch.setattr(dashboard_server, "read_component_property_sync", inspect_owner)
    monkeypatch.setattr(dashboard_server, "prepare_unity_checkpoint_sync", prepare)
    request = {**ARGUMENTS, "_checkpointTargetTool": "vrcforge_apply_blendshapes"}
    result = dashboard_server.prepare_authoritative_unity_checkpoint_sync(ROOT, request)
    assert result["ok"] is True
    assert captured["resolve"][0] == ARGUMENTS["executionTarget"]
    assert captured["resolve"][1]["project_root"] == str(ROOT)
    assert captured["resolve"][1]["required_scope"] == "project"
    assert captured["owner"]["executionTarget"]["scope"] == "component"
    assert captured["owner"]["executionTarget"]["component"]["globalObjectId"] == "component-guid"
    assert captured["prepare"] == (ROOT, {"checkpoint_files": [SCENE["assetPath"]]})
    assert request["executionTarget"] == ARGUMENTS["executionTarget"]
