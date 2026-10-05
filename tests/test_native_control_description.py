import json

import pytest

from runtime_planner_service import RuntimePlannerService, PlannerCatalogSnapshot


class Catalog:
    def read(self, *args, **kwargs):
        return PlannerCatalogSnapshot()


@pytest.mark.parametrize('layer,plan_mode,expected', [
    ('planning', False, ['reply', 'shell', 'correct', 'enter_execution']),
    ('execution', False, ['reply', 'shell', 'correct']),
    ('planning', True, ['reply', 'correct']),
])
def test_native_control_schema_is_fixed_and_runtime_state_describes_allowed_actions(layer, plan_mode, expected):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    request, _ = planner._build_native_plan_request([], observe={'planMode': plan_mode},
        exposure_layer=layer, project_context_active=True, project_path='C:/fixture',
        internal_tool_blocks=['core'], global_instructions='', project_instructions='')
    control = request['tools'][-1]['function']
    action = control['parameters']['properties']['action']
    assert action['enum'] == ['reply', 'shell', 'correct', 'enter_execution']
    assert [tool['function']['name'] for tool in request['tools']] == [
        'load_internal_tool_block', 'unload_internal_tool_block', 'call_tool', 'vrcforge_runtime_action']
    prefix = 'Current runtime state (data): '
    state_message = next(message for message in request['messages']
                         if message.get('role') == 'system' and message.get('content', '').startswith(prefix))
    state = json.loads(state_message['content'][len(prefix):])
    assert state['allowedRuntimeActions'] == expected
    for name in ('reply', 'shell', 'correct', 'enter_execution'):
        assert name in action['description']
        assert name in control['description']
    assert 'When to use:' in control['description']
    assert 'When NOT to use:' in control['description']
    assert 'ordinary tool operations; use call_tool instead' in control['description']
    assert 'never grants permissions' in control['description']
    assert 'enter execution through vrcforge_runtime_action' not in request['instructions']
    assert 'The host enforces permissions and approvals.' in request['instructions']
    assert 'performs no write' in action['description']
    assert 'does not bypass approval' in action['description']
    if plan_mode:
        assert 'Shell commands are unavailable.' in request['messages'][-1]['content']
        assert 'do not use shell or enter_execution in user-selected Plan' in control['description']
    if layer == 'execution':
        assert 'enter_execution' not in state['allowedRuntimeActions']
