"""Generic component checkpoint scope through registration and real archive restore."""
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


@pytest.mark.parametrize('kind',['path_mismatch','missing_target','remove_wrong_type','project_drift','invalid_scene'])
def test_unproven_component_scope_keeps_full_preparation(case,kind):
    server,root,target,request=case;tool='vrcforge_add_component'
    if kind=='path_mismatch':request['gameObjectPath']='Other'
    elif kind=='missing_target':request.pop('executionTarget')
    elif kind=='remove_wrong_type':
        tool='vrcforge_remove_component';target['scope']='component';target['component']={'globalObjectId':'component','type':'Example.CustomBehaviour'}
    elif kind=='project_drift':target['project']['root']=str(root/'Other')
    else:target['scene']['assetPath']='Assets/../Outside.unity'
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{**request,'_checkpointTargetTool':tool})
    assert 'archiveAssetPaths' not in result


def test_custom_component_add_uses_full_project_checkpoint_after_identity_revalidation(case,monkeypatch):
    server,root,target,request=case
    request['componentType']='Example.PluginBehaviour'
    calls=[]
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',lambda project,*args,**kwargs:
        calls.append((project,args,kwargs)) or {'ok':True,'scope':'full_project'})
    handler=server.AGENT_GATEWAY._write_handlers['vrcforge_add_component']
    result=handler.checkpoint_prepare_handler(root,{**request,'_checkpointTargetTool':'vrcforge_add_component'})
    assert result=={'ok':True,'scope':'full_project'}
    assert calls==[(root,(),{})]


def test_custom_component_remove_keeps_existing_path_and_uses_full_project_checkpoint(case,monkeypatch):
    server,root,target,request=case
    tool='vrcforge_remove_component'
    custom_type='Example.PluginBehaviour'
    request['componentType']=custom_type
    target['scope']='component'
    target['component']={'globalObjectId':'component','type':custom_type}
    calls=[]
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',lambda project,*args,**kwargs:
        calls.append((project,args,kwargs)) or {'ok':True,'scope':'full_project'})
    handler=server.AGENT_GATEWAY._write_handlers[tool]
    result=handler.checkpoint_prepare_handler(root,{**request,'_checkpointTargetTool':tool})
    assert result=={'ok':True,'scope':'full_project'}
    assert calls==[(root,(),{})]


@pytest.mark.parametrize('kind',['scene','object','editor','ambiguous','ambiguous_flag','candidate_count'])
def test_fresh_identity_drift_blocks_archive_offer(case,monkeypatch,kind):
    server,root,target,request=case;fresh=deepcopy(target)
    if kind=='scene':fresh['scene']['guid']='other-scene'
    elif kind=='object':fresh['object']['globalObjectId']='other'
    elif kind=='editor':fresh['editor']['coreInstanceId']='other'
    elif kind=='ambiguous_flag':fresh['ambiguous']=True
    elif kind=='candidate_count':fresh['resolutionCandidateCount']=2
    monkeypatch.setattr(server,'list_execution_targets_sync',lambda *_:{'ok':True,'targets':[fresh,fresh] if kind=='ambiguous' else [fresh]})
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{**request,'_checkpointTargetTool':'vrcforge_add_component'})
    assert result['ok'] is False and 'archiveAssetPaths' not in result


@pytest.mark.parametrize('tool', ['vrcforge_add_component', 'vrcforge_remove_component', 'vrcforge_toggle_scene_object'])
def test_saved_scene_content_changes_do_not_replace_bound_checkpoint_owner(case, monkeypatch, tool):
    server, root, target, request = case
    target.update(ambiguous=False, resolutionCandidateCount=1)
    if tool == 'vrcforge_remove_component':
        target['scope'] = 'component'
        target['component'] = {'globalObjectId': 'component', 'type': 'UnityEngine.CanvasGroup'}
    request.update(objectPath='Avatar/Clothing', active=False)
    fresh = deepcopy(target)
    target['scene'].pop('digest')
    fresh['scene'].update(digest='different saved bytes', revision='later save')
    monkeypatch.setattr(server, 'list_execution_targets_sync', lambda *_: {'ok': True, 'targets': [fresh]})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**request, '_checkpointTargetTool': tool})
    assert result['ok'] is True, result
    assert result['archiveAssetPaths'] == ['Assets/Main.unity']


def test_remove_discovery_uses_complete_component_query_once(case,monkeypatch):
    server,root,target,request=case
    target['scope']='component';target['component']={'globalObjectId':'component','type':'UnityEngine.CanvasGroup'}
    observed=[]
    def exact_discover(query):
        observed.append(query)
        matches=query.get('componentGlobalObjectId')=='component' and query.get('objectGlobalObjectId')=='object'
        return {'ok':True,'targets':[deepcopy(target)] if matches else []}
    monkeypatch.setattr(server,'list_execution_targets_sync',exact_discover)
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{**request,'_checkpointTargetTool':'vrcforge_remove_component'})
    assert result['ok'] is True,result
    assert result['archiveAssetPaths']==['Assets/Main.unity']
    assert len(observed)==1
