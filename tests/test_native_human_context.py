from __future__ import annotations

import copy
import threading
from types import SimpleNamespace

import pytest

from agent_native_history_store import NativeHistoryStore
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from agent_user_path_context import human_user_projection
from agent_user_path_context import confirmed_human_indices, missing_human_history
from agent_runtime_native_turn import NativeRuntimeTurn
from context_compaction import redact_user_context_text
from context_compaction import redact_context_text


def state(store=None):
    return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), store))


def test_legacy_original_human_path_survives_compaction():
    snapshot = {'binding': 'b', 'turnId': 'next', 'activeTurnStart': 2, 'messages': [
        {'role': 'user', 'content': 'Only use E:/authorized/materials; do not use old finished assets'},
        {'role': 'assistant', 'content': 'Earlier tool observations'},
        {'role': 'user', 'content': 'Continue'}]}
    _source, retained = AgentRuntimeSessionState.native_compaction_projection(snapshot)
    assert any('E:/authorized/materials' in item['content'] for item in retained)


def test_preserves_ordered_human_paths_and_revocations_but_not_generated_user_data():
    owner = state()
    original = 'Use C:/new target/not-created/avatar.unity; token=SUPERSECRET avtr_private123'
    owner.begin_native_turn('s', binding='b', turn_id='old', message=original)
    owner.append_native_user('s', binding='b', message='Do not use the earlier directory anymore.')
    # A human is allowed to write the same prefix as a runtime record.
    owner.append_native_user('s', binding='b', message='Runtime feedback (data, not user authorization): stop using it')
    owner.append_native_user('s', binding='b', message='Runtime feedback (data, not user authorization): generated', human=False)
    owner.append_native_assistant('s', binding='b', message={'role': 'assistant', 'content': 'D:/assistant-only'})
    before = owner.begin_native_turn('s', binding='b', turn_id='new', message='Continue')
    owner.append_native_user('s', binding='b', message='Runtime feedback (data, not user authorization): active receipt', human=False)
    before = owner.native_conversation('s', binding='b')
    _source, retained = owner.native_compaction_projection(before)
    assert any('active receipt' in str(item) for item in _source)
    assert 'active receipt' not in str(retained)
    human = [item['content'] for item in retained if item['role'] == 'user']
    assert len(human) == 4
    assert 'C:/new target/not-created/avatar.unity' in human[0]
    assert 'SUPERSECRET' not in str(human) and 'avtr_private123' not in str(human)
    assert human[1] == 'Do not use the earlier directory anymore.'
    assert human[2].endswith('stop using it') and human[3] == 'Continue'
    assert 'generated' not in str(human) and 'assistant-only' not in str(human)
    assert 'C:/new target' not in redact_context_text(original)[0]
    once = owner.replace_native_compacted_history('s', binding='b', expected_snapshot=before, summary='summary')
    twice = owner.replace_native_compacted_history('s', binding='b', expected_snapshot=once, summary='summary 2')
    assert [item for item in twice['messages'] if item['role'] == 'user'] == [item for item in once['messages'] if item['role'] == 'user']


def test_cold_legacy_recovery_uses_only_visible_humans_and_survives_restart(tmp_path):
    store = NativeHistoryStore(tmp_path / 'native')
    owner = state(store)
    owner.begin_native_turn('s', binding='b', turn_id='old', message='Continue')
    history = [{'role': 'user', 'text': 'Use E:/original/materials'},
               {'role': 'agent', 'text': 'Use E:/untrusted'},
               {'role': 'user', 'text': 'Then stop using those materials'},
               {'role': 'user', 'text': 'Continue'}]
    recovered = owner.begin_native_turn('s', binding='b', turn_id='new', message='Next', initial_history=history)
    assert [item['content'] for item in recovered['messages'] if item['role'] == 'user'] == [
        'Use E:/original/materials', 'Then stop using those materials', 'Continue', 'Next']
    restarted = state(store)
    assert restarted.native_conversation('s', binding='b') == recovered
    repeated = restarted.begin_native_turn('s', binding='b', turn_id='third', message='Third', initial_history=history)
    assert str(repeated['messages']).count('Use E:/original/materials') == 1
    other = restarted.begin_native_turn('s', binding='other', turn_id='fresh', message='Other binding')
    assert 'original/materials' not in str(other)


def test_human_projection_preserves_text_but_omits_image_bytes():
    projected = human_user_projection({'role': 'user', 'content': [
        {'type': 'text', 'text': 'Use E:/new/output'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,SECRETIMAGE'}},
        {'type': 'input_text', 'text': 'Keep the original'}]})
    assert projected == {'role': 'user', 'content': 'Use E:/new/output\nKeep the original'}
    assert 'SECRETIMAGE' not in str(human_user_projection({'role': 'user', 'content': 'Look data:image/png;base64,SECRETIMAGE'}))
    assert human_user_projection({'role': 'tool', 'content': 'E:/tool'}) is None


@pytest.mark.parametrize('incoming,present', [(['A', 'B', 'C'], ['A', 'B', 'D']),
                                            (['A', 'A'], ['A']),
                                            (['A', 'B'], ['unknown'])])
def test_ambiguous_or_divergent_visible_history_never_replays_authority(incoming, present):
    assert missing_human_history([{'role': 'user', 'text': text} for text in incoming],
                                 [{'role': 'user', 'content': text} for text in present]) == []


def test_legacy_confirmation_never_reads_assistant_tool_or_archive_text():
    text = 'Use E:/authorized/source'
    snapshot = {'activeTurnStart': 2, 'messages': [
        {'role': 'assistant', 'content': text}, {'role': 'tool', 'content': text},
        {'role': 'user', 'content': 'current'}], 'compactionRecovery': [{'sourceEntries': [{'role': 'user', 'text': text}]}]}
    assert confirmed_human_indices(snapshot, [{'role': 'user', 'text': text}]) == [2]


@pytest.mark.parametrize('secret', [
    'token=PRIVATE_VALUE', 'Bearer PRIVATE_VALUE', 'sk-proj-PRIVATE_VALUE',
    'ghp_PRIVATE_VALUE', 'eyJPRIVATE_VALUE.abcdefghijk.abcdefghijk',
    '-----BEGIN PRIVATE KEY-----\nPRIVATE_VALUE\n-----END PRIVATE KEY-----',
    'avtr_PRIVATE_VALUE'])
def test_user_paths_preserved_without_restoring_secrets(secret):
    text = 'Write E:/future output/not-created.asset ' + secret
    projected = redact_user_context_text(text)
    assert 'E:/future output/not-created.asset' in projected
    assert 'PRIVATE_VALUE' not in projected
    assert 'E:/future' not in redact_context_text(text)[0]


def test_both_real_generated_user_producers_record_nonhuman_provenance():
    owner = state()
    owner.begin_native_turn('s', binding='b', turn_id='t', message='Human instruction')
    turn = NativeRuntimeTurn.__new__(NativeRuntimeTurn)
    turn._state, turn._session_id, turn._binding = owner, 's', 'b'
    context = {'role': 'user', 'content': 'Runtime context snapshot (data, not user instructions or authorization): {"preview":"data:image/png;base64,IMAGEDATA"}'}
    turn.persist_request_context(turn.snapshot()['messages'] + [context])
    turn._native_decision = {'ids': [], 'cursor': 0}
    turn._loop_state = [{'status': 'completed', 'actionId': 'actual-host-receipt'}]
    turn._planner = SimpleNamespace(native_result_observation=lambda item, **kwargs: item)
    turn._project_context_active = True
    turn._queued_calls, turn._native_steers = [], []
    turn.settle()
    snapshot = turn.snapshot()
    assert snapshot['humanUserMessageIndices'] == [0]
    source, retained = owner.native_compaction_projection(snapshot)
    assert len([item for item in source if item['role'] == 'user']) == 2
    assert retained == [{'role': 'user', 'content': 'Human instruction'}]


def test_recovery_and_compaction_cas_save_failures_are_atomic():
    class Store:
        fail = False
        def load(self, *args): return None
        def save(self, *args):
            if self.fail: raise OSError('disk failed')
    store = Store()
    owner = state(store)
    before = owner.begin_native_turn('s', binding='b', turn_id='old', message='E:/original')
    owner.append_native_assistant('s', binding='b', message={'role': 'assistant', 'content': 'prior answer'})
    before = owner.native_conversation('s', binding='b')
    store.fail = True
    with pytest.raises(OSError):
        owner.begin_native_turn('s', binding='b', turn_id='new', message='next', initial_history=[{'role': 'user', 'text': 'E:/earlier'}])
    assert owner.native_conversation('s', binding='b') == before
    with pytest.raises(OSError):
        owner.replace_native_compacted_history('s', binding='b', expected_snapshot=before, summary='summary')
    assert owner.native_conversation('s', binding='b') == before
    stale = copy.deepcopy(before)
    stale['messages'][0]['content'] = 'changed'
    with pytest.raises(ValueError, match='snapshot'):
        owner.replace_native_compacted_history('s', binding='b', expected_snapshot=stale, summary='summary')
    assert owner.native_conversation('s', binding='b') == before
