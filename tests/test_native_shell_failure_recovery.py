"""Ordinary background Shell failures return evidence without reopening stop paths."""
import json
import pytest
from agent_task_loop import AgentTaskLoop, prepare_shell_task_continuation
from tests.test_native_runtime_gateway import call, setup_gateway
from tests.test_native_async_continuations import _start_background_shell, _finish_shell


def prepared(**overrides):
    seed = AgentTaskLoop("Inspect", session_id="s", client_turn_id="t").approval_seed(
        requested_kind="shell", requested_tool="shell", requested_arguments={"command": "inspect"},
        continue_after_approval=True)
    event = {"shellSessionId": "p", "status": "finished", "exitCode": 1,
             "timedOut": False, "cancelled": False, "terminationFailed": False, **overrides}
    return prepare_shell_task_continuation(seed, event)["taskContinuation"]


def test_normal_nonzero_exit_returns_failed_evidence_to_planner():
    result = prepared()
    assert result['terminalPlan'] is None
    assert result['completion']['status'] == 'failed'
    assert result['completion']['outcome']['success'] is False


def test_zero_exit_retains_successful_continuation():
    result = prepared(exitCode=0)
    assert result['terminalPlan'] is None
    assert result['completion']['status'] == 'completed'


@pytest.mark.parametrize('change', [
    {'cancelled': True}, {'timedOut': True}, {'terminationFailed': True},
    {'status': 'unknown'}, {'exitCode': '1'}, {'exitCode': 1.5},
    {'exitCode': None}, {'exitCode': True}, {'exitCode': False}, {'exitCode': 0.0},
])
def test_unsafe_or_unknown_failure_retains_terminal_plan(change):
    result = prepared(**change)
    assert result['terminalPlan']
    assert result['completion']['status'] != 'completed'


def test_failed_background_call_resamples_once_with_original_id_without_reexecution(tmp_path, monkeypatch):
    def inspect_failed(request):
        results = [m for m in request['messages'] if m.get('role') == 'tool' and m.get('tool_call_id') == 'shell-call']
        assert len(results) == 1
        assert 'failed' in results[0]['content']
        return {'role': 'assistant', 'content': 'The inspection failed; I cannot claim success.'}
    gateway, model, _ = setup_gateway(tmp_path, [
        call('phase', 'vrcforge_runtime_action', {'action': 'enter_execution'}),
        call('shell-call', 'vrcforge_runtime_action', {'action': 'shell', 'shell_command': 'Get-ChildItem'}),
        inspect_failed,
    ])
    calls, seed = _start_background_shell(gateway, tmp_path, monkeypatch)
    before = len(model.requests)
    _finish_shell(gateway, seed, exit_code=1)
    _finish_shell(gateway, seed, exit_code=1)
    assert len(model.requests) == before + 1
    assert len(calls) == 1
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert len([m for m in snapshot['messages'] if m.get('role') == 'tool' and m.get('tool_call_id') == 'shell-call']) == 1


def test_host_stop_does_not_restart_after_failed_process_event(tmp_path, monkeypatch):
    gateway, model, _ = setup_gateway(tmp_path, [
        call('phase', 'vrcforge_runtime_action', {'action': 'enter_execution'}),
        call('shell-call', 'vrcforge_runtime_action', {'action': 'shell', 'shell_command': 'Get-ChildItem'}),
    ])
    calls, seed = _start_background_shell(gateway, tmp_path, monkeypatch)
    monkeypatch.setattr(gateway.shell, 'cancel_owner', lambda owner_id: ['shell-async-1'])
    gateway.request_runtime_cancel({'sessionId': 'native-shell-session', 'clientTurnId': 'native-shell-turn', 'reason': 'user_stop'})
    before = len(model.requests)
    _finish_shell(gateway, seed, cancelled=True, exit_code=1)
    assert len(model.requests) == before
    assert len(calls) == 1
