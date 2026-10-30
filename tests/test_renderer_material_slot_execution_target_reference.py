from copy import deepcopy
from pathlib import Path

import pytest

import dashboard_server
from execution_target import EXECUTION_TARGET_REF_SCHEMA, EXECUTION_TARGET_SCHEMA
from renderer_material_slot_assignment import TOOL_NAME


def _reference():
    return {"schema": EXECUTION_TARGET_REF_SCHEMA, "ref": "vrcforge-target-synthetic-avatar"}


def _request(project_root):
    return {
        "projectPath": str(project_root),
        "executionTarget": _reference(),
        "assignments": [
            {
                "rendererPath": "Avatar/Body",
                "rendererComponentId": "a" * 64,
                "slotIndex": 0,
                "newMaterialAssetPath": "Assets/New.mat",
            }
        ],
    }


def _resolved_avatar(project_root):
    scene_path = project_root / "Assets" / "Main.unity"
    return {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": "avatar",
        "project": {"root": str(project_root)},
        "editor": {"unityPid": 1234, "processStartTime": "1", "coreInstanceId": "synthetic"},
        "scene": {"absolutePath": str(scene_path), "guid": "1" * 32, "digest": "2" * 64},
        "avatar": {"globalObjectId": "avatar-id", "exactHierarchyPath": "Avatar"},
    }


def _preview():
    row = _request(Path("D:/SyntheticUnity"))["assignments"][0]
    before_material = {
        "name": "Old", "shader": "lilToon", "renderQueue": 2000,
        "assetPath": "Assets/Old.mat", "assetGuid": "b" * 32, "fileDigest": "c" * 64,
    }
    new_material = {
        "name": "New", "shader": "lilToon", "renderQueue": 2000,
        "assetPath": "Assets/New.mat", "assetGuid": "d" * 32, "fileDigest": "e" * 64,
    }
    before = {
        "rendererPath": row["rendererPath"],
        "rendererComponentId": row["rendererComponentId"],
        "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
        "rendererComponentIndex": 0,
        "slots": ["Assets/Old.mat|" + "b" * 32],
    }
    after = deepcopy(before)
    after["slots"][0] = "Assets/New.mat|" + "d" * 32
    plan = {
        "scene": {
            "scenePath": "Assets/Main.unity", "sceneGuid": "1" * 32,
            "sceneHandle": 1,
        },
        "beforeRenderers": [before],
        "afterRenderers": [after],
        "assignments": [{**row, "beforeMaterial": before_material, "newMaterial": new_material}],
    }
    return {
        "schema": "vrcforge.renderer_material_slot.v1", "batch": True, "preview": True,
        "verified": True, "mutationStarted": False, "applied": False, "committed": False,
        "sceneSaved": False, "persistedReadback": False, "plan": plan,
    }


def test_expanded_target_keeps_existing_preparer_path(monkeypatch, tmp_path):
    (tmp_path / "Assets").mkdir()
    request = _request(tmp_path)
    resolved = _resolved_avatar(tmp_path)
    request["executionTarget"] = resolved
    monkeypatch.setattr(
        dashboard_server.AGENT_GATEWAY,
        "resolve_execution_target_reference",
        lambda *_args, **_kwargs: pytest.fail("expanded targets must not be resolved as references"),
    )
    monkeypatch.setattr(dashboard_server, "_invoke_authoritative_unity_preview", lambda *_args: _preview())

    canonical, approval = dashboard_server.prepare_renderer_material_slot_request(request, None)

    assert canonical["executionTarget"] == resolved
    assert approval["batch"] is True
