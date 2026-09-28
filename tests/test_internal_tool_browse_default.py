"""Discovery must not silently expand the provider's tool definitions."""
import pytest

import dashboard_server
from internal_tool_blocks import CANONICAL_TOOL_LEAVES
from runtime_planner_service import planner_tool_input_schema, validate_planner_tool_arguments


def test_leaf_browse_does_not_load_and_retains_complete_names(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('browsing must not mutate selected tools')
    monkeypatch.setattr(type(dashboard_server.AGENT_GATEWAY.runtime_sessions),
                        'load_internal_tool_block_selected', forbidden)
    for leaf in CANONICAL_TOOL_LEAVES:
        args = {'block': leaf, 'tools': [], 'allTools': False, 'exposureLayer': 'execution', 'projectContextActive': True}
        expected = dashboard_server.build_internal_tool_block_inventory(args)['tree']['tools']
        result = dashboard_server.load_internal_tool_block(args)
        assert result['view'] == 'selected_leaf'
        assert result['tree']['tools'] == expected
        assert result['loadedBlocks'] == ['core']
        assert 'inputSchema' not in str(result)


def test_named_subset_browse_and_explicit_whole_load():
    state = dashboard_server.AGENT_GATEWAY.runtime_sessions
    session = 'browse-default-selection-regression'
    state.discard_session(session)
    args = {'sessionId': session, 'block': 'project_environment/files', 'exposureLayer': 'execution'}
    try:
        subset = dashboard_server.load_internal_tool_block({**args, 'tools': ['read_installed_skill'], 'allTools': False})
        assert subset['selectedTools'] == ['read_installed_skill']
        dashboard_server.load_internal_tool_block({**args, 'tools': []})
        assert state.internal_tool_selections(session) == {'project_environment/files': ['read_installed_skill']}
        names = [row['name'] for row in dashboard_server.build_internal_tool_block_inventory(args)['tree']['tools']]
        whole = dashboard_server.load_internal_tool_block({**args, 'tools': names})
        assert set(whole['selectedTools']) == set(names)
    finally:
        state.discard_session(session)


@pytest.mark.parametrize('args', [
    {'block': 'project_environment', 'allTools': True},
    {'allTools': True},
    {'block': 'project_environment/files', 'allTools': 'true'},
    {'block': 'project_environment/files', 'allTools': True, 'tools': ['read_installed_skill']},
    {},
    {'block': 'project_environment/files'},
    {'block': 'project_environment/files', 'tools': None},
    {'block': 'project_environment/files', 'tools': '*'},
    {'block': 'project_environment/files', 'tools': ['*']},
])
def test_invalid_whole_load_never_mutates(args, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid request mutated selections')
    monkeypatch.setattr(type(dashboard_server.AGENT_GATEWAY.runtime_sessions),
                        'load_internal_tool_block_selected', forbidden)
    result = dashboard_server.load_internal_tool_block(args)
    assert result['ok'] is False
    assert result['mutationStarted'] is False


def test_schema_requires_tools_and_has_copyable_examples():
    schema = planner_tool_input_schema('vrcforge_load_internal_tool_block')
    assert schema['required'] == ['tools']
    assert schema['properties']['allTools']['type'] == 'boolean'
    assert not validate_planner_tool_arguments(schema, {'block': 'project_environment/files'})['ok']
    for example in schema['examples']:
        assert validate_planner_tool_arguments(schema, example)['ok']
    assert {'block': 'project_environment/files', 'tools': ['read_installed_skill']} in schema['examples']
    assert 'omit to load the whole block' not in str(schema)


def test_explicit_all_tools_loads_only_visible_current_leaf(monkeypatch):
    state = dashboard_server.AGENT_GATEWAY.runtime_sessions
    session = 'explicit-all-leaf-regression'
    leaf = 'project_environment/files'
    def visible(exposure, **kwargs):
        assert exposure == 'planning'
        return [
            {'name': 'read_a', 'block': leaf, 'mode': 'read'},
            {'name': 'read_b', 'block': leaf, 'mode': 'read'},
            {'name': 'other', 'block': 'diagnostics_build/compile_logs', 'mode': 'read'},
        ]
    monkeypatch.setattr(dashboard_server, '_internal_tool_block_leaves', visible)
    state.discard_session(session)
    try:
        result = dashboard_server.load_internal_tool_block({
            'sessionId': session, 'block': leaf, 'tools': [], 'allTools': True,
            'exposureLayer': 'planning',
        })
        assert result['ok'] is True
        assert set(result['selectedTools']) == {'read_a', 'read_b'}
        assert set(state.internal_tool_selections(session)) == {leaf}
    finally:
        state.discard_session(session)


@pytest.mark.parametrize('extra', [
    {'block': 'project_environment', 'allTools': True},
    {'allTools': True},
    {'block': 'project_environment/files', 'allTools': 'true'},
    {'block': 'project_environment/files', 'allTools': None},
    {'block': 'project_environment/files', 'allTools': 1},
])
def test_all_tools_invalid_boundary_rejected(extra):
    result = dashboard_server.load_internal_tool_block({'tools': [], **extra})
    assert result['ok'] is False
    assert result['mutationStarted'] is False


def test_native_loop_rejects_omission_then_loads_only_corrected_names(tmp_path, monkeypatch):
    import json
    from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool
    from tests.test_runtime_planner_service import FakeCatalog
    from tests.test_native_runtime_gateway import setup_gateway, call, finish, run

    gateway, model, invoked = setup_gateway(tmp_path, [])
    leaf = 'project_environment/files'
    schema = planner_tool_input_schema('vrcforge_load_internal_tool_block')
    tools = (
        PlannerTool('load_internal_tool_block', 'Discover or load.', 'read',
                    runtime_name='vrcforge_load_internal_tool_block', input_schema=schema),
        PlannerTool('read_text_file', 'Read.', 'read', runtime_name='vrcforge_read_text_file', block=leaf),
        PlannerTool('unused_read', 'Unrelated read.', 'read', runtime_name='vrcforge_unused_read', block=leaf),
    )
    monkeypatch.setattr(dashboard_server, 'AGENT_GATEWAY', gateway)
    monkeypatch.setattr(dashboard_server, '_internal_tool_block_leaves', lambda *args, **kwargs: [
        {'name': 'read_text_file', 'block': leaf, 'mode': 'read'},
        {'name': 'unused_read', 'block': leaf, 'mode': 'read'},
    ])
    dispatched = []
    def load(args):
        dispatched.append(args)
        return dashboard_server.load_internal_tool_block(args)
    gateway.register_tool('vrcforge_load_internal_tool_block', 'When to use: browse. When NOT to use: write.', 'read/debug', load)
    gateway._runtime_planner._catalog = FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools))
    model.replies = iter([
        call('missing', 'load_internal_tool_block', {'block': leaf}),
        call('browse', 'load_internal_tool_block', {'block': leaf, 'tools': []}),
        call('selected', 'load_internal_tool_block', {'block': leaf, 'tools': ['read_text_file']}),
        call('read', 'read_text_file', {'path': 'fixture.txt'}), finish,
    ])
    result = run(gateway, tmp_path, maxAgenticTurns=8)
    assert result['plan']['nextStep'] == 'done'
    assert len(dispatched) == 2  # Missing tools rejected before handler dispatch.
    assert len(invoked) == 1
    assert gateway.runtime_sessions.internal_tool_selections('native-session')[leaf] == ['read_text_file']
    for i, request in enumerate(model.requests):
        definitions = {item['function']['name']: item['function'] for item in request['tools']}
        assert definitions['load_internal_tool_block']['parameters']['required'] == ['tools']
        assert 'unused_read' not in definitions
        assert ('read_text_file' in definitions) == (i >= 3)
    rejection = next(m for m in model.requests[1]['messages'] if m.get('tool_call_id') == 'missing')
    assert 'missing_required' in rejection['content']
    assert 'tools' in rejection['content']
