"""Local persistence size is not the model's token context window."""
import json
import threading

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts


def state_at(path):
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), NativeHistoryStore(path)))


def test_large_history_can_settle_once_and_resume_after_restart(tmp_path):
    state = state_at(tmp_path)
    state.begin_native_turn('s', binding='b', turn_id='old', message='Start')
    # Multiple ordinary messages, not one oversized provider output. UTF-8
    # bytes cross both the former 2 MiB state and 3 MiB storage boundaries.
    for _ in range(12):
        state.append_native_user('s', binding='b', message='历史内容' * 25000)
    receipt = {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'done-once', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]}
    state.append_native_assistant('s', binding='b', message=receipt)
    state.settle_native_call('s', binding='b', call_id='done-once', content='{"result":"verified"}')
    snapshot = state.native_conversation('s', binding='b')
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) > 3 * 1024 * 1024
    restarted = state_at(tmp_path)
    next_snapshot = restarted.begin_native_turn('s', binding='b', turn_id='new', message='Continue')
    assert next_snapshot['messages'][:-1] == snapshot['messages']
    with pytest.raises(ValueError, match='already settled'):
        restarted.settle_native_call('s', binding='b', call_id='done-once', content='duplicate')


def test_store_keeps_large_owner_snapshot_and_identity_checks(tmp_path):
    store = NativeHistoryStore(tmp_path)
    snapshot = {'binding': 'b', 'messages': [{'role': 'user', 'content': 'x' * (3 * 1024 * 1024)}]}
    store.save('s', snapshot, {})
    assert store.load('s', 'b')['native'] == snapshot
    assert store.load('different', 'b') is None


def test_compaction_keeps_parallel_calls_and_all_results_in_one_transaction():
    from agent_runtime_native_turn import NativeRuntimeTurn
    from tests.test_native_context_compaction import Compactor, fixture, plan

    compactor = Compactor()
    state, planner, model, turn = fixture(compactor)
    state.append_native_assistant('s', binding='b', message={
        'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': name, 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}
            for name in ('first-read', 'second-read')]})
    state.settle_native_call('s', binding='b', call_id='first-read', content='FIRST_RESULT')
    before = turn.snapshot()
    assert plan(planner, turn)['nextStep'] == 'context_compaction_required'
    assert not compactor.calls and not model.requests
    assert turn.snapshot() == before
    state.settle_native_call('s', binding='b', call_id='second-read', content='SECOND_RESULT')
    following = NativeRuntimeTurn(state, planner, 's', 'following', 'b', 'Continue', [], None, None, None)
    plan(planner, following)
    grouped = [entry['text'] for entry in compactor.calls[0][0] if 'first-read' in entry['text']]
    assert len(grouped) == 1
    assert all(text in grouped[0] for text in ('second-read', 'FIRST_RESULT', 'SECOND_RESULT'))
    assert not state._native_pending(following.snapshot())
