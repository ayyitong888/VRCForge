"""Historical scene topology must be recoverable before publishing archive files."""
import pytest

from agent_gateway import AgentGateway


@pytest.mark.parametrize('mode', ['missing', 'present', 'scoped', 'known_empty',
                                 'known_present', 'known_missing', 'reopen_failure'])
def test_scene_preflight_preserves_files_and_reopens_original_context(tmp_path, mode):
    service = AgentGateway(tmp_path / 'config.json', tmp_path / 'audit').checkpoint_recovery
    project = tmp_path / 'project'
    assets = project / 'Assets'
    assets.mkdir(parents=True)
    payload = assets / 'payload.txt'
    payload.write_bytes(b'checkpoint')
    payload.with_suffix('.txt.meta').write_bytes(b'metadata')
    scene = assets / 'Current.unity'
    if mode == 'present':
        scene.write_bytes(b'old scene')
    record = {'schema': 'vrcforge.checkpoint.v1', 'id': 'ckpt_scene_preflight',
              'createdAt': '2026-10-06T00:00:00+00:00', 'targetTool': 'vrcforge_scene_save',
              'projectRoot': str(project), 'status': 'unavailable'}
    if mode == 'scoped':
        record['archiveFiles'] = ['Assets/payload.txt']
    if mode == 'known_empty':
        record['unityPrepare'] = {'ok': True, 'scenes': [], 'activeScenePath': ''}
    if mode in {'known_present', 'known_missing'}:
        record['unityPrepare'] = {'ok': True, 'scenes': ['Assets/Original.unity'],
                                  'activeScenePath': 'Assets/Original.unity'}
    if mode == 'known_present':
        (assets / 'Original.unity').write_bytes(b'original scene')
    checkpoint = service._create_archive_checkpoint(project, record)
    assert checkpoint['ok']
    payload.write_bytes(b'current')
    scene.write_bytes(b'current scene')
    cache = project / 'Library/Bee/keep.txt'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'keep cache')
    context = {'ok': True, 'scenes': ['Assets/Current.unity'], 'activeScenePath': 'Assets/Current.unity'}
    service.checkpoint_restore_prepare_handler = lambda _, *, checkpoint_files=None: dict(context)
    seen = []

    def reload(_root, value):
        seen.append(value)
        return {'ok': mode != 'reopen_failure', 'error': 'Reopen failed' if mode == 'reopen_failure' else ''}

    service.checkpoint_restore_handler = reload
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'confirmRestore': True})
    blocked = mode in {'missing', 'known_missing', 'reopen_failure'}
    assert result['ok'] is not blocked, result
    assert payload.read_bytes() == (b'current' if blocked else b'checkpoint')
    assert cache.read_bytes() == b'keep cache'
    assert len(seen) == 1
    if blocked:
        assert result['code'] == 'checkpoint_scene_not_in_archive'
        assert result['missingScenes'] == ['Assets/Original.unity' if mode == 'known_missing' else 'Assets/Current.unity']
        assert scene.read_bytes() == b'current scene'
        assert seen[0]['scenes'] == context['scenes']
        assert seen[0]['activeScenePath'] == context['activeScenePath']
        assert seen[0]['restoredFiles'] == seen[0]['deletedFiles'] == []
        assert result.get('restored') is not True
        assert bool(result.get('checkpointRecoveryRequired')) is (mode == 'reopen_failure')
    elif mode == 'known_empty':
        assert not scene.exists()
        assert seen[0]['scenes'] == []
    elif mode == 'known_present':
        assert not scene.exists()
        assert seen[0]['scenes'] == ['Assets/Original.unity']
        assert (assets / 'Original.unity').read_bytes() == b'original scene'
    elif mode == 'present':
        assert scene.read_bytes() == b'old scene'
    else:
        assert scene.read_bytes() == b'current scene'
