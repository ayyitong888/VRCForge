import pytest
from jsonschema import Draft202012Validator

from unity_tool_schema_projection import canonical_unity_write_tool_input_schema


@pytest.mark.parametrize(('tool', 'fields'), [
    ('vrcforge_add_component', {'gameObjectPath': 'Avatar/Hat', 'componentType': 'UnityEngine.BoxCollider'}),
    ('vrcforge_rename_gameobject', {'gameObjectPath': 'Avatar/Hat', 'newName': 'Cap'}),
    ('vrcforge_toggle_scene_object', {'objectPath': 'Avatar/Hat', 'active': False}),
    ('vrcforge_unpack_prefab', {'gameObjectPath': 'Avatar/Hat', 'mode': 'outermost'}),
])
def test_scene_actions_publish_handler_inputs_without_hiding_legacy_arguments(tool, fields):
    schema = canonical_unity_write_tool_input_schema(tool)
    assert {'projectPath', 'executionTarget', *fields} <= schema['properties'].keys()
    target = {'schema': 'vrcforge.execution_target.v1', 'namespace': 'fixture', 'scope': 'object', 'project': {}, 'editor': {}}
    validator = Draft202012Validator(schema)
    validator.validate({'projectPath': '/fixture', 'executionTarget': target, **fields})
    # Existing snake-case aliases are still handled by the implementation.
    validator.validate({'projectPath': '/fixture', 'executionTarget': target, 'game_object_path': 'Avatar/Hat'})


def test_toggle_discovery_rejects_string_false_before_boolean_coercion():
    schema = canonical_unity_write_tool_input_schema('vrcforge_toggle_scene_object')
    errors = list(Draft202012Validator(schema).iter_errors({'active': 'false'}))
    assert any(list(error.path) == ['active'] for error in errors)
