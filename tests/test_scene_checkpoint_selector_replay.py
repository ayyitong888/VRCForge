from copy import deepcopy

from scene_object_copy import bind_authoritative_preview, build_wrapper_arguments, compute_preview_digest, DUPLICATE_TOOL_NAME
from test_scene_object_copy import _duplicate_payload
from test_scene_workflow_checkpoint_scope import _target, _run, scene_case


def _duplicate(root, *, cross_scene=False):
    source = _target(root, 'object')
    payload = _duplicate_payload()
    payload['source'].update(scenePath='Assets/Main.unity', sceneGuid=source['scene']['guid'],
        objectPath='Avatar/Outfit')
    # The preview contract uses canonical GUIDs; mirror that real shape in the target.
    source['scene']['guid'] = payload['source']['sceneGuid'] = 'a' * 32
    source['scene']['digest'] = payload['source']['sceneFileDigest']
    destination = 'Assets/Destination.unity' if cross_scene else 'Assets/Main.unity'
    payload['target'].update(scenePath=destination, parentPath='Slime_Comparison',
        objectPath='Slime_Comparison/Slime_Hand_Test_Normal', name='Slime_Hand_Test_Normal')
    if cross_scene:
        payload['target'].update(sceneGuid='b' * 32, sceneHandle=22, sceneFileDigest='e' * 64)
        (root / destination).write_text('destination scene')
        (root / (destination+'.meta')).write_text('guid: '+'b'*32)
    payload['preserveWorldTransform'] = True
    payload['previewDigest'] = compute_preview_digest(payload)
    wrapper = build_wrapper_arguments({'projectPath':str(root),
        'sourceScenePath':'Assets/Main.unity','sourceObjectPath':'Avatar/Outfit',
        'targetParentScenePath':destination,'targetParentPath':'Slime_Comparison',
        'targetName':'Slime_Hand_Test_Normal','preserveWorldTransform':True}, DUPLICATE_TOOL_NAME)
    arguments, _approval = bind_authoritative_preview(wrapper, payload)
    arguments['executionTarget'] = source
    return arguments, destination


def test_original_duplicate_checkpoint_request_uses_target_parent_path(scene_case):
    args, destination = _duplicate(scene_case)
    before = deepcopy(args)
    result, observed = _run(scene_case, 'vrcforge_duplicate_scene_object', args)
    assert result['ok'] is True, result
    assert result['archiveAssetPaths'] == [destination]
    assert observed[-1] == ('prepare', scene_case, [destination])
    assert args == before


def test_cross_scene_duplicate_archives_destination_not_source(scene_case):
    args, destination = _duplicate(scene_case, cross_scene=True)
    result, observed = _run(scene_case, 'vrcforge_duplicate_scene_object', args)
    assert result['ok'] is True, result
    assert result['archiveAssetPaths'] == [destination]
    assert observed[-1] == ('prepare', scene_case, [destination])
    # Only the bound source needs discovery: Core revalidates both preview identities before mutation.
    assert len([x for x in observed if x[0]=='discover']) == 1


def test_duplicate_source_drift_does_not_prepare_destination(scene_case):
    args, _ = _duplicate(scene_case, cross_scene=True)
    fresh = deepcopy(args['executionTarget'])
    fresh['object']['globalObjectId'] = 'replaced-source'
    result, observed = _run(scene_case, 'vrcforge_duplicate_scene_object', args,
        fresh={'ok':True,'targets':[fresh]})
    assert result['ok'] is False
    assert all(x[0] != 'prepare' for x in observed)


def test_duplicate_rejects_nonexact_source_or_unsealed_destination(scene_case):
    for mutate in (lambda a:a['arguments'].update(sourceObjectPath='Avatar/Other'),
                   lambda a:a['arguments'].update(expectedSourceSceneGuid='other'),
                   lambda a:a['arguments'].update(targetParentScenePath='Assets/../Outside.unity'),
                   lambda a:a['arguments'].update(expectedTargetParentObjectId=''),
                   lambda a:a['arguments'].update(expectedTargetSceneGuid=''),
                   lambda a:a['arguments'].update(expectedPreviewDigest='')):
        args, _ = _duplicate(scene_case)
        mutate(args)
        result, observed = _run(scene_case, 'vrcforge_duplicate_scene_object', args)
        assert result['ok'] is False, args
        assert observed == []


def test_reparent_to_scene_root_uses_exact_source_scene(scene_case):
    target = _target(scene_case, 'object')
    result, observed = _run(scene_case, 'vrcforge_reparent_gameobject', {
        'executionTarget':target, 'gameObjectPath':'Avatar/Outfit','newParentPath':''})
    assert result['ok'] is True, result
    assert observed[-1] == ('prepare',scene_case,['Assets/Main.unity'])


def test_reparent_explicit_destination_does_not_require_one_avatar(scene_case):
    target = _target(scene_case,'object')
    target.pop('avatar')
    target['object']['exactHierarchyPath'] = 'ExternalOutfit'
    result, observed = _run(scene_case,'vrcforge_reparent_gameobject',{
        'executionTarget':target,'gameObjectPath':'ExternalOutfit','newParentPath':'AvatarB'})
    assert result['ok'] is True, result
    assert [x[1]['scope'] for x in observed if x[0]=='discover'] == ['object']


def test_reparent_does_not_accept_a_different_source(scene_case):
    result, observed = _run(scene_case,'vrcforge_reparent_gameobject',{
        'executionTarget':_target(scene_case,'object'),'gameObjectPath':'Avatar/Other','newParentPath':''})
    assert result['ok'] is False and observed == []


def test_standalone_visibility_has_no_avatar_prerequisite(scene_case):
    target = _target(scene_case,'object')
    target.pop('avatar')
    target['object']['exactHierarchyPath']='Slime_Comparison'
    result, observed = _run(scene_case,'vrcforge_set_gameobject_active',{
        'executionTarget':target,'gameObjectPath':'Slime_Comparison','active':False})
    assert result['ok'] is True, result
    assert observed[-1] == ('prepare',scene_case,['Assets/Main.unity'])


def test_cross_scene_duplicate_zip_retains_only_actual_destination(scene_case, tmp_path):
    from dataclasses import replace
    import zipfile
    from test_agent_checkpoint_recovery_service import _gateway
    from test_scene_workflow_checkpoint_scope import _plan
    from unity_scene_workflow_checkpoint import prepare_scene_workflow_checkpoint
    args, destination = _duplicate(scene_case, cross_scene=True)
    for directory in ('Packages','ProjectSettings'):
        (scene_case/directory).mkdir()
    (scene_case/'Assets/Unrelated.mat').write_text('unchanged asset')
    service = _gateway(tmp_path/'app').approval_transactions
    service.register_write_handler('vrcforge_duplicate_scene_object', 'Duplicate one scene object', 'medium', lambda _:None,
        checkpoint_prepare_handler=lambda root, arguments:prepare_scene_workflow_checkpoint(
            root,arguments,checkpoint_target='vrcforge_duplicate_scene_object',build_plan=_plan,
            discover=lambda _query:{'ok':True,'targets':[deepcopy(args['executionTarget'])]},
            prepare=lambda root,**kw:{'ok':True}))
    service._ports=replace(service._ports,run_git=lambda *a,**kw:{'ok':False,'returncode':1,'stdout':'','error':'not git'})
    result=service._create_pre_write_checkpoint({'id':'approval','targetTool':'vrcforge_duplicate_scene_object'},
        {**args,'archiveFiles':['Assets/Main.unity','Assets/Unrelated.mat']})
    assert result['ok'] is True,result
    with zipfile.ZipFile(result['archivePath']) as archive:
        assert set(archive.namelist()) == {destination,destination+'.meta'}
