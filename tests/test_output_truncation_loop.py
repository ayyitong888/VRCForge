import json
from copy import deepcopy

from runtime_planner_service import PlannerModelResult
from tests.test_native_runtime_gateway import setup_gateway, run, call


def scripted(tmp_path, responses):
    gateway, model, invoked = setup_gateway(tmp_path, [])
    iterator = iter(responses)
    def plan(request):
        model.requests.append(deepcopy(request))
        return next(iterator)
    model.plan_native = plan
    return gateway, model, invoked


def response(text='', *, length=False, calls=None, metadata=None):
    return PlannerModelResult('', assistant_message={
        'role': 'assistant', 'content': text or None, 'tool_calls': calls or [],
    }, finish_reason='length' if length else 'tool_calls' if calls else 'stop',
        truncation=metadata or {})


def test_text_continuation_is_hidden_durable_and_one_final(tmp_path):
    gateway, model, _ = scripted(tmp_path, [response('第一段', length=True), response('第二段')])
    result = run(gateway, tmp_path)
    assert result['plan']['reply'] == '第一段第二段'
    assert len(model.requests) == 2
    hidden = [m for m in model.requests[1]['messages'] if m.get('hidden')]
    assert [m['content'] for m in hidden] == ['上条回复因长度上限中断，请从断点直接继续，不要重复已写内容。']
    assert not any('上条回复因' in str(e) for e in result['timeline'])
    snapshot = next(iter(gateway.runtime_sessions._native_conversations.values()))
    from tests.test_native_history_storage import owner
    restored = owner(gateway.audit_dir / 'native-history').native_conversation('native-session', binding=snapshot['binding'])
    assert restored and any(m.get('hidden') is True for m in restored['messages'])


def test_fourth_truncation_stops_after_three_continuations(tmp_path):
    gateway, model, _ = scripted(tmp_path, [response(str(i), length=True) for i in range(4)])
    result = run(gateway, tmp_path)
    assert len(model.requests) == 4
    assert result['plan']['reason'] == 'output_length_limit'
    assert '输出长度上限' in result['plan']['reply']
    assert not result['plan'].get('plannerFailed')


def test_empty_truncation_increases_limit_without_continue_message(tmp_path):
    gateway, model, _ = scripted(tmp_path, [
        response(length=True, metadata={'maxOutputTokens': 8, 'modelMaxOutputTokens': 32}), response('done')])
    result = run(gateway, tmp_path)
    assert result['plan']['reply'] == 'done'
    assert model.requests[1]['_maxOutputTokens'] == 16
    assert not any(m.get('hidden') for m in model.requests[1]['messages'])


def test_empty_truncation_at_model_limit_stops(tmp_path):
    gateway, model, _ = scripted(tmp_path, [response(length=True, metadata={'maxOutputTokens': 32, 'modelMaxOutputTokens': 32})])
    result = run(gateway, tmp_path)
    assert len(model.requests) == 1
    assert result['plan']['reason'] == 'output_length_limit'


def test_complete_tools_execute_discarded_call_never_enters_history(tmp_path):
    calls = [call(str(i), 'vrcforge_read_text_file', {'path': f'{i}.txt'})['tool_calls'][0] for i in range(2)]
    dropped = {'id': 'third', 'name': 'call_tool', 'arguments': '{"tool":',
               'reason': '输出长度上限，调用块未结束', 'suggestion': '拆成更小的调用重发'}
    gateway, model, invoked = scripted(tmp_path, [response(length=True, calls=calls, metadata={'discardedToolCalls': [dropped]}), response('已读两个文件')])
    result = run(gateway, tmp_path)
    assert len(invoked) == 2
    assert len(model.requests) == 2
    messages = model.requests[-1]['messages']
    ids = [c['id'] for m in messages for c in m.get('tool_calls', [])]
    results = [m['tool_call_id'] for m in messages if m['role'] == 'tool']
    assert set(ids) == set(results) == {'0', '1'}
    feedback = next(m['content'] for m in messages if m.get('hidden'))
    assert 'third' in feedback and '未执行' in feedback and 'fixture-body' in feedback
    assert result['plan']['reply'] == '已读两个文件'


def test_final_continuation_preserves_boundary_whitespace(tmp_path):
    gateway, model, _ = scripted(tmp_path, [response('hello', length=True), response(' world')])
    assert run(gateway, tmp_path)['plan']['reply'] == 'hello world'


def test_three_truncations_then_final_completes(tmp_path):
    gateway, model, _ = scripted(tmp_path, [response(str(i), length=True) for i in range(3)] + [response('done')])
    result = run(gateway, tmp_path)
    assert result['plan']['reply'] == '012done'
    assert result['plan']['nextStep'] == 'done'
    assert len(model.requests) == 4


def test_output_limit_is_normal_waiting_terminal():
    from agent_gateway import AgentGateway
    status = AgentGateway._terminal_turn_status({'nextStep': 'output_length_limit'})
    assert status['status'] == 'waiting_user'
    assert not status.get('error')


def test_empty_completed_continuation_keeps_prior_text_and_skips_empty_history(tmp_path):
    gateway, model, _ = scripted(tmp_path, [response('already complete', length=True), response()])
    result = run(gateway, tmp_path)
    assert result['plan']['nextStep'] == 'done'
    assert result['plan']['reply'] == 'already complete'
    snapshot = next(iter(gateway.runtime_sessions._native_conversations.values()))
    assert all(m.get('content') or m.get('tool_calls') for m in snapshot['messages'] if m['role'] == 'assistant')


def test_long_truncated_arguments_do_not_hide_frontend_reason_or_suggestion(tmp_path):
    dropped = {'id': 'partial', 'name': 'call_tool', 'arguments': '{"large":"' + 'x' * 4000}
    gateway, _, invoked = scripted(tmp_path, [response(length=True, metadata={'discardedToolCalls': [dropped]}), response('Please retry smaller')])
    result = run(gateway, tmp_path)
    notice = next(e['payload']['summary'] for e in result['timeline'] if e['kind'] == 'tool_result')
    assert 'call_tool' in notice and 'large' in notice
    assert '输出长度上限' in notice and '建议' in notice and '更小' in notice
    assert invoked == []
