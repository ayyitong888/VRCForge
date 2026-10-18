from pathlib import Path
import os
import pytest
import runtime_observation as domain
from test_runtime_observation import args, complete_receipt


def test_registered_preparer_uses_core_managed_spool_not_profile(tmp_path, monkeypatch):
    import dashboard_server as ds
    monkeypatch.setattr(ds, 'DASHBOARD_ARTIFACTS_DIR', tmp_path / 'profile' / 'dashboard')
    request, _ = ds.prepare_runtime_observation_request(args(), None)
    expected = Path(os.environ['LOCALAPPDATA']) / 'VRCForge/agentic-app/artifacts/dashboard/latest/runtime-observations' / request['jobId']
    assert Path(request['outputDirectory']) == expected

def verified_receipt(tmp_path):
    request, receipt = complete_receipt(tmp_path / 'spool')
    receipt.update(width=128, height=128)
    return request, domain.validate_result(receipt, request)


def test_verified_pngs_publish_idempotently_and_keep_originals(tmp_path):
    request, receipt = verified_receipt(tmp_path)
    source = Path(request['outputDirectory']).parent
    target = tmp_path / 'profile/runtime-observations'
    frames = domain.publish_artifact_copies(receipt, source, target)
    assert domain.publish_artifact_copies(receipt, source, target) == frames
    for frame in frames:
        assert frame['sourceImagePath'] == frame['imagePath']
        assert Path(frame['imagePath']).read_bytes() == Path(frame['artifactImagePath']).read_bytes()
        assert Path(frame['artifactImagePath']).parent == target / request['jobId']
    assert domain.validate_result({**receipt, 'frames': frames}, request)['ok'] is True


@pytest.mark.parametrize('case', ['wrong_job', 'changed_source', 'changed_destination', 'unverified', 'linked_destination'])
def test_unsafe_artifact_publication_rejected(tmp_path, case):
    request, receipt = verified_receipt(tmp_path)
    source = Path(request['outputDirectory']).parent
    target = tmp_path / 'profile/runtime-observations'
    original = Path(receipt['frames'][0]['imagePath'])
    destination = target / request['jobId'] / '000.png'
    if case == 'wrong_job': receipt['jobId'] = 'f' * 32
    if case == 'changed_source': original.write_bytes(b'changed')
    if case == 'unverified': receipt['verified'] = False
    if case == 'changed_destination':
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'user-modified')
    if case == 'linked_destination':
        target.parent.mkdir(parents=True)
        outside = tmp_path / 'outside'; outside.mkdir()
        import subprocess
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(target), str(outside)], capture_output=True)
            assert result.returncode == 0
        else: target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        domain.publish_artifact_copies(receipt, source, target)
    assert original.exists()
    if case == 'changed_destination': assert destination.read_bytes() == b'user-modified'


@pytest.mark.parametrize('field', ['jobId', 'outputDirectory'])
def test_agent_cannot_supply_spool_identity(field):
    import dashboard_server as ds
    with pytest.raises(ValueError, match='App-owned'):
        ds.prepare_runtime_observation_request({**args(), field: 'agent-owned'}, None)

def test_status_and_finalizer_validate_original_then_publish_profile_url(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import dashboard_server as ds
    request, receipt = verified_receipt(tmp_path)
    request['stateDetail'] = 'inline'
    monkeypatch.setattr(domain, 'core_artifact_root', lambda: tmp_path / 'spool')
    monkeypatch.setattr(ds, 'DASHBOARD_ARTIFACTS_DIR', tmp_path / 'profile/dashboard')
    monkeypatch.setattr(ds, 'load_dashboard_settings', lambda _: {})
    monkeypatch.setattr(ds, 'build_agent_connection_request', lambda _: {})
    monkeypatch.setattr(ds, 'bound_editor_readback', lambda _: nullcontext())
    monkeypatch.setattr(ds, 'invoke_unity_mcp', lambda *a, **kw: receipt)
    monkeypatch.setattr(ds, 'extract_tool_result_payload', lambda value: value)
    status = ds.runtime_observation_status_raw(request)
    assert status['ok'] is True
    for frame in status['frames']:
        assert frame['resourceUri'].startswith('vrcforge://runtime-observation/')
        assert frame['imageUrl'].startswith('/artifacts/latest/runtime-observations/')
        assert Path(frame['artifactImagePath']).is_file()
        assert Path(frame['imagePath']).parent == Path(request['outputDirectory'])
    monkeypatch.setattr(ds, 'runtime_observation_status_raw', lambda _: status)
    final = ds.runtime_observation_finalize(request, {}, {'status': 'pending', 'ok': True})
    assert final['ok'] is True and final['verified'] is True
