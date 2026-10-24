"""Model-visible identity guidance follows the shared runtime scope contract."""
from copy import deepcopy

import pytest

from mcp_tool_descriptor import standardize_tool_descriptor
from unity_tool_schema_projection import (
    UNITY_READ_TOOL_INPUT_SCHEMAS, EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS,
    canonical_unity_read_tool_input_schema as read_schema,
    canonical_unity_write_tool_input_schema as write_schema,
)


def guidance(name, *, write=False):
    schema = write_schema(name) if write else read_schema(name)
    return schema['properties']['executionTarget']['description']


@pytest.mark.parametrize('name,write', [
    ('vrcforge_toggle_scene_object', True),
    ('vrcforge_add_modular_avatar_component', True),
    ('vrcforge_preview_add_modular_avatar_component', False),
    ('vrcforge_create_component_feature', True),
    ('vrcforge_preview_component_feature', False),
])
def test_actual_host_creation_workflows_explain_object_binding(name, write):
    text = guidance(name, write=write)
    assert 'minimum scope: object' in text
    assert 'avatar reference is insufficient' in text
    assert 'existing host object' in text


@pytest.mark.parametrize('name,write', [
    *[(name, True) for name in EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS],
    *[(name, False) for name in UNITY_READ_TOOL_INPUT_SCHEMAS],
])
def test_every_registered_execution_target_explains_its_scope(name, write):
    schema = write_schema(name) if write else read_schema(name)
    if 'executionTarget' not in schema.get('properties', {}):
        return
    text = schema['properties']['executionTarget']['description']
    assert 'Identity scope:' in text, name
    descriptor = standardize_tool_descriptor({'name': name, 'inputSchema': schema}, write=write)
    assert descriptor['inputSchema']['properties']['executionTarget']['description'] == text


@pytest.mark.parametrize('name,write,terms', [
    ('vrcforge_scene_transition', True, ('open_single', 'project', 'otherwise', 'scene')),
    ('vrcforge_preview_scene_transition', False, ('open_single', 'project', 'otherwise', 'scene')),
    ('vrcforge_create_gameobject', True, ('parentPath', 'parent_path', 'non-empty', 'object', 'otherwise', 'scene')),
    ('vrcforge_set_renderer_material_slot', True, ('assignments', 'scene or avatar', 'component')),
    ('vrcforge_preview_renderer_material_slot', False, ('assignments', 'scene or avatar', 'component')),
    ('vrcforge_set_material_shader', True, ('assignments', 'materialAssetPath', 'rendererPath', 'rendererComponentId', 'slotIndex', 'project', 'component')),
    ('vrcforge_preview_material_shader_assignment', False, ('assignments', 'materialAssetPath', 'rendererPath', 'rendererComponentId', 'slotIndex', 'project', 'component')),
    ('vrcforge_preview_material_variant_flatten', False, ('project',)),
    ('vrcforge_preview_material_texture_assignment', False, ('project',)),
])
def test_dynamic_and_project_preview_guidance(name, write, terms):
    text = guidance(name, write=write)
    for term in terms:
        assert term in text, (name, term, text)


def test_optional_read_does_not_require_a_stronger_binding():
    schema = read_schema('vrcforge_scan_materials')
    assert 'executionTarget' not in schema.get('required', [])
    text = schema['properties']['executionTarget']['description']
    assert 'optional read' in text
    assert 'own bound scope' in text
    assert 'minimum scope:' not in text


def test_bootstrap_and_handler_owned_calls_do_not_demand_a_binding():
    for name in ('vrcforge_list_execution_targets', 'vrcforge_bind_execution_target'):
        assert 'executionTarget' not in read_schema(name).get('properties', {})
    refresh = read_schema('vrcforge_refresh_execution_target')
    assert 'own bound scope' in refresh['properties']['executionTarget']['description']
    schema = write_schema('vrcforge_install_unity_core')
    assert 'executionTarget' not in schema.get('required', [])
    if 'executionTarget' in schema.get('properties', {}):
        assert 'handler-managed' in schema['properties']['executionTarget']['description']


def test_guidance_preserves_original_schema_and_description():
    schema = write_schema('vrcforge_add_modular_avatar_component')
    schema['properties']['executionTarget']['description'] = 'Existing identity documentation.'
    before = deepcopy(schema)
    result = standardize_tool_descriptor({'name': 'vrcforge_add_modular_avatar_component', 'inputSchema': schema}, write=True)
    assert result['inputSchema']['properties']['executionTarget']['description'].startswith('Existing identity documentation.')
    assert 'minimum scope: object' in result['inputSchema']['properties']['executionTarget']['description']
    assert schema == before
    assert result['inputSchema']['required'] == schema['required']


@pytest.mark.parametrize('name', ['vrcforge_set_material_shader', 'vrcforge_preview_material_shader_assignment'])
@pytest.mark.parametrize('asset', [False, True])
@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('selector', [None, 'rendererPath', 'rendererComponentId', 'slotIndex'])
def test_shader_scope_metadata_matches_runtime_for_selector_combinations(name, asset, batch, selector):
    from jsonschema import Draft202012Validator
    from mcp_tool_descriptor import identity_scope, tool_identity_metadata
    arguments = {}
    if asset:
        arguments['materialAssetPath'] = 'Assets/A.mat'
    if batch:
        arguments['assignments'] = []
    if selector:
        arguments[selector] = ''
    policy = tool_identity_metadata(name, write=True)['parameterScope']
    actual = identity_scope(name, write=True, arguments=arguments)
    project_allowed = Draft202012Validator(policy).is_valid({**arguments, 'executionTarget': {'scope': 'project'}})
    assert project_allowed == (actual == 'project')
    assert Draft202012Validator(policy).is_valid({**arguments, 'executionTarget': {'scope': actual}})


@pytest.mark.parametrize('snake,camel,expected', [
    ('  ', 'Avatar', 'scene'), ('', 'Avatar', 'object'),
    ('Avatar', '', 'object'), (None, '', 'scene'),
])
def test_create_guidance_preserves_parent_alias_resolution(snake, camel, expected):
    from mcp_tool_descriptor import identity_scope, tool_identity_metadata
    name = 'vrcforge_create_gameobject'
    assert identity_scope(name, arguments={'parent_path': snake, 'parentPath': camel}) == expected
    assert tool_identity_metadata(name, write=True)['actionAwareScope']['when'] == {
        'resolvedNonEmpty': ['parent_path', 'parentPath']}
    assert "str(parent_path or parentPath or '').strip()" in guidance(name, write=True)


def test_complete_scope_guidance_survives_planner_projection_with_bounded_size():
    from runtime_planner_service import _project_planner_schema
    for name in EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS:
        schema = write_schema(name)
        target = schema.get('properties', {}).get('executionTarget')
        if target is None:
            continue
        description = target['description']
        # Scope prose must remain compact enough to be useful on every loaded
        # tool; the actual planner projection must preserve even its final words.
        assert len(description) < 1600, (name, len(description))
        assert _project_planner_schema(schema)['properties']['executionTarget']['description'] == description
        assert 'Identity scope:' in description


def test_opt_in_descriptor_schema_preserves_existing_target_contract():
    source = {'type': 'object', 'properties': {'executionTarget': {
        'type': 'object', 'description': 'Existing target contract.',
        'required': ['ref'], 'additionalProperties': False}}}
    result = standardize_tool_descriptor({'name': 'vrcforge_set_property', 'inputSchema': source}, write=True, add_identity_schema=True)
    target = result['inputSchema']['properties']['executionTarget']
    assert target['required'] == ['ref']
    assert target['additionalProperties'] is False
    assert target['description'].startswith('Existing target contract.')
    bootstrap = standardize_tool_descriptor({'name': 'vrcforge_list_execution_targets', 'inputSchema': {'type': 'object'}}, write=False, add_identity_schema=True)
    assert 'executionTarget' not in bootstrap['inputSchema']['properties']
