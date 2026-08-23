from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any


TOOL_NAME = "vrc_save_current_scene"
RESULT_SCHEMA = "vrcforge.current_scene_save.v1"
APPROVAL_SCHEMA = "vrcforge.current_scene_save_approval.v1"
OPERATION = "save_current_scene"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GUID = re.compile(r"^[0-9a-f]{32}$")


class CurrentSceneSaveError(ValueError):
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
    return {"scenePath": deepcopy(request.get("scenePath")), "preview": True}


def bind_authoritative_preview(
    wrapper_arguments: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = wrapper_arguments.get("arguments")
    if not isinstance(nested, dict):
        raise CurrentSceneSaveError("Current scene arguments are required.")
    requested_path = _scene_path(nested.get("scenePath"))
    project_path = _project_path(wrapper_arguments.get("projectPath"))
    result = _dict(payload, "preview result")
    _require_common_preview(result)
    if _project_path(result.get("projectPath")) != project_path:
        raise CurrentSceneSaveError("Current scene preview changed the Unity project.")
    if _scene_path(result.get("scenePath")) != requested_path:
        raise CurrentSceneSaveError("Current scene preview changed the requested scene.")
    if result.get("sceneWasDirty") is not True:
        raise CurrentSceneSaveError("Current scene preview did not prove unsaved in-memory changes.")
    if _integer(result.get("openSceneCount"), "openSceneCount", 1, 1) != 1:
        raise CurrentSceneSaveError("Current scene preview did not prove one open scene.")

    scene_handle = _integer(result.get("sceneHandle"), "sceneHandle", -2_147_483_648, 2_147_483_647)
    scene_name = _text(result.get("sceneName"), "sceneName", allow_empty=True, maximum=512)
    root_count = _integer(result.get("rootObjectCount"), "rootObjectCount", 0, 1_000_000)
    hierarchy_digest = _hex(result.get("sceneHierarchyDigest"), "sceneHierarchyDigest", _DIGEST)
    scene_guid = _hex(result.get("sceneGuid"), "sceneGuid", _GUID)
    before_file_digest = _hex(result.get("sceneFileDigestBefore"), "sceneFileDigestBefore", _DIGEST)
    before_file_identity = _hex(result.get("sceneFileIdentityBefore"), "sceneFileIdentityBefore", _DIGEST)
    before_meta_digest = _hex(result.get("sceneMetaDigestBefore"), "sceneMetaDigestBefore", _DIGEST)
    before_meta_identity = _hex(result.get("sceneMetaIdentityBefore"), "sceneMetaIdentityBefore", _DIGEST)
    preview_digest = _hex(result.get("previewDigest"), "previewDigest", _DIGEST)
    if compute_preview_digest(result) != preview_digest:
        raise CurrentSceneSaveError("Current scene preview digest is invalid.")

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
        "expectedSceneWasDirty": True,
        "expectedOpenSceneCount": 1,
        "expectedRootObjectCount": root_count,
        "expectedSceneHierarchyDigest": hierarchy_digest,
        "expectedSceneGuid": scene_guid,
        "expectedSceneFileDigestBefore": before_file_digest,
        "expectedSceneFileIdentityBefore": before_file_identity,
        "expectedSceneMetaDigestBefore": before_meta_digest,
        "expectedSceneMetaIdentityBefore": before_meta_identity,
        "expectedPreviewDigest": preview_digest,
    }
    approval = {
        "schema": APPROVAL_SCHEMA,
        "operation": OPERATION,
        "projectPath": str(project_path),
        "scenePath": requested_path,
        "sceneHandle": scene_handle,
        "sceneName": scene_name,
        "sceneWasDirty": True,
        "openSceneCount": 1,
        "rootObjectCount": root_count,
        "sceneHierarchyDigest": hierarchy_digest,
        "sceneGuid": scene_guid,
        "sceneFileDigestBefore": before_file_digest,
        "sceneMetaDigestBefore": before_meta_digest,
        "persistsExistingMemoryState": True,
        "preSaveCheckpointAvailable": False,
        "requiresExplicitUserApproval": True,
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
        ("manualRecoveryRequired", False),
        ("sceneIsDirty", False),
    ):
        if result.get(key) != expected:
            raise CurrentSceneSaveError(f"Current scene apply {key} is invalid.")
    if _integer(result.get("mutationCount"), "mutationCount", 1, 1) != 1:
        raise CurrentSceneSaveError("Current scene apply mutationCount is invalid.")
    if _scene_path(result.get("scenePath")) != _scene_path(arguments.get("scenePath")):
        raise CurrentSceneSaveError("Current scene apply path does not match approval.")
    if result.get("sceneGuid") != arguments.get("expectedSceneGuid"):
        raise CurrentSceneSaveError("Current scene apply GUID changed.")
    if result.get("sceneHierarchyDigest") != arguments.get("expectedSceneHierarchyDigest"):
        raise CurrentSceneSaveError("Current scene apply hierarchy changed.")
    if result.get("sceneMetaDigestAfter") != arguments.get("expectedSceneMetaDigestBefore"):
        raise CurrentSceneSaveError("Current scene apply metadata changed.")
    if result.get("previewDigest") != arguments.get("expectedPreviewDigest"):
        raise CurrentSceneSaveError("Current scene apply preview binding changed.")
    for name in ("sceneFileDigestAfter", "sceneFileIdentityAfter", "sceneMetaIdentityAfter"):
        _hex(result.get(name), name, _DIGEST)
    return result


def compute_preview_digest(payload: dict[str, Any]) -> str:
    value = payload if isinstance(payload, dict) else {}
    fields = (
        value.get("schema"), value.get("operation"), value.get("ok"), value.get("preview"),
        value.get("verified"), value.get("changed"), value.get("saved"), value.get("mutationCount"),
        value.get("projectPath"), value.get("scenePath"), value.get("sceneGuid"), value.get("sceneHandle"),
        value.get("sceneName"), value.get("sceneWasDirty"), value.get("openSceneCount"),
        value.get("rootObjectCount"), value.get("sceneHierarchyDigest"),
        value.get("sceneFileDigestBefore"), value.get("sceneFileIdentityBefore"),
        value.get("sceneMetaDigestBefore"), value.get("sceneMetaIdentityBefore"),
    )
    return hashlib.sha256("".join(_digest_field(field) for field in fields).encode("utf-8")).hexdigest()


def _require_common_preview(result: dict[str, Any]) -> None:
    for key, expected in (
        ("schema", RESULT_SCHEMA), ("operation", OPERATION), ("ok", True), ("preview", True),
        ("verified", True), ("changed", False), ("saved", False), ("mutationStarted", False),
        ("commitState", "not_started"), ("checkpointRestoreRequired", False),
        ("manualRecoveryRequired", False),
    ):
        if result.get(key) != expected:
            raise CurrentSceneSaveError(f"Current scene preview {key} is invalid.")
    if _integer(result.get("mutationCount"), "mutationCount", 0, 0) != 0:
        raise CurrentSceneSaveError("Current scene preview mutationCount is invalid.")


def _scene_path(value: Any) -> str:
    text = _text(value, "scenePath", maximum=1024).replace("\\", "/")
    path = PurePosixPath(text)
    if text != path.as_posix() or not text.startswith("Assets/") or not text.lower().endswith(".unity") or any(part in {"", ".", ".."} for part in path.parts):
        raise CurrentSceneSaveError("scenePath must be a canonical existing .unity path below Assets.")
    return text


def _project_path(value: Any) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise CurrentSceneSaveError("projectPath must be an absolute Unity project path.")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CurrentSceneSaveError("projectPath is unavailable.") from exc


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CurrentSceneSaveError(f"Current scene {label} is invalid.")
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    if not isinstance(data, dict):
        raise CurrentSceneSaveError(f"Current scene {label} is invalid.")
    return data


def _text(value: Any, label: str, *, allow_empty: bool = False, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum or (not allow_empty and not value):
        raise CurrentSceneSaveError(f"{label} is invalid.")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise CurrentSceneSaveError(f"{label} is invalid.")
    return value


def _hex(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value or "")
    if pattern.fullmatch(text) is None:
        raise CurrentSceneSaveError(f"{label} is invalid.")
    return text


def _digest_field(value: Any) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif value is None:
        text = ""
    else:
        text = str(value)
    return f"{len(text.encode('utf-8'))}:{text}"
