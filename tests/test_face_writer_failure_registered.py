"""Registered face writers: Core failure must not create success state."""
import pytest
import dashboard_server as dashboard
from test_avatar_tuning_state_service import _prepared_service


@pytest.mark.parametrize('suffix', ['run_face_tuning', 'reapply_tuning_history', 'apply_tuning_preset'])
@pytest.mark.parametrize('core_success', [False, True])
def test_registered_face_writer_only_records_success_after_core_success(tmp_path, monkeypatch, suffix, core_success):
    service, stores, undo, live = _prepared_service(tmp_path)
    stores.save_history_record({'id': 'hist-1', 'avatar_name': 'Avatar', 'avatar_path': 'Avatar/Path',
        'changes': [{'renderer_path': 'Face', 'blendshape': 'Smile', 'after': 60.0}]})
    if suffix == 'run_face_tuning':
        arguments = {'avatar': 'Avatar/Path', 'instruction': 'gentler smile', 'mock_execute': False}
    elif suffix == 'reapply_tuning_history':
        arguments = {'historyId': 'hist-1'}
    else:
        preset = stores.create_preset({'history_id': 'hist-1', 'name': 'Test'})['preset']
        arguments = {'presetId': preset['id']}
    registered = dashboard.AGENT_GATEWAY.approval_transactions._ports.state.write_handlers['vrcforge_' + suffix]
    owner = registered.handler.__self__
    for field, value in [('_ports', service._ports), ('_stores', stores), ('_undo', undo)]:
        monkeypatch.setattr(owner, field, value)
    prepared, _ = registered.request_preparer(arguments, None)
    history_before = stores.load_history()
    presets_before = stores.load_presets()
    live['unity_result'] = {'exitCode': 0, 'payload': {'isError': not core_success,
        'structuredContent': {'success': core_success, 'message': 'Core write result', 'data': {
            'errorCode': 'scene_save_failed', 'mutationStarted': True, 'commitState': 'uncertain',
            'checkpointRecoveryRequired': True}}}}
    live['verified'] = core_success
    result = registered.handler(prepared)
    assert result['ok'] is core_success
    assert undo.depth('Avatar/Path') == int(core_success)
    assert len(live['unity_calls']) == 1
    if not core_success:
        assert result['result'] == live['unity_result']
        assert result['errorCode'] == 'scene_save_failed'
        assert result['mutationStarted'] is True
        assert result['commitState'] == 'uncertain'
        assert result['checkpointRecoveryRequired'] is True
        assert live['finalize'] == []
        assert stores.load_history() == history_before
        assert stores.load_presets() == presets_before
    elif suffix == 'run_face_tuning':
        assert [x[0] for x in live['finalize']] == ['artifacts', 'history']
    elif suffix == 'reapply_tuning_history':
        assert stores.find_history('hist-1')['applied'] is True
    else:
        assert stores.find_preset(arguments['presetId']).get('last_applied_at')
