from copy import copy
import pytest
import dashboard_server as server
from agent_completion_verifier import UnityConsoleCompletionVerifier
from test_console_baseline_provenance import snapshot
from test_external_mcp_apply_recovery import _gateway, _project

NAME='vrcforge_refresh_asset_database'

@pytest.mark.parametrize('mode', ['package_resolution', 'importer_restoration'])
def test_registered_project_refresh_creates_checkpoint_before_dispatch(tmp_path, monkeypatch, mode):
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    gateway._write_handlers[NAME] = copy(server.AGENT_GATEWAY._write_handlers[NAME])
    service = gateway.approval_transactions
    (project/'Packages/manifest.json').write_text('{}', encoding='utf-8')
    order = []
    def prepare(root, checkpoint_asset_paths=None, *, checkpoint_files=None):
        assert root == project
        assert checkpoint_asset_paths is None
        assert checkpoint_files is None
        order.append('prepare_full_project')
        return {'ok': True, 'projectPath': str(root)}
    monkeypatch.setattr(server, 'prepare_unity_checkpoint_sync', prepare)
    reads = [snapshot(), snapshot(), snapshot()]
    monkeypatch.setattr(server, 'UNITY_CONSOLE_COMPLETION_VERIFIER', UnityConsoleCompletionVerifier(
        lambda _: reads.pop(0), timeout_seconds=2, sleep=lambda _: None))
    def transport(*args, **kwargs):
        order.append('dispatch')
        return {'ok': True, 'status': 'done', 'completionKnown': True,
                'mutationStarted': False, 'committed': True, 'commitState': 'none'}
    monkeypatch.setattr(type(service), '_call_external_mcp_write_handler', transport)
    changes = {'resolvePackages': True} if mode == 'package_resolution' else {
        'resolvePackages': False,
        'reimportAssets': [dict(item(), restoreScriptedImporterReference={
            'scriptGuid': 'c'*32, 'expectedMetadataSha256': 'd'*64, 'restoredMetadataSha256': 'e'*64})],
    }
    prepared = service.prepare_external_mcp_write(NAME, {'projectPath': str(project), **changes})
    assert order == []
    result = service.execute_prepared_external_mcp_write(prepared)
    assert result['ok'] is True, result
    assert order == ['prepare_full_project', 'dispatch']
    assert result.get('checkpoint'), result
def item():
    return {'assetPath':'Assets/Editor/AnyScript.cs','guid':'a'*32,'expectedSourceSha256':'b'*64,'expectedImporterType':'UnityEditor.MonoImporter'}

@pytest.mark.parametrize('changes,checkpoint', [
    ({},False),({'resolvePackages':False},False),({'resolvePackages':True},True),
    ({'resolvePackages':0},True),({'resolve_packages':True},True),
    ({'reimportAssets':[]},True),({'reimportAssets':[dict(item(),unexpected=True)]},True),
    ({'reimportAssets':[dict(item(),restoreScriptedImporterReference={'scriptGuid':'c'*32,'expectedMetadataSha256':'d'*64,'restoredMetadataSha256':'e'*64})]},True),
    ({'reimportAssets':None},True),
])
def test_registered_policy_follows_actual_operation(changes,checkpoint):
    from agent_approval_transactions import _requires_pre_write_checkpoint
    args={'projectPath':'D:/Project','reimportAssets':[item()],**changes}
    assert _requires_pre_write_checkpoint(server.AGENT_GATEWAY._write_handlers[NAME],args) is checkpoint


@pytest.mark.parametrize('exact_reimport',[False,True])
def test_production_registration_approval_execute_pure_reimport_never_checkpoints(tmp_path,monkeypatch,exact_reimport):
    gateway=_gateway(tmp_path);project=_project(tmp_path)
    gateway._write_handlers[NAME]=copy(server.AGENT_GATEWAY._write_handlers[NAME]);service=gateway.approval_transactions
    reads=[snapshot(),snapshot(),snapshot()]
    monkeypatch.setattr(server,'UNITY_CONSOLE_COMPLETION_VERIFIER',UnityConsoleCompletionVerifier(lambda _:reads.pop(0),timeout_seconds=2,sleep=lambda _:None))
    monkeypatch.setattr(type(service),'_create_pre_write_checkpoint',lambda *_:pytest.fail('global-save checkpoint invoked'))
    calls=[]
    def transport(*args,**kwargs):
        calls.append(args)
        return {'ok':True,'status':'done','completionKnown':True,'mutationStarted':False,'committed':True,'commitState':'none'}
    monkeypatch.setattr(type(service),'_call_external_mcp_write_handler',transport)
    args={'projectPath':str(project),'resolvePackages':False,'reimportAssets':[item()]}
    if not exact_reimport:args.pop('reimportAssets')
    prepared=service.prepare_external_mcp_write(NAME,args)
    assert calls==[]
    assert prepared['approvedUnityExecutionPlan']['calls'][0]['toolName']=='vrc_refresh_asset_database'
    result=service.execute_prepared_external_mcp_write(prepared)
    assert result['ok'] is True,result
    assert len(calls)==1
    assert not result.get('checkpoint')
    assert not result.get('recovery')


def test_registered_reimport_disabled_write_authority_rejected(tmp_path):
    gateway=_gateway(tmp_path);project=_project(tmp_path)
    gateway._write_handlers[NAME]=copy(server.AGENT_GATEWAY._write_handlers[NAME])
    config=gateway.ensure_config();config.allow_write_requests=False;gateway.save_config(config)
    from agent_gateway import AgentGatewayError
    with pytest.raises(AgentGatewayError):
        gateway.approval_transactions.prepare_external_mcp_write(NAME,{'projectPath':str(project),'reimportAssets':[item()]})



def test_default_refresh_no_longer_saves_assets_in_core():
    from pathlib import Path
    source=(Path(__file__).parents[1]/'Assets/VRCForge/Editor/OutfitPackageImporter.cs').read_text(encoding='utf-8')
    body=source.split('private static void RunScheduledRefresh()',1)[1].split('private static void TryCompleteScheduledRefresh()',1)[0]
    assert 'AssetDatabase.SaveAssets' not in body
    assert 'AssetDatabase.Refresh();' in body

@pytest.mark.parametrize('capability,allowed', [(None,False),(True,False),(False,True)])
def test_default_refresh_checks_loaded_core_semantics_before_dispatch(monkeypatch,capability,allowed):
    from types import SimpleNamespace
    from agent_gateway import AgentGatewayError
    settings=SimpleNamespace(unity_project_path='D:/Project',unity_mcp_timeout_seconds=30)
    monkeypatch.setattr(server,'load_dashboard_settings',lambda _:settings)
    monkeypatch.setattr(server,'build_agent_connection_request',lambda p:p)
    class Client:
        def __init__(self,*a,**k):pass
        def core_info(self):return {} if capability is None else {'assetDatabaseRefreshSavesDirtyAssets':capability}
    monkeypatch.setattr(server,'UnityMcpCoreClient',Client)
    calls=[]
    monkeypatch.setattr(server,'invoke_unity_mcp',lambda *a:calls.append(a) or {'ok':True})
    monkeypatch.setattr(server,'extract_tool_result_payload',lambda p:p)
    monkeypatch.setattr(server,'poll_unity_async_job_sync',lambda *a,**k:{'status':'running'})
    if allowed:
        server.refresh_asset_database_sync({'projectPath':'D:/Project'})
        assert len(calls)==1
    else:
        with pytest.raises(AgentGatewayError,match='non-saving refresh'):
            server.refresh_asset_database_sync({'projectPath':'D:/Project'})
        assert calls==[]
