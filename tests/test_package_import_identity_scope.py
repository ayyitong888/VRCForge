"""Project asset import precedes any scene or Avatar instance identity."""
import pytest

from execution_target import ExecutionTargetError, canonical_namespace, project_identity, validate_execution_target
from mcp_tool_descriptor import identity_scope, read_runtime_identity_required


def project_target(tmp_path):
    target = {
        "schema": "vrcforge.execution_target.v1", "scope": "project",
        "project": {"root": str(tmp_path), "projectId": project_identity(str(tmp_path))},
        "editor": {"unityPid": 1234, "processStartTime": "1", "coreInstanceId": "core"},
    }
    target["namespace"] = canonical_namespace(target)
    return target


@pytest.mark.parametrize("name", ["vrcforge_import_outfit_package", "vrc_import_unitypackage"])
def test_first_package_import_accepts_project_without_existing_avatar(tmp_path, name):
    result = validate_execution_target(
        project_target(tmp_path), required_scope=identity_scope(name, write=True)
    )
    assert result["scope"] == "project"
    assert "avatar" not in result
    assert "scene" not in result


@pytest.mark.parametrize("name", ["vrcforge_inspect_outfit_package", "vrcforge_plan_outfit_import"])
def test_source_inspection_and_project_import_plan_do_not_require_live_avatar(name):
    assert identity_scope(name) == "project"
    assert read_runtime_identity_required(name) is False


@pytest.mark.parametrize("name", [
    "vrcforge_setup_outfit", "vrc_setup_outfit", "vrcforge_add_outfit",
    "vrcforge_add_wardrobe_outfit",
    "vrcforge_preview_setup_outfit", "vrcforge_preview_add_outfit",
])
def test_existing_avatar_outfit_operations_still_reject_project_only_target(tmp_path, name):
    with pytest.raises(ExecutionTargetError) as error:
        validate_execution_target(project_target(tmp_path), required_scope=identity_scope(name, write=True))
    assert error.value.code == "identity_scope_insufficient"


def test_registered_import_descriptor_advertises_project_scope():
    import dashboard_server

    descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
        "vrcforge_import_outfit_package", write=True
    )
    assert descriptor["requiredIdentity"]["scope"] == "project"
