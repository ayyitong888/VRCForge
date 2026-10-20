import hashlib
import json
from pathlib import Path

import pytest

from test_recovery_safety_scope import scoped_incident as _scoped_incident


def scoped_incident(tmp_path):
    result = _scoped_incident(tmp_path)
    result[3].update(createdAt='2026-10-17T00:00:00Z', targetTool='vrcforge_manage_fx_animator')
    result[0].checkpoint_recovery._append_checkpoint(result[3])
    return result


def evidence(project, *, large=False, name='a'):
    relative = 'Library/VRCForge/CheckpointEvidence/' + name * 32 + '.json'
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps([{'assetPath': 'Assets/Unrelated.shader', 'serializedState': 'x' * (9_000_000 if large else 30)}]).encode()
    path.write_bytes(raw)
    return path, {'reference': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'byteCount': len(raw), 'objectCount': 1, 'summary': {'total': 1}}


def test_large_original_json_is_validated_without_inlining(tmp_path):
    from checkpoint_unity_evidence import validate_evidence_reference
    path, ref = evidence(tmp_path, large=True)
    assert validate_evidence_reference(tmp_path, ref) == ref
    assert len(json.dumps(ref)) < 500
    assert path.stat().st_size > 8_388_608


@pytest.mark.parametrize('field,value', [('sha256', '0' * 64), ('byteCount', 1),
                                        ('objectCount', 5), ('reference', '../outside.json')])
def test_invalid_evidence_is_rejected(tmp_path, field, value):
    from checkpoint_unity_evidence import validate_evidence_reference
    _, ref = evidence(tmp_path)
    with pytest.raises(ValueError):
        validate_evidence_reference(tmp_path, {**ref, field: value})


def test_symlink_directory_does_not_escape_project(tmp_path):
    from checkpoint_unity_evidence import validate_evidence_reference
    outside = tmp_path / 'outside'
    _, ref = evidence(outside)
    project = tmp_path / 'project'
    project.mkdir()
    try:
        (project / 'Library').symlink_to(outside / 'Library', target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks needs OS privileges')
    with pytest.raises(ValueError):
        validate_evidence_reference(project, ref)


def test_prepare_failure_registers_refs_and_manual_close_cleans(tmp_path):
    gateway, project, _, checkpoint, recovery = scoped_incident(tmp_path)
    path, ref = evidence(project)
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = lambda _, **kw: {'ok': False, 'error': 'blocked', 'dirtyAssetEvidenceBefore': ref}
    service.checkpoint_restore_handler = lambda *_: {'ok': True}
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'recoveryId': recovery['id'], 'confirmRestore': True})
    assert result['status'] == 'restore_prepare_failed'
    current = service._select_apply_recovery({'recoveryId': recovery['id']})
    assert current['unityEvidenceReferences'] == [ref]
    assert path.exists()
    assert current.get('restoreAttemptCount', 0) == 1
    closed = service.resolve_interrupted_apply_recovery({'recoveryId': recovery['id'], 'confirmResolved': True})
    assert closed['ok'] and not path.exists()
    assert closed['recovery']['unityEvidenceCleanup']['deleted'] == [ref['reference']]


def test_prepare_refs_retained_on_safety_backup_failure(tmp_path, monkeypatch):
    gateway, project, _, checkpoint, recovery = scoped_incident(tmp_path)
    path, ref = evidence(project)
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = lambda _, **kw: {'ok': True, 'dirtyAssetEvidenceBefore': ref}
    service.checkpoint_restore_handler = lambda *_: {'ok': True}
    monkeypatch.setattr(type(service), '_create_archive_checkpoint', lambda *_a, **_kw: {'ok': False, 'error': 'backup failed'})
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'recoveryId': recovery['id'], 'confirmRestore': True})
    assert result['status'] == 'restore_safety_backup_failed'
    assert path.exists()
    assert service._select_apply_recovery({'recoveryId': recovery['id']})['unityEvidenceReferences'] == [ref]


def test_closed_cleanup_preserves_modified_or_unknown_files(tmp_path):
    from checkpoint_unity_evidence import cleanup_recovery_evidence
    path, ref = evidence(tmp_path)
    path.write_text('changed')
    unknown = path.with_name('b' * 32 + '.json')
    unknown.write_text('[]')
    result = cleanup_recovery_evidence({'projectRoot': str(tmp_path), 'unityEvidenceReferences': [ref]})
    assert path.exists() and unknown.exists()
    assert result['errors'] and result['preserved'] == [ref['reference']]


def test_restore_error_preserves_transport_byte_counts(tmp_path):
    from unity_mcp_core_auth import UnityMcpCoreError
    gateway, _, _, checkpoint, recovery = scoped_incident(tmp_path)
    failure = UnityMcpCoreError('response too large')
    failure.details = {'kind': 'json_rpc_error', 'data': {'actualResponseBytes': 18_000_000, 'maxResponseBytes': 8_388_608}, 'large': 'x' * 100_000}
    def prepare(*_args, **_kw):
        raise failure
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = prepare
    service.checkpoint_restore_handler = lambda *_: {'ok': True}
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'recoveryId': recovery['id'], 'confirmRestore': True})
    details = result['unityRestorePrepare']['details']
    assert details['data']['actualResponseBytes'] == 18_000_000
    assert details['data']['maxResponseBytes'] == 8_388_608
    assert len(json.dumps(details)) < 8192


@pytest.mark.parametrize('reload_ok', [False, True])
def test_reload_refs_registered_before_natural_close_or_retained_for_retry(tmp_path, reload_ok):
    gateway, project, controller, checkpoint, recovery = scoped_incident(tmp_path)
    before_path, before_ref = evidence(project)
    after_path, after_ref = evidence(project, name='b')
    controller.write_text('changed')
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = lambda _, **kw: {'ok': True, 'scenes': [], 'dirtyAssetEvidenceBefore': before_ref}
    service.checkpoint_restore_handler = lambda *_: {'ok': reload_ok, 'error': '' if reload_ok else 'retry', 'dirtyAssetEvidenceAfter': after_ref}
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'recoveryId': recovery['id'], 'confirmRestore': True})
    assert result['ok'] is reload_ok
    current = service._select_apply_recovery({'recoveryId': recovery['id']}, include_resolved=True)
    assert current['unityEvidenceReferences'] == [before_ref, after_ref]
    assert current['restoreAttemptCount'] == 1
    assert controller.read_text() == 'original controller'
    assert (project / 'Assets/unrelated.txt').read_text() == 'keep unrelated'
    if reload_ok:
        assert current['status'] == 'restored'
        assert current['unityEvidenceCleanup']['deleted'] == [before_ref['reference'], after_ref['reference']]
        assert not before_path.exists() and not after_path.exists()
    else:
        assert current['status'] == 'needs_recovery'
        assert before_path.exists() and after_path.exists()


def test_invalid_prepare_evidence_blocks_before_attempt_and_preserves_file(tmp_path):
    gateway, project, controller, checkpoint, recovery = scoped_incident(tmp_path)
    path, ref = evidence(project)
    ref['sha256'] = '0' * 64
    controller.write_text('changed')
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = lambda _, **kw: {'ok': True, 'dirtyAssetEvidenceBefore': ref}
    service.checkpoint_restore_handler = lambda *_: pytest.fail('Invalid evidence must block restore')
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'recoveryId': recovery['id'], 'confirmRestore': True})
    assert result['status'] == 'restore_prepare_failed'
    assert result['unityRestorePrepare']['errorCode'] == 'checkpoint_evidence_invalid'
    current = service._select_apply_recovery({'recoveryId': recovery['id']})
    assert current.get('restoreAttemptCount', 0) == 1
    assert current['unityEvidenceWarnings']
    assert controller.read_text() == 'changed' and path.exists()


def test_explicit_needs_recovery_cleanup_keeps_registered_file(tmp_path):
    from checkpoint_unity_evidence import cleanup_recovery_evidence
    path, ref = evidence(tmp_path)
    result = cleanup_recovery_evidence({'status': 'needs_recovery', 'projectRoot': str(tmp_path), 'unityEvidenceReferences': [ref]})
    assert path.exists() and result['deleted'] == []


def test_failed_normalized_core_envelope_registers_before_reference(tmp_path):
    gateway, project, _, checkpoint, recovery = scoped_incident(tmp_path)
    path, ref = evidence(project)
    service = gateway.checkpoint_recovery
    service.checkpoint_restore_prepare_handler = lambda _, **kw: {
        'ok': False, 'result': {'payload': {'structuredContent': {
            'success': False, 'data': {'dirtyAssetEvidenceBefore': ref}}}}}
    service.checkpoint_restore_handler = lambda *_: {'ok': True}
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'recoveryId': recovery['id'], 'confirmRestore': True})
    assert not result['ok'] and path.exists()
    assert service._select_apply_recovery({'recoveryId': recovery['id']})['unityEvidenceReferences'] == [ref]


def test_manual_restore_without_incident_retains_evidence_until_checkpoint_deleted(tmp_path):
    gateway, project, _, checkpoint, recovery = scoped_incident(tmp_path)
    service = gateway.checkpoint_recovery
    service.resolve_interrupted_apply_recovery({'recoveryId': recovery['id'], 'confirmResolved': True})
    path, ref = evidence(project)
    service.checkpoint_restore_prepare_handler = lambda _, **kw: {'ok': True, 'scenes': [], 'dirtyAssetEvidenceBefore': ref}
    service.checkpoint_restore_handler = lambda *_: {'ok': True}
    result = service.restore_checkpoint({'checkpointId': checkpoint['id'], 'confirmRestore': True})
    assert result['ok'] and path.exists()
    assert service._load_checkpoint(checkpoint['id'])['unityEvidenceReferences'] == [ref]
    deleted = service.delete_checkpoint_archives([checkpoint['id']])
    assert deleted['deletedCount'] == 1 and not path.exists()


def test_closed_transition_uses_latest_refs_even_with_stale_outer_recovery(tmp_path):
    gateway, project, _, _, recovery = scoped_incident(tmp_path)
    path, ref = evidence(project)
    gateway.checkpoint_recovery._append_apply_recovery_entry({**recovery, 'unityEvidenceReferences': [ref]})
    saved = gateway.approval_transactions._finish_apply_recovery(
        recovery, status='dismissed', resolution='test_closed')
    assert saved['unityEvidenceReferences'] == [ref]
    assert not path.exists()


def test_failed_closed_persistence_does_not_delete_active_evidence(tmp_path, monkeypatch):
    gateway, project, _, _, recovery = scoped_incident(tmp_path)
    path, ref = evidence(project)
    service = gateway.checkpoint_recovery
    service._append_apply_recovery_entry({**recovery, 'unityEvidenceReferences': [ref]})
    def fail_append(*_args):
        raise OSError('ledger unavailable')
    # Other suites reload the facade/module. Patch the exact append callable's
    # globals retained in this gateway's ports, rather than a newer import.
    append = gateway.approval_transactions._ports.checkpoint.append_apply_recovery_entry
    monkeypatch.setitem(append.__func__.__globals__, 'flush_and_fsync', fail_append)
    with pytest.raises(OSError, match='ledger unavailable'):
        gateway.approval_transactions._finish_apply_recovery(
            recovery, status='dismissed', resolution='test_closed')
    assert path.exists()
