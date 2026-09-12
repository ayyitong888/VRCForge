"""Exact preview and persisted-readback receipts for one material texture slot."""

from __future__ import annotations

import json
import math
import os
import re
import struct
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


ASSIGNMENT_SCHEMA = "vrcforge.material_texture_assignment.v1"
APPROVAL_PREVIEW_SCHEMA = "vrcforge.material_texture_assignment_approval.v1"
TOOL_NAME = "vrc_set_material_texture"
REQUEST_ARGUMENT_KEYS = ("materialAssetPath", "propertyName", "textureAssetPath", "textureScale", "textureOffset", "assignments")
_TRANSFORMS = ("textureScale", "textureOffset")
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
    nested = _transform_batch(nested)
    if "assignments" in nested:
        nested = {**nested, "assignments": _batch_rows(nested)}
    for key in REQUEST_ARGUMENT_KEYS:
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    request = _transform_batch(arguments if isinstance(arguments, dict) else {})
    if "assignments" in request:
        return {"assignments": _batch_rows(request), "preview": True}
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
    request = _transform_batch(_mapping(wrapper.get("arguments"), "material texture arguments"))
    if "assignments" in request:
        return _bind_batch(wrapper, request, payload)
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
    if "assignments" in arguments:
        return _validate_batch(arguments, payload)
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
    if not name:
        raise MaterialTextureAssignmentError("An exact material texture property name is required.")
    # The authoritative Unity preview validates the installed shader property type.
    return name


def _hex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text):
        raise MaterialTextureAssignmentError(f"{label} is invalid.")
    return text


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MaterialTextureAssignmentError(message)


def _vector2(value, label):
    _require(isinstance(value, dict) and set(value) == {"x", "y"}, label + " requires exactly numeric x and y.")
    result = {}
    for axis in ("x", "y"):
        number = value[axis]
        _require(type(number) in (int, float) and abs(number) <= 3.4028234663852886e38 and math.isfinite(number),
                 label + " must contain finite float32 values.")
        result[axis] = struct.unpack("f", struct.pack("f", number))[0]
    return result


def _transform_batch(args):
    # New single-slot transform syntax uses the existing sealed batch transaction.
    # Requests without transforms retain the legacy single-slot contract.
    if "assignments" not in args and any(key in args for key in _TRANSFORMS):
        row = {key: deepcopy(args[key]) for key in REQUEST_ARGUMENT_KEYS if key in args}
        return {**{key: deepcopy(value) for key, value in args.items() if key not in REQUEST_ARGUMENT_KEYS}, "assignments": [row]}
    return args


def _batch_rows(args):
    _require(not any(k in args for k in REQUEST_ARGUMENT_KEYS if k != 'assignments'), 'Batch and single selectors cannot be mixed.')
    rows = args.get('assignments')
    _require(isinstance(rows, list) and 1 <= len(rows) <= 128, 'Texture batch requires 1..128 rows.')
    _require(len(json.dumps(args, ensure_ascii=False).encode('utf-8')) <= 512*1024, 'Texture batch exceeds 512 KiB.')
    seen = set()
    normalized = deepcopy(rows)
    for row in normalized:
        _require(isinstance(row, dict) and {'materialAssetPath','propertyName','textureAssetPath'} <= set(row)
                 and set(row) <= {'materialAssetPath','propertyName','textureAssetPath', *_TRANSFORMS}, 'Batch row requires exact material, property, and texture fields.')
        path = _asset_path(row['materialAssetPath'], 'materialAssetPath', suffix='.mat')
        prop = _property(row['propertyName'])
        tex = _asset_path(row['textureAssetPath'], 'textureAssetPath')
        _require(all(row[key] == value for key, value in dict(materialAssetPath=path, propertyName=prop, textureAssetPath=tex).items()), 'Batch paths and properties must be exact.')
        for key in _TRANSFORMS:
            if key in row:
                row[key] = _vector2(row[key], key)
        _require((path,prop) not in seen, 'Duplicate material/property assignment.')
        seen.add((path,prop))
    return normalized


def _plan_rows(rows, plan):
    _require(isinstance(plan, list) and len(plan) == len(rows), 'Texture plan row count differs.')
    for row, evidence in zip(rows, plan):
        _require(isinstance(evidence,dict) and all(evidence.get(k) == v for k,v in row.items()), 'Texture plan changed an ordered request.')
        for key in ('materialAssetGuid','textureAssetGuid','afterTextureAssetGuid'):
            _hex(evidence.get(key), _GUID, key)
        for key in ('materialFileDigestBefore','materialMetaDigest','materialStateDigest','textureFileDigest'):
            _hex(evidence.get(key), _DIGEST, key)
        _require(type(evidence.get('textureLocalId')) is int and bool(evidence.get('shaderDependencyHash')), 'Texture or shader identity missing.')
        _require(evidence.get('afterTextureAssetGuid') == evidence['textureAssetGuid'] and evidence.get('afterTextureAssetPath') == row['textureAssetPath'], 'Texture plan destination differs.')
        before = _optional_asset_path(evidence.get('beforeTextureAssetPath'), 'beforeTextureAssetPath')
        if before: _hex(evidence.get('beforeTextureAssetGuid'), _GUID, 'beforeTextureAssetGuid')
        else: _require(evidence.get('beforeTextureAssetGuid') == '', 'Empty texture has a GUID.')
        if any(key in row for key in _TRANSFORMS):
            for name in ('Scale', 'Offset'):
                before = _vector2(evidence.get('beforeTexture' + name), 'beforeTexture' + name)
                after = _vector2(evidence.get('afterTexture' + name), 'afterTexture' + name)
                _require(after == row.get('texture' + name, before), 'Texture plan changed requested or preserved transform.')
    return deepcopy(plan)


def _batch_flags(result, preview):
    _require(result.get('schema') == ASSIGNMENT_SCHEMA, 'Texture batch schema invalid.')
    for k,v in {'batch':True,'ok':True,'preview':preview,'verified':True,'persistedReadback':not preview,'committed':not preview}.items():
        _require(result.get(k) is v, 'Texture batch receipt flag invalid: '+k)


def _bind_batch(wrapper, args, payload):
    rows = _batch_rows(args); result = _mapping(payload, 'texture batch preview')
    _batch_flags(result, True)
    _require(all(result.get(k) is False for k in ('changed','saved','mutationStarted')), 'Texture batch preview mutated.')
    project = _project_path(wrapper.get('projectPath'))
    _require(os.path.normcase(project) == os.path.normcase(_project_path(result.get('projectPath'))), 'Texture batch project differs.')
    plan = {'assignments':_plan_rows(rows, result.get('assignments'))}
    _require(args.get('expectedBatchPlan', plan) == plan, 'Explicit texture plan differs.')
    canonical = deepcopy(wrapper); canonical['arguments'] = {'assignments':rows,'expectedBatchPlan':plan,'expectedProjectPath':project,'preview':False}
    _batch_rows(canonical['arguments'])
    return canonical, {'schema':APPROVAL_PREVIEW_SCHEMA,'batch':True,'projectPath':project,'assignments':deepcopy(plan['assignments']),'rollbackRequired':True}


def _validate_batch(args, payload):
    rows = _batch_rows(args); result = _mapping(payload, 'texture batch result'); _batch_flags(result,False)
    plan = _mapping(args.get('expectedBatchPlan'), 'sealed texture plan')
    expected = _plan_rows(rows,plan.get('assignments'))
    _require(result.get('assignments') == expected, 'Texture apply differs from sealed plan.')
    _require(os.path.normcase(_project_path(result.get('projectPath'))) == os.path.normcase(_project_path(args.get('expectedProjectPath'))), 'Texture batch project differs.')
    actual = result.get('readback')
    _require(isinstance(actual,list) and len(actual) == len(expected), 'Texture readback count differs.')
    changed = any(p['beforeTextureAssetPath'] != p['textureAssetPath'] or p['beforeTextureAssetGuid'] != p['textureAssetGuid']
                  or any(p.get('beforeTexture' + name) != p.get('afterTexture' + name) for name in ('Scale', 'Offset')) for p in expected)
    _require(result.get('changed') is changed and result.get('saved') is changed and result.get('mutationStarted') is changed and result.get('commitState') == ('committed' if changed else 'no_change'), 'Texture commit state differs.')
    for before, after in zip(expected, actual):
        _require(isinstance(after,dict), 'Texture readback missing.')
        for key in ('materialAssetPath','materialAssetGuid','materialMetaDigest','propertyName','afterTextureAssetPath','afterTextureAssetGuid','textureLocalId','textureFileDigest'):
            _require(after.get(key) == before[key], 'Texture persisted identity differs: '+key)
        if any(key in before for key in _TRANSFORMS):
            for name in ('Scale', 'Offset'):
                key = 'afterTexture' + name
                _require(_vector2(after.get(key), key) == _vector2(before.get(key), key), 'Texture persisted transform differs: ' + key)
        _hex(after.get('materialFileDigestAfter'), _DIGEST, 'materialFileDigestAfter')
        _hex(after.get('materialStateDigest'), _DIGEST, 'materialStateDigest')
    return deepcopy(result)
