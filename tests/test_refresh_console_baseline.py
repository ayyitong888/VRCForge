import pytest
import dashboard_server as server
from agent_completion_verifier import UnityConsoleCompletionVerifier
from test_console_baseline_provenance import snapshot, WARNING


@pytest.mark.parametrize('mode', ['cold', 'new_warning', 'error', 'fallback', 'changed_process', 'pending'])
def test_registered_refresh_cold_baseline(mode, monkeypatch):
    registered = server.AGENT_GATEWAY._write_handlers['vrcforge_refresh_asset_database']
    before = snapshot() if mode == 'new_warning' else snapshot('console_log', '')
    after = snapshot(warnings=[WARNING], errors=[{'message': 'error CS0000'}] if mode == 'error' else [])
    if mode == 'fallback': after = snapshot('console_log', '', warnings=[WARNING])
    if mode == 'changed_process': after['result']['data']['unityProcessId'] += 1
    reads = [before, after, after]
    verifier = UnityConsoleCompletionVerifier(lambda _: reads.pop(0), timeout_seconds=2, sleep=lambda _: None)
    monkeypatch.setattr(server, 'UNITY_CONSOLE_COMPLETION_VERIFIER', verifier)
    args = {'projectPath': 'D:/Project'}
    baseline = registered.verification_prepare_handler(args)
    raw = {'ok': mode != 'pending', 'status': 'pending' if mode == 'pending' else 'done',
           'completionKnown': mode != 'pending', 'commitState': 'unknown', 'committed': None}
    result = registered.verification_finalize_handler(args, baseline, raw)
    assert result['consoleVerified'] is (mode == 'cold')
    assert result['commitState'] == 'unknown' and result['committed'] is None
    if mode == 'cold':
        check = result['consoleVerification']
        assert check['comparisonAvailable'] is False
        assert check['newWarningCount'] is None
        assert check['observedWarningCount'] == 1
        assert 'not classified as new' in check['summary']


def test_registered_refresh_shared_recovery_closes_without_fabricating_commit(tmp_path, monkeypatch):
    from test_external_mcp_apply_recovery import _gateway, _project
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    name = 'vrcforge_refresh_asset_database'
    gateway._write_handlers[name] = server.AGENT_GATEWAY._write_handlers[name]
    service = gateway.approval_transactions
    reads = [snapshot('console_log', ''), snapshot(warnings=[WARNING]), snapshot(warnings=[WARNING])]
    monkeypatch.setattr(server, 'UNITY_CONSOLE_COMPLETION_VERIFIER',
                        UnityConsoleCompletionVerifier(lambda _: reads.pop(0), timeout_seconds=2, sleep=lambda _: None))
    prepared = service.prepare_external_mcp_write(name, {'projectPath': str(project), 'resolvePackages': False})
    monkeypatch.setattr(type(service), '_create_pre_write_checkpoint',
                        lambda *_: {'ok': True, 'id': 'refresh-checkpoint', 'projectRoot': str(project)})
    # Replace only the Core transport: registered plan, metadata, baseline/finalizer,
    # external transaction and persisted recovery state all remain production code.
    monkeypatch.setattr(type(service), '_call_external_mcp_write_handler',
                        lambda *_: server.normalize_refresh_asset_database_outcome({
                            'ok': True, 'status': 'done', 'after': {'compile': {
                                'captureComplete': True, 'isCompiling': False, 'errorCount': 0,
                                'warnings': [WARNING], 'warningCount': 1}}}))
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result['ok'] is True, result
    assert result['commitState'] == 'unknown'
    assert result['consoleVerification']['newWarningCount'] is None
    assert result['recovery']['status'] == 'applied'
    assert result['recovery']['resolution'] == 'write_completed'
    assert service._ports.checkpoint.active_apply_recoveries() == []
