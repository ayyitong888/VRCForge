"""Canonical request binding for relocating generated Unity assets."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


TOOL_NAME = "vrc_relocate_generated_assets"
RESULT_SCHEMA = "vrcforge.generated_asset_relocation.v1"
OPERATION = "relocate_generated_assets"
_SOURCE_ROOT = "Assets/VRCForge/Generated/"
_DESTINATION_ROOT = "Assets/VRCForgeGenerated/"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GUID = re.compile(r"^[0-9a-f]{32}$")
_ENTRY_KEYS = {
    "sourceAssetPath",
    "destinationAssetPath",
    "expectedGuid",
    "expectedAssetSha256",
    "expectedMetaSha256",
}


class GeneratedAssetRelocationError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    """Build the outer project-bound wrapper consumed by the generic Unity lane."""

    if not isinstance(params, dict):
        raise GeneratedAssetRelocationError("Generated asset relocation parameters are required.")
    source = dict(params)
    nested = source.get("arguments")
    if not isinstance(nested, dict):
        nested = source.get("params")
    if not isinstance(nested, dict):
        nested = source
    nested = dict(nested)
    if "expectedProjectPath" not in nested and isinstance(source.get("projectPath"), str):
        nested["expectedProjectPath"] = source["projectPath"]
    arguments = normalize_arguments(nested)
    wrapper: dict[str, Any] = {
        key: deepcopy(value)
        for key, value in source.items()
        if key not in {"entries", "expectedProjectPath", "preview", "arguments", "params", "tool_name", "toolName"}
    }
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = arguments
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    canonical = normalize_arguments(arguments)
    canonical["preview"] = True
    return canonical


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wrapper = _dict(wrapper_arguments, "generated asset relocation wrapper")
    if str(wrapper.get("toolName") or wrapper.get("tool_name") or "").strip() != TOOL_NAME:
        raise GeneratedAssetRelocationError("Generated asset relocation tool binding is invalid.")
    project = wrapper.get("projectPath")
    if not isinstance(project, str) or not project.strip():
        raise GeneratedAssetRelocationError("projectPath is required.")
    requested = normalize_arguments(wrapper.get("arguments"))
    result = _dict(payload, "generated asset relocation preview")
    if result.get("schema") != RESULT_SCHEMA or result.get("operation") != OPERATION:
        raise GeneratedAssetRelocationError("Generated asset relocation preview schema is invalid.")
    for key, expected in (
        ("ok", True), ("verified", True), ("preview", True), ("changed", False),
        ("mutationStarted", False), ("committed", False),
    ):
        if result.get(key) is not expected:
            raise GeneratedAssetRelocationError(f"Generated asset relocation preview {key} is invalid.")
    if result.get("mutationCount") != 0 or result.get("commitState") != "not_started":
        raise GeneratedAssetRelocationError("Generated asset relocation preview mutationCount is invalid.")
    _assert_result_binding(result, requested, project)
    canonical = deepcopy(wrapper)
    canonical["projectPath"] = project
    canonical["toolName"] = TOOL_NAME
    canonical["arguments"] = {**requested, "expectedProjectPath": project, "preview": False}
    approval = {
        "schema": f"{RESULT_SCHEMA}.approval",
        "toolName": TOOL_NAME,
        "operation": OPERATION,
        "projectPath": project,
        "entries": deepcopy(requested["entries"]),
        "mutationCount": len(requested["entries"]),
        "rollbackRequired": True,
    }
    return canonical, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    expected = normalize_arguments(arguments)
    result = _dict(payload, "generated asset relocation apply result")
    if result.get("schema") != RESULT_SCHEMA or result.get("operation") != OPERATION:
        raise GeneratedAssetRelocationError("Generated asset relocation apply schema is invalid.")
    for key, value in (
        ("ok", True), ("verified", True), ("preview", False), ("changed", True),
        ("mutationStarted", True), ("committed", True),
        ("checkpointRecoveryRequired", False),
    ):
        if result.get(key) is not value:
            raise GeneratedAssetRelocationError(f"Generated asset relocation apply {key} is invalid.")
    if result.get("mutationCount") != len(expected["entries"]):
        raise GeneratedAssetRelocationError("Generated asset relocation apply mutationCount is invalid.")
    if result.get("commitState") != "committed" or result.get("persistenceState") != "persisted":
        raise GeneratedAssetRelocationError("Generated asset relocation apply persistence state is invalid.")
    if result.get("readbackState") != "verified" or result.get("cleanupState") != "complete":
        raise GeneratedAssetRelocationError("Generated asset relocation apply readback state is invalid.")
    _assert_result_binding(result, expected, expected["expectedProjectPath"])
    return deepcopy(result)


def _assert_result_binding(result: dict[str, Any], expected: dict[str, Any], project: str) -> None:
    if result.get("expectedProjectPath") != project:
        raise GeneratedAssetRelocationError("Generated asset relocation project binding changed.")
    returned = result.get("entries")
    if returned != expected["entries"]:
        raise GeneratedAssetRelocationError("Generated asset relocation entries changed.")


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GeneratedAssetRelocationError(f"{label} is invalid.")
    return value


def normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise GeneratedAssetRelocationError("Relocation arguments are required.")
    entries = arguments.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > 500:
        raise GeneratedAssetRelocationError("entries must contain 1 to 500 exact asset rows.")
    normalized: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
            raise GeneratedAssetRelocationError("Each relocation row must contain the exact five fields.")
        source = _asset_path(raw["sourceAssetPath"], _SOURCE_ROOT, "sourceAssetPath")
        destination = _asset_path(raw["destinationAssetPath"], _DESTINATION_ROOT, "destinationAssetPath")
        if PurePosixPath(source).name != PurePosixPath(destination).name:
            raise GeneratedAssetRelocationError("Source and destination must preserve the exact basename and extension.")
        source_key = source.casefold()
        destination_key = destination.casefold()
        if source_key in seen_sources or destination_key in seen_destinations:
            raise GeneratedAssetRelocationError("Relocation rows must have unique source and destination paths.")
        seen_sources.add(source_key)
        seen_destinations.add(destination_key)
        normalized.append(
            {
                "sourceAssetPath": source,
                "destinationAssetPath": destination,
                "expectedGuid": _match(raw["expectedGuid"], _GUID, "expectedGuid"),
                "expectedAssetSha256": _match(raw["expectedAssetSha256"], _DIGEST, "expectedAssetSha256"),
                "expectedMetaSha256": _match(raw["expectedMetaSha256"], _DIGEST, "expectedMetaSha256"),
            }
        )
    project = arguments.get("expectedProjectPath")
    if not isinstance(project, str) or not project.strip():
        raise GeneratedAssetRelocationError("expectedProjectPath is required.")
    preview = arguments.get("preview", True)
    if not isinstance(preview, bool):
        raise GeneratedAssetRelocationError("preview must be a boolean.")
    return {"entries": normalized, "expectedProjectPath": project, "preview": preview}


def build_execution_plan(params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Freeze the approved rows and force the apply phase at the Core boundary."""

    wrapper = build_wrapper_arguments(params)
    arguments = deepcopy(wrapper["arguments"])
    arguments["preview"] = False
    return [(TOOL_NAME, arguments)]


def _asset_path(value: Any, root: str, label: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise GeneratedAssetRelocationError(f"{label} is invalid.")
    path = value.replace("\\", "/")
    if not path.startswith(root) or any(part in {"", ".", ".."} for part in path.split("/")):
        raise GeneratedAssetRelocationError(f"{label} must remain below {root}.")
    if path.lower().endswith(".meta"):
        raise GeneratedAssetRelocationError(f"{label} must name an asset, not its .meta file.")
    return path


def _match(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value.lower()) is None:
        raise GeneratedAssetRelocationError(f"{label} is invalid.")
    return value.lower()
