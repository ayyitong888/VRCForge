from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


TOOL_NAME = "vrc_duplicate_scene_asset"
RESULT_SCHEMA = "vrcforge.scene_asset_duplicate.v1"
APPROVAL_SCHEMA = "vrcforge.scene_asset_duplicate_approval.v1"
OPERATION = "duplicate_scene_asset"
PREVIEW_DIGEST_SCHEMA = "vrcforge.scene_asset_duplicate_preview.v1"

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_KEYS = (
    "sourceScenePath",
    "destinationScenePath",
    "openAsOnlyActiveScene",
)


class SceneAssetDuplicateError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in _REQUEST_KEYS if key in wrapper}
    for key in _REQUEST_KEYS:
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    provided = arguments if isinstance(arguments, dict) else {}
    preview = {key: deepcopy(provided[key]) for key in _REQUEST_KEYS if key in provided}
    preview.setdefault("openAsOnlyActiveScene", False)
    preview["preview"] = True
    preview["overwrite"] = False
    return preview


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wrapper = _dict(wrapper_arguments, "scene duplicate wrapper")
    if str(wrapper.get("toolName") or wrapper.get("tool_name") or "").strip() != TOOL_NAME:
        raise SceneAssetDuplicateError("Scene duplicate tool binding is invalid.")
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    nested = _dict(nested, "scene duplicate arguments")
    project_path = _project_path(wrapper.get("projectPath"))
    requested_source = _scene_path(nested.get("sourceScenePath"), "sourceScenePath")
    requested_destination = _scene_path(
        nested.get("destinationScenePath"),
        "destinationScenePath",
    )
    if requested_source == requested_destination:
        raise SceneAssetDuplicateError("Source and destination scene paths are identical.")
    requested_open = _bool(
        nested.get("openAsOnlyActiveScene", False),
        "openAsOnlyActiveScene",
    )

    result = _dict(payload, "scene duplicate preview")
    if result.get("schema") != RESULT_SCHEMA or result.get("operation") != OPERATION:
        raise SceneAssetDuplicateError("Scene duplicate preview schema is invalid.")
    if _project_path(result.get("projectPath")) != project_path:
        raise SceneAssetDuplicateError("Scene duplicate preview came from a different Unity project.")
    for key, expected in (
        ("ok", True),
        ("preview", True),
        ("verified", True),
        ("changed", False),
        ("saved", False),
        ("cleanupRequired", False),
    ):
        if result.get(key) is not expected:
            raise SceneAssetDuplicateError(f"Scene duplicate preview {key} is invalid.")
    if _int(result.get("mutationCount"), "mutationCount", 0, 0) != 0:
        raise SceneAssetDuplicateError("Scene duplicate preview mutationCount is invalid.")

    source = _source(result.get("source"))
    target = _preview_target(result.get("target"))
    if source["assetPath"] != requested_source or target["assetPath"] != requested_destination:
        raise SceneAssetDuplicateError("The preview changed the requested scene paths.")
    if target["openAsOnlyActiveScene"] is not requested_open:
        raise SceneAssetDuplicateError("The preview changed the requested scene-open behavior.")
    setup_digest = _hex(result.get("sceneSetupDigest"), "sceneSetupDigest", _HEX_64)
    state_digest = _hex(
        result.get("openSceneStateDigest"),
        "openSceneStateDigest",
        _HEX_64,
    )
    preview_digest = _hex(result.get("previewDigest"), "previewDigest", _HEX_64)
    if compute_preview_digest(result) != preview_digest:
        raise SceneAssetDuplicateError("Scene duplicate preview digest is invalid.")

    canonical_arguments = {
        "sourceScenePath": source["assetPath"],
        "destinationScenePath": target["assetPath"],
        "openAsOnlyActiveScene": requested_open,
        "preview": False,
        "overwrite": False,
        "expectedProjectPath": project_path,
        "expectedSourceGuid": source["guid"],
        "expectedSourceFileDigest": source["fileDigest"],
        "expectedSourceFileIdentity": source["fileIdentity"],
        "expectedSourceMetaDigest": source["metaDigest"],
        "expectedSourceMetaIdentity": source["metaIdentity"],
        "expectedDestinationParentPath": target["parentPath"],
        "expectedDestinationAbsent": True,
        "expectedSceneSetupDigest": setup_digest,
        "expectedOpenSceneStateDigest": state_digest,
        "expectedOpenAsOnlyActiveScene": requested_open,
        "expectedPreviewDigest": preview_digest,
    }
    canonical = deepcopy(wrapper)
    canonical.pop("params", None)
    canonical["toolName"] = TOOL_NAME
    canonical["projectPath"] = project_path
    canonical["arguments"] = canonical_arguments
    approval = {
        "schema": APPROVAL_SCHEMA,
        "toolName": TOOL_NAME,
        "operation": OPERATION,
        "source": source,
        "target": target,
        "mutationCount": 2 if requested_open else 1,
        "createNew": True,
        "overwrite": False,
        "openAsOnlyActiveScene": requested_open,
        "rollbackRequired": True,
        "previewDigest": preview_digest,
    }
    return canonical, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    expected = _dict(arguments, "scene duplicate apply arguments")
    result = _dict(payload, "scene duplicate apply result")
    if result.get("schema") != RESULT_SCHEMA or result.get("operation") != OPERATION:
        raise SceneAssetDuplicateError("Scene duplicate apply schema is invalid.")
    for key, value in (
        ("ok", True),
        ("preview", False),
        ("verified", True),
        ("changed", True),
        ("saved", True),
        ("cleanupRequired", False),
        ("checkpointRestoreRequired", False),
    ):
        if result.get(key) is not value:
            raise SceneAssetDuplicateError(f"Scene duplicate apply {key} is invalid.")
    source = _source(result.get("source"), require_unchanged=True)
    target = _apply_target(result.get("target"))
    if source["assetPath"] != expected.get("sourceScenePath"):
        raise SceneAssetDuplicateError("Scene duplicate apply source path changed.")
    for key, expected_key in (
        ("guid", "expectedSourceGuid"),
        ("fileDigest", "expectedSourceFileDigest"),
        ("fileIdentity", "expectedSourceFileIdentity"),
        ("metaDigest", "expectedSourceMetaDigest"),
        ("metaIdentity", "expectedSourceMetaIdentity"),
    ):
        if source[key] != expected.get(expected_key):
            raise SceneAssetDuplicateError(f"Scene duplicate apply source {key} changed.")
    if target["assetPath"] != expected.get("destinationScenePath"):
        raise SceneAssetDuplicateError("Scene duplicate apply destination path changed.")
    if target["guid"] == source["guid"]:
        raise SceneAssetDuplicateError("Scene duplicate apply did not create an independent GUID.")
    if target["fileDigest"] != source["fileDigest"] or target["bytesIdenticalToSource"] is not True:
        raise SceneAssetDuplicateError("Scene duplicate apply scene bytes changed.")
    requested_open = _bool(
        expected.get("openAsOnlyActiveScene"),
        "openAsOnlyActiveScene",
    )
    if target["openAsOnlyActiveScene"] is not requested_open:
        raise SceneAssetDuplicateError("Scene duplicate apply open behavior changed.")
    if target["opened"] is not requested_open or target["active"] is not requested_open:
        raise SceneAssetDuplicateError("Scene duplicate apply active-scene readback is invalid.")
    expected_mutations = 2 if requested_open else 1
    if _int(result.get("mutationCount"), "mutationCount", expected_mutations, expected_mutations) != expected_mutations:
        raise SceneAssetDuplicateError("Scene duplicate apply mutationCount is invalid.")
    if result.get("previewDigest") != expected.get("expectedPreviewDigest"):
        raise SceneAssetDuplicateError("Scene duplicate apply preview digest changed.")
    return deepcopy(result)


def compute_preview_digest(payload: dict[str, Any]) -> str:
    value = _dict(payload, "scene duplicate preview")
    source = _dict(value.get("source"), "scene duplicate preview source")
    target = _dict(value.get("target"), "scene duplicate preview target")
    fields = (
        PREVIEW_DIGEST_SCHEMA,
        value.get("schema"),
        value.get("operation"),
        value.get("projectPath"),
        source.get("assetPath"),
        source.get("guid"),
        source.get("fileDigest"),
        source.get("fileIdentity"),
        source.get("metaDigest"),
        source.get("metaIdentity"),
        "true" if source.get("loaded") is True else "false",
        target.get("assetPath"),
        target.get("parentPath"),
        "destination_absent",
        "true" if target.get("openAsOnlyActiveScene") is True else "false",
        value.get("sceneSetupDigest"),
        value.get("openSceneStateDigest"),
    )
    framed = b"".join(
        str(len(str(item or "").encode("utf-8"))).encode("ascii")
        + b":"
        + str(item or "").encode("utf-8")
        for item in fields
    )
    return hashlib.sha256(framed).hexdigest()


def _source(value: Any, *, require_unchanged: bool = False) -> dict[str, Any]:
    source = _dict(value, "scene duplicate source")
    result = {
        "assetPath": _scene_path(source.get("assetPath"), "source.assetPath"),
        "guid": _hex(source.get("guid"), "source.guid", _HEX_32),
        "fileDigest": _hex(source.get("fileDigest"), "source.fileDigest", _HEX_64),
        "fileIdentity": _hex(source.get("fileIdentity"), "source.fileIdentity", _HEX_64),
        "metaDigest": _hex(source.get("metaDigest"), "source.metaDigest", _HEX_64),
        "metaIdentity": _hex(source.get("metaIdentity"), "source.metaIdentity", _HEX_64),
        "mainAssetType": str(source.get("mainAssetType") or ""),
        "loaded": _bool(source.get("loaded"), "source.loaded"),
    }
    if result["mainAssetType"] != "UnityEditor.SceneAsset":
        raise SceneAssetDuplicateError("Scene duplicate source type is invalid.")
    if require_unchanged:
        if source.get("unchanged") is not True:
            raise SceneAssetDuplicateError("Scene duplicate apply did not verify the source unchanged.")
        result["unchanged"] = True
    return result


def _preview_target(value: Any) -> dict[str, Any]:
    target = _dict(value, "scene duplicate preview target")
    if target.get("assetExists") is not False or target.get("metaExists") is not False:
        raise SceneAssetDuplicateError("Scene duplicate destination is not absent.")
    if target.get("createNew") is not True:
        raise SceneAssetDuplicateError("Scene duplicate destination is not create-new.")
    opened = _bool(target.get("openAsOnlyActiveScene"), "target.openAsOnlyActiveScene")
    if target.get("willBecomeOnlyActiveScene") is not opened:
        raise SceneAssetDuplicateError("Scene duplicate preview open readback is invalid.")
    path = _scene_path(target.get("assetPath"), "target.assetPath")
    parent = _asset_folder(target.get("parentPath"), "target.parentPath")
    if str(PurePosixPath(path).parent) != parent:
        raise SceneAssetDuplicateError("Scene duplicate destination parent changed.")
    return {
        "assetPath": path,
        "parentPath": parent,
        "assetExists": False,
        "metaExists": False,
        "createNew": True,
        "openAsOnlyActiveScene": opened,
        "willBecomeOnlyActiveScene": opened,
    }


def _apply_target(value: Any) -> dict[str, Any]:
    target = _dict(value, "scene duplicate apply target")
    result = {
        "assetPath": _scene_path(target.get("assetPath"), "target.assetPath"),
        "guid": _hex(target.get("guid"), "target.guid", _HEX_32),
        "fileDigest": _hex(target.get("fileDigest"), "target.fileDigest", _HEX_64),
        "fileIdentity": _hex(target.get("fileIdentity"), "target.fileIdentity", _HEX_64),
        "metaDigest": _hex(target.get("metaDigest"), "target.metaDigest", _HEX_64),
        "metaIdentity": _hex(target.get("metaIdentity"), "target.metaIdentity", _HEX_64),
        "mainAssetType": str(target.get("mainAssetType") or ""),
        "bytesIdenticalToSource": target.get("bytesIdenticalToSource"),
        "createNew": target.get("createNew"),
        "readbackVerified": target.get("readbackVerified"),
        "openAsOnlyActiveScene": _bool(
            target.get("openAsOnlyActiveScene"),
            "target.openAsOnlyActiveScene",
        ),
        "opened": _bool(target.get("opened"), "target.opened"),
        "active": _bool(target.get("active"), "target.active"),
    }
    if result["mainAssetType"] != "UnityEditor.SceneAsset":
        raise SceneAssetDuplicateError("Scene duplicate target type is invalid.")
    if result["createNew"] is not True or result["readbackVerified"] is not True:
        raise SceneAssetDuplicateError("Scene duplicate target readback is invalid.")
    if not isinstance(result["bytesIdenticalToSource"], bool):
        raise SceneAssetDuplicateError("Scene duplicate byte-identity evidence is invalid.")
    return result


def _scene_path(value: Any, label: str) -> str:
    path = _asset_path(value, label)
    if not path.startswith("Assets/") or PurePosixPath(path).suffix.lower() != ".unity":
        raise SceneAssetDuplicateError(f"{label} must be a saved Assets scene path.")
    return path


def _asset_folder(value: Any, label: str) -> str:
    path = _asset_path(value, label)
    if path != "Assets" and not path.startswith("Assets/"):
        raise SceneAssetDuplicateError(f"{label} must be below Assets.")
    return path


def _asset_path(value: Any, label: str) -> str:
    path = str(value or "")
    if (
        not path
        or path != path.strip()
        or "\\" in path
        or path.startswith("/")
        or path.endswith("/")
        or "//" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(character) < 32 for character in path)
    ):
        raise SceneAssetDuplicateError(f"{label} is not a canonical Unity asset path.")
    return path


def _project_path(value: Any) -> str:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise SceneAssetDuplicateError("projectPath must be an absolute Unity project path.")
    return str(path)


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SceneAssetDuplicateError(f"{label} must be an object.")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value or "").strip().lower()
    if pattern.fullmatch(text) is None:
        raise SceneAssetDuplicateError(f"{label} is invalid.")
    return text


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SceneAssetDuplicateError(f"{label} is invalid.")
    return value


def _int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise SceneAssetDuplicateError(f"{label} is invalid.")
    return value
