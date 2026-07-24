from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


RESULT_SCHEMA = "vrcforge.scene_object_copy.v1"
APPROVAL_SCHEMA = "vrcforge.scene_object_copy_approval.v1"
DUPLICATE_TOOL_NAME = "vrc_duplicate_scene_object"
PREFAB_TOOL_NAME = "vrc_save_scene_object_as_prefab"

_DUPLICATE_OPERATION = "duplicate_scene_object"
_PREFAB_OPERATION = "save_scene_object_as_prefab"
_SUPPORTED_TOOLS = {DUPLICATE_TOOL_NAME, PREFAB_TOOL_NAME}
_TOOL_OPERATIONS = {
    DUPLICATE_TOOL_NAME: _DUPLICATE_OPERATION,
    PREFAB_TOOL_NAME: _PREFAB_OPERATION,
}
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_WINDOWS_ABSOLUTE_PATTERN = re.compile(r"^[A-Za-z]:[/\\]")
_GENERATED_ROOT = "Assets/VRCForge/Generated"
_COMMON_REQUEST_KEYS = (
    "sourceScenePath",
    "sourceObjectPath",
)
_DUPLICATE_REQUEST_KEYS = (
    *_COMMON_REQUEST_KEYS,
    "targetParentScenePath",
    "targetParentPath",
    "targetName",
    "preserveWorldTransform",
)
_PREFAB_REQUEST_KEYS = (
    *_COMMON_REQUEST_KEYS,
    "prefabAssetPath",
)


class SceneObjectCopyError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any], tool_name: str) -> dict[str, Any]:
    _require_supported_tool(tool_name)
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    request_keys = _request_keys(tool_name)
    if not isinstance(nested, dict):
        nested = {key: wrapper[key] for key in request_keys if key in wrapper}
    for key in (*_DUPLICATE_REQUEST_KEYS, *_PREFAB_REQUEST_KEYS):
        wrapper.pop(key, None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = tool_name
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    _require_supported_tool(tool_name)
    provided = arguments if isinstance(arguments, dict) else {}
    preview_arguments = {
        key: deepcopy(provided[key])
        for key in _request_keys(tool_name)
        if key in provided
    }
    preview_arguments["preview"] = True
    preview_arguments["saveScene"] = False
    preview_arguments["saveAssets"] = False
    preview_arguments["overwrite"] = False
    return preview_arguments


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(wrapper_arguments, dict):
        raise SceneObjectCopyError("Scene object copy wrapper is required.")
    tool_name = str(wrapper_arguments.get("toolName") or wrapper_arguments.get("tool_name") or "").strip()
    _require_supported_tool(tool_name)
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper_arguments.get("params")
    if not isinstance(nested, dict):
        raise SceneObjectCopyError("Scene object copy arguments are required.")
    project_path = _project_path(wrapper_arguments.get("projectPath"))

    result = _require_dict(payload, "preview result")
    if result.get("schema") != RESULT_SCHEMA:
        raise SceneObjectCopyError("Scene object copy preview schema is invalid.")
    operation = _TOOL_OPERATIONS[tool_name]
    if result.get("operation") != operation:
        raise SceneObjectCopyError("Scene object copy preview operation is invalid.")
    for key, expected in (
        ("ok", True),
        ("preview", True),
        ("verified", True),
        ("changed", False),
        ("saved", False),
    ):
        if result.get(key) is not expected:
            raise SceneObjectCopyError(f"Scene object copy preview {key} is invalid.")
    if _bounded_int(result.get("mutationCount"), label="mutationCount", minimum=0, maximum=0) != 0:
        raise SceneObjectCopyError("Scene object copy preview mutationCount is invalid.")

    source = _canonical_source(result.get("source"))
    preview_digest = _lower_hex(
        result.get("previewDigest"),
        label="previewDigest",
        pattern=_DIGEST_PATTERN,
    )
    if compute_preview_digest(result) != preview_digest:
        raise SceneObjectCopyError("Scene object copy preview digest is invalid.")

    requested_source_scene = _scene_asset_path(
        nested.get("sourceScenePath"),
        label="sourceScenePath",
    )
    requested_source_object = _scene_object_path(
        nested.get("sourceObjectPath"),
        label="sourceObjectPath",
    )
    if (
        source["scenePath"] != requested_source_scene
        or source["objectPath"] != requested_source_object
    ):
        raise SceneObjectCopyError("The preview changed the requested source selector.")

    if tool_name == DUPLICATE_TOOL_NAME:
        return _bind_duplicate(
            wrapper_arguments,
            nested,
            project_path,
            source,
            result,
            preview_digest,
        )
    return _bind_prefab(
        wrapper_arguments,
        nested,
        project_path,
        source,
        result,
        preview_digest,
    )


def compute_preview_digest(payload: dict[str, Any]) -> str:
    value = payload if isinstance(payload, dict) else {}
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    target = value.get("target") if isinstance(value.get("target"), dict) else {}
    fields: list[Any] = [
        value.get("schema"),
        value.get("operation"),
        value.get("ok"),
        value.get("preview"),
        value.get("verified"),
        value.get("changed"),
        value.get("saved"),
        value.get("mutationCount"),
        source.get("scenePath"),
        source.get("sceneGuid"),
        source.get("sceneHandle"),
        source.get("objectPath"),
        source.get("objectId"),
        source.get("hierarchyDigest"),
        source.get("sceneFileDigest"),
        source.get("sceneFileIdentity"),
        source.get("sceneMetaDigest"),
        source.get("sceneMetaIdentity"),
        source.get("pathUnique"),
    ]
    if value.get("operation") == _DUPLICATE_OPERATION:
        fields.extend(
            [
                target.get("scenePath"),
                target.get("sceneGuid"),
                target.get("sceneHandle"),
                target.get("parentPath"),
                target.get("parentObjectId"),
                target.get("parentHierarchyDigest"),
                target.get("sceneFileDigest"),
                target.get("sceneFileIdentity"),
                target.get("sceneMetaDigest"),
                target.get("sceneMetaIdentity"),
                target.get("objectPath"),
                target.get("name"),
                target.get("parentPathUnique"),
                target.get("nameCollision"),
                target.get("sameDestination"),
                target.get("targetWithinSource"),
                value.get("preserveWorldTransform"),
            ]
        )
    elif value.get("operation") == _PREFAB_OPERATION:
        fields.extend(
            [
                target.get("assetPath"),
                target.get("parentFolderPath"),
                target.get("parentFolderGuid"),
                target.get("parentFolderIdentity"),
                target.get("stagingRootPath"),
                target.get("stagingRootGuid"),
                target.get("stagingRootIdentity"),
                target.get("stagingPolicy"),
                target.get("assetExists"),
                target.get("metaExists"),
                target.get("createNew"),
            ]
        )
    framed = "".join(_digest_field(item) for item in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _bind_duplicate(
    wrapper: dict[str, Any],
    nested: dict[str, Any],
    project_path: str,
    source: dict[str, Any],
    result: dict[str, Any],
    preview_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_target_scene = _scene_asset_path(
        nested.get("targetParentScenePath"),
        label="targetParentScenePath",
    )
    requested_target_parent = _scene_object_path(
        nested.get("targetParentPath"),
        label="targetParentPath",
    )
    requested_target_name = _object_name(nested.get("targetName"), label="targetName")
    preserve_world_transform = _strict_bool(
        nested.get("preserveWorldTransform", False),
        label="preserveWorldTransform",
    )
    if result.get("preserveWorldTransform") is not preserve_world_transform:
        raise SceneObjectCopyError("The preview changed the requested transform policy.")

    target = _canonical_duplicate_target(result.get("target"))
    destination = requested_target_parent + "/" + requested_target_name
    if (
        target["scenePath"] != requested_target_scene
        or target["parentPath"] != requested_target_parent
        or target["name"] != requested_target_name
        or target["objectPath"] != destination
    ):
        raise SceneObjectCopyError("The preview changed the requested duplicate destination.")
    if source["scenePath"] == target["scenePath"]:
        if source["objectPath"] == destination:
            raise SceneObjectCopyError("The requested duplicate destination is the source path.")
        if target["parentPath"] == source["objectPath"] or target["parentPath"].startswith(
            source["objectPath"] + "/"
        ):
            raise SceneObjectCopyError("The requested target parent is within the source hierarchy.")

    canonical_arguments = {
        "sourceScenePath": source["scenePath"],
        "sourceObjectPath": source["objectPath"],
        "targetParentScenePath": target["scenePath"],
        "targetParentPath": target["parentPath"],
        "targetName": target["name"],
        "preserveWorldTransform": preserve_world_transform,
        "preview": False,
        "saveScene": True,
        "overwrite": False,
        "expectedProjectPath": project_path,
        "expectedSourceSceneGuid": source["sceneGuid"],
        "expectedSourceSceneHandle": source["sceneHandle"],
        "expectedSourceObjectId": source["objectId"],
        "expectedSourceHierarchyDigest": source["hierarchyDigest"],
        "expectedSourceSceneFileDigest": source["sceneFileDigest"],
        "expectedSourceSceneFileIdentity": source["sceneFileIdentity"],
        "expectedSourceSceneMetaDigest": source["sceneMetaDigest"],
        "expectedSourceSceneMetaIdentity": source["sceneMetaIdentity"],
        "expectedTargetSceneGuid": target["sceneGuid"],
        "expectedTargetSceneHandle": target["sceneHandle"],
        "expectedTargetParentObjectId": target["parentObjectId"],
        "expectedTargetParentHierarchyDigest": target["parentHierarchyDigest"],
        "expectedTargetSceneFileDigest": target["sceneFileDigest"],
        "expectedTargetSceneFileIdentity": target["sceneFileIdentity"],
        "expectedTargetSceneMetaDigest": target["sceneMetaDigest"],
        "expectedTargetSceneMetaIdentity": target["sceneMetaIdentity"],
        "expectedDestinationPath": target["objectPath"],
        "expectedPreviewDigest": preview_digest,
    }
    canonical = _canonical_wrapper(wrapper, DUPLICATE_TOOL_NAME, canonical_arguments)
    approval = {
        "schema": APPROVAL_SCHEMA,
        "toolName": DUPLICATE_TOOL_NAME,
        "operation": _DUPLICATE_OPERATION,
        "source": source,
        "target": target,
        "preserveWorldTransform": preserve_world_transform,
        "mutationCount": 1,
        "createNew": True,
        "rollbackRequired": True,
        "previewDigest": preview_digest,
    }
    return canonical, approval


def _bind_prefab(
    wrapper: dict[str, Any],
    nested: dict[str, Any],
    project_path: str,
    source: dict[str, Any],
    result: dict[str, Any],
    preview_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_asset_path = _generated_prefab_path(
        nested.get("prefabAssetPath"),
        label="prefabAssetPath",
    )
    target = _canonical_prefab_target(result.get("target"))
    if target["assetPath"] != requested_asset_path:
        raise SceneObjectCopyError("The preview changed the requested prefab destination.")

    canonical_arguments = {
        "sourceScenePath": source["scenePath"],
        "sourceObjectPath": source["objectPath"],
        "prefabAssetPath": target["assetPath"],
        "preview": False,
        "saveAssets": True,
        "overwrite": False,
        "expectedProjectPath": project_path,
        "expectedSourceSceneGuid": source["sceneGuid"],
        "expectedSourceSceneHandle": source["sceneHandle"],
        "expectedSourceObjectId": source["objectId"],
        "expectedSourceHierarchyDigest": source["hierarchyDigest"],
        "expectedSourceSceneFileDigest": source["sceneFileDigest"],
        "expectedSourceSceneFileIdentity": source["sceneFileIdentity"],
        "expectedSourceSceneMetaDigest": source["sceneMetaDigest"],
        "expectedSourceSceneMetaIdentity": source["sceneMetaIdentity"],
        "expectedPrefabParentFolderGuid": target["parentFolderGuid"],
        "expectedPrefabParentFolderIdentity": target["parentFolderIdentity"],
        "expectedStagingRootGuid": target["stagingRootGuid"],
        "expectedStagingRootIdentity": target["stagingRootIdentity"],
        "expectedStagingPolicy": target["stagingPolicy"],
        "expectedPreviewDigest": preview_digest,
    }
    canonical = _canonical_wrapper(wrapper, PREFAB_TOOL_NAME, canonical_arguments)
    approval = {
        "schema": APPROVAL_SCHEMA,
        "toolName": PREFAB_TOOL_NAME,
        "operation": _PREFAB_OPERATION,
        "source": source,
        "target": target,
        "mutationCount": 1,
        "createNew": True,
        "rollbackRequired": True,
        "previewDigest": preview_digest,
    }
    return canonical, approval


def _canonical_source(value: Any) -> dict[str, Any]:
    source = _require_dict(value, "source")
    if source.get("pathUnique") is not True:
        raise SceneObjectCopyError("Source hierarchy path is not unique.")
    return {
        "scenePath": _scene_asset_path(source.get("scenePath"), label="source.scenePath"),
        "sceneGuid": _nonzero_hex(
            source.get("sceneGuid"),
            label="source.sceneGuid",
            pattern=_GUID_PATTERN,
        ),
        "sceneHandle": _bounded_int(
            source.get("sceneHandle"),
            label="source.sceneHandle",
            minimum=1,
            maximum=2_147_483_647,
        ),
        "objectPath": _scene_object_path(source.get("objectPath"), label="source.objectPath"),
        "objectId": _nonzero_hex(
            source.get("objectId"),
            label="source.objectId",
            pattern=_DIGEST_PATTERN,
        ),
        "hierarchyDigest": _nonzero_hex(
            source.get("hierarchyDigest"),
            label="source.hierarchyDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneFileDigest": _nonzero_hex(
            source.get("sceneFileDigest"),
            label="source.sceneFileDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneFileIdentity": _nonzero_hex(
            source.get("sceneFileIdentity"),
            label="source.sceneFileIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneMetaDigest": _nonzero_hex(
            source.get("sceneMetaDigest"),
            label="source.sceneMetaDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneMetaIdentity": _nonzero_hex(
            source.get("sceneMetaIdentity"),
            label="source.sceneMetaIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "pathUnique": True,
    }


def _canonical_duplicate_target(value: Any) -> dict[str, Any]:
    target = _require_dict(value, "target")
    for key, expected in (
        ("parentPathUnique", True),
        ("nameCollision", False),
        ("sameDestination", False),
        ("targetWithinSource", False),
    ):
        if target.get(key) is not expected:
            raise SceneObjectCopyError(f"Duplicate target {key} is invalid.")
    return {
        "scenePath": _scene_asset_path(target.get("scenePath"), label="target.scenePath"),
        "sceneGuid": _nonzero_hex(
            target.get("sceneGuid"),
            label="target.sceneGuid",
            pattern=_GUID_PATTERN,
        ),
        "sceneHandle": _bounded_int(
            target.get("sceneHandle"),
            label="target.sceneHandle",
            minimum=1,
            maximum=2_147_483_647,
        ),
        "parentPath": _scene_object_path(target.get("parentPath"), label="target.parentPath"),
        "parentObjectId": _nonzero_hex(
            target.get("parentObjectId"),
            label="target.parentObjectId",
            pattern=_DIGEST_PATTERN,
        ),
        "parentHierarchyDigest": _nonzero_hex(
            target.get("parentHierarchyDigest"),
            label="target.parentHierarchyDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneFileDigest": _nonzero_hex(
            target.get("sceneFileDigest"),
            label="target.sceneFileDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneFileIdentity": _nonzero_hex(
            target.get("sceneFileIdentity"),
            label="target.sceneFileIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneMetaDigest": _nonzero_hex(
            target.get("sceneMetaDigest"),
            label="target.sceneMetaDigest",
            pattern=_DIGEST_PATTERN,
        ),
        "sceneMetaIdentity": _nonzero_hex(
            target.get("sceneMetaIdentity"),
            label="target.sceneMetaIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "objectPath": _scene_object_path(target.get("objectPath"), label="target.objectPath"),
        "name": _object_name(target.get("name"), label="target.name"),
        "parentPathUnique": True,
        "nameCollision": False,
        "sameDestination": False,
        "targetWithinSource": False,
    }


def _canonical_prefab_target(value: Any) -> dict[str, Any]:
    target = _require_dict(value, "target")
    for key, expected in (
        ("assetExists", False),
        ("metaExists", False),
        ("createNew", True),
    ):
        if target.get(key) is not expected:
            raise SceneObjectCopyError(f"Prefab target {key} is invalid.")
    asset_path = _generated_prefab_path(target.get("assetPath"), label="target.assetPath")
    parent_folder = _generated_folder_path(
        target.get("parentFolderPath"),
        label="target.parentFolderPath",
    )
    if str(PurePosixPath(asset_path).parent) != parent_folder:
        raise SceneObjectCopyError("Prefab parent folder does not match the destination path.")
    staging_root = _generated_folder_path(
        target.get("stagingRootPath"),
        label="target.stagingRootPath",
    )
    if staging_root != _GENERATED_ROOT:
        raise SceneObjectCopyError("Prefab staging root is invalid.")
    staging_policy = _bounded_text(
        target.get("stagingPolicy"),
        label="target.stagingPolicy",
        max_length=128,
    )
    if staging_policy != "random_create_new_folder_v1":
        raise SceneObjectCopyError("Prefab staging policy is invalid.")
    return {
        "assetPath": asset_path,
        "parentFolderPath": parent_folder,
        "parentFolderGuid": _nonzero_hex(
            target.get("parentFolderGuid"),
            label="target.parentFolderGuid",
            pattern=_GUID_PATTERN,
        ),
        "parentFolderIdentity": _nonzero_hex(
            target.get("parentFolderIdentity"),
            label="target.parentFolderIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "stagingRootPath": staging_root,
        "stagingRootGuid": _nonzero_hex(
            target.get("stagingRootGuid"),
            label="target.stagingRootGuid",
            pattern=_GUID_PATTERN,
        ),
        "stagingRootIdentity": _nonzero_hex(
            target.get("stagingRootIdentity"),
            label="target.stagingRootIdentity",
            pattern=_DIGEST_PATTERN,
        ),
        "stagingPolicy": staging_policy,
        "assetExists": False,
        "metaExists": False,
        "createNew": True,
    }


def _canonical_wrapper(
    original: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    wrapper = {
        key: deepcopy(value)
        for key, value in original.items()
        if key not in {"arguments", "params", "toolName", "tool_name"}
    }
    wrapper["toolName"] = tool_name
    wrapper["arguments"] = arguments
    return wrapper


def _request_keys(tool_name: str) -> tuple[str, ...]:
    return _DUPLICATE_REQUEST_KEYS if tool_name == DUPLICATE_TOOL_NAME else _PREFAB_REQUEST_KEYS


def _require_supported_tool(tool_name: str) -> None:
    if tool_name not in _SUPPORTED_TOOLS:
        raise SceneObjectCopyError("Unsupported scene object copy tool.")


def _project_path(value: Any) -> str:
    path = _bounded_text(value, label="projectPath", max_length=4096)
    if "\x00" in path or not (
        path.startswith("/")
        or path.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATTERN.match(path)
    ):
        raise SceneObjectCopyError("projectPath must be absolute.")
    return path


def _scene_asset_path(value: Any, *, label: str) -> str:
    path = _asset_path(value, label=label)
    if not path.startswith("Assets/") or not path.lower().endswith(".unity"):
        raise SceneObjectCopyError(f"{label} must be a saved scene under Assets.")
    return path


def _generated_prefab_path(value: Any, *, label: str) -> str:
    path = _asset_path(value, label=label)
    if not path.startswith(_GENERATED_ROOT + "/") or not path.lower().endswith(".prefab"):
        raise SceneObjectCopyError(
            f"{label} must be a prefab below {_GENERATED_ROOT}."
        )
    if PurePosixPath(path).name.startswith(".") and ".vrcforge-stage-" not in PurePosixPath(path).name:
        raise SceneObjectCopyError(f"{label} uses a reserved hidden filename.")
    return path


def _generated_folder_path(value: Any, *, label: str) -> str:
    path = _asset_path(value, label=label)
    if path != _GENERATED_ROOT and not path.startswith(_GENERATED_ROOT + "/"):
        raise SceneObjectCopyError(f"{label} must be below {_GENERATED_ROOT}.")
    return path


def _asset_path(value: Any, *, label: str) -> str:
    path = _bounded_text(value, label=label, max_length=1024)
    if "\\" in path or path.startswith("/") or path.endswith("/"):
        raise SceneObjectCopyError(f"{label} is not a canonical project asset path.")
    parts = path.split("/")
    if any(not part or part in {".", ".."} or any(ord(char) < 32 for char in part) for part in parts):
        raise SceneObjectCopyError(f"{label} contains an unsafe segment.")
    if str(PurePosixPath(path)) != path:
        raise SceneObjectCopyError(f"{label} is not canonical.")
    return path


def _scene_object_path(value: Any, *, label: str) -> str:
    path = _bounded_text(value, label=label, max_length=2048)
    if "\\" in path or path.startswith("/") or path.endswith("/"):
        raise SceneObjectCopyError(f"{label} is not a canonical hierarchy path.")
    parts = path.split("/")
    if any(not part or part in {".", ".."} or any(ord(char) < 32 for char in part) for part in parts):
        raise SceneObjectCopyError(f"{label} contains an unsafe hierarchy segment.")
    return path


def _object_name(value: Any, *, label: str) -> str:
    name = _bounded_text(value, label=label, max_length=256)
    if name != name.strip() or name in {".", ".."} or "/" in name or "\\" in name:
        raise SceneObjectCopyError(f"{label} is not a canonical object name.")
    if any(ord(character) < 32 for character in name):
        raise SceneObjectCopyError(f"{label} contains a control character.")
    return name


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SceneObjectCopyError(f"{label} must be an object.")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise SceneObjectCopyError(f"{label} must be boolean.")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SceneObjectCopyError(f"{label} is outside its allowed range.")
    return value


def _bounded_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise SceneObjectCopyError(f"{label} is invalid.")
    if "\x00" in value:
        raise SceneObjectCopyError(f"{label} contains a null character.")
    return value


def _lower_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value or "").strip()
    if not pattern.fullmatch(text):
        raise SceneObjectCopyError(f"{label} is invalid.")
    return text


def _nonzero_hex(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    text = _lower_hex(value, label=label, pattern=pattern)
    if set(text) == {"0"}:
        raise SceneObjectCopyError(f"{label} cannot be zero.")
    return text


def _digest_field(value: Any) -> str:
    if value is True:
        text = "true"
    elif value is False:
        text = "false"
    elif value is None:
        text = ""
    else:
        text = str(value)
    utf16_units = len(text.encode("utf-16-le")) // 2
    return f"{utf16_units}:{text}"
