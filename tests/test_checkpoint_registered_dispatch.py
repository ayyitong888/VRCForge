"""Replay actual registered plans through the shared internal/external checkpoint dispatcher."""
from copy import deepcopy
from dataclasses import replace

import pytest
import dashboard_server as server
from test_component_property_checkpoint_scope import case as property_case


@pytest.fixture
def project(tmp_path, monkeypatch):
    root=tmp_path/'Project'
    for folder in ('Assets','Packages','ProjectSettings'):(root/folder).mkdir(parents=True)
    for name in ('Assets/Main.unity','Assets/Main.unity.meta','Assets/Params.asset','Assets/Params.asset.meta'):
        (root/name).write_text(name)
    calls=[]
    def prepare(project, checkpoint_asset_paths=None, *, checkpoint_files=None):
        calls.append((project,checkpoint_files))
        return {'ok':True,'mode':'read_only'}
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',prepare)
    return root,calls


@pytest.mark.parametrize('tool,args',[
 ('vrcforge_build_test_avatar',{'avatarPath':'Avatar'}),
 ('vrcforge_build_and_upload_avatar',{'avatarPath':'Avatar','uploadMode':'private','buildType':'Avatar','platforms':['StandaloneWindows64'],'expectedAvatarGlobalObjectId':'gid','expectedCurrentPipelineId':'','expectedSdkUserId':'usr_fixture','expectedPlatform':'StandaloneWindows64','readinessDigest':'a'*64}),
 ('vrcforge_export_vrm',{'avatarPath':'Avatar','outputPath':'Assets/VRCForge/Exports/Avatar.vrm','confirmRights':True,'author':'fixture'}),
 ('vrcforge_convert_unity_constraint',{'scenePath':'Assets/Main.unity','gameObjectPath':'Avatar/Thing','componentType':'UnityEngine.ParentConstraint','componentIndex':0,'conversionMode':'convert','saveScene':True}),
 ('vrcforge_restore_safe_backup',{'backupPath':'Backups/fixture.zip','backupId':'fixture','assetPaths':['Assets/Main.unity']}),
 ('vrcforge_add_outfit_part',{'avatarPath':'Avatar','parameterName':'Wardrobe','value':1,'partName':'Hat','objectPaths':['Avatar/Hat']}),
 ('vrcforge_manage_wardrobe',{'avatarPath':'Avatar','parameterName':'Wardrobe','action':'rename','outfitName':'Original','newName':'Updated'}),
 ('vrcforge_relocate_generated_assets',{'entries':[{'sourceAssetPath':'Assets/VRCForge/Generated/A.asset','destinationAssetPath':'Assets/VRCForgeGenerated/A.asset','expectedGuid':'a'*32,'expectedAssetSha256':'b'*64,'expectedMetaSha256':'c'*64}]}),
])
def test_registered_project_writes_prepare_without_inventing_nested_tool_name(project, tool, args):
    root,calls=project
    arguments={'projectPath':str(root),**args}
    original=deepcopy(arguments)
    handler=server.AGENT_GATEWAY._write_handlers[tool]
    plan=handler.approved_execution_plan_builder(arguments)
    assert plan and all(name in server.REQUIRED_VRCFORGE_UNITY_TOOLS for name,_ in plan)
    result=handler.checkpoint_prepare_handler(root,{**arguments,'_checkpointTargetTool':tool})
    assert result['ok'] is True,result
    assert calls==[(root,None)]
    assert arguments==original


def test_nonpersistent_selection_does_not_request_a_project_checkpoint():
    handler=server.AGENT_GATEWAY._write_handlers['vrcforge_select_scene_object']
    assert handler.requires_approved_execution_context is True
    assert handler.pre_write_checkpoint_required is False


def test_expression_parameter_checkpoint_uses_authoritative_preview_asset(project,monkeypatch):
    root,calls=project
    monkeypatch.setattr(server,'manage_expression_parameters_sync',lambda args,preview=False:{'ok':True,'preview':preview,'plan':{'action':'update','assetPath':'Assets/Params.asset'}})
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{'projectPath':str(root),'_checkpointTargetTool':'vrcforge_manage_expression_parameters','avatarPath':'Avatar','action':'update','parameterName':'Wardrobe','defaultValue':1,'archiveFiles':['Assets/Main.unity']})
    assert result['ok'] is True,result
    assert calls==[(root,['Assets/Params.asset','Assets/Params.asset.meta'])]
    assert result['assetFootprint']['archiveFiles']==['Assets/Params.asset','Assets/Params.asset.meta']


def test_unknown_or_invalid_registered_plan_never_prepares(project,monkeypatch):
    root,calls=project
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{'projectPath':str(root),'_checkpointTargetTool':'vrcforge_unknown_tool'})
    assert result['ok'] is False
    handler=server.AGENT_GATEWAY._write_handlers['vrcforge_export_vrm']
    monkeypatch.setitem(server.AGENT_GATEWAY._write_handlers,handler.name,replace(handler,approved_execution_plan_builder=lambda args:[('not_a_core_write',{})]))
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{'projectPath':str(root),'_checkpointTargetTool':handler.name})
    assert result['ok'] is False
    assert calls==[]


def test_remap_uses_native_bones_property_owner_without_whole_project(property_case):
    root,target,args,scope,calls=property_case
    target['component']['type']=scope['componentType']='UnityEngine.SkinnedMeshRenderer'
    scope['propertyPath']='bones'
    request={'projectPath':str(root),'executionTarget':target,'gameObjectPath':target['object']['exactHierarchyPath'],'componentIndex':0,'boneIndex':1,'expectedCurrentBonePath':'Avatar/A','targetBonePath':'Avatar/B','expectedMeshName':'Body'}
    result=server.prepare_authoritative_unity_checkpoint_sync(root,{**request,'_checkpointTargetTool':'vrcforge_remap_skinned_mesh_bone'})
    assert result['ok'] is True,result
    assert calls==[(root,['Assets/Main.unity'])]


@pytest.mark.parametrize('tool,core',[
 ('vrcforge_configure_optimizer_component','vrc_configure_aao_merge_physbone'),
 ('vrcforge_rollback_parameters','vrc_rollback_avatar_parameters'),
])
def test_registered_prepared_callback_plans_keep_project_checkpoint(project,tool,core):
    from prepared_unity_execution import install_prepared_calls
    root,calls=project
    args=install_prepared_calls({'projectPath':str(root)},[(core,{'avatarPath':'Avatar','preview':False})],{'scope':'project'})
    handler=server.AGENT_GATEWAY._write_handlers[tool]
    assert handler.approved_execution_plan_builder(args)==[(core,{'avatarPath':'Avatar','preview':False})]
    result=handler.checkpoint_prepare_handler(root,{**args,'_checkpointTargetTool':tool})
    assert result['ok'] is True,result
    assert calls==[(root,None)]
