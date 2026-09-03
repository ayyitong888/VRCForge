import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from execution_target import (
    EXECUTION_TARGET_SCHEMA,
    ExecutionTargetBindingRegistry,
    ExecutionTargetError,
    canonical_namespace,
    execution_target_digest,
    project_identity,
    process_start_time,
    standard_identity_metadata,
    validate_execution_target,
    validate_runtime_execution_target,
)


def _target(tmp_path: Path, *, scope: str = "component") -> dict:
    root = str(tmp_path.resolve())
    target = {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": scope,
        "project": {"root": root, "projectId": project_identity(root)},
        "editor": {"unityPid": 1234, "processStartTime": "2026-09-02T00:00:00Z", "coreInstanceId": "core-1"},
    }
    if scope in {"scene", "avatar", "object", "component"}:
        target["scene"] = {"absolutePath": str(tmp_path / "Assets" / "Main.unity"), "guid": "scene-guid", "revision": "7", "digest": "a" * 64}
    if scope in {"avatar", "object", "component"}:
        target["avatar"] = {"globalObjectId": "avatar-global-id", "exactHierarchyPath": "Root/Avatar"}
    if scope in {"object", "component"}:
        target["object"] = {"globalObjectId": "object-global-id", "exactHierarchyPath": "Root/Avatar/Body"}
    if scope == "component":
        target["component"] = {"globalObjectId": "component-global-id", "type": "SkinnedMeshRenderer"}
    target["namespace"] = canonical_namespace(target)
    return target


def test_execution_target_accepts_exact_namespace_and_digest(tmp_path: Path):
    target = _target(tmp_path)
    validated = validate_execution_target(
        target,
        project_root=str(tmp_path),
        core_identity={"processId": 1234, "processStartTime": "2026-09-02T00:00:00Z", "instanceId": "core-1", "sceneRevision": "7"},
        required_scope="component",
    )
    assert validated["namespace"].startswith("vrcforge://projects/")
    assert execution_target_digest(validated)


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda value: value["editor"].update({"unityPid": 9999}), "editor_identity_mismatch"),
        (lambda value: value["scene"].update({"revision": "8"}), "scene_identity_mismatch"),
        (lambda value: value["object"].pop("globalObjectId"), "identity_field_missing"),
        (lambda value: value["scene"].update({"absolutePath": "C:/Other/Other.unity"}), "identity_path_outside_project"),
    ],
)
def test_identity_drift_fails_closed(tmp_path: Path, mutator, code: str):
    target = _target(tmp_path)
    mutator(target)
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_execution_target(
            target,
            project_root=str(tmp_path),
            core_identity={"processId": 1234, "processStartTime": "2026-09-02T00:00:00Z", "instanceId": "core-1", "sceneRevision": "7"},
            required_scope="component",
        )
    assert exc_info.value.code == code


def test_identity_metadata_declares_no_hierarchy_fallback():
    metadata = standard_identity_metadata(scope="object", write=True)
    assert metadata["hierarchyPathFallback"] is False
    assert "object.globalObjectId" in metadata["required"]


def _live_runtime_target(tmp_path: Path) -> dict:
    assets = tmp_path / "Assets"
    assets.mkdir()
    scene = assets / "Main.unity"
    scene.write_bytes(b"%YAML 1.1\n--- !u!1 &1\n")
    scene.with_suffix(".unity.meta").write_text(
        "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    root = str(tmp_path.resolve())
    start = process_start_time(os.getpid())
    descriptor_dir = tmp_path / "Library" / "VRCForge"
    descriptor_dir.mkdir(parents=True)
    descriptor_dir.joinpath("mcp-core.json").write_text(
        json.dumps(
            {
                "projectPath": root,
                "projectId": project_identity(root),
                "processId": os.getpid(),
                "processStartTime": start,
                "instanceId": "core-live",
            }
        ),
        encoding="utf-8",
    )
    stat = scene.stat()
    target = {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": "avatar",
        "project": {"root": root, "projectId": project_identity(root)},
        "editor": {
            "unityPid": os.getpid(),
            "processStartTime": start,
            "coreInstanceId": "core-live",
        },
        "scene": {
            "assetPath": "Assets/Main.unity",
            "absolutePath": str(scene.resolve()),
            "guid": "0123456789abcdef0123456789abcdef",
            "revision": str(621355968000000000 + stat.st_mtime_ns // 100),
            "digest": hashlib.sha256(scene.read_bytes()).hexdigest(),
        },
        "avatar": {
            "globalObjectId": "GlobalObjectId_V1-2-avatar",
            "exactHierarchyPath": "Root/Avatar",
            "name": "Avatar",
        },
        "ambiguous": False,
        "resolutionCandidateCount": 1,
    }
    target["namespace"] = canonical_namespace(target)
    return target


def test_runtime_target_rechecks_descriptor_and_scene_file_identity(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    assert validate_runtime_execution_target(
        target,
        project_root=str(tmp_path),
        required_scope="avatar",
    )["scene"]["digest"] == target["scene"]["digest"]

    stale = copy.deepcopy(target)
    stale["scene"]["digest"] = "f" * 64
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_runtime_execution_target(stale, project_root=str(tmp_path), required_scope="avatar")
    assert exc_info.value.code == "scene_identity_mismatch"


def test_binding_refresh_allows_display_change_but_rejects_avatar_replacement(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    registry = ExecutionTargetBindingRegistry()
    bound = registry.bind(target, project_root=str(tmp_path))

    display_only = copy.deepcopy(target)
    display_only["avatar"]["exactHierarchyPath"] = "Renamed/Avatar"
    display_only["avatar"]["name"] = "Avatar Renamed"
    refreshed = registry.refresh(
        bound["executionTargetHandle"],
        display_only,
        project_root=str(tmp_path),
    )
    assert refreshed["changed"] is False
    assert refreshed["executionTargetDigest"] == bound["executionTargetDigest"]

    replaced = copy.deepcopy(target)
    replaced["avatar"]["globalObjectId"] = "GlobalObjectId_V1-2-replacement"
    replaced["namespace"] = canonical_namespace(replaced)
    with pytest.raises(ExecutionTargetError) as exc_info:
        registry.refresh(
            bound["executionTargetHandle"],
            replaced,
            project_root=str(tmp_path),
        )
    assert exc_info.value.code == "execution_target_identity_drifted"


def test_runtime_target_rejects_descriptor_from_another_project(tmp_path: Path):
    target = _live_runtime_target(tmp_path)
    descriptor_path = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["projectPath"] = str((tmp_path / "Other").resolve())
    descriptor["projectId"] = project_identity(descriptor["projectPath"])
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    with pytest.raises(ExecutionTargetError) as exc_info:
        validate_runtime_execution_target(target, project_root=str(tmp_path), required_scope="avatar")
    assert exc_info.value.code == "project_root_drifted"
