"""Read-only Core preview and approval binding for one prefab instance unpack."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Callable

from unity_execution_plans_scene import build_unpack_prefab_request


class PrefabUnpackPreviewError(ValueError):
    def __init__(self, message: str, payload: Any = None) -> None:
        super().__init__(message)
        self.raw_result = deepcopy(payload) if isinstance(payload, dict) else None


def prepare_unpack_prefab(
    params: dict[str, Any], invoke_preview: Callable[[dict[str, Any]], Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Always obtain current Core evidence; never trust a caller-supplied preview."""
    arguments = deepcopy(params or {})
    request = build_unpack_prefab_request(arguments, preview=True)
    path = request["gameObjectPath"]
    if not path or request["mode"] not in {"completely", "outermost"}:
        raise PrefabUnpackPreviewError("An exact gameObjectPath and completely/outermost mode are required.")
    target = arguments.get("executionTarget")
    if not isinstance(target, dict) or target.get("schema") != "vrcforge.execution_target.v1":
        raise PrefabUnpackPreviewError("A resolved live executionTarget is required for prefab preview.")
    project = str(arguments.get("projectPath") or "").strip()
    bound_project = str((target.get("project") or {}).get("root") or "").strip()
    if not project or Path(project).resolve() != Path(bound_project).resolve():
        raise PrefabUnpackPreviewError("Prefab preview project differs from the bound project.")
    avatar = target.get("avatar") or {}
    avatar_path = str(avatar.get("exactHierarchyPath") or "").strip()
    if not avatar_path or (path != avatar_path and not path.startswith(avatar_path + "/")):
        raise PrefabUnpackPreviewError("Prefab target must be the exact bound Avatar or its descendant.")
    scene = target.get("scene") or {}
    absolute_scene = str(scene.get("absolutePath") or "").strip()
    try:
        scene_path = Path(absolute_scene).resolve().relative_to(Path(project).resolve()).as_posix()
    except ValueError as exc:
        raise PrefabUnpackPreviewError("The bound saved scene must belong to the selected project.") from exc
    if not scene_path.startswith("Assets/") or not scene_path.endswith(".unity"):
        raise PrefabUnpackPreviewError("A bound saved Assets scene is required for prefab preview.")
    # Supply known object and scene identities even on the read-only call.
    request["expectedScenePath"] = scene_path
    obj = target.get("object") or {}
    expected_object = obj if obj.get("exactHierarchyPath") == path else avatar if path == avatar_path else {}
    if expected_object.get("globalObjectId"):
        request["expectedGlobalObjectId"] = expected_object["globalObjectId"]
    payload = invoke_preview(request)
    if not isinstance(payload, dict):
        raise PrefabUnpackPreviewError("Core prefab preview did not return an object.")
    # Preserve Core's own non-prefab/no-unpack rejection before parsing guards.
    if payload.get("isError") is True or payload.get("success") is False or payload.get("ok") is False:
        message = str(payload.get("error") or payload.get("message") or "Core rejected the prefab preview.")
        raise PrefabUnpackPreviewError(message, payload)
    if (payload.get("action") != "unpack_prefab" or payload.get("preview") is not True
            or payload.get("gameObjectPath") != path or payload.get("unpackMode") != request["mode"]
            or payload.get("scenePath") != scene_path):
        raise PrefabUnpackPreviewError("Core prefab preview does not match the selected object, mode and scene.", payload)
    global_id = payload.get("globalObjectId")
    guid = payload.get("prefabGuid")
    dependency = payload.get("dependencyHash")
    if (not isinstance(global_id, str) or not global_id.strip()
            or not isinstance(guid, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", guid)
            or not isinstance(dependency, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", dependency)):
        raise PrefabUnpackPreviewError("Core prefab preview is missing a stable prefab identity.", payload)
    if expected_object.get("globalObjectId") and global_id != expected_object["globalObjectId"]:
        raise PrefabUnpackPreviewError("Core prefab object identity differs from the bound target.", payload)
    guards = {"expectedGlobalObjectId": global_id, "expectedPrefabGuid": guid,
              "expectedAssetDependencyHash": dependency, "expectedScenePath": scene_path}
    for key, value in guards.items():
        if key in arguments and arguments[key] != value:
            raise PrefabUnpackPreviewError(f"Prefab preview drifted from {key}.", payload)
    arguments.update(guards)
    arguments.update({"gameObjectPath": path, "mode": request["mode"], "preview": False})
    return arguments, {"ok": True, **deepcopy(payload)}
