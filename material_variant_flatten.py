"""Authoritative preview and persisted-readback receipts for one Material Variant."""
from __future__ import annotations
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

TOOL_NAME = "vrc_flatten_material_variant"
SCHEMA = "vrcforge.material_variant_flatten.v1"
APPROVAL_SCHEMA = "vrcforge.material_variant_flatten_approval.v1"
_GUID = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_HASH128 = re.compile(r"^[0-9a-f]{32}$")

class MaterialVariantFlattenError(ValueError):
    pass

def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    raw = deepcopy(params or {})
    nested = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else raw.get("params")
    if not isinstance(nested, dict):
        nested = {key: raw[key] for key in ("assetPath", "expectedGuid", "expectedDependencyHash", "expectedFileDigest", "preview") if key in raw}
    for key in ("assetPath", "expectedGuid", "expectedDependencyHash", "expectedFileDigest", "params", "arguments", "toolName", "tool_name"):
        raw.pop(key, None)
    raw["toolName"] = TOOL_NAME
    raw["arguments"] = deepcopy(nested)
    return raw

def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    request = arguments if isinstance(arguments, dict) else {}
    return {key: deepcopy(request[key]) for key in ("assetPath",) if key in request} | {"preview": True}

def bind_authoritative_preview(wrapper_arguments: dict[str, Any], payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    wrapper = _mapping(wrapper_arguments, "material variant wrapper")
    request = _mapping(wrapper.get("arguments"), "material variant arguments")
    project = _project_path(wrapper.get("projectPath"))
    asset = _asset_path(request.get("assetPath"))
    result = _mapping(payload, "material variant preview")
    _require(result.get("schema") == SCHEMA, "Material Variant preview schema is invalid.")
    _require(result.get("ok", True) is True, "Material Variant preview failed.")
    no_change = result.get("state") == "no_change"
    _require(result.get("state") in {"preview", "no_change"} and result.get("preview") is (not no_change), "Material Variant preview state is invalid.")
    _require(result.get("committed") is False and result.get("commitState") == "not_started" and result.get("mutationStarted") is False and result.get("pending") is False, "Material Variant preview reported a mutation.")
    _require(result.get("persistenceState") == "not_applicable" and result.get("readbackState") == "not_required", "Material Variant preview persistence state is invalid.")
    _require(result.get("persistedReadback") is False and result.get("readback") is None, "Material Variant preview reported a persisted write.")
    _require(result.get("assetPath") == asset, "Material Variant preview changed the asset path.")
    guid = _hex(result.get("guid"), _GUID, "guid")
    dependency = _hex(result.get("dependencyHash"), _HASH128, "dependencyHash")
    file_digest = _hex(result.get("fileDigest"), _DIGEST, "fileDigest")
    _require(result.get("isVariant") is (not no_change), "Material Variant preview target state is invalid.")
    if no_change:
        _require(result.get("parent") == "", "Independent Material still has a parent.")
    effective = result.get("before")
    _require(isinstance(effective, dict), "Material Variant preview effective snapshot is missing.")
    canonical = deepcopy(wrapper)
    canonical["projectPath"] = project
    canonical["toolName"] = TOOL_NAME
    canonical["arguments"] = {"assetPath": asset, "expectedGuid": guid, "expectedDependencyHash": dependency, "expectedFileDigest": file_digest, "preview": False}
    return canonical, {"schema": APPROVAL_SCHEMA, "projectPath": project, "assetPath": asset, "guid": guid, "dependencyHash": dependency, "fileDigest": file_digest, "variant": bool(result.get("isVariant")), "effectiveSnapshot": deepcopy(effective)}

def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    expected = _mapping(arguments, "approved material variant arguments")
    result = _mapping(payload, "material variant apply result")
    _require(result.get("schema") == SCHEMA, "Material Variant apply schema is invalid.")
    _require(result.get("ok", True) is True, "Material Variant apply failed.")
    _require(result.get("assetPath") == _asset_path(expected.get("assetPath")), "Material Variant apply changed the asset path.")
    _require(str(result.get("guid") or "").lower() == _hex(expected.get("expectedGuid"), _GUID, "expectedGuid"), "Material Variant apply changed the GUID.")
    _hex(result.get("dependencyHash"), _HASH128, "dependencyHash")
    _hex(result.get("fileDigest"), _DIGEST, "fileDigest")
    if result.get("state") == "no_change":
        _require(result.get("preview") is False and result.get("committed") is False and result.get("commitState") == "not_started" and result.get("mutationStarted") is False and result.get("pending") is False, "Material Variant no-change receipt is invalid.")
        _require(result.get("fileDigest") == expected.get("expectedFileDigest") and result.get("dependencyHash") == expected.get("expectedDependencyHash"), "Independent Material changed after preview.")
        _require(result.get("parent") == "" and result.get("isVariant") is False and result.get("after") == result.get("before"), "Material Variant no-change state is invalid.")
        _require(result.get("persistenceState") == "not_applicable" and result.get("readbackState") == "not_required" and result.get("persistedReadback") is False and result.get("readback") is None, "Material Variant no-change persistence is invalid.")
        return dict(result)
    for key in ("verified", "persistedReadback", "committed"):
        _require(result.get(key) is True, f"Material Variant apply {key} is invalid.")
    _require(result.get("preview") is False and result.get("pending") is False, "Material Variant apply remained pending or in preview mode.")
    _require(result.get("persistenceState") == "persisted" and result.get("readbackState") == "verified", "Material Variant apply was not saved and verified.")
    _require(result.get("state") == "flattened" and result.get("commitState") == "committed", "Material Variant apply commit state is invalid.")
    _require(_hex(result.get("fileDigest"), _DIGEST, "fileDigest") != _hex(expected.get("expectedFileDigest"), _DIGEST, "expectedFileDigest"), "Material Variant apply did not produce new saved bytes.")
    after = _mapping(result.get("after"), "Material Variant apply after snapshot")
    before = _mapping(result.get("before"), "Material Variant apply before snapshot")
    _require(after == before, "Material Variant apply changed effective Material values.")
    _require(result.get("parent") in ("", None) and result.get("isVariant") is False, "Material Variant apply did not remove the parent.")
    readback = _mapping(result.get("readback"), "Material Variant persisted readback")
    _require(readback.get("schema") == "vrcforge.material_variant_flatten.readback.v1" and all(readback.get(key) == result.get(key) for key in ("assetPath", "guid", "dependencyHash", "fileDigest")), "Material Variant readback identity is invalid.")
    _require(readback.get("verified") is True and readback.get("parent") in ("", None) and readback.get("isVariant") is False, "Material Variant persisted readback is invalid.")
    _require(readback.get("effectiveState") == before, "Material Variant persisted effective state changed.")
    return dict(result)

def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict): raise MaterialVariantFlattenError(f"A valid {label} is required.")
    return value

def _project_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not Path(text).is_absolute(): raise MaterialVariantFlattenError("projectPath must be an absolute Unity project path.")
    return str(Path(text).resolve(strict=False))

def _asset_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text.startswith("Assets/") or not text.lower().endswith(".mat") or ".." in Path(text).parts: raise MaterialVariantFlattenError("assetPath must be one exact Assets/... .mat path.")
    return text

def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text): raise MaterialVariantFlattenError(f"{label} has an invalid digest or identity.")
    return text

def _require(condition: bool, message: str) -> None:
    if not condition: raise MaterialVariantFlattenError(message)
