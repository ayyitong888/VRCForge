from __future__ import annotations

import pytest


@pytest.fixture
def checkpoint_server(tmp_path, monkeypatch):
    import dashboard_server as server

    root = tmp_path / "project"
    (root / "Assets").mkdir(parents=True)
    prepared = []

    def prepare(project, *, checkpoint_files):
        prepared.append((checkpoint_files, {}))
        return {"ok": True, "assetBaseline": []}

    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", prepare)
    return server, root, prepared


def test_runtime_sealed_blendshape_call_uses_exact_scene_checkpoint(checkpoint_server, monkeypatch):
    from prepared_unity_execution import install_prepared_calls

    server, root, prepared = checkpoint_server
    scene = {"assetPath": "Assets/Avatar.unity", "guid": "scene-guid", "digest": "a" * 64}
    avatar = {"globalObjectId": "avatar-id", "exactHierarchyPath": "Avatar"}
    project = {"root": str(root), "projectId": "project-id"}
    editor = {"unityPid": 123, "processStartTime": "1", "coreInstanceId": "core-id"}
    target = {"schema": "vrcforge.execution_target.v1", "scope": "avatar",
              "ambiguous": False, "resolutionCandidateCount": 1,
              "project": project, "editor": editor, "scene": scene, "avatar": avatar}
    renderer_path = "Avatar/Face"
    renderer = {**target, "scope": "component",
                "object": {"globalObjectId": "renderer-object-id", "exactHierarchyPath": renderer_path},
                "component": {"globalObjectId": "renderer-component-id",
                              "type": "UnityEngine.SkinnedMeshRenderer",
                              "exactHierarchyPath": renderer_path}}
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda _query: {
        "ok": True, "targets": [renderer], "totalCandidateCount": 1,
        "candidateSetHash": "renderer-set", "complete": True, "hasMore": False})
    monkeypatch.setattr(server, "read_component_property_sync", lambda _query: {
        "ok": True, "checkpointScope": {
            "schema": "vrcforge.component_property_checkpoint.v1", "ownerKind": "scene",
            "scenePath": scene["assetPath"], "sceneGuid": scene["guid"], "sceneDigest": scene["digest"],
            "objectGlobalObjectId": "renderer-object-id", "componentGlobalObjectId": "renderer-component-id",
            "componentType": "UnityEngine.SkinnedMeshRenderer", "componentIndex": 0,
            "propertyPath": "enabled", "archiveAssetPaths": [scene["assetPath"]]}})
    args = install_prepared_calls(
        {"projectPath": str(root), "executionTarget": target, "avatar": "Avatar", "scope": "face"},
        [("vrc_apply_blendshapes", {"saveAssets": True, "avatarPath": "Avatar",
          "adjustments": [{"rendererPath": renderer_path, "blendshapeName": "Smile", "value": 0.2}]})],
        {"source": "runtime-derived-face-plan"})
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_run_face_tuning"})
    assert result["ok"] is True, result
    assert result["archiveAssetPaths"] == [scene["assetPath"]]
    assert prepared and prepared[0][0] == [scene["assetPath"]]


def test_mixed_runtime_plan_containing_blendshape_fails_closed(checkpoint_server):
    from prepared_unity_execution import install_prepared_calls

    server, root, prepared = checkpoint_server
    args = install_prepared_calls(
        {"projectPath": str(root), "avatar": "Avatar"},
        [("vrc_apply_blendshapes", {"saveAssets": True, "avatarPath": "Avatar",
          "adjustments": [{"rendererPath": "Avatar/Face"}]}), ("vrc_other_write", {})], {})
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, "_checkpointTargetTool": "vrcforge_run_face_tuning"})
    assert result["ok"] is False and result["blocking"] is True
    assert prepared == []
