"""Property checkpoint owner binding and real archive boundary regression."""
from copy import deepcopy
from dataclasses import replace
import zipfile

import pytest
import dashboard_server as server
from test_agent_checkpoint_recovery_service import _gateway


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    for folder in ('Assets', 'Packages', 'ProjectSettings'):
        (root / folder).mkdir(parents=True)
    for name in ('Assets/Main.unity', 'Assets/Main.unity.meta', 'Assets/Other.mat', 'Assets/Other.mat.meta'):
        (root / name).write_text(name)
    target = {'schema': 'vrcforge.execution_target.v1', 'scope': 'component',
              'project': {'root': str(root)}, 'editor': {'coreInstanceId': 'core'},
              'scene': {'assetPath': 'Assets/Main.unity', 'guid': 'scene', 'digest': 'a' * 64},
              'object': {'globalObjectId': 'object', 'exactHierarchyPath': 'Avatar/Rig/Neck'},
              'component': {'globalObjectId': 'component', 'type': 'UnityEngine.Transform'}}
    args = {'projectPath': str(root), 'executionTarget': target,
            'gameObjectPath': 'Avatar/Rig/Neck', 'componentType': 'UnityEngine.Transform',
            'propertyPath': 'localScale', 'value': {'x': .98, 'y': .98, 'z': .98}}
    scope = {'schema': 'vrcforge.component_property_checkpoint.v1', 'ownerKind': 'scene',
             'scenePath': 'Assets/Main.unity', 'sceneGuid': 'scene', 'sceneDigest': 'a' * 64,
             'objectGlobalObjectId': 'object', 'componentGlobalObjectId': 'component',
             'componentType': 'UnityEngine.Transform', 'componentIndex': 0, 'propertyPath': 'localScale',
             'archiveAssetPaths': ['Assets/Main.unity']}
    calls = []
    monkeypatch.setattr(server, 'list_execution_targets_sync', lambda query: {'ok': True, 'targets': [deepcopy(target)]})
    monkeypatch.setattr(server, 'read_component_property_sync', lambda request: {'ok': True, 'checkpointScope': deepcopy(scope)})
    monkeypatch.setattr(server, 'set_component_property_sync', lambda _: pytest.fail('checkpoint must never call a managed writer, even in preview mode'))
    def prepare(project, checkpoint_asset_paths=None, *, checkpoint_files):
        calls.append((project, checkpoint_files))
        return {'ok': True, 'mode': 'read_only'}
    monkeypatch.setattr(server, 'prepare_unity_checkpoint_sync', prepare)
    return root, target, args, scope, calls


def test_property_write_derives_exact_scene_before_checkpoint(case):
    root, target, args, scope, calls = case
    before = deepcopy(args)
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is True, result
    assert result['archiveAssetPaths'] == ['Assets/Main.unity']
    assert calls == [(root, ['Assets/Main.unity'])]
    assert args == before


def test_serialized_leaf_checkpoint_matches_root_owner_and_exact_leaf(case):
    root, _, args, scope, calls = case
    serialized = 'settings.entries.Array.data[2].weight'
    args.pop('propertyPath')
    args['serializedPropertyPath'] = serialized
    scope['propertyPath'] = 'settings'
    scope['serializedPropertyPath'] = serialized
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is True, result
    assert calls == [(root, ['Assets/Main.unity'])]


def test_serialized_checkpoint_rejects_different_leaf_with_same_root(case):
    root, _, args, scope, calls = case
    args.pop('propertyPath')
    args['serializedPropertyPath'] = 'settings.value'
    scope['propertyPath'] = 'settings'
    scope['serializedPropertyPath'] = 'settings.other'
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is False
    assert calls == []


@pytest.mark.parametrize('other_path', ['missing', 'propertyPath'])
def test_serialized_checkpoint_rejects_missing_or_mixed_path(case, other_path):
    root, _, args, scope, calls = case
    args.pop('propertyPath')
    args['serializedPropertyPath'] = 'settings.value'
    scope['propertyPath'] = 'settings'
    scope['serializedPropertyPath'] = 'settings.value'
    if other_path == 'missing':
        args.pop('serializedPropertyPath')
    else:
        args[other_path] = 'enabled'
    result = server.prepare_authoritative_unity_checkpoint_sync(
        root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is False
    assert calls == []


def test_internal_property_reference_is_resolved_without_changing_approved_arguments(case, monkeypatch):
    root, target, args, _, calls = case
    reference = {'schema': 'vrcforge.execution_target_ref.v1', 'ref': 'session-bound-reference'}
    args['executionTarget'] = reference
    def resolve(value, **kwargs):
        assert value == reference
        assert kwargs == {'project_root': str(root), 'required_scope': 'component'}
        return deepcopy(target)
    monkeypatch.setattr(server.AGENT_GATEWAY, 'resolve_execution_target_reference', resolve)
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is True, result
    assert calls == [(root, ['Assets/Main.unity'])]
    assert args['executionTarget'] == reference


@pytest.mark.parametrize('field,value', [
    ('schema', 'forged'), ('ownerKind', 'prefab'), ('scenePath', 'Assets/Other.unity'),
    ('sceneGuid', 'other'), ('sceneDigest', 'b' * 64), ('objectGlobalObjectId', 'other'),
    ('componentGlobalObjectId', 'other'), ('componentType', 'UnityEngine.Camera'),
    ('componentIndex', 1), ('propertyPath', 'position'),
    ('archiveAssetPaths', ['Assets/Other.mat']),
])
def test_owner_preview_mismatch_never_reaches_prepare(case, field, value):
    root, _, args, scope, calls = case
    scope[field] = value
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is False, result
    assert calls == []


def test_caller_cannot_supply_footprint_for_legacy_or_unknown_setter(case, monkeypatch):
    root, _, args, scope, calls = case
    monkeypatch.setattr(server, 'read_component_property_sync', lambda _: {'ok': True})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**args, 'checkpointScope': scope, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is False
    assert calls == []


def test_identity_drift_blocks_before_owner_preview(case, monkeypatch):
    root, target, args, _, calls = case
    fresh = deepcopy(target); fresh['editor']['coreInstanceId'] = 'new'
    monkeypatch.setattr(server, 'list_execution_targets_sync', lambda _: {'ok': True, 'targets': [fresh]})
    monkeypatch.setattr(server, 'read_component_property_sync', lambda _: pytest.fail('stale target must not reach preview'))
    assert server.prepare_authoritative_unity_checkpoint_sync(root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})['ok'] is False
    assert calls == []


def test_scope_failure_preserves_core_reason(case, monkeypatch):
    root, _, args, _, calls = case
    monkeypatch.setattr(server, 'read_component_property_sync', lambda _: {
        'ok': False, 'code': 'owner_unsupported', 'error': 'Custom setter side effects have no proven scope.'})
    result = server.prepare_authoritative_unity_checkpoint_sync(root, {**args, '_checkpointTargetTool': 'vrcforge_set_property'})
    assert result['ok'] is False and result['code'] == 'owner_unsupported'
    assert result['error'] == 'Custom setter side effects have no proven scope.'
    assert calls == []


def test_property_checkpoint_zip_contains_scene_and_meta_only(case, tmp_path):
    root, _, args, _, _ = case
    gateway = _gateway(tmp_path / 'app')
    service = gateway.approval_transactions
    handler = server.AGENT_GATEWAY._write_handlers['vrcforge_set_property']
    service.register_write_handler('vrcforge_set_property', 'component', 'medium', lambda _: None,
                                   checkpoint_prepare_handler=handler.checkpoint_prepare_handler)
    service._ports = replace(service._ports, run_git=lambda *a, **kw: {'ok': False, 'returncode': 1, 'stdout': '', 'error': 'not git'})
    result = service._create_pre_write_checkpoint({'id': 'approval', 'targetTool': 'vrcforge_set_property'},
                                                  {**args, 'archiveFiles': ['Assets/Other.mat']})
    assert result['ok'] is True, result
    with zipfile.ZipFile(result['archivePath']) as archive:
        assert set(archive.namelist()) == {'Assets/Main.unity', 'Assets/Main.unity.meta'}
