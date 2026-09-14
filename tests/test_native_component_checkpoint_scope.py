"""CanvasGroup add/remove checkpoint scope through registration and real archive restore."""
from copy import deepcopy
from dataclasses import replace
import zipfile
import pytest
from test_agent_checkpoint_recovery_service import _gateway


@pytest.fixture
def case(tmp_path,monkeypatch):
    import dashboard_server as server
    root=tmp_path/'project'
    for folder in ('Assets','Packages','ProjectSettings'):(root/folder).mkdir(parents=True)
    for name in ('Assets/Main.unity','Assets/Main.unity.meta','Assets/Unrelated.mat','Assets/Unrelated.mat.meta'):(root/name).write_text(name)
    target={'schema':'vrcforge.execution_target.v1','scope':'object','project':{'root':str(root)},
            'editor':{'coreInstanceId':'core'},'scene':{'assetPath':'Assets/Main.unity','guid':'scene','digest':'a'*64},
            'object':{'globalObjectId':'object','exactHierarchyPath':'Avatar/Clothing'}}
    request={'projectPath':str(root),'executionTarget':target,'gameObjectPath':'Avatar/Clothing','componentType':'UnityEngine.CanvasGroup'}
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',lambda *_:{'ok':True,'fallback':True})
    def discover(arguments):
        assert arguments['objectGlobalObjectId']=='object'
        return {'ok':True,'targets':[deepcopy(target)]}
    monkeypatch.setattr(server,'list_execution_targets_sync',discover)
    return server,root,target,request


@pytest.mark.parametrize('remove',[False,True])
def test_native_component_archive_and_restore_preserve_unrelated_asset(case,tmp_path,remove):
    server,root,target,request=case
    tool='vrcforge_remove_component' if remove else 'vrcforge_add_component'
    if remove:
        target['scope']='component';target['component']={'globalObjectId':'component','type':'UnityEngine.CanvasGroup'}
    handler=server.AGENT_GATEWAY._write_handlers[tool]
    prepared=handler.checkpoint_prepare_handler(root,{**request,'_checkpointTargetTool':tool})
    assert prepared['archiveAssetPaths']==['Assets/Main.unity']
    gateway=_gateway(tmp_path/'app');service=gateway.approval_transactions
    service.register_write_handler(tool,'component','medium',lambda _:None,checkpoint_prepare_handler=handler.checkpoint_prepare_handler)
    service._ports=replace(service._ports,run_git=lambda *a,**k:{'ok':False,'returncode':1,'stdout':'','error':'not git'})
    checkpoint=service._create_pre_write_checkpoint({'id':'approval','targetTool':tool},{**request,'archiveFiles':['Assets/Unrelated.mat']})
    assert checkpoint['ok'] is True
    with zipfile.ZipFile(checkpoint['archivePath']) as archive:assert set(archive.namelist())=={'Assets/Main.unity','Assets/Main.unity.meta'}
    (root/'Assets/Main.unity').write_text('changed');(root/'Assets/Unrelated.mat').write_text('preserve')
    assert gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)['ok'] is True
    assert (root/'Assets/Main.unity').read_text()=='Assets/Main.unity'
    assert (root/'Assets/Unrelated.mat').read_text()=='preserve'


@pytest.mark.parametrize('kind',['user_script','path_mismatch','missing_target','remove_wrong_type','project_drift','invalid_scene'])
def test_unproven_component_scope_keeps_full_preparation(case,kind):
    server,root,target,request=case;tool='vrcforge_add_component'
    if kind=='user_script':request['componentType']='Example.CustomBehaviour'
    elif kind=='path_mismatch':request['gameObjectPath']='Other'
    elif kind=='missing_target':request.pop('executionTarget')
    elif kind=='remove_wrong_type':
        tool='vrcforge_remove_component';target['scope']='component';target['component']={'globalObjectId':'component','type':'Example.CustomBehaviour'}
    elif kind=='project_drift':target['project']['root']=str(root/'Other')
    else:target['scene']['assetPath']='Assets/../Outside.unity'
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{**request,'_checkpointTargetTool':tool})
    assert 'archiveAssetPaths' not in result


@pytest.mark.parametrize('kind',['scene','object','editor','ambiguous'])
def test_fresh_identity_drift_blocks_archive_offer(case,monkeypatch,kind):
    server,root,target,request=case;fresh=deepcopy(target)
    if kind=='scene':fresh['scene']['digest']='b'*64
    elif kind=='object':fresh['object']['globalObjectId']='other'
    elif kind=='editor':fresh['editor']['coreInstanceId']='other'
    monkeypatch.setattr(server,'list_execution_targets_sync',lambda *_:{'ok':True,'targets':[fresh,fresh] if kind=='ambiguous' else [fresh]})
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{**request,'_checkpointTargetTool':'vrcforge_add_component'})
    assert result['ok'] is False and 'archiveAssetPaths' not in result
