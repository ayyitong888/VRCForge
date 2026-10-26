"""Checkpoint saves may change mtime while preserving approved scene bytes."""
import copy

import pytest

from execution_target import (
    EXECUTION_TARGET_SCHEMA, ExecutionTargetError, canonical_namespace,
    execution_target_digest, project_identity, validate_execution_target,
)


def identities(tmp_path):
    target = {
        'schema': EXECUTION_TARGET_SCHEMA, 'scope': 'scene',
        'project': {'root': str(tmp_path), 'projectId': project_identity(str(tmp_path))},
        'editor': {'unityPid': 1234, 'processStartTime': 'start-a', 'coreInstanceId': 'core-a'},
        'scene': {'absolutePath': str(tmp_path / 'Assets/Main.unity'), 'guid': 'scene-a',
                  'revision': '100', 'digest': 'a' * 64},
    }
    target['namespace'] = canonical_namespace(target)
    core = {'processId': 1234, 'processStartTime': 'start-a', 'instanceId': 'core-a',
            'sceneGuid': 'scene-a', 'sceneRevision': '101', 'sceneDigest': 'a' * 64}
    return target, core


def test_scene_state_change_does_not_change_stable_target_identity(tmp_path):
    target, core = identities(tmp_path)
    frozen = copy.deepcopy(target)
    digest = execution_target_digest(target)
    core['sceneDigest'] = 'b' * 64
    validated = validate_execution_target(target, project_root=str(tmp_path), core_identity=core, required_scope='scene')
    assert target == frozen
    assert validated['scene']['revision'] == '100'
    assert execution_target_digest(validated) == digest


@pytest.mark.parametrize('observed', ['b' * 64, '', 'short-digest', None])
def test_scene_revision_and_content_are_not_identity_proof(tmp_path, observed):
    target, core = identities(tmp_path)
    core['sceneDigest'] = observed
    validated = validate_execution_target(target, core_identity=core, required_scope='scene')
    assert execution_target_digest(validated) == execution_target_digest(target)


def test_scene_content_placeholder_does_not_affect_stable_identity(tmp_path):
    target, core = identities(tmp_path)
    target['scene']['digest'] = core['sceneDigest'] = 'placeholder'
    validated = validate_execution_target(target, core_identity=core, required_scope='scene')
    assert execution_target_digest(validated) == execution_target_digest(target)


@pytest.mark.parametrize('field,value', [('sceneGuid', 'scene-b'), ('instanceId', 'core-b'), ('processId', 5678)])
def test_equal_scene_bytes_do_not_allow_replaced_identity(tmp_path, field, value):
    target, core = identities(tmp_path)
    core[field] = value
    with pytest.raises(ExecutionTargetError):
        validate_execution_target(target, core_identity=core, required_scope='scene')


def test_recreated_object_identity_is_still_rejected(tmp_path):
    target, core = identities(tmp_path)
    target['scope'] = 'object'
    target['object'] = {'globalObjectId': 'object-current', 'exactHierarchyPath': 'Root/Object'}
    target['namespace'] = canonical_namespace(target)
    core['objectGlobalObjectId'] = 'object-recreated'
    with pytest.raises(ExecutionTargetError) as failure:
        validate_execution_target(target, core_identity=core, required_scope='object')
    assert failure.value.code == 'object_identity_mismatch'
