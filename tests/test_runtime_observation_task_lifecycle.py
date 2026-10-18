import pytest
from agent_tool_result_contract import normalize_agent_tool_result
from agent_task_loop import AgentTaskLoop


def receipt(status='pending', **patch):
    return {'schema':'vrcforge.runtime_observation.v1','jobId':'a'*32,'avatarPath':'Avatar','coreIdentity':'project|instance',
        'status':status,'ok':True,'verified':status=='completed','mutationStarted':True,'commitState': 'pending' if status=='pending' else 'runtime_observed',
        **({'readback':{'jobId':'a'*32}} if status=='completed' else {}), **patch}


def record(loop, tool, raw):
    return loop.record_action(kind='write' if tool.endswith('start_runtime_observation') else 'skill',tool=tool,arguments={'jobId':raw['jobId']},raw_result=raw,
        outcome=normalize_agent_tool_result(raw,fallback_summary='observation',write=tool.endswith('start_runtime_observation')))


def test_pending_is_pollable_and_same_job_verified_poll_closes_start_obligation():
    loop=AgentTaskLoop('observe')
    start=record(loop,'vrcforge_start_runtime_observation',receipt())
    assert start['outcome']['status']=='pending'
    assert start['status']=='running' and start['runtimeStatus']=='polling'
    assert start['actionId'] not in loop.completed_action_ids()
    record(loop,'vrcforge_get_runtime_observation',receipt('completed'))
    assert start['actionId'] in loop.completed_action_ids()


@pytest.mark.parametrize('patch',[{'jobId':'b'*32},{'avatarPath':'Other'},{'coreIdentity':'other-instance'},{'verified':False},{'readback':None}])
def test_unrelated_or_unverified_poll_cannot_close_start(patch):
    loop=AgentTaskLoop('observe')
    start=record(loop,'vrcforge_start_runtime_observation',receipt())
    record(loop,'vrcforge_get_runtime_observation',receipt('completed',**patch))
    assert start['actionId'] not in loop.completed_action_ids()


def test_pending_approval_still_requires_user():
    value={'status':'pending','ok':True,'approval':{'id':'approval'}}
    assert normalize_agent_tool_result(value,fallback_summary='approval',write=True)['status']=='needs_user_action'

def test_native_pending_start_returns_to_planner_for_same_job_poll(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, call, finish
    from tests.test_native_permission_modes import _set_mode
    from tests.test_native_approval_success import _project
    project = _project(tmp_path)
    start, poll = 'vrcforge_start_runtime_observation', 'vrcforge_get_runtime_observation'
    gateway, model, _ = setup_gateway(tmp_path, [
        call('start', start, {'projectPath':str(project), 'avatarPath':'Avatar'}),
        call('poll', poll, {'jobId':'a'*32}), finish])
    _set_mode(gateway, 'roslyn_full_auto')
    calls=[]
    def begin(args): calls.append('start'); return receipt()
    def read(args): calls.append('poll'); return receipt('completed')
    gateway.register_tool(start,'When to use: start observation. When NOT to use: poll.','write',begin,write=True)
    gateway.approval_transactions.register_write_handler(start,'Start observation','medium',begin,pre_write_checkpoint_required=False)
    gateway.register_tool(poll,'When to use: poll observation. When NOT to use: start.','plan/preview',read)
    result=gateway.runtime_message({'message':'Observe once and inspect the result','sessionId':'observation-session','clientTurnId':'observation-turn',
        'projectRoot':str(project),'maxAgenticTurns':5})
    assert calls==['start','poll'], str({k: result['plan'].get(k) for k in ('summary','reply','plannerFailure')})
    assert result['plan']['nextStep']=='done', str((result['plan'].get('completionGate'), result['task']['actions'], result['task']['requirements']))
    assert len(model.requests)==3





@pytest.mark.parametrize('state', ['partial','failed'])
def test_failed_terminal_poll_closes_running_job_as_failed(state):
    loop=AgentTaskLoop('observe')
    start=record(loop,'vrcforge_start_runtime_observation',receipt())
    record(loop,'vrcforge_get_runtime_observation',receipt(state,ok=False,verified=False))
    action=next(a for a in loop.planner_projection()['actions'] if a['actionId']==start['actionId'])
    assert action['status']=='failed'
    assert start['actionId'] not in loop.completed_action_ids()


def test_completed_observation_does_not_clear_earlier_unknown_write_failure():
    loop=AgentTaskLoop('observe')
    old=loop.record_action(kind='write',tool='vrcforge_instantiate_prefab',arguments={},raw_result={'ok':False},
        outcome={'status':'failed','summary':'unknown write'})
    record(loop,'vrcforge_start_runtime_observation',receipt())
    record(loop,'vrcforge_get_runtime_observation',receipt('completed'))
    assert next(a for a in loop.planner_projection()['actions'] if a['actionId']==old['actionId'])['status']=='failed'
    assert loop.gate_terminal({'nextStep':'done'})['nextStep']!='done'
