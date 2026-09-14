"""Registered renderer checkpoint scope and real filesystem archive, no Unity."""
from copy import deepcopy
from dataclasses import replace
import zipfile
import pytest
from test_agent_checkpoint_recovery_service import _gateway
from test_renderer_material_slot_contract import _preview
from test_renderer_material_slots_batch import request as batch_request, preview as batch_preview

TOOL='vrcforge_set_renderer_material_slot'

@pytest.mark.parametrize('batch',[False,True])
def test_registered_prepare_and_archive_use_only_revalidated_scene(tmp_path,monkeypatch,batch):
    import dashboard_server as server
    root=tmp_path/'project'
    for folder in ('Assets','Packages','ProjectSettings'):(root/folder).mkdir(parents=True)
    preview=batch_preview() if batch else _preview()
    scene=preview['plan']['scene']['scenePath'] if batch else preview['scenePath']
    for name in (scene,scene+'.meta','Assets/Unrelated.mat','Assets/Unrelated.mat.meta'):(root/name).write_text(name,encoding='utf8')
    params=batch_request() if batch else {'rendererPath':preview['rendererPath'],'slotIndex':0,'newMaterialAssetPath':preview['newMaterialAssetPath']}
    params['projectPath']=str(root)
    if batch:params['executionTarget']['scene']['absolutePath']=str(root/scene)
    monkeypatch.setattr(server,'_invoke_authoritative_unity_preview',lambda *a:deepcopy(preview))
    canonical,_=server.prepare_renderer_material_slot_request(params,None)
    handler=server.AGENT_GATEWAY._write_handlers[TOOL]
    prepared=handler.checkpoint_prepare_handler(root,{**canonical,'_checkpointTargetTool':TOOL})
    assert prepared['archiveAssetPaths']==[scene]
    assert prepared['archiveScopeReason']=='revalidated_renderer_saved_scene_only'
    gateway=_gateway(tmp_path/'app');service=gateway.approval_transactions
    service.register_write_handler(TOOL,'renderer slot','medium',lambda _:None,checkpoint_prepare_handler=handler.checkpoint_prepare_handler)
    service._ports=replace(service._ports,run_git=lambda *a,**k:{'ok':False,'returncode':1,'stdout':'','error':'not git'})
    checkpoint=service._create_pre_write_checkpoint({'id':'approval','targetTool':TOOL},{**canonical,'archiveFiles':['Assets/Unrelated.mat']})
    assert checkpoint['ok'] is True
    with zipfile.ZipFile(checkpoint['archivePath']) as archive:assert set(archive.namelist())=={scene,scene+'.meta'}
    (root/scene).write_text('changed',encoding='utf8')
    (root/'Assets/Unrelated.mat').write_text('preserve',encoding='utf8')
    assert gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)['ok'] is True
    assert (root/scene).read_text(encoding='utf8')==scene
    assert (root/'Assets/Unrelated.mat').read_text(encoding='utf8')=='preserve'
    # Fresh authoritative preview drift must block before any archive offer.
    if batch:preview['plan']['scene']['sceneFileDigest']='9'*64
    else:preview['sceneFileDigest']='9'*64
    rejected=handler.checkpoint_prepare_handler(root,{**canonical,'_checkpointTargetTool':TOOL})
    assert rejected['ok'] is False and 'archiveAssetPaths' not in rejected

@pytest.mark.parametrize('offer',[None,['Assets/Missing.unity'],['Assets/../Outside.unity'],['Assets/NoMeta.unity']])
def test_unproven_or_missing_scene_offer_keeps_full_archive(tmp_path,offer):
    root=tmp_path/'project'
    for folder in ('Assets','Packages','ProjectSettings'):(root/folder).mkdir(parents=True)
    for name in ('Assets/Keep.mat','Assets/Keep.mat.meta','Assets/NoMeta.unity'):(root/name).write_text(name,encoding='utf8')
    gateway=_gateway(tmp_path/'app');service=gateway.approval_transactions
    service.register_write_handler(TOOL,'renderer slot','medium',lambda _:None,checkpoint_prepare_handler=lambda *a:{'ok':True,**({'archiveAssetPaths':offer} if offer is not None else {})})
    service._ports=replace(service._ports,run_git=lambda *a,**k:{'ok':False,'returncode':1,'stdout':'','error':'not git'})
    checkpoint=service._create_pre_write_checkpoint({'id':'approval','targetTool':TOOL},{'projectPath':str(root),'archiveFiles':['Assets/Keep.mat']})
    assert checkpoint['ok'] is True and not checkpoint.get('archiveFiles')
    with zipfile.ZipFile(checkpoint['archivePath']) as archive:assert 'Assets/NoMeta.unity' in archive.namelist() and 'Assets/Keep.mat' in archive.namelist()
