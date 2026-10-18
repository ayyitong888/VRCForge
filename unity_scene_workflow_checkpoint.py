"""Bind proven scene-only workflow writes to one freshly revalidated scene."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


_CORE_TOOLS = {
    "vrcforge_setup_outfit": "vrc_setup_outfit",
    "vrcforge_instantiate_prefab": "vrc_instantiate_prefab",
    "vrcforge_unpack_prefab": "vrc_unpack_prefab",
    "vrcforge_create_gameobject": "vrc_create_gameobject",
}


def prepare_scene_workflow_checkpoint(
    project_root: Path,
    arguments: dict[str, Any],
    *,
    checkpoint_target: str,
    build_plan: Callable[[str, dict[str, Any]], list[tuple[str, dict[str, Any]]]],
    discover: Callable[[dict[str, Any]], dict[str, Any]],
    prepare: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Prepare one exact saved-scene checkpoint without trusting caller footprints."""

    def reject(message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "blocking": True,
            "code": "checkpoint_scope_unresolved",
            "error": message,
            "mutationStarted": False,
            "saved": False,
        }

    expected_core_tool = _CORE_TOOLS.get(checkpoint_target)
    if expected_core_tool is None:
        return reject("This write is not a proven scene-only workflow.")

    target = arguments.get("executionTarget")
    if not isinstance(target, dict) or target.get("schema") != "vrcforge.execution_target.v1":
        return reject("The approved write has no complete materialized ExecutionTarget.")
    scope = target.get("scope")
    project = target.get("project") if isinstance(target.get("project"), dict) else {}
    editor = target.get("editor") if isinstance(target.get("editor"), dict) else {}
    scene = target.get("scene") if isinstance(target.get("scene"), dict) else {}
    avatar = target.get("avatar") if isinstance(target.get("avatar"), dict) else {}
    obj = target.get("object") if isinstance(target.get("object"), dict) else {}
    component = target.get("component") if isinstance(target.get("component"), dict) else {}
    scene_path = scene.get("assetPath")
    absolute_scene_path = scene.get("absolutePath")
    try:
        expected_absolute_scene_path = (project_root / str(scene_path)).resolve()
        target_project_root = Path(str(project.get("root") or "")).resolve()
        target_absolute_scene_path = Path(str(absolute_scene_path or "")).resolve()
        expected_absolute_scene_path.relative_to(project_root.resolve())
    except (OSError, RuntimeError, ValueError):
        return reject("The approved write does not identify a saved scene inside the project.")
    valid_scene_path = (
        isinstance(scene_path, str)
        and scene_path.startswith("Assets/")
        and scene_path.lower().endswith(".unity")
        and "\\" not in scene_path
        and all(part not in {"", ".", ".."} for part in scene_path.split("/"))
    )
    valid_identity = (
        scope in {"scene", "avatar", "object", "component"}
        and target.get("ambiguous") is not True
        and target.get("resolutionCandidateCount") in (None, 1)
        and target_project_root == project_root.resolve()
        and bool(project.get("projectId"))
        and type(editor.get("unityPid")) is int
        and editor.get("unityPid", 0) > 0
        and bool(editor.get("processStartTime"))
        and bool(editor.get("coreInstanceId"))
        and valid_scene_path
        and target_absolute_scene_path == expected_absolute_scene_path
        and bool(scene.get("guid"))
        and bool(scene.get("revision"))
        and isinstance(scene.get("digest"), str)
        and len(scene["digest"]) == 64
        and all(char in "0123456789abcdef" for char in scene["digest"])
    )
    if not valid_identity:
        return reject("The approved write does not identify one complete saved-scene session.")
    if scope in {"avatar", "object", "component"} and (
        not avatar.get("globalObjectId") or not _hierarchy_path(avatar.get("exactHierarchyPath"))
    ):
        return reject("The approved write has no exact Avatar identity.")
    if scope in {"object", "component"} and (
        not obj.get("globalObjectId") or not _hierarchy_path(obj.get("exactHierarchyPath"))
    ):
        return reject("The approved write has no exact object identity.")
    if scope == "component" and (
        not component.get("globalObjectId") or not component.get("type")
    ):
        return reject("The approved write has no exact component identity.")

    try:
        calls = build_plan(checkpoint_target, arguments)
    except Exception as exc:
        return reject(f"The approved scene workflow plan is invalid: {exc}")
    if (
        not isinstance(calls, list)
        or len(calls) != 1
        or not isinstance(calls[0], tuple)
        or len(calls[0]) != 2
        or calls[0][0] != expected_core_tool
        or not isinstance(calls[0][1], dict)
    ):
        return reject("The approved workflow must contain exactly one matching Core scene call.")
    request = calls[0][1]
    selector_error = _selector_error(checkpoint_target, request, scope, avatar, obj)
    if selector_error:
        return reject(selector_error)

    query: dict[str, Any] = {"projectPath": str(project_root), "scope": scope}
    for section, query_key in (
        (avatar, "avatarGlobalObjectId"),
        (obj, "objectGlobalObjectId"),
        (component, "componentGlobalObjectId"),
    ):
        if section.get("globalObjectId"):
            query[query_key] = section["globalObjectId"]
    if component.get("type"):
        query["componentType"] = component["type"]
    try:
        live = discover(query)
    except Exception as exc:
        return reject(f"Fresh scene identity discovery failed: {exc}")
    if live.get("ok") is False or live.get("success") is False:
        return {
            **live,
            "ok": False,
            "blocking": True,
            "mutationStarted": False,
            "saved": False,
        }
    if live.get("ok") is not True or live.get("targets") != [target]:
        return reject("The approved scene workflow identity changed before checkpointing.")

    prepared = prepare(project_root, checkpoint_files=[scene_path])
    if prepared.get("ok") is not True:
        return prepared
    return {
        **prepared,
        "archiveAssetPaths": [scene_path],
        "archiveScopeReason": "revalidated_scene_only_workflow_owner",
    }


def _hierarchy_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        return ""
    return value if all(part not in {"", ".", ".."} for part in value.split("/")) else ""


def _selector_error(
    checkpoint_target: str,
    request: dict[str, Any],
    scope: str,
    avatar: dict[str, Any],
    obj: dict[str, Any],
) -> str:
    avatar_path = _hierarchy_path(avatar.get("exactHierarchyPath"))
    object_path = _hierarchy_path(obj.get("exactHierarchyPath"))
    if checkpoint_target == "vrcforge_setup_outfit":
        outfit_path = _hierarchy_path(request.get("outfitPath"))
        if (
            not avatar_path
            or request.get("avatarPath") != avatar_path
            or not outfit_path
            or not outfit_path.startswith(avatar_path + "/")
            or request.get("saveScene") is not True
        ):
            return "Setup Outfit must save one strict descendant of the bound Avatar."
        return ""
    if checkpoint_target == "vrcforge_unpack_prefab":
        selected_path = _hierarchy_path(request.get("gameObjectPath"))
        if not selected_path or selected_path != (object_path or avatar_path):
            return "Unpack Prefab must select the exact freshly bound object."
        return ""
    parent_path = _hierarchy_path(request.get("parentPath"))
    if not parent_path:
        return "Creating a scene object requires an exact non-empty bound parent."
    if scope in {"object", "component"} and parent_path == object_path:
        return ""
    if scope == "avatar" and parent_path == avatar_path:
        return ""
    return "The requested parent is outside the exact freshly bound Avatar or object."
