"""Exact preview and persisted-readback receipts for one material texture slot."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


ASSIGNMENT_SCHEMA = "vrcforge.material_texture_assignment.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.material_texture_assignment_approval.v1"
TOOL_NAME = "vrc_set_material_texture"
ALLOWED_TEXTURE_PROPERTIES = (
    "_MainTex",
    "_Main2ndTex",
    "_Main3rdTex",
    "_ShadowColorTex",
)
REQUEST_ARGUMENT_KEYS = ("materialAssetPath", "propertyName", "textureAssetPath")
_GUID = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class MaterialTextureAssignmentError(ValueError):
    """Reject incomplete, unsafe, or drifted material texture evidence."""


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
    request = arguments if isinstance(arguments, dict) else {}
    return {
        **{key: deepcopy(request[key]) for key in REQUEST_ARGUMENT_KEYS if key in request},
        "preview": True,
        "saveAssets": False,
    }


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wrapper = _mapping(wrapper_arguments, "material texture wrapper")
    request = _mapping(wrapper.get("arguments"), "material texture arguments")
    project_path = _project_path(wrapper.get("projectPath"))
    requested_material = _asset_path(request.get("materialAssetPath"), "materialAssetPath", suffix=".mat")
    requested_property = _property(request.get("propertyName"))
    requested_texture = _asset_path(request.get("textureAssetPath"), "textureAssetPath")
    result = _mapping(payload, "material texture preview")

    _require(result.get("schema") == ASSIGNMENT_SCHEMA, "Material texture preview schema is invalid.")
    for key in ("ok", "preview", "verified"):
        _require(result.get(key) is True, f"Material texture preview {key} is invalid.")
    for key in ("changed", "saved", "persistedReadback"):
        _require(result.get(key) is False, f"Material texture preview reported {key}.")
    actual_project = _project_path(result.get("projectPath"))
    _require(os.path.normcase(actual_project) == os.path.normcase(project_path), "Material texture preview changed the project.")

    material_path = _asset_path(result.get("materialAssetPath"), "materialAssetPath", suffix=".mat")
    property_name = _property(result.get("propertyName"))
    texture_path = _asset_path(result.get("textureAssetPath"), "textureAssetPath")
    _require(material_path == requested_material, "Material texture preview changed the material.")
    _require(property_name == requested_property, "Material texture preview changed the property.")
    _require(texture_path == requested_texture, "Material texture preview changed the texture.")

    material_guid = _hex(result.get("materialAssetGuid"), _GUID, "materialAssetGuid")
    material_digest = _hex(result.get("materialFileDigestBefore"), _DIGEST, "materialFileDigestBefore")
    _require(
        _hex(result.get("materialFileDigestAfter"), _DIGEST, "materialFileDigestAfter") == material_digest,
        "Material texture preview changed material bytes.",
    )
    texture_guid = _hex(result.get("textureAssetGuid"), _GUID, "textureAssetGuid")
    texture_digest = _hex(result.get("textureFileDigest"), _DIGEST, "textureFileDigest")
    before_path = _optional_asset_path(result.get("beforeTextureAssetPath"), "beforeTextureAssetPath")
    before_guid = str(result.get("beforeTextureAssetGuid") or "").strip().lower()
    if before_path:
        before_guid = _hex(before_guid, _GUID, "beforeTextureAssetGuid")
    else:
        _require(not before_guid, "beforeTextureAssetGuid requires beforeTextureAssetPath.")
    _require(
        _asset_path(result.get("afterTextureAssetPath"), "afterTextureAssetPath") == texture_path,
        "Material texture preview after path is invalid.",
    )
    _require(
        _hex(result.get("afterTextureAssetGuid"), _GUID, "afterTextureAssetGuid") == texture_guid,
        "Material texture preview after GUID is invalid.",
    )
    _require(type(result.get("wouldChange")) is bool, "Material texture preview wouldChange is invalid.")

    canonical = deepcopy(wrapper)
    canonical["projectPath"] = project_path
    canonical["toolName"] = TOOL_NAME
    canonical["arguments"] = {
        "materialAssetPath": material_path,
        "propertyName": property_name,
        "textureAssetPath": texture_path,
        "expectedProjectPath": project_path,
        "expectedMaterialAssetGuid": material_guid,
        "expectedMaterialFileDigest": material_digest,
        "expectedBeforeTextureAssetPath": before_path,
        "expectedBeforeTextureAssetGuid": before_guid,
        "expectedTextureAssetGuid": texture_guid,
        "expectedTextureFileDigest": texture_digest,
        "preview": False,
        "saveAssets": True,
    }
    return canonical, {
        "schema": APPROVAL_PREVIEW_SCHEMA,
        "projectPath": project_path,
        "materialAssetPath": material_path,
        "materialAssetGuid": material_guid,
        "propertyName": property_name,
        "beforeTextureAssetPath": before_path,
        "beforeTextureAssetGuid": before_guid,
        "textureAssetPath": texture_path,
        "textureAssetGuid": texture_guid,
        "textureFileDigest": texture_digest,
        "wouldChange": result["wouldChange"],
    }


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    expected = _mapping(arguments, "approved material texture arguments")
    result = _mapping(payload, "material texture apply result")
    _require(result.get("schema") == ASSIGNMENT_SCHEMA, "Material texture apply schema is invalid.")
    for key in ("ok", "verified", "persistedReadback", "committed"):
        _require(result.get(key) is True, f"Material texture apply {key} is invalid.")
    _require(result.get("preview") is False, "Material texture apply remained in preview mode.")
    changed = result.get("changed")
    _require(type(changed) is bool, "Material texture apply changed is invalid.")
    _require(result.get("saved") is changed, "Material texture apply persistence does not match its change.")
    _require(
        result.get("commitState") == ("committed" if changed else "no_change"),
        "Material texture apply commit state is invalid.",
    )
    for field in ("materialAssetPath", "propertyName", "textureAssetPath"):
        _require(result.get(field) == expected.get(field), f"Material texture apply changed {field}.")
    project_path = _project_path(result.get("projectPath"))
    _require(
        os.path.normcase(project_path) == os.path.normcase(_project_path(expected.get("expectedProjectPath"))),
        "Material texture apply changed the project.",
    )
    for result_key, expected_key, pattern in (
        ("materialAssetGuid", "expectedMaterialAssetGuid", _GUID),
        ("materialFileDigestBefore", "expectedMaterialFileDigest", _DIGEST),
        ("textureAssetGuid", "expectedTextureAssetGuid", _GUID),
        ("textureFileDigest", "expectedTextureFileDigest", _DIGEST),
        ("afterTextureAssetGuid", "expectedTextureAssetGuid", _GUID),
    ):
        _require(
            _hex(result.get(result_key), pattern, result_key)
            == _hex(expected.get(expected_key), pattern, expected_key),
            f"Material texture apply changed {result_key}.",
        )
    _require(result.get("afterTextureAssetPath") == expected.get("textureAssetPath"), "Material texture apply persisted a different texture.")
    after_digest = _hex(result.get("materialFileDigestAfter"), _DIGEST, "materialFileDigestAfter")
    before_digest = _hex(expected.get("expectedMaterialFileDigest"), _DIGEST, "expectedMaterialFileDigest")
    _require((after_digest != before_digest) is changed, "Material texture apply material bytes do not match its change.")
    return dict(result)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaterialTextureAssignmentError(f"A valid {label} is required.")
    return value


def _project_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not Path(text).is_absolute():
        raise MaterialTextureAssignmentError("An absolute Unity project path is required.")
    return str(Path(text).resolve())


def _asset_path(value: Any, label: str, *, suffix: str = "") -> str:
    text = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if (
        not text.startswith("Assets/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in text.split("/"))
        or (suffix and path.suffix.casefold() != suffix.casefold())
    ):
        raise MaterialTextureAssignmentError(f"{label} must be an exact project Assets path.")
    return text


def _optional_asset_path(value: Any, label: str) -> str:
    return _asset_path(value, label) if str(value or "").strip() else ""


def _property(value: Any) -> str:
    name = str(value or "").strip()
    if name not in ALLOWED_TEXTURE_PROPERTIES:
        raise MaterialTextureAssignmentError("The requested material texture property is not allowed.")
    return name


def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text):
        raise MaterialTextureAssignmentError(f"{label} is invalid.")
    return text


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MaterialTextureAssignmentError(message)
