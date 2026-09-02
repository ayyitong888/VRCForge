from pathlib import Path

import pytest

from execution_target import (
    EXECUTION_TARGET_SCHEMA,
    ExecutionTargetError,
    canonical_namespace,
    execution_target_digest,
    project_identity,
    standard_identity_metadata,
    validate_execution_target,
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
