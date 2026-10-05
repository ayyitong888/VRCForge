import json
from copy import deepcopy

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService


class Catalog:
    def read(self, layer, **kwargs):
        tools = (PlannerTool('resident', 'Full resident guidance ' * 500, 'read', block='core'),
                 PlannerTool('deferred', 'Deferred schema sentinel', 'read', block='domain'),
                 PlannerTool('core_write', 'Write schema sentinel', 'write', block='core', write=True))
        return PlannerCatalogSnapshot(visible_tools=tuple(t for t in tools if layer == 'execution' or not t.write),
                                      routable_tools=tools)


def build(planner, messages=(), *, layer='planning', observe=None, blocks=('core',)):
    return planner._build_native_plan_request(list(messages), observe=observe or {}, exposure_layer=layer,
        project_context_active=True, project_path='C:/fixture', internal_tool_blocks=blocks,
        global_instructions='', project_instructions='')[0]


def test_resident_prefix_stays_fixed_and_tail_has_no_large_context():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    base = build(planner)
    context = {'turn': {'attachments': [{'name': 'note', 'text': 'CONTEXT_SENTINEL ' * 2000}]}}
    loaded = build(planner, layer='execution', observe=context, blocks=('core', 'domain'))
    assert base['instructions'] == loaded['instructions']
    assert 'Full resident guidance' in base['instructions']
    assert 'Deferred schema sentinel' not in base['instructions']
    assert 'Write schema sentinel' not in base['instructions']
    assert base['tools'] == loaded['tools']
    for request in (base, loaded):
        tail = request['messages'][-1]['content']
        assert len(tail) < 2000
        assert 'Full resident guidance' not in tail
        assert 'CONTEXT_SENTINEL' not in tail
        assert 'residentToolDefinitions' not in tail
    assert 'CONTEXT_SENTINEL' in json.dumps(loaded['messages'][:-1])
    assert 'Write schema sentinel' in json.dumps(loaded['messages'][:-1])


def test_context_appends_once_without_rewriting_earlier_messages():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    observe = {'memory': {'items': [{'scope': 'project', 'kind': 'note', 'text': 'unique-note'}]}}
    first = build(planner, [{'role': 'user', 'content': 'inspect'}], observe=observe)
    history = deepcopy(first['messages'][:-1])
    history.append({'role': 'assistant', 'content': 'read complete'})
    second = build(planner, history, observe=observe)
    assert second['messages'][:-1] == history
    assert json.dumps(second['messages']).count('unique-note') == 1
    changed = build(planner, history, observe={'memory': {'items': [{'text': 'new-note'}]}})
    assert changed['messages'][:len(history)] == history
    assert 'new-note' in changed['messages'][-2]['content']


def test_actual_gateway_persists_context_once(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, run, call, finish
    gateway, model, _ = setup_gateway(tmp_path, [call('read', 'vrcforge_read_text_file', {'path': 'a.txt'}), finish])
    gateway.runtime_planner.complete_runtime_context_information = lambda *a, **kw: 'UNIQUE_RUNTIME_CONTEXT'
    run(gateway, tmp_path)
    assert len(model.requests) == 2
    before = model.requests[0]['messages'][:-1]
    assert model.requests[1]['messages'][:len(before)] == before
    assert sum('UNIQUE_RUNTIME_CONTEXT' in m.get('content', '') for m in before) == 1
    assert 'UNIQUE_RUNTIME_CONTEXT' not in model.requests[1]['messages'][-1]['content']
    with gateway.runtime_planner.bind_turn({}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    saved = gateway.runtime_sessions.native_conversation('native-session', binding=binding)
    assert sum('UNIQUE_RUNTIME_CONTEXT' in (m.get('content') or '') for m in saved['messages']) == 1
