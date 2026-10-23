"""First scene creation binds the project and its authoritative unsaved-scene preview."""
import pytest

from execution_target import ExecutionTargetError, canonical_namespace, project_identity, validate_execution_target
from mcp_tool_descriptor import identity_scope


def project_target(tmp_path):
    value = {
        "schema": "vrcforge.execution_target.v1", "scope": "project",
        "project": {"root": str(tmp_path), "projectId": project_identity(str(tmp_path))},
        "editor": {"unityPid": 1234, "processStartTime": "1", "coreInstanceId": "core"},
    }
    value["namespace"] = canonical_namespace(value)
    return value


@pytest.mark.parametrize("name", ["vrcforge_save_new_scene", "vrc_save_new_scene"])
def test_first_save_accepts_project_without_a_scene_asset_identity(tmp_path, name):
    target = project_target(tmp_path)
    result = validate_execution_target(target, required_scope=identity_scope(name, write=True))
    assert result["scope"] == "project"
    assert "scene" not in result


@pytest.mark.parametrize("name", ["vrcforge_scene_save", "vrcforge_save_current_scene", "vrc_scene_save"])
def test_existing_scene_save_still_requires_saved_scene_identity(tmp_path, name):
    with pytest.raises(ExecutionTargetError) as error:
        validate_execution_target(project_target(tmp_path), required_scope=identity_scope(name, write=True))
    assert error.value.code == "identity_scope_insufficient"


def test_registered_first_save_advertises_the_project_scope():
    import dashboard_server
    descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor("vrcforge_save_new_scene", write=True)
    assert descriptor["requiredIdentity"]["scope"] == "project"
