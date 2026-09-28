from copy import deepcopy
import json

import pytest

from runtime_planner_service import estimate_runtime_context_tokens, PlannerCatalogSnapshot, PlannerSkill, PlannerTool, RuntimePlannerService
from tests.test_native_context_compaction import Compactor, fixture


def build(planner, messages, budget=None):
    return planner._build_native_plan_request(
        messages, observe={} if budget is None else {'modelTurnBudget': budget},
        exposure_layer='execution', project_context_active=True, project_path='C:/fixture',
        internal_tool_blocks=['core'], global_instructions='constant global', project_instructions='constant project',
    )[0]


def test_runtime_budget_is_host_only_and_native_request_is_unchanged():
    _, planner, _, _ = fixture(prior=False)
    history = [{'role': 'user', 'content': 'Inspect'}, {'role': 'assistant', 'content': None,
        'tool_calls': [{'id': 'read1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'read1', 'content': 'readback'}]
    before = deepcopy(history)
    one = {'maxModelTurns': 36, 'modelTurnsUsed': 1, 'remainingModelTurns': 35}
    two = {'maxModelTurns': 36, 'modelTurnsUsed': 2, 'remainingModelTurns': 34}
    a, b, unlimited = build(planner, history, one), build(planner, history, two), build(planner, history)
    assert a == b == unlimited
    for key in ('instructions', 'tools'):
        assert json.dumps(a[key], ensure_ascii=False) == json.dumps(b[key], ensure_ascii=False)
        assert a[key] == unlimited[key]
    assert set(unlimited) == {'instructions', 'messages', 'tools'}
    assert a['messages'][:-1] == b['messages'][:-1] == unlimited['messages'][:-1] == before
    assert unlimited['messages'][-1]['role'] == 'system'
    assert 'modelTurnBudget' not in json.loads(unlimited['messages'][-1]['content'].split(': ', 1)[1])
    assert 'modelTurnBudget' not in a['instructions']
    for request, budget in ((a, one), (b, two)):
        assert len(request['messages']) == len(before) + 1
        assert request['messages'][-1]['role'] == 'system'
        state = json.loads(request['messages'][-1]['content'].split(': ', 1)[1])
        assert 'modelTurnBudget' not in state
        assert state['loadedToolBlocks'] == ['core']
    assert history == before


@pytest.mark.parametrize('success', [True, False])
def test_compaction_retains_runtime_state_without_budget_and_guard_counts_it(success):
    state, planner, _, turn = fixture(Compactor(summary='short summary' if success else ''))
    budget = {'maxModelTurns': 36, 'modelTurnsUsed': 12, 'remainingModelTurns': 24}
    request = build(planner, turn.messages(), budget)
    assert 'modelTurnBudget' not in json.dumps(request)
    assert request['messages'][-1]['role'] == 'system'
    with planner.bind_turn({}):
        result, guard = planner.maybe_compact_native_context(request, turn)
        assert result['messages'][-1] == request['messages'][-1]
        expected = estimate_runtime_context_tokens(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
        assert guard['beforeTokens'] == expected
        without_budget = {**result, 'messages': result['messages'][:-1]}
        assert planner.native_context_guard(without_budget)['beforeTokens'] < guard['beforeTokens']
    assert turn.compaction['applied'] is success
    assert all(message['role'] != 'system' for message in state.native_conversation('s', binding='b')['messages'])
    assert 'modelTurnBudget' not in json.dumps(state.native_conversation('s', binding='b'))


def test_entire_runtime_state_changes_only_tail_and_keeps_tool_authority():
    class Catalog:
        enabled = False

        def read(self, layer, **kwargs):
            tools = (PlannerTool('inspect', 'Read', 'read', block='domain'),)
            if layer == 'execution':
                tools += (PlannerTool('change', 'Write', 'write', write=True, block='domain'),)
            return PlannerCatalogSnapshot(visible_tools=tools, skills=(PlannerSkill(
                'guide', title='Guide', source='user', skill_type='package', description='Complete guidance', enabled=self.enabled),))

    catalog = Catalog()
    planner = RuntimePlannerService(catalog=catalog, desktop=None)
    history = [{'role': 'user', 'content': 'fixture'}]
    def make(blocks, layer='execution', observe=None, path='C:/fixture'):
        return planner._build_native_plan_request(history, observe=observe or {}, exposure_layer=layer,
            project_context_active=True, project_path=path, internal_tool_blocks=blocks,
            global_instructions='constant global', project_instructions='constant project')[0]
    def state(request):
        assert request['messages'][:-1] == history
        assert request['messages'][-1]['role'] == 'system'
        return json.loads(request['messages'][-1]['content'].split(': ', 1)[1])
    def tools(request):
        return {item['function']['name'] for item in request['tools']}
    base = make(['core'])
    loaded = make(['core', 'domain'])
    catalog.enabled = True
    skill_enabled = make(['core', 'domain'])
    shell_data = {'available': True, 'shell': 'powershell', 'timeoutSeconds': 90}
    changed = make(['core', 'domain'], observe={'shellExecutor': shell_data}, path='C:/other')
    plan = make(['core', 'domain'], layer='planning', observe={'planMode': True})
    unloaded = make(['core'])
    for request in (loaded, skill_enabled, changed, plan, unloaded):
        assert request['instructions'] == base['instructions']
        assert 'Current runtime state (data)' not in request['instructions']
        state(request)
    assert state(loaded)['loadedToolBlocks'] == ['core', 'domain']
    assert 'runtimeContextInformation' not in state(loaded)
    assert '"items":[{"name":"guide","title":"Guide","description":"Complete guidance"}]' in state(skill_enabled)['runtimeContextInformation']
    assert state(changed)['shellExecutor'] == shell_data
    assert state(changed)['projectPath'] == 'C:/other'
    assert state(plan)['exposureLayer'] == 'planning'
    assert state(plan)['planMode'] is True and 'Only the user' in state(plan)['modeConstraint']
    assert tools(loaded) == {'inspect', 'change', 'vrcforge_runtime_action'}
    assert tools(plan) == {'inspect', 'vrcforge_runtime_action'}
    assert tools(unloaded) == tools(base) == {'vrcforge_runtime_action'}
    assert state(unloaded)['loadedToolBlocks'] == ['core']
