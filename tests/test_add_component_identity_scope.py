"""Adding a component binds its existing object; editing/removal binds the component."""
import pytest

from execution_target import ExecutionTargetError, canonical_namespace, project_identity, validate_execution_target
from mcp_tool_descriptor import identity_scope


def object_target(tmp_path):
    target = {
        'schema': 'vrcforge.execution_target.v1', 'scope': 'object',
        'project': {'root': str(tmp_path), 'projectId': project_identity(str(tmp_path))},
        'editor': {'unityPid': 1234, 'processStartTime': '1', 'coreInstanceId': 'core'},
        'scene': {'absolutePath': str(tmp_path/'Assets/Main.unity'), 'guid': 'scene', 'revision': '1', 'digest': 'a'*64},
        'object': {'globalObjectId': 'object-id', 'exactHierarchyPath': 'Avatar/Clothing'},
    }
    target['namespace'] = canonical_namespace(target)
    return target


@pytest.mark.parametrize('name', [
    'vrcforge_add_component', 'vrcforge_add_modular_avatar_component',
    'vrcforge_preview_add_modular_avatar_component',
    'vrcforge_create_component_feature', 'vrcforge_preview_component_feature',
])
def test_add_component_accepts_exact_object_without_existing_component(tmp_path, name):
    target = object_target(tmp_path)
    scope = identity_scope(name, write=True, arguments={
        'gameObjectPath': 'Avatar/Clothing', 'componentType': 'UnityEngine.CanvasGroup'})
    assert validate_execution_target(target, required_scope=scope)['scope'] == 'object'
    assert 'component' not in target


@pytest.mark.parametrize('name', ['vrcforge_remove_component', 'vrcforge_set_property'])
def test_existing_component_operations_still_reject_object_only_identity(tmp_path, name):
    with pytest.raises(ExecutionTargetError) as error:
        validate_execution_target(object_target(tmp_path), required_scope=identity_scope(name, write=True))
    assert error.value.code == 'identity_scope_insufficient'


def test_add_component_still_requires_exact_object_identity(tmp_path):
    target = object_target(tmp_path)
    target['object'].pop('globalObjectId')
    with pytest.raises(ExecutionTargetError) as error:
        validate_execution_target(target, required_scope=identity_scope('vrcforge_add_component', write=True))
    assert error.value.code == 'identity_field_missing'


def test_registered_component_tools_advertise_their_actual_target_scope():
    import dashboard_server
    for name, scope in [('vrcforge_add_component', 'object'), ('vrcforge_add_modular_avatar_component', 'object'), ('vrcforge_create_component_feature', 'object'), ('vrcforge_remove_component', 'component'), ('vrcforge_set_property', 'component')]:
        descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(name, write=True)
        assert descriptor['requiredIdentity']['scope'] == scope
