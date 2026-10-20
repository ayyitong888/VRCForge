"""New objects bind their existing container, never the object being created."""
import pytest

from execution_target import ExecutionTargetError, canonical_namespace, project_identity, validate_execution_target
from mcp_tool_descriptor import identity_scope


def scene_target(tmp_path):
    target = {
        "schema": "vrcforge.execution_target.v1", "scope": "scene",
        "project": {"root": str(tmp_path), "projectId": project_identity(str(tmp_path))},
        "editor": {"unityPid": 1234, "processStartTime": "1", "coreInstanceId": "core"},
        "scene": {"absolutePath": str(tmp_path / "Assets/Main.unity"), "guid": "scene", "revision": "1", "digest": "a" * 64},
    }
    target["namespace"] = canonical_namespace(target)
    return target


@pytest.mark.parametrize("arguments", [
    {"name": "Preview_Key_Light", "parentPath": ""},
    {"name": "Preview_Key_Light"},
    {"name": "Preview_Key_Light", "parent_path": "  "},
    {"arguments": {"name": "Preview_Key_Light", "parentPath": ""}},
    {"params": {"name": "Preview_Key_Light", "parentPath": ""}},
])
def test_new_scene_root_accepts_saved_scene_without_existing_object(tmp_path, arguments):
    scope = identity_scope("vrcforge_create_gameobject", write=True, arguments=arguments)
    assert validate_execution_target(scene_target(tmp_path), required_scope=scope)["scope"] == "scene"


@pytest.mark.parametrize("arguments", [
    {"name": "Accessory", "parentPath": "Avatar"},
    {"name": "Accessory", "parent_path": "Avatar"},
    {"arguments": {"name": "Accessory", "parentPath": "Avatar"}},
])
def test_parented_creation_still_requires_existing_parent_identity(tmp_path, arguments):
    with pytest.raises(ExecutionTargetError) as error:
        validate_execution_target(scene_target(tmp_path), required_scope=identity_scope(
            "vrcforge_create_gameobject", write=True, arguments=arguments))
    assert error.value.code == "identity_scope_insufficient"


@pytest.mark.parametrize("name", ["vrcforge_get_gameobject", "vrcforge_delete_gameobject"])
def test_existing_object_operations_do_not_accept_scene_only_identity(tmp_path, name):
    with pytest.raises(ExecutionTargetError) as error:
        validate_execution_target(scene_target(tmp_path), required_scope=identity_scope(name, write=True))
    assert error.value.code == "identity_scope_insufficient"
