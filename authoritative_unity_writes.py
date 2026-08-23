from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from material_shader_assignment import (
    MaterialShaderAssignmentError,
    TOOL_NAME as MATERIAL_SHADER_ASSIGNMENT_TOOL,
    bind_authoritative_preview as bind_material_shader_preview,
    build_preview_arguments as build_material_shader_preview_arguments,
)
from atomic_reference_rename import (
    TOOL_NAME as ATOMIC_REFERENCE_RENAME_TOOL,
    AtomicReferenceRenameError,
    bind_authoritative_preview as bind_atomic_reference_rename_preview,
    build_preview_arguments as build_atomic_reference_rename_preview_arguments,
    validate_authoritative_apply_result as validate_atomic_reference_rename_apply_result,
)
from parameter_bit_packing import (
    TOOL_NAME as PARAMETER_BIT_PACKING_TOOL,
    ParameterBitPackingError,
    bind_authoritative_preview as bind_parameter_bit_packing_preview,
    build_preview_arguments as build_parameter_bit_packing_preview_arguments,
    validate_apply_result as validate_parameter_bit_packing_apply_result,
)
from component_feature_write import (
    TOOL_NAME as COMPONENT_FEATURE_TOOL,
    ComponentFeatureWriteError,
    bind_authoritative_preview as bind_component_feature_preview,
    build_preview_arguments as build_component_feature_preview_arguments,
)
from constraint_source_write import (
    TOOL_NAME as CONSTRAINT_SOURCE_TOOL,
    ConstraintSourceWriteError,
    bind_authoritative_preview as bind_constraint_source_preview,
    build_preview_arguments as build_constraint_source_preview_arguments,
)
from scene_object_copy import (
    DUPLICATE_TOOL_NAME,
    PREFAB_TOOL_NAME,
    SceneObjectCopyError,
    bind_authoritative_preview as bind_scene_object_copy_preview,
    build_preview_arguments as build_scene_object_copy_preview_arguments,
)
from texture_import_settings import (
    TOOL_NAME as TEXTURE_IMPORT_SETTINGS_TOOL,
    TextureImportSettingsError,
    bind_authoritative_preview as bind_texture_import_settings_preview,
    build_preview_arguments as build_texture_import_settings_preview_arguments,
)
from scene_asset_save import (
    TOOL_NAME as SAVE_NEW_SCENE_TOOL,
    SceneAssetSaveError,
    bind_authoritative_preview as bind_scene_asset_save_preview,
    build_preview_arguments as build_scene_asset_save_preview_arguments,
    validate_apply_result as validate_scene_asset_save_apply_result,
)
from scene_asset_save_current import (
    TOOL_NAME as SAVE_CURRENT_SCENE_TOOL,
    CurrentSceneSaveError,
    bind_authoritative_preview as bind_current_scene_save_preview,
    build_preview_arguments as build_current_scene_save_preview_arguments,
    validate_apply_result as validate_current_scene_save_apply_result,
)
from project_asset_copy import (
    TOOL_NAME as PROJECT_ASSET_COPY_TOOL,
    ProjectAssetCopyError,
    bind_authoritative_preview as bind_project_asset_copy_preview,
    build_preview_arguments as build_project_asset_copy_preview_arguments,
    validate_apply_result as validate_project_asset_copy_apply_result,
)


PreviewInvoker = Callable[[str, dict[str, Any]], Any]
PreviewBuilder = Callable[[dict[str, Any]], dict[str, Any]]
PreviewBinder = Callable[[dict[str, Any], Any], tuple[dict[str, Any], dict[str, Any]]]
ApplyValidator = Callable[[dict[str, Any], Any], dict[str, Any]]


@dataclass(frozen=True)
class AuthoritativeUnityWriteSpec:
    tool_name: str
    request_error: str
    bridge_error: str
    receipt_error: str
    domain_error: type[ValueError]
    build_preview: PreviewBuilder
    bind_preview: PreviewBinder
    include_project_path_in_preview: bool = False
    validate_apply: ApplyValidator | None = None
    result_error: str = ""


class AuthoritativeUnityWriteError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
        raw_result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = dict(details or {})
        self.raw_result = dict(raw_result or {})


def _scene_preview_builder(tool_name: str) -> PreviewBuilder:
    return lambda arguments: build_scene_object_copy_preview_arguments(tool_name, arguments)


_SPECS = {
    MATERIAL_SHADER_ASSIGNMENT_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=MATERIAL_SHADER_ASSIGNMENT_TOOL,
        request_error="Material shader arguments are required.",
        bridge_error="Material shader preview could not be verified against the current project.",
        receipt_error="Material shader preview returned an invalid verification receipt.",
        domain_error=MaterialShaderAssignmentError,
        build_preview=build_material_shader_preview_arguments,
        bind_preview=bind_material_shader_preview,
        include_project_path_in_preview=True,
    ),
    DUPLICATE_TOOL_NAME: AuthoritativeUnityWriteSpec(
        tool_name=DUPLICATE_TOOL_NAME,
        request_error="Scene object copy arguments are required.",
        bridge_error="Scene object copy preview could not be verified against the current project.",
        receipt_error="Scene object copy preview returned an invalid verification receipt.",
        domain_error=SceneObjectCopyError,
        build_preview=_scene_preview_builder(DUPLICATE_TOOL_NAME),
        bind_preview=bind_scene_object_copy_preview,
        include_project_path_in_preview=True,
    ),
    PREFAB_TOOL_NAME: AuthoritativeUnityWriteSpec(
        tool_name=PREFAB_TOOL_NAME,
        request_error="Scene object copy arguments are required.",
        bridge_error="Scene object copy preview could not be verified against the current project.",
        receipt_error="Scene object copy preview returned an invalid verification receipt.",
        domain_error=SceneObjectCopyError,
        build_preview=_scene_preview_builder(PREFAB_TOOL_NAME),
        bind_preview=bind_scene_object_copy_preview,
        include_project_path_in_preview=True,
    ),
    TEXTURE_IMPORT_SETTINGS_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=TEXTURE_IMPORT_SETTINGS_TOOL,
        request_error="Texture import settings arguments are required.",
        bridge_error="Texture import settings preview could not be verified against the current project.",
        receipt_error="Texture import settings preview returned an invalid verification receipt.",
        domain_error=TextureImportSettingsError,
        build_preview=build_texture_import_settings_preview_arguments,
        bind_preview=bind_texture_import_settings_preview,
        include_project_path_in_preview=True,
    ),
    CONSTRAINT_SOURCE_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=CONSTRAINT_SOURCE_TOOL,
        request_error="Constraint source arguments are required.",
        bridge_error="Constraint source preview could not be verified against the current project.",
        receipt_error="Constraint source preview returned an invalid verification receipt.",
        domain_error=ConstraintSourceWriteError,
        build_preview=build_constraint_source_preview_arguments,
        bind_preview=bind_constraint_source_preview,
        include_project_path_in_preview=True,
    ),
    COMPONENT_FEATURE_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=COMPONENT_FEATURE_TOOL,
        request_error="Component feature arguments are required.",
        bridge_error="Component feature preview could not be verified against the current project.",
        receipt_error="Component feature preview returned an invalid verification receipt.",
        domain_error=ComponentFeatureWriteError,
        build_preview=build_component_feature_preview_arguments,
        bind_preview=bind_component_feature_preview,
        include_project_path_in_preview=True,
    ),
    PARAMETER_BIT_PACKING_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=PARAMETER_BIT_PACKING_TOOL,
        request_error="Parameter bit-packing arguments are required.",
        bridge_error="Parameter bit-packing preview could not be verified against the current project.",
        receipt_error="Parameter bit-packing preview returned an invalid verification receipt.",
        domain_error=ParameterBitPackingError,
        build_preview=build_parameter_bit_packing_preview_arguments,
        bind_preview=bind_parameter_bit_packing_preview,
        validate_apply=validate_parameter_bit_packing_apply_result,
        result_error="Parameter bit-packing apply returned an invalid verification receipt.",
    ),
    ATOMIC_REFERENCE_RENAME_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=ATOMIC_REFERENCE_RENAME_TOOL,
        request_error="Atomic reference rename arguments are required.",
        bridge_error="Atomic reference rename preview could not be verified against the current project.",
        receipt_error="Atomic reference rename preview returned an invalid verification receipt.",
        domain_error=AtomicReferenceRenameError,
        build_preview=build_atomic_reference_rename_preview_arguments,
        bind_preview=bind_atomic_reference_rename_preview,
        validate_apply=validate_atomic_reference_rename_apply_result,
        result_error="Atomic reference rename apply returned an invalid verification receipt.",
    ),
    SAVE_NEW_SCENE_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=SAVE_NEW_SCENE_TOOL,
        request_error="New scene arguments are required.",
        bridge_error="New scene preview could not be verified against the current project.",
        receipt_error="New scene preview returned an invalid verification receipt.",
        domain_error=SceneAssetSaveError,
        build_preview=build_scene_asset_save_preview_arguments,
        bind_preview=bind_scene_asset_save_preview,
        include_project_path_in_preview=True,
        validate_apply=validate_scene_asset_save_apply_result,
        result_error="New scene apply returned an invalid verification receipt.",
    ),
    SAVE_CURRENT_SCENE_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=SAVE_CURRENT_SCENE_TOOL,
        request_error="Current scene arguments are required.",
        bridge_error="Current scene preview could not be verified against the current project.",
        receipt_error="Current scene preview returned an invalid verification receipt.",
        domain_error=CurrentSceneSaveError,
        build_preview=build_current_scene_save_preview_arguments,
        bind_preview=bind_current_scene_save_preview,
        include_project_path_in_preview=True,
        validate_apply=validate_current_scene_save_apply_result,
        result_error="Current scene apply returned an invalid verification receipt.",
    ),
    PROJECT_ASSET_COPY_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=PROJECT_ASSET_COPY_TOOL,
        request_error="Project asset copy arguments are required.",
        bridge_error="Project asset copy preview could not be verified against the current project.",
        receipt_error="Project asset copy preview returned an invalid verification receipt.",
        domain_error=ProjectAssetCopyError,
        build_preview=build_project_asset_copy_preview_arguments,
        bind_preview=bind_project_asset_copy_preview,
        include_project_path_in_preview=True,
        validate_apply=validate_project_asset_copy_apply_result,
        result_error="Project asset copy apply returned an invalid verification receipt.",
    ),
}


AUTHORITATIVE_UNITY_WRITE_TOOLS = frozenset(_SPECS)


def prepare_authoritative_unity_write(
    params: dict[str, Any],
    caller_preview: Any,
    invoke_preview: PreviewInvoker,
) -> tuple[dict[str, Any], Any]:
    request = params or {}
    tool_name = str(request.get("tool_name") or request.get("toolName") or "").strip()
    spec = _SPECS.get(tool_name)
    if spec is None:
        return params, caller_preview

    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else request.get("params")
    if not isinstance(arguments, dict):
        raise AuthoritativeUnityWriteError(spec.request_error, status_code=400)

    canonical_project_path = _canonical_unity_project(request.get("projectPath"))
    canonical_request = deepcopy(request)
    canonical_request["projectPath"] = str(canonical_project_path)
    canonical_arguments = deepcopy(arguments)
    canonical_arguments["expectedProjectPath"] = str(canonical_project_path)
    canonical_request.pop("params", None)
    canonical_request["arguments"] = canonical_arguments

    preview_arguments = spec.build_preview(arguments)
    if spec.include_project_path_in_preview:
        preview_arguments["expectedProjectPath"] = str(canonical_project_path)
    try:
        payload = invoke_preview(spec.tool_name, preview_arguments)
    except Exception as exc:  # noqa: BLE001 - transport details must not cross this boundary.
        raise AuthoritativeUnityWriteError(spec.bridge_error, status_code=409) from exc

    if isinstance(payload, dict):
        structured = payload.get("structuredContent") if isinstance(payload.get("structuredContent"), dict) else payload
        if payload.get("isError") is True or structured.get("ok") is False or (
            "code" in structured and "schema" not in structured
        ):
            code = str(structured.get("code") or structured.get("errorCode") or "preview_failed").strip()
            reason = str(structured.get("message") or structured.get("error") or spec.bridge_error).strip()
            raw_failure = dict(structured)
            raw_failure.setdefault("failureLayer", "unity_core_preview")
            raw_failure.setdefault("failurePhase", "preview_rejected")
            raw_failure.setdefault("toolRoutingStarted", False)
            raw_failure.setdefault("mutationStarted", False)
            raw_failure.setdefault("committed", False)
            raw_failure.setdefault("commitState", "not_started")
            raw_failure.setdefault("requestMayHaveCommitted", False)
            raw_failure.setdefault("checkpointRecoveryRequired", False)
            raise AuthoritativeUnityWriteError(
                reason,
                status_code=409,
                details={
                    "failureLayer": "unity_core_preview",
                    "failurePhase": "preview_rejected",
                    "errorCode": code,
                    "error": reason,
                    "mutationStarted": False,
                    "committed": False,
                    "commitState": "not_started",
                    "requestMayHaveCommitted": False,
                    "checkpointRecoveryRequired": False,
                },
                raw_result=raw_failure,
            )

    try:
        return spec.bind_preview(canonical_request, payload)
    except spec.domain_error as exc:
        raise AuthoritativeUnityWriteError(
            f"{spec.receipt_error} Reason: {exc}",
            status_code=409,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - receipt parser details must not cross this boundary.
        raise AuthoritativeUnityWriteError(spec.receipt_error, status_code=409) from exc


def validate_authoritative_unity_write_result(
    params: dict[str, Any],
    payload: Any,
) -> Any:
    request = params or {}
    tool_name = str(request.get("tool_name") or request.get("toolName") or "").strip()
    spec = _SPECS.get(tool_name)
    if spec is None or spec.validate_apply is None:
        return payload

    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else request.get("params")
    if not isinstance(arguments, dict):
        raise AuthoritativeUnityWriteError(spec.request_error, status_code=400)

    try:
        return spec.validate_apply(deepcopy(arguments), payload)
    except spec.domain_error as exc:
        raise AuthoritativeUnityWriteError(
            spec.result_error or spec.receipt_error,
            status_code=409,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - result parser details must not cross this boundary.
        raise AuthoritativeUnityWriteError(
            spec.result_error or spec.receipt_error,
            status_code=409,
        ) from exc


def authoritative_unity_write_has_strict_result(params: dict[str, Any]) -> bool:
    request = params or {}
    tool_name = str(request.get("tool_name") or request.get("toolName") or "").strip()
    spec = _SPECS.get(tool_name)
    return spec is not None and spec.validate_apply is not None


def _canonical_unity_project(value: Any) -> Path:
    project_text = str(value or "").strip()
    project_path = Path(project_text)
    if not project_text or not project_path.is_absolute():
        raise AuthoritativeUnityWriteError(
            "projectPath must be an absolute Unity project path.",
            status_code=400,
        )
    try:
        canonical_project_path = project_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise AuthoritativeUnityWriteError(
            "projectPath is not an accessible Unity project.",
            status_code=400,
        ) from exc
    if not canonical_project_path.is_dir() or not (canonical_project_path / "Assets").is_dir():
        raise AuthoritativeUnityWriteError(
            "projectPath is not an accessible Unity project.",
            status_code=400,
        )
    return canonical_project_path
