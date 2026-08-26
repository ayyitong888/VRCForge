from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


TOOL_NAME = "vrc_duplicate_project_asset"
RESULT_SCHEMA = "vrcforge.project_asset_copy.v2"
APPROVAL_SCHEMA = "vrcforge.project_asset_copy_approval.v1"
OPERATION = "duplicate_project_asset"
GENERATED_ROOT = "Assets/VRCForge/Generated"
ANCHOR_ROOT = "Assets/VRCForge"
PREVIEW_DIGEST_SCHEMA = "vrcforge.project_asset_copy_preview.v2"

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_EXTENSIONS = {".controller", ".asset", ".anim", ".overridecontroller", ".mat"}
_REQUEST_KEYS = ("sourceAssetPath", "destinationAssetPath")


class ProjectAssetCopyError(ValueError):
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
    preview["preview"] = True
    preview["overwrite"] = False
    return preview


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wrapper = _dict(wrapper_arguments, "project asset copy wrapper")
    if str(wrapper.get("toolName") or wrapper.get("tool_name") or "").strip() != TOOL_NAME:
        raise ProjectAssetCopyError("Project asset copy tool binding is invalid.")
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    nested = _dict(nested, "project asset copy arguments")
    project_path = _project_path(wrapper.get("projectPath"))
    result = _dict(payload, "project asset copy preview")
    if result.get("schema") != RESULT_SCHEMA or result.get("operation") != OPERATION:
        raise ProjectAssetCopyError("Project asset copy preview schema is invalid.")
    for key, expected in (
        ("ok", True),
        ("preview", True),
        ("verified", True),
        ("changed", False),
        ("saved", False),
        ("cleanupRequired", False),
    ):
        if result.get(key) is not expected:
            raise ProjectAssetCopyError(f"Project asset copy preview {key} is invalid.")
    if _int(result.get("mutationCount"), "mutationCount", 0, 0) != 0:
        raise ProjectAssetCopyError("Project asset copy preview mutationCount is invalid.")

    source = _source(result.get("source"))
    target = _target(result.get("target"))
    requested_source = _source_path(nested.get("sourceAssetPath"))
    requested_destination = _destination_path(nested.get("destinationAssetPath"))
    if source["assetPath"] != requested_source or target["assetPath"] != requested_destination:
        raise ProjectAssetCopyError("The preview changed the requested asset paths.")
    if PurePosixPath(requested_source).suffix.lower() != PurePosixPath(requested_destination).suffix.lower():
        raise ProjectAssetCopyError("Source and destination extensions must match.")

    preview_digest = _hex(result.get("previewDigest"), "previewDigest", _HEX_64)
    if compute_preview_digest(result) != preview_digest:
        raise ProjectAssetCopyError("Project asset copy preview digest is invalid.")

    canonical_arguments = {
        "sourceAssetPath": source["assetPath"],
        "destinationAssetPath": target["assetPath"],
        "preview": False,
        "overwrite": False,
        "expectedProjectPath": project_path,
        "expectedSourceGuid": source["guid"],
        "expectedSourceFileDigest": source["fileDigest"],
        "expectedSourceFileIdentity": source["fileIdentity"],
        "expectedSourceMetaDigest": source["metaDigest"],
        "expectedSourceMetaIdentity": source["metaIdentity"],
        "expectedSourceMainAssetType": source["mainAssetType"],
        "expectedSourceObjectLayoutDigest": source["objectLayoutDigest"],
        "expectedGeneratedRootExists": target["generatedRootExists"],
        "expectedGeneratedRootGuid": target["generatedRootGuid"],
        "expectedGeneratedRootIdentity": target["generatedRootIdentity"],
        "expectedAnchorFolderGuid": target["anchorFolderGuid"],
        "expectedAnchorFolderIdentity": target["anchorFolderIdentity"],
        "expectedDestinationAbsent": True,
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
        "mutationCount": 1 + (0 if target["generatedRootExists"] else 1),
        "createNew": True,
        "overwrite": False,
        "rollbackRequired": True,
        "previewDigest": preview_digest,
    }
    return canonical, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    expected = _dict(arguments, "project asset copy apply arguments")
    result = _dict(payload, "project asset copy apply result")
    if result.get("schema") != RESULT_SCHEMA or result.get("operation") != OPERATION:
        raise ProjectAssetCopyError("Project asset copy apply schema is invalid.")
    for key, value in (
        ("ok", True),
        ("preview", False),
        ("verified", True),
        ("changed", True),
        ("saved", True),
        ("cleanupRequired", False),
    ):
        if result.get(key) is not value:
            raise ProjectAssetCopyError(f"Project asset copy apply {key} is invalid.")
    source = _source(result.get("source"))
    target = _dict(result.get("target"), "project asset copy apply target")
    if source["assetPath"] != expected.get("sourceAssetPath"):
        raise ProjectAssetCopyError("Project asset copy apply source path changed.")
    if source["guid"] != expected.get("expectedSourceGuid"):
        raise ProjectAssetCopyError("Project asset copy apply source GUID changed.")
    for key, expected_key in (
        ("fileDigest", "expectedSourceFileDigest"),
        ("fileIdentity", "expectedSourceFileIdentity"),
        ("metaDigest", "expectedSourceMetaDigest"),
        ("metaIdentity", "expectedSourceMetaIdentity"),
        ("mainAssetType", "expectedSourceMainAssetType"),
        ("objectLayoutDigest", "expectedSourceObjectLayoutDigest"),
    ):
        if source[key] != expected.get(expected_key):
            raise ProjectAssetCopyError(f"Project asset copy apply source {key} changed.")
    if source.get("unchanged") is not True:
        raise ProjectAssetCopyError("Project asset copy apply did not verify the source unchanged.")

    target_path = _destination_path(target.get("assetPath"))
    target_guid = _hex(target.get("guid"), "target.guid", _HEX_32)
    _hex(target.get("fileDigest"), "target.fileDigest", _HEX_64)
    _hex(target.get("fileIdentity"), "target.fileIdentity", _HEX_64)
    _hex(target.get("metaDigest"), "target.metaDigest", _HEX_64)
    _hex(target.get("metaIdentity"), "target.metaIdentity", _HEX_64)
    if target_path != expected.get("destinationAssetPath"):
        raise ProjectAssetCopyError("Project asset copy apply destination path changed.")
    if target_guid == source["guid"]:
        raise ProjectAssetCopyError("Project asset copy apply did not create an independent GUID.")
    if target.get("mainAssetType") != source["mainAssetType"]:
        raise ProjectAssetCopyError("Project asset copy apply main asset type changed.")
    if target.get("generatedRootPath") != GENERATED_ROOT:
        raise ProjectAssetCopyError("Project asset copy apply generated root changed.")
    if target.get("objectLayoutDigest") != source["objectLayoutDigest"]:
        raise ProjectAssetCopyError("Project asset copy apply Unity object layout changed.")
    if not isinstance(target.get("bytesIdenticalToSource"), bool):
        raise ProjectAssetCopyError("Project asset copy apply byte-identity evidence is invalid.")
    if target.get("createNew") is not True or target.get("readbackVerified") is not True:
        raise ProjectAssetCopyError("Project asset copy apply readback is invalid.")
    created_root = target.get("generatedRootCreated")
    if created_root is not (not bool(expected.get("expectedGeneratedRootExists"))):
        raise ProjectAssetCopyError("Project asset copy apply generated-root result is invalid.")
    expected_mutations = 1 + (1 if created_root else 0)
    if _int(result.get("mutationCount"), "mutationCount", expected_mutations, expected_mutations) != expected_mutations:
        raise ProjectAssetCopyError("Project asset copy apply mutationCount is invalid.")
    if result.get("previewDigest") != expected.get("expectedPreviewDigest"):
        raise ProjectAssetCopyError("Project asset copy apply preview digest changed.")
    return deepcopy(result)


def compute_preview_digest(payload: dict[str, Any]) -> str:
    value = _dict(payload, "project asset copy preview")
    source = _dict(value.get("source"), "project asset copy preview source")
    target = _dict(value.get("target"), "project asset copy preview target")
    fields = (
        PREVIEW_DIGEST_SCHEMA,
        value.get("schema"),
        value.get("operation"),
        source.get("assetPath"),
        source.get("guid"),
        source.get("fileDigest"),
        source.get("fileIdentity"),
        source.get("metaDigest"),
        source.get("metaIdentity"),
        source.get("mainAssetType"),
        source.get("objectLayoutDigest"),
        target.get("assetPath"),
        target.get("generatedRootPath"),
        "true" if target.get("generatedRootExists") is True else "false",
        target.get("generatedRootGuid"),
        target.get("generatedRootIdentity"),
        target.get("anchorFolderPath"),
        target.get("anchorFolderGuid"),
        target.get("anchorFolderIdentity"),
        "destination_absent",
    )
    framed = "".join(f"{len(str(item or ''))}:{str(item or '')}" for item in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _source(value: Any) -> dict[str, Any]:
    source = _dict(value, "project asset copy source")
    path = _source_path(source.get("assetPath"))
    return {
        "assetPath": path,
        "guid": _hex(source.get("guid"), "source.guid", _HEX_32),
        "fileDigest": _hex(source.get("fileDigest"), "source.fileDigest", _HEX_64),
        "fileIdentity": _hex(source.get("fileIdentity"), "source.fileIdentity", _HEX_64),
        "metaDigest": _hex(source.get("metaDigest"), "source.metaDigest", _HEX_64),
        "metaIdentity": _hex(source.get("metaIdentity"), "source.metaIdentity", _HEX_64),
        "mainAssetType": _text(source.get("mainAssetType"), "source.mainAssetType", 512),
        "objectLayoutDigest": _hex(
            source.get("objectLayoutDigest"),
            "source.objectLayoutDigest",
            _HEX_64,
        ),
        **({"unchanged": source.get("unchanged")} if "unchanged" in source else {}),
    }


def _target(value: Any) -> dict[str, Any]:
    target = _dict(value, "project asset copy target")
    if target.get("generatedRootPath") != GENERATED_ROOT or target.get("anchorFolderPath") != ANCHOR_ROOT:
        raise ProjectAssetCopyError("Project asset copy target root is invalid.")
    exists = target.get("generatedRootExists")
    if not isinstance(exists, bool):
        raise ProjectAssetCopyError("Project asset copy generatedRootExists is invalid.")
    root_guid = str(target.get("generatedRootGuid") or "")
    root_identity = str(target.get("generatedRootIdentity") or "")
    if exists:
        root_guid = _hex(root_guid, "target.generatedRootGuid", _HEX_32)
        root_identity = _hex(root_identity, "target.generatedRootIdentity", _HEX_64)
    elif root_guid or root_identity:
        raise ProjectAssetCopyError("Absent generated root returned an identity.")
    if target.get("assetExists") is not False or target.get("metaExists") is not False or target.get("createNew") is not True:
        raise ProjectAssetCopyError("Project asset copy destination is not create-new.")
    return {
        "assetPath": _destination_path(target.get("assetPath")),
        "generatedRootPath": GENERATED_ROOT,
        "generatedRootExists": exists,
        "generatedRootGuid": root_guid,
        "generatedRootIdentity": root_identity,
        "anchorFolderPath": ANCHOR_ROOT,
        "anchorFolderGuid": _hex(target.get("anchorFolderGuid"), "target.anchorFolderGuid", _HEX_32),
        "anchorFolderIdentity": _hex(target.get("anchorFolderIdentity"), "target.anchorFolderIdentity", _HEX_64),
        "assetExists": False,
        "metaExists": False,
        "createNew": True,
    }


def _source_path(value: Any) -> str:
    path = _asset_path(value, "sourceAssetPath")
    if not path.startswith("Assets/"):
        raise ProjectAssetCopyError("Source must be an existing non-generated Assets authoring asset.")
    extension = _extension(path)
    generated_prefix = f"{GENERATED_ROOT}/"
    if path.startswith(generated_prefix):
        generated_leaf = path[len(generated_prefix) :]
        if extension != ".mat" or "/" in generated_leaf:
            raise ProjectAssetCopyError(
                "Only an existing generated material may be copied from the generated root."
            )
    return path


def _destination_path(value: Any) -> str:
    path = _asset_path(value, "destinationAssetPath")
    prefix = f"{GENERATED_ROOT}/"
    leaf = path[len(prefix) :] if path.startswith(prefix) else ""
    if not leaf or "/" in leaf or leaf.startswith("."):
        raise ProjectAssetCopyError("Destination must be a direct child of Assets/VRCForge/Generated.")
    _extension(path)
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
        or any(ord(ch) < 32 for ch in path)
    ):
        raise ProjectAssetCopyError(f"{label} is not a canonical Unity asset path.")
    return path


def _extension(path: str) -> str:
    extension = PurePosixPath(path).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise ProjectAssetCopyError("Unsupported Unity authoring asset extension.")
    return extension


def _project_path(value: Any) -> str:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise ProjectAssetCopyError("projectPath must be an absolute Unity project path.")
    return str(path)


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectAssetCopyError(f"{label} must be an object.")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value or "").strip().lower()
    if pattern.fullmatch(text) is None:
        raise ProjectAssetCopyError(f"{label} is invalid.")
    return text


def _text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(ord(ch) < 32 for ch in text):
        raise ProjectAssetCopyError(f"{label} is invalid.")
    return text


def _int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ProjectAssetCopyError(f"{label} is invalid.")
    return value
