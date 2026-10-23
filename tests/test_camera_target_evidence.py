"""Validate Core camera geometry separately from optional Avatar diagnostics."""

import ast
import copy
import math
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Any

import pytest
from camera_evidence_contract import validate_camera_evidence


@lru_cache(maxsize=1)
def validator():
    source = (Path(__file__).parents[1] / 'dashboard_server.py').read_text(encoding='utf-8')
    names = {'_finite_camera_value', '_camera_vector', '_camera_values_close',
             '_free_camera_look_rotation_up', '_require_camera_evidence'}
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = {'math': math, 'Mapping': Mapping, 'Any': Any,
             'validate_camera_evidence': validate_camera_evidence}
    exec(compile(tree, 'dashboard_server.py', 'exec'), scope)
    return scope['_require_camera_evidence']


def original_request_shape():
    # Public parameters from the failed native receipt. No Unity call is made.
    return {'cameraMode': 'free', 'avatarPath': '',
            'cameraPosition': {'x': 0, 'y': 1.08, 'z': 0.8},
            'targetPosition': {'x': 0, 'y': 1.08, 'z': 0.02},
            'upVector': {'x': 0, 'y': 1, 'z': 0},
            'projection': 'orthographic', 'orthographicSize': 0.24,
            'width': 1536, 'height': 1536}


def source_derived_evidence():
    expected = original_request_shape()
    identity = [1.0 if row == column else 0.0 for row in range(4) for column in range(4)]
    # Finite geometry fixture, NOT a claim to be the lost original Core matrices.
    return {'position': expected['cameraPosition'], 'target': expected['targetPosition'],
            'basis': {'right': {'x': -1, 'y': 0, 'z': 0}, 'up': {'x': 0, 'y': 1, 'z': 0},
                      'forward': {'x': 0, 'y': 0, 'z': -1}},
            'quaternion': {'x': 0, 'y': 1, 'z': 0, 'w': 0},
            'projection': 'orthographic', 'orthographicSize': 0.24, 'aspect': 1.0,
            'nearClip': 0.01, 'farClip': 1000.0,
            'matrix': {key: list(identity) for key in ('cameraToWorld', 'worldToCamera', 'projection', 'gpuProjection', 'viewProjection')},
            'matrixOrder': 'row_major', 'coordinateSpace': 'unity_world', 'cullingMask': -1,
            'targetSceneValid': False, 'targetSceneName': '', 'targetPath': '',
            'targetRendererCount': 0, 'targetVisibleRendererCount': 0,
            'targetRendererLayerMask': 0, 'targetRendererLayersIncluded': False}


def test_free_camera_without_avatar_accepts_core_unbound_target_diagnostics():
    evidence = source_derived_evidence()
    before = copy.deepcopy(evidence)
    assert validator()({'cameraEvidence': evidence}, original_request_shape()) == evidence
    assert evidence == before


@pytest.mark.parametrize('mode,avatar', [('framed', 'Avatar/Explicit'), ('free', 'Avatar/Explicit')])
def test_absent_target_diagnostics_never_satisfy_explicit_avatar(mode, avatar):
    expected = {**original_request_shape(), 'cameraMode': mode, 'avatarPath': avatar}
    with pytest.raises(RuntimeError, match='targetSceneName'):
        validator()({'cameraEvidence': source_derived_evidence()}, expected)


def test_optional_target_is_independent_of_camera_mode():
    evidence = source_derived_evidence()
    assert validator()({'cameraEvidence': evidence}, {**original_request_shape(), 'cameraMode': 'framed'}) == evidence


@pytest.mark.parametrize('key,value', [
    ('targetSceneValid', True), ('targetSceneValid', 0),
    ('targetRendererCount', 1), ('targetRendererCount', False),
    ('targetVisibleRendererCount', 1), ('targetRendererLayerMask', 1),
    ('targetRendererLayersIncluded', True), ('targetRendererLayersIncluded', 0),
    ('targetPath', 'Avatar'), ('targetSceneName', 'UnexpectedScene'),
    ('targetRendererCount', None)])
def test_only_complete_core_no_target_combination_allows_empty_target_strings(key, value):
    evidence = source_derived_evidence()
    evidence[key] = value
    with pytest.raises(RuntimeError):
        validator()({'cameraEvidence': evidence}, original_request_shape())


@pytest.mark.parametrize('mutation', ['nan_matrix', 'wrong_position', 'wrong_up', 'wrong_projection', 'extra_empty', 'missing_count'])
def test_unbound_target_keeps_all_geometry_and_other_metadata_strict(mutation):
    evidence = source_derived_evidence()
    if mutation == 'nan_matrix': evidence['matrix']['viewProjection'][0] = float('nan')
    elif mutation == 'wrong_position': evidence['position'] = {'x': 5, 'y': 1.08, 'z': 0.8}
    elif mutation == 'wrong_up': evidence['basis']['up']['x'] = 1
    elif mutation == 'wrong_projection': evidence['projection'] = 'perspective'; evidence['fieldOfView'] = 42
    elif mutation == 'extra_empty': evidence['unrelatedMetadata'] = ''
    elif mutation == 'missing_count': del evidence['targetRendererCount']
    with pytest.raises(RuntimeError):
        validator()({'cameraEvidence': evidence}, original_request_shape())


def test_bound_camera_target_diagnostics_remain_supported():
    evidence = source_derived_evidence()
    evidence.update(targetSceneValid=True, targetSceneName='Fresh', targetPath='Avatar',
                    targetRendererCount=2, targetVisibleRendererCount=2,
                    targetRendererLayerMask=1, targetRendererLayersIncluded=True)
    expected = {**original_request_shape(), 'avatarPath': 'Avatar'}
    assert validator()({'cameraEvidence': evidence}, expected) == evidence


@pytest.mark.parametrize('path,value', [
    (('matrix', 'worldToCamera', 0), 'not-a-number'),
    (('matrix', 'projection', 0), False),
    (('position', 'x'), True), (('aspect',), '1'),
    (('nearClip',), 0), (('farClip',), 0.001),
    (('orthographicSize',), True), (('targetRendererCount',), -5),
    (('targetRendererCount',), 'two'), (('targetVisibleRendererCount',), 3),
    (('targetSceneValid',), 'false'), (('targetRendererLayersIncluded',), 1),
    (('targetPath',), 'OtherAvatar'), (('matrix', 'extra'), [1]*16),
])
def test_typed_core_evidence_rejects_invalid_values_and_wrong_target(path, value):
    evidence = source_derived_evidence()
    evidence.update(targetSceneValid=True, targetSceneName='Fresh', targetPath='Avatar',
                    targetRendererCount=2, targetVisibleRendererCount=2,
                    targetRendererLayerMask=1, targetRendererLayersIncluded=True)
    owner = evidence
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    with pytest.raises(RuntimeError):
        validator()({'cameraEvidence': evidence}, {**original_request_shape(), 'avatarPath': 'Avatar'})


def test_bound_target_with_empty_unsaved_scene_name_is_valid():
    evidence = source_derived_evidence()
    evidence.update(targetSceneValid=True, targetSceneName='', targetPath='Avatar',
                    targetRendererCount=0, targetVisibleRendererCount=0,
                    targetRendererLayerMask=0, targetRendererLayersIncluded=False)
    assert validator()({'cameraEvidence': evidence}, {**original_request_shape(), 'avatarPath': 'Avatar'}) == evidence


@pytest.mark.parametrize('requested', ['/Root/Avatar/', 'Root\\Avatar', 'Avatar'])
def test_target_binding_preserves_core_path_resolution(requested):
    evidence = source_derived_evidence()
    evidence.update(targetSceneValid=True, targetSceneName='Fresh', targetPath='Root/Avatar')
    assert validator()({'cameraEvidence': evidence}, {**original_request_shape(), 'avatarPath': requested}) == evidence


@pytest.mark.parametrize('count,mask,included', [(0, 1, False), (2, 0, True), (1, 3, True)])
def test_renderer_layers_require_corresponding_renderers(count, mask, included):
    evidence = source_derived_evidence()
    evidence.update(targetSceneValid=True, targetSceneName='Fresh', targetPath='Avatar',
                    targetRendererCount=count, targetVisibleRendererCount=0,
                    targetRendererLayerMask=mask, targetRendererLayersIncluded=included)
    with pytest.raises(RuntimeError):
        validator()({'cameraEvidence': evidence}, {**original_request_shape(), 'avatarPath': 'Avatar'})
