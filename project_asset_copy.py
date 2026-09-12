from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


TOOL_NAME = "vrc_duplicate_project_asset"
RESULT_SCHEMA = "vrcforge.project_asset_copy.v2"
APPROVAL_SCHEMA = "vrcforge.project_asset_copy_approval.v1"
OPERATION = "duplicate_project_asset"
GENERATED_ROOT = "Assets/VRCForgeGenerated"
ANCHOR_ROOT = "Assets"
_LEGACY_GENERATED_ROOT = "Assets/VRCForge/Generated"
PREVIEW_DIGEST_SCHEMA = "vrcforge.project_asset_copy_preview.v2"

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_EXTENSIONS = {".controller", ".asset", ".anim", ".overridecontroller", ".mat"}
_GENERATED_SOURCE_TYPES = {
    ".mat": "UnityEngine.Material",
    ".anim": "UnityEngine.AnimationClip",
    ".controller": "UnityEditor.Animations.AnimatorController",
    ".overridecontroller": "UnityEngine.AnimatorOverrideController",
}
_REQUEST_KEYS = ("sourceAssetPath", "destinationAssetPath", "copies")


class ProjectAssetCopyError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    if "copies" in wrapper:
        _batch_rows(wrapper)
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
    if "copies" in provided:
        _batch_rows(provided)
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
    if "copies" in nested:
        return _bind_batch(wrapper, nested, payload)
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
        "expectedDestinationParentFolderGuid": target["parentFolderGuid"],
        "expectedDestinationParentFolderIdentity": target["parentFolderIdentity"],
        "expectedDestinationAbsent": True,
        "expectedPreviewDigest": preview_digest,
    }
    if target.get("folderCreation"):
        canonical_arguments["expectedCreatedFolders"] = target["folderCreation"]["paths"]
    # Caller locks are constraints, not hints. A fresh preview may fill omitted
    # locks, but must never silently supersede explicitly supplied evidence.
    for envelope in (wrapper, nested, wrapper.get("params")):
        if not isinstance(envelope, dict):
            continue
        for key, value in envelope.items():
            if not isinstance(key, str) or not key.startswith("expected"):
                continue
            if key not in canonical_arguments or json.dumps(value, sort_keys=True) != json.dumps(canonical_arguments[key], sort_keys=True):
                raise ProjectAssetCopyError(f"Explicit project asset copy precondition {key} differs from the authoritative preview.")
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
        "mutationCount": 1 + (len(target["folderCreation"]["paths"]) if target.get("folderCreation") else (0 if target["generatedRootExists"] else 1)),
        "createNew": True,
        "overwrite": False,
        "rollbackRequired": True,
        "previewDigest": preview_digest,
    }
    return canonical, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    expected = _dict(arguments, "project asset copy apply arguments")
    if "copies" in expected:
        return _validate_batch_apply(expected, payload)
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
    planned_folders = expected.get("expectedCreatedFolders")
    if planned_folders is not None and target.get("createdFolders") != planned_folders:
        raise ProjectAssetCopyError("Project asset copy created folders differ from the approved plan.")
    expected_mutations = 1 + (len(planned_folders) if planned_folders is not None else (1 if created_root else 0))
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
        target.get("parentFolderPath"),
        target.get("parentFolderGuid"),
        target.get("parentFolderIdentity"),
        "destination_absent",
    )
    creation = target.get("folderCreation")
    if creation:
        fields += ("folder_creation", creation.get("ancestorPath"), creation.get("ancestorGuid"), creation.get("ancestorIdentity"), *creation.get("paths", []))
    framed = "".join(f"{len(str(item or ''))}:{str(item or '')}" for item in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _source(value: Any) -> dict[str, Any]:
    source = _dict(value, "project asset copy source")
    path = _source_path(source.get("assetPath"))
    if path.lower().startswith(f"{GENERATED_ROOT}/".lower()) and source.get("mainAssetType") != _GENERATED_SOURCE_TYPES[_extension(path)]:
        raise ProjectAssetCopyError("Generated source asset type does not match its supported native extension.")
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
    asset_path = _destination_path(target.get("assetPath"))
    parent_path = _asset_path(target.get("parentFolderPath"), "target.parentFolderPath")
    if str(PurePosixPath(asset_path).parent) != parent_path:
        raise ProjectAssetCopyError("Project asset copy parent folder does not match the destination.")
    parent_guid = str(target.get("parentFolderGuid") or "")
    parent_identity = str(target.get("parentFolderIdentity") or "")
    creation = target.get("folderCreation")
    if creation is not None:
        creation = _dict(creation, "target.folderCreation")
        if parent_path == GENERATED_ROOT or parent_guid or parent_identity:
            raise ProjectAssetCopyError("Folder creation requires an absent classified destination parent.")
        ancestor = _asset_path(creation.get("ancestorPath"), "folderCreation.ancestorPath")
        if ancestor != ANCHOR_ROOT and ancestor != GENERATED_ROOT and not ancestor.startswith(GENERATED_ROOT + "/"):
            raise ProjectAssetCopyError("Folder creation ancestor escaped the generated root.")
        paths = creation.get("paths")
        expected_paths = []
        cursor = parent_path
        while cursor != ancestor:
            if cursor != GENERATED_ROOT and not cursor.startswith(GENERATED_ROOT + "/"):
                raise ProjectAssetCopyError("Folder creation ancestor is not above the destination parent.")
            expected_paths.insert(0, cursor)
            cursor = str(PurePosixPath(cursor).parent)
        if not expected_paths or paths != expected_paths:
            raise ProjectAssetCopyError("Folder creation paths are not the exact ordered parent chain.")
        ancestor_guid = _hex(creation.get("ancestorGuid"), "folderCreation.ancestorGuid", _HEX_32)
        ancestor_identity = _hex(creation.get("ancestorIdentity"), "folderCreation.ancestorIdentity", _HEX_64)
        if exists and (ancestor == ANCHOR_ROOT or GENERATED_ROOT in paths):
            raise ProjectAssetCopyError("Folder creation includes the existing generated root.")
        if not exists and ancestor != ANCHOR_ROOT:
            raise ProjectAssetCopyError("Missing generated root requires the Assets anchor.")
        if ancestor == GENERATED_ROOT and (ancestor_guid != root_guid or ancestor_identity != root_identity):
            raise ProjectAssetCopyError("Folder creation ancestor identity differs from generated root.")
        if ancestor == ANCHOR_ROOT and (ancestor_guid != target.get("anchorFolderGuid") or ancestor_identity != target.get("anchorFolderIdentity")):
            raise ProjectAssetCopyError("Folder creation ancestor identity differs from Assets anchor.")
        creation = {"ancestorPath": ancestor, "ancestorGuid": ancestor_guid, "ancestorIdentity": ancestor_identity, "paths": paths}
    elif exists:
        parent_guid = _hex(parent_guid, "target.parentFolderGuid", _HEX_32)
        parent_identity = _hex(parent_identity, "target.parentFolderIdentity", _HEX_64)
        if parent_path == GENERATED_ROOT and (parent_guid != root_guid or parent_identity != root_identity):
            raise ProjectAssetCopyError("The destination parent identity differs from the generated root.")
    elif parent_path != GENERATED_ROOT or parent_guid or parent_identity:
        raise ProjectAssetCopyError("A classified destination parent must already exist.")
    if target.get("assetExists") is not False or target.get("metaExists") is not False or target.get("createNew") is not True:
        raise ProjectAssetCopyError("Project asset copy destination is not create-new.")
    return {
        "assetPath": asset_path,
        "generatedRootPath": GENERATED_ROOT,
        "generatedRootExists": exists,
        "generatedRootGuid": root_guid,
        "generatedRootIdentity": root_identity,
        "anchorFolderPath": ANCHOR_ROOT,
        "anchorFolderGuid": _hex(target.get("anchorFolderGuid"), "target.anchorFolderGuid", _HEX_32),
        "anchorFolderIdentity": _hex(target.get("anchorFolderIdentity"), "target.anchorFolderIdentity", _HEX_64),
        "parentFolderPath": parent_path,
        "parentFolderGuid": parent_guid,
        "parentFolderIdentity": parent_identity,
        **({"folderCreation": creation} if creation is not None else {}),
        "assetExists": False,
        "metaExists": False,
        "createNew": True,
    }


def _source_path(value: Any) -> str:
    path = _asset_path(value, "sourceAssetPath")
    if not path.startswith("Assets/"):
        raise ProjectAssetCopyError("Source must be an existing Assets authoring asset.")
    extension = _extension(path)
    legacy_prefix = f"{_LEGACY_GENERATED_ROOT}/"
    legacy_generated = path.startswith(legacy_prefix)
    if legacy_generated:
        if extension != ".mat" or "/" in path[len(legacy_prefix) :]:
            raise ProjectAssetCopyError("Only an existing root-level legacy generated material may be copied.")
    if path.lower().startswith(f"{GENERATED_ROOT}/".lower()) and extension not in _GENERATED_SOURCE_TYPES:
        raise ProjectAssetCopyError("Only native material, animation, controller, and override-controller generated assets may be copied.")
    return path


def _destination_path(value: Any) -> str:
    path = _asset_path(value, "destinationAssetPath")
    prefix = f"{GENERATED_ROOT}/"
    leaf = path[len(prefix) :] if path.startswith(prefix) else ""
    if not leaf or any(part.startswith(".") for part in leaf.split("/")):
        raise ProjectAssetCopyError("Destination must be below Assets/VRCForgeGenerated without reserved names.")
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


def _batch_rows(arguments: dict[str, Any]) -> list[dict[str, str]]:
    if any(key in arguments for key in ("sourceAssetPath", "destinationAssetPath")) or arguments.get("overwrite") not in (None, False):
        raise ProjectAssetCopyError("copies and single-copy fields/overwrite are mutually exclusive.")
    rows = arguments.get("copies")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ProjectAssetCopyError("copies requires 1..128 rows.")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"sourceAssetPath", "destinationAssetPath"}:
            raise ProjectAssetCopyError("Each copy requires only sourceAssetPath and destinationAssetPath.")
        source, target = _source_path(row["sourceAssetPath"]), _destination_path(row["destinationAssetPath"])
        if _extension(source) != _extension(target):
            raise ProjectAssetCopyError("Copy source/destination extensions differ.")
        result.append({"sourceAssetPath": source, "destinationAssetPath": target})
    sources = {row["sourceAssetPath"].casefold() for row in result}
    destinations = [row["destinationAssetPath"].casefold() for row in result]
    if len(set(destinations)) != len(destinations) or sources.intersection(destinations):
        raise ProjectAssetCopyError("Batch destination collision or source-target intersection.")
    _batch_size(arguments)
    return result


def _batch_size(value: Any) -> None:
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 512 * 1024:
        raise ProjectAssetCopyError("Sealed copy batch exceeds 512 KiB.")


def _batch_digest(previews: list[dict[str, Any]]) -> str:
    return hashlib.sha256(("vrcforge.project_asset_copy_batch.v1:" + "".join(_hex(row.get("previewDigest"), "row previewDigest", _HEX_64) for row in previews)).encode("utf-8")).hexdigest()


def _bind_batch(wrapper: dict[str, Any], nested: dict[str, Any], payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _batch_rows(nested)
    result = _dict(payload, "batch copy preview")
    project = _project_path(wrapper.get("projectPath"))
    for key, value in {"schema": RESULT_SCHEMA, "operation": OPERATION, "batch": True, "ok": True, "preview": True, "verified": True, "changed": False, "saved": False, "cleanupRequired": False, "mutationCount": 0}.items():
        if type(result.get(key)) is not type(value) or result[key] != value:
            raise ProjectAssetCopyError(f"Batch preview {key} is invalid.")
    previews = result.get("copies")
    if not isinstance(previews, list) or len(previews) != len(rows):
        raise ProjectAssetCopyError("Batch preview row count changed.")
    for row, preview in zip(rows, previews):
        _, approval = bind_authoritative_preview({"toolName": TOOL_NAME, "projectPath": project, "arguments": row}, preview)
        if not approval["target"]["generatedRootExists"] or approval["target"].get("folderCreation"):
            raise ProjectAssetCopyError("Every batch destination parent must already exist.")
    digest = _batch_digest(previews)
    if result.get("previewDigest") != digest:
        raise ProjectAssetCopyError("Batch preview digest mismatch.")
    arguments = {"copies": rows, "preview": False, "overwrite": False, "expectedProjectPath": project, "expectedPreviewDigest": digest, "expectedCopies": deepcopy(previews)}
    for envelope in (wrapper, nested, wrapper.get("params")):
        if isinstance(envelope, dict):
            for key, value in envelope.items():
                if key.startswith("expected") and (key not in arguments or json.dumps(value, sort_keys=True) != json.dumps(arguments[key], sort_keys=True)):
                    raise ProjectAssetCopyError(f"Explicit batch precondition {key} differs from fresh preview.")
    _batch_size(arguments)
    _batch_size(result)
    canonical = deepcopy(wrapper); canonical.pop("params", None); canonical["arguments"] = arguments
    return canonical, {"schema": APPROVAL_SCHEMA, "toolName": TOOL_NAME, "operation": OPERATION, "batch": True, "copies": deepcopy(previews), "previewDigest": digest, "mutationCount": len(rows), "overwrite": False, "createNew": True, "rollbackRequired": True}


def _validate_batch_apply(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    rows = _batch_rows(arguments)
    previews = arguments.get("expectedCopies")
    if not isinstance(previews, list) or len(previews) != len(rows) or _batch_digest(previews) != arguments.get("expectedPreviewDigest"):
        raise ProjectAssetCopyError("Batch apply lacks sealed previews.")
    result = _dict(payload, "batch copy apply")
    for key, value in {"schema": RESULT_SCHEMA, "operation": OPERATION, "batch": True, "ok": True, "preview": False, "verified": True, "changed": True, "saved": True, "cleanupRequired": False, "mutationCount": len(rows), "previewDigest": arguments["expectedPreviewDigest"]}.items():
        if type(result.get(key)) is not type(value) or result[key] != value:
            raise ProjectAssetCopyError(f"Batch apply {key} is invalid.")
    results = result.get("copies")
    if not isinstance(results, list) or len(results) != len(rows):
        raise ProjectAssetCopyError("Batch apply row count changed.")
    guids = {preview["source"]["guid"] for preview in previews}
    for row, preview, actual in zip(rows, previews, results):
        canonical, approval = bind_authoritative_preview({"toolName": TOOL_NAME, "projectPath": arguments.get("expectedProjectPath"), "arguments": row}, preview)
        if not approval["target"]["generatedRootExists"] or approval["target"].get("folderCreation"):
            raise ProjectAssetCopyError("Batch parent must already exist.")
        verified = validate_apply_result(canonical["arguments"], actual)
        guid = verified["target"]["guid"]
        if guid in guids: raise ProjectAssetCopyError("Batch destination GUID is not independent.")
        guids.add(guid)
    _batch_size(result)
    return deepcopy(result)
