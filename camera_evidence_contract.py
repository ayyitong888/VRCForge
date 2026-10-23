"""Typed Core camera receipt; geometry and optional target are separate facts."""
from typing import Annotated, Literal, Mapping, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Positive = Annotated[Finite, Field(gt=0)]
Count = Annotated[int, Field(strict=True, ge=0)]
Mask = Annotated[int, Field(strict=True, ge=-(2**31), le=2**31-1)]
Matrix = Annotated[list[Finite], Field(min_length=16, max_length=16)]


class _Receipt(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class _Vector(_Receipt):
    x: Finite
    y: Finite
    z: Finite


class _Quaternion(_Vector):
    w: Finite


class _Basis(_Receipt):
    right: _Vector
    up: _Vector
    forward: _Vector


class _Matrices(_Receipt):
    cameraToWorld: Matrix
    worldToCamera: Matrix
    projection: Matrix
    gpuProjection: Matrix
    viewProjection: Matrix


class _CameraReceipt(_Receipt):
    position: _Vector
    target: _Vector
    basis: _Basis
    quaternion: _Quaternion
    projection: Literal['perspective', 'orthographic']
    aspect: Positive
    nearClip: Positive
    farClip: Positive
    fieldOfView: Annotated[Finite, Field(gt=0, lt=180)] | None = None
    orthographicSize: Positive | None = None
    matrix: _Matrices
    matrixOrder: Literal['row_major']
    coordinateSpace: Literal['unity_world']
    cullingMask: Mask
    targetSceneValid: bool
    targetSceneName: str
    targetPath: str
    targetRendererCount: Count
    targetVisibleRendererCount: Count
    targetRendererLayerMask: Mask
    targetRendererLayersIncluded: bool

    @model_validator(mode='after')
    def validate_relationships(self):
        if self.farClip <= self.nearClip:
            raise ValueError('farClip must exceed nearClip')
        optics = self.orthographicSize if self.projection == 'orthographic' else self.fieldOfView
        if optics is None:
            raise ValueError('projection requires its optics field')
        if self.targetVisibleRendererCount > self.targetRendererCount:
            raise ValueError('visible renderer count exceeds total')
        layer_count = (self.targetRendererLayerMask & 0xffffffff).bit_count()
        if (bool(layer_count) != bool(self.targetRendererCount)
                or layer_count > self.targetRendererCount):
            raise ValueError('renderer layer mask disagrees with renderer count')
        if not self.targetPath:
            if (self.targetSceneValid or self.targetSceneName or self.targetRendererCount
                    or self.targetVisibleRendererCount or self.targetRendererLayerMask
                    or self.targetRendererLayersIncluded):
                raise ValueError('unbound target has inconsistent targetSceneName or visibility')
        elif not self.targetSceneValid:
            raise ValueError('bound target scene must be valid')
        included = (self.targetRendererCount > 0
                    and self.cullingMask & self.targetRendererLayerMask == self.targetRendererLayerMask)
        if self.targetRendererLayersIncluded != included:
            raise ValueError('target layer visibility disagrees with culling mask')
        return self


def validate_camera_evidence(evidence: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        receipt = _CameraReceipt.model_validate(evidence)
    except ValidationError as exc:
        raise RuntimeError(f'Unity returned invalid camera evidence: {exc}') from exc
    # Match Core ResolveTransform: normalize separators, then accept the full
    # path or its component-delimited suffix. Core rejects ambiguous suffixes.
    requested_target = str(expected.get('avatarPath') or '').strip().replace('\\', '/').strip('/')
    observed_target = receipt.targetPath.strip().replace('\\', '/').strip('/')
    if requested_target and not (observed_target == requested_target
                                 or observed_target.endswith('/' + requested_target)):
        raise RuntimeError('Unity camera evidence targetSceneName/targetPath does not match the approved target.')
    return dict(evidence)
