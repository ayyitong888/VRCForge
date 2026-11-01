from __future__ import annotations

from copy import deepcopy
from typing import Any

RESULT_SCHEMA = "vrcforge.model_import_settings.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.model_import_settings_approval.v1"
TOOL_NAME = "vrc_set_model_import_settings"
REQUEST_ARGUMENT_KEYS = ("modelAssetPath", "isReadable")


class ModelImportSettingsError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in REQUEST_ARGUMENT_KEYS if key in wrapper}
    for key in REQUEST_ARGUMENT_KEYS:
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    preview = {key: deepcopy((arguments or {}).get(key)) for key in REQUEST_ARGUMENT_KEYS}
    preview["preview"] = True
    preview["saveAndReimport"] = False
    return preview


def _asset_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ModelImportSettingsError("modelAssetPath must be a project-relative Assets path.")
    path = value.strip()
    parts = path.split("/")
    if "\\" in path or not path.startswith("Assets/") or any(part in {"", ".", ".."} for part in parts):
        raise ModelImportSettingsError("modelAssetPath must be an exact Assets/... asset path without traversal segments.")
    return path


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict) or wrapper_arguments.get("toolName", TOOL_NAME) != TOOL_NAME:
        raise ModelImportSettingsError("Model import settings wrapper is invalid.")
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        raise ModelImportSettingsError("Model import settings arguments are required.")
    path = _asset_path(nested.get("modelAssetPath"))
    target_value = nested.get("isReadable")
    if not isinstance(target_value, bool):
        raise ModelImportSettingsError("isReadable must be a boolean.")
    if not isinstance(payload, dict) or payload.get("schema") != RESULT_SCHEMA:
        raise ModelImportSettingsError("Model import preview schema is invalid.")
    if payload.get("ok") is not True or payload.get("preview") is not True or payload.get("verified") is not True:
        raise ModelImportSettingsError("Model import preview did not verify its read-only result.")
    if payload.get("saved") is not False or payload.get("reimported") is not False:
        raise ModelImportSettingsError("Model import preview reported an unexpected write.")
    if _asset_path(payload.get("modelAssetPath")) != path:
        raise ModelImportSettingsError("Model import preview changed the target asset path.")
    importer_type = payload.get("importerType")
    if importer_type != "UnityEditor.ModelImporter":
        raise ModelImportSettingsError("Model import preview did not identify the Unity ModelImporter type.")
    before = payload.get("beforeSettings")
    after = payload.get("targetSettings")
    if not isinstance(before, dict) or not isinstance(before.get("isReadable"), bool):
        raise ModelImportSettingsError("Model import preview did not return the current isReadable value.")
    if not isinstance(after, dict) or after.get("isReadable") is not target_value:
        raise ModelImportSettingsError("Model import preview did not match the requested isReadable value.")
    would_change = payload.get("wouldChange")
    if not isinstance(would_change, bool):
        raise ModelImportSettingsError("Model import preview did not return its wouldChange fact.")

    canonical = deepcopy(wrapper_arguments)
    arguments = deepcopy(nested)
    for key in tuple(arguments):
        if key.startswith("expected"):
            arguments.pop(key, None)
    arguments.update({
        "modelAssetPath": path,
        "isReadable": target_value,
        "preview": False,
        "saveAndReimport": True,
        "expectedProjectPath": str(wrapper_arguments.get("projectPath") or ""),
    })
    canonical.pop("params", None)
    canonical.pop("tool_name", None)
    canonical["toolName"] = TOOL_NAME
    canonical["arguments"] = arguments
    approval = {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "toolName": TOOL_NAME,
        "projectPath": str(wrapper_arguments.get("projectPath") or ""),
        "target": {"modelAssetPath": path, "importerType": importer_type},
        "change": {
            "before": {"isReadable": before["isReadable"]},
            "after": {"isReadable": target_value},
            "wouldChange": would_change,
        },
        "rollbackRequired": True,
    }
    return canonical, approval
