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
def test_native_control_describes_only_currently_allowed_actions(layer, plan_mode, expected):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    request, _ = planner._build_native_plan_request([], observe={'planMode': plan_mode},
        exposure_layer=layer, project_context_active=True, project_path='C:/fixture',
        internal_tool_blocks=['core'], global_instructions='', project_instructions='')
    control = request['tools'][-1]['function']
    action = control['parameters']['properties']['action']
    assert action['enum'] == expected
    for description in (action['description'], control['description']):
        for name in ('reply', 'shell', 'correct', 'enter_execution'):
            assert (name in description) is (name in expected)
    assert 'When to use:' in control['description']
    assert 'When NOT to use:' in control['description']
    assert 'call the advertised tool directly' in control['description']
    assert 'never grants permissions' in control['description']
    assert 'enter execution through vrcforge_runtime_action' not in request['instructions']
    assert 'The host enforces permissions and approvals.' in request['instructions']
    if 'enter_execution' in expected:
        assert 'performs no write' in action['description']
        assert 'does not bypass approval' in action['description']
