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


PreviewInvoker = Callable[[str, dict[str, Any]], Any]
PreviewBuilder = Callable[[dict[str, Any]], dict[str, Any]]
PreviewBinder = Callable[[dict[str, Any], Any], tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True)
class AuthoritativeUnityWriteSpec:
    tool_name: str
    request_error: str
    bridge_error: str
    receipt_error: str
    domain_error: type[ValueError]
    build_preview: PreviewBuilder
    bind_preview: PreviewBinder


class AuthoritativeUnityWriteError(ValueError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    ),
    DUPLICATE_TOOL_NAME: AuthoritativeUnityWriteSpec(
        tool_name=DUPLICATE_TOOL_NAME,
        request_error="Scene object copy arguments are required.",
        bridge_error="Scene object copy preview could not be verified against the current project.",
        receipt_error="Scene object copy preview returned an invalid verification receipt.",
        domain_error=SceneObjectCopyError,
        build_preview=_scene_preview_builder(DUPLICATE_TOOL_NAME),
        bind_preview=bind_scene_object_copy_preview,
    ),
    PREFAB_TOOL_NAME: AuthoritativeUnityWriteSpec(
        tool_name=PREFAB_TOOL_NAME,
        request_error="Scene object copy arguments are required.",
        bridge_error="Scene object copy preview could not be verified against the current project.",
        receipt_error="Scene object copy preview returned an invalid verification receipt.",
        domain_error=SceneObjectCopyError,
        build_preview=_scene_preview_builder(PREFAB_TOOL_NAME),
        bind_preview=bind_scene_object_copy_preview,
    ),
    TEXTURE_IMPORT_SETTINGS_TOOL: AuthoritativeUnityWriteSpec(
        tool_name=TEXTURE_IMPORT_SETTINGS_TOOL,
        request_error="Texture import settings arguments are required.",
        bridge_error="Texture import settings preview could not be verified against the current project.",
        receipt_error="Texture import settings preview returned an invalid verification receipt.",
        domain_error=TextureImportSettingsError,
        build_preview=build_texture_import_settings_preview_arguments,
        bind_preview=bind_texture_import_settings_preview,
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
    preview_arguments["expectedProjectPath"] = str(canonical_project_path)
    try:
        payload = invoke_preview(spec.tool_name, preview_arguments)
    except Exception as exc:  # noqa: BLE001 - transport details must not cross this boundary.
        raise AuthoritativeUnityWriteError(spec.bridge_error, status_code=409) from exc

    try:
        return spec.bind_preview(canonical_request, payload)
    except spec.domain_error as exc:
        raise AuthoritativeUnityWriteError(spec.receipt_error, status_code=409) from exc
    except Exception as exc:  # noqa: BLE001 - receipt parser details must not cross this boundary.
        raise AuthoritativeUnityWriteError(spec.receipt_error, status_code=409) from exc


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
