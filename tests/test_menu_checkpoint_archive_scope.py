"""Production checkpoint preparation/archiver seams; no Unity Editor or project opened."""
import zipfile
from dataclasses import replace
from pathlib import Path
import pytest
from test_agent_checkpoint_recovery_service import _gateway

MENU='vrcforge_manage_expression_menu'
ASSET='Assets/Menu.asset'

def _prepare(monkeypatch,root,*,action='update',extra=None,plan_changes=None):
    import dashboard_server as server
    plan={'action':action,'targetMenuExists':True,'targetMenuAssetPath':ASSET,'checkpointAssetPaths':[ASSET],'newMenuAssetPaths':[]}
    plan.update(plan_changes or {})
    monkeypatch.setattr(server,'manage_expression_menu_sync',lambda *a,**kw:{'ok':True,'plan':plan})
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',lambda *a:{'ok':True,'assetBaseline':[{'assetPath':ASSET,'assetGuid':'a'*32,'serializedState':{}}]})
    handler=server.AGENT_GATEWAY._write_handlers[MENU]
    return handler.checkpoint_prepare_handler(root,{'_checkpointTargetTool':MENU,'action':action,**(extra or {})})

@pytest.mark.parametrize('action',['update','delete','reorder'])
def test_registered_existing_menu_prepare_offers_only_server_asset_scope(tmp_path,monkeypatch,action):
    result=_prepare(monkeypatch,tmp_path,action=action,extra={'archiveFiles':['Assets/Caller.asset']})
    assert result['archiveAssetPaths']==[ASSET]
    assert result['archiveScopeReason']=='existing_menu_asset_only'
    assert result['assetBaselineRequired'] is True

@pytest.mark.parametrize('extra,change',[
 ({},{'action':'create'}),({},{'targetMenuExists':False}),({},{'newMenuAssetPaths':['Assets/New.asset']}),
 ({'createSubMenu':True},{}),({'create_sub_menu':True},{}),({'subMenuAssetPath':'Assets/Other.asset'},{}),
 ({'sub_menu_asset_path':'Assets/Other.asset'},{}),({'iconAssetPath':''},{}),({'icon_asset_path':''},{}),
 ({},{'targetMenuAssetPath':'Assets/Other.asset'}),({},{'newMenuAssetPaths':None}),
])
def test_unbounded_menu_changes_keep_full_scope_with_reason(tmp_path,monkeypatch,extra,change):
    result=_prepare(monkeypatch,tmp_path,extra=extra,plan_changes=change)
    assert 'archiveAssetPaths' not in result
    assert result['archiveScopeReason']=='full_project_menu_scope_not_proven'

@pytest.mark.parametrize('offer,expected',[
 ([ASSET],True),(['Assets/Missing.asset'],False),(['Assets/../outside.asset'],False),(['../outside.asset'],False),
 (['Packages/pkg.asset'],False),('Assets/Menu.asset',False),([],False),([ASSET,False],False),
])
def test_actual_checkpoint_archiver_consumes_only_valid_dedicated_scope(tmp_path,monkeypatch,offer,expected):
    root=tmp_path/'files'
    for directory in ('Assets','Packages','ProjectSettings'):(root/directory).mkdir(parents=True)
    for path in (ASSET,ASSET+'.meta','Assets/Unrelated.asset','Assets/Unrelated.asset.meta'):(root/path).write_text(path,encoding='utf-8')
    gateway=_gateway(tmp_path/'app');service=gateway.approval_transactions
    service.register_write_handler(MENU,'menu','low',lambda _:None,checkpoint_prepare_handler=lambda *a:{'ok':True,'archiveAssetPaths':offer,'archiveScopeReason':'existing_menu_asset_only'})
    service._ports=replace(service._ports,run_git=lambda *a,**kw:{'ok':False,'returncode':1,'stdout':'','error':'no repository'})
    checkpoint=service._create_pre_write_checkpoint({'id':'approval','targetTool':MENU},{'projectPath':str(root),'archiveFiles':[ASSET]})
    assert checkpoint['ok'] is True
    with zipfile.ZipFile(checkpoint['archivePath']) as archive: names=set(archive.namelist())
    if expected:
        assert names=={ASSET,ASSET+'.meta'}
        assert checkpoint['archiveFiles']==[ASSET,ASSET+'.meta']
        (root/ASSET).write_text('changed menu',encoding='utf-8')
        (root/'Assets/Unrelated.asset').write_text('keep later unrelated change',encoding='utf-8')
        restored=gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)
        assert restored['ok'] is True
        assert (root/ASSET).read_text(encoding='utf-8')==ASSET
        assert (root/'Assets/Unrelated.asset').read_text(encoding='utf-8')=='keep later unrelated change'
    else:
        assert 'Assets/Unrelated.asset' in names
        assert not checkpoint.get('archiveFiles')
        assert checkpoint['archiveScopeReason']=='full_project_prepared_asset_scope_invalid'

def test_caller_archive_scope_never_selects_menu_checkpoint(tmp_path,monkeypatch):
    result=_prepare(monkeypatch,tmp_path,action='create',extra={'archiveAssetPaths':[ASSET],'archiveFiles':[ASSET]})
    assert 'archiveAssetPaths' not in result
