from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


TOOL_NAME = "vrc_save_new_scene"
RESULT_SCHEMA = "vrcforge.scene_asset_save.v1"
APPROVAL_SCHEMA = "vrcforge.scene_asset_save_approval.v1"
OPERATION = "save_new_scene"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GUID = re.compile(r"^[0-9a-f]{32}$")


class SceneAssetSaveError(ValueError):
    pass


def build_wrapper_arguments(params: dict[str, Any]) -> dict[str, Any]:
    wrapper = deepcopy(params or {})
    nested = wrapper.get("arguments")
    if not isinstance(nested, dict):
        nested = wrapper.get("params")
    if not isinstance(nested, dict):
        nested = {"scenePath": wrapper.get("scenePath")}
    wrapper.pop("scenePath", None)
    wrapper.pop("params", None)
    wrapper.pop("tool_name", None)
    wrapper["toolName"] = TOOL_NAME
    wrapper["arguments"] = deepcopy(nested)
    return wrapper


def build_preview_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    request = arguments if isinstance(arguments, dict) else {}
    return {
        "scenePath": deepcopy(request.get("scenePath")),
        "preview": True,
    }


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        raise SceneAssetSaveError("New scene arguments are required.")
    requested_path = _scene_path(nested.get("scenePath"))
    project_path = _project_path(wrapper_arguments.get("projectPath"))
    result = _dict(payload, "preview result")
    _require_common_preview(result)
    if _project_path(result.get("projectPath")) != project_path:
        raise SceneAssetSaveError("New scene preview changed the Unity project.")
    if _scene_path(result.get("scenePath")) != requested_path:
        raise SceneAssetSaveError("New scene preview changed the requested destination.")
    if result.get("targetExists") is not False or result.get("targetMetaExists") is not False:
        raise SceneAssetSaveError("New scene preview did not prove CreateNew destination absence.")

    scene_handle = _integer(
        result.get("sceneHandle"),
        "sceneHandle",
        -2_147_483_648,
        2_147_483_647,
    )
    scene_name = _text(result.get("sceneName"), "sceneName", allow_empty=True, maximum=512)
    scene_was_dirty = result.get("sceneWasDirty")
    if not isinstance(scene_was_dirty, bool):
        raise SceneAssetSaveError("New scene preview sceneWasDirty is invalid.")
    root_count = _integer(result.get("rootObjectCount"), "rootObjectCount", 0, 1_000_000)
    hierarchy_digest = _hex(result.get("sceneHierarchyDigest"), "sceneHierarchyDigest", _DIGEST)
    preview_digest = _hex(result.get("previewDigest"), "previewDigest", _DIGEST)
    if compute_preview_digest(result) != preview_digest:
        raise SceneAssetSaveError("New scene preview digest is invalid.")

    prepared = deepcopy(wrapper_arguments)
    prepared.pop("params", None)
    prepared["projectPath"] = str(project_path)
    prepared["toolName"] = TOOL_NAME
    prepared["arguments"] = {
        "scenePath": requested_path,
        "preview": False,
        "expectedProjectPath": str(project_path),
        "expectedSceneHandle": scene_handle,
        "expectedSceneName": scene_name,
        "expectedSceneWasDirty": scene_was_dirty,
        "expectedRootObjectCount": root_count,
        "expectedSceneHierarchyDigest": hierarchy_digest,
        "expectedPreviewDigest": preview_digest,
    }
    approval = {
        "schema": APPROVAL_SCHEMA,
        "operation": OPERATION,
        "projectPath": str(project_path),
        "scenePath": requested_path,
        "sceneHandle": scene_handle,
        "sceneName": scene_name,
        "sceneWasDirty": scene_was_dirty,
        "rootObjectCount": root_count,
        "sceneHierarchyDigest": hierarchy_digest,
        "createNew": True,
        "overwrite": False,
        "previewDigest": preview_digest,
    }
    return prepared, approval


def validate_apply_result(arguments: dict[str, Any], payload: Any) -> dict[str, Any]:
    result = _dict(payload, "apply result")
    for key, expected in (
        ("schema", RESULT_SCHEMA),
        ("operation", OPERATION),
        ("ok", True),
        ("preview", False),
        ("verified", True),
        ("changed", True),
        ("saved", True),
        ("mutationStarted", True),
        ("commitState", "committed"),
        ("checkpointRestoreRequired", False),
    ):
        if result.get(key) != expected:
            raise SceneAssetSaveError(f"New scene apply {key} is invalid.")
    if _integer(result.get("mutationCount"), "mutationCount", 1, 1) != 1:
        raise SceneAssetSaveError("New scene apply mutationCount is invalid.")
    if _scene_path(result.get("scenePath")) != _scene_path(arguments.get("scenePath")):
        raise SceneAssetSaveError("New scene apply destination does not match approval.")
    if _integer(
        result.get("sceneHandle"),
        "sceneHandle",
        -2_147_483_648,
        2_147_483_647,
    ) != _integer(
        arguments.get("expectedSceneHandle"),
        "expectedSceneHandle",
        -2_147_483_648,
        2_147_483_647,
    ):
        raise SceneAssetSaveError("New scene apply scene identity changed.")
    if _hex(result.get("sceneHierarchyDigest"), "sceneHierarchyDigest", _DIGEST) != _hex(
        arguments.get("expectedSceneHierarchyDigest"), "expectedSceneHierarchyDigest", _DIGEST
    ):
        raise SceneAssetSaveError("New scene apply hierarchy changed.")
    if _hex(result.get("previewDigest"), "previewDigest", _DIGEST) != _hex(
        arguments.get("expectedPreviewDigest"), "expectedPreviewDigest", _DIGEST
    ):
        raise SceneAssetSaveError("New scene apply preview binding changed.")
    _hex(result.get("sceneGuid"), "sceneGuid", _GUID)
    for name in (
        "sceneFileDigest",
        "sceneFileIdentity",
        "sceneMetaDigest",
        "sceneMetaIdentity",
    ):
        _hex(result.get(name), name, _DIGEST)
    return result


def compute_preview_digest(payload: dict[str, Any]) -> str:
    value = payload if isinstance(payload, dict) else {}
    fields = (
        value.get("schema"),
        value.get("operation"),
        value.get("ok"),
        value.get("preview"),
        value.get("verified"),
        value.get("changed"),
        value.get("saved"),
        value.get("mutationCount"),
        value.get("projectPath"),
        value.get("scenePath"),
        value.get("sceneHandle"),
        value.get("sceneName"),
        value.get("sceneWasDirty"),
        value.get("rootObjectCount"),
        value.get("sceneHierarchyDigest"),
        value.get("targetExists"),
        value.get("targetMetaExists"),
    )
    framed = "".join(_digest_field(field) for field in fields)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _require_common_preview(result: dict[str, Any]) -> None:
    for key, expected in (
        ("schema", RESULT_SCHEMA),
        ("operation", OPERATION),
        ("ok", True),
        ("preview", True),
        ("verified", True),
        ("changed", False),
        ("saved", False),
        ("mutationStarted", False),
        ("commitState", "not_started"),
        ("checkpointRestoreRequired", False),
    ):
        if result.get(key) != expected:
            raise SceneAssetSaveError(f"New scene preview {key} is invalid.")
    if _integer(result.get("mutationCount"), "mutationCount", 0, 0) != 0:
        raise SceneAssetSaveError("New scene preview mutationCount is invalid.")


def _scene_path(value: Any) -> str:
    text = _text(value, "scenePath", maximum=1024).replace("\\", "/")
    path = PurePosixPath(text)
    if (
        text != path.as_posix()
        or not text.startswith("Assets/")
        or not text.lower().endswith(".unity")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SceneAssetSaveError("scenePath must be a canonical new .unity path below Assets.")
    return text


def _project_path(value: Any) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise SceneAssetSaveError("projectPath must be an absolute Unity project path.")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SceneAssetSaveError("projectPath is unavailable.") from exc


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SceneAssetSaveError(f"New scene {label} is invalid.")
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    if not isinstance(data, dict):
        raise SceneAssetSaveError(f"New scene {label} is invalid.")
    return data


def _text(value: Any, label: str, *, allow_empty: bool = False, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum:
        raise SceneAssetSaveError(f"{label} is invalid.")
    if not allow_empty and not value:
        raise SceneAssetSaveError(f"{label} is required.")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise SceneAssetSaveError(f"{label} is invalid.")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value or "")
    if pattern.fullmatch(text) is None:
        raise SceneAssetSaveError(f"{label} is invalid.")
    return text


def _digest_field(value: Any) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif value is None:
        text = ""
    else:
        text = str(value)
    return f"{len(text.encode('utf-8'))}:{text}"
