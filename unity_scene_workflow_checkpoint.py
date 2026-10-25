"""Bind proven scene-only workflow writes to one freshly revalidated scene."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from prepared_unity_execution import build_prepared_execution_plan, prepared_evidence


_CORE_TOOLS = {
    "vrcforge_setup_outfit": "vrc_setup_outfit",
    "vrcforge_instantiate_prefab": "vrc_instantiate_prefab",
    "vrcforge_unpack_prefab": "vrc_unpack_prefab",
    "vrcforge_create_gameobject": "vrc_create_gameobject",
    "vrcforge_set_gameobject_active": "vrc_set_gameobject_active",
    "vrcforge_add_modular_avatar_component": "vrc_add_modular_avatar_component",
    "vrcforge_create_component_feature": "vrc_create_component_feature",
    "vrcforge_rename_gameobject": "vrc_rename_gameobject",
    "vrcforge_reparent_gameobject": "vrc_reparent_gameobject",
    "vrcforge_delete_gameobject": "vrc_delete_gameobject",
    "vrcforge_duplicate_scene_object": "vrc_duplicate_scene_object",
}


def prepare_add_outfit_checkpoint(project_root: Path, arguments: dict[str, Any], *, discover, prepare) -> dict[str, Any]:
    """Bind a sealed scene-only Add Outfit installation to its one saved scene."""
    def reject(message):
        return {"ok": False, "blocking": True, "code": "checkpoint_scope_unresolved",
                "error": message, "mutationStarted": False, "saved": False}
    try:
        evidence = prepared_evidence(arguments)
        calls = build_prepared_execution_plan(arguments)
        unpack = evidence.get("unpackPrefab") is True
        setup = evidence.get("setupOutfit") is True
        continuation_tools = []
        if unpack:
            continuation_tools.append("vrc_unpack_prefab")
        if setup:
            continuation_tools.append("vrc_setup_outfit")
        expected_tools = ["vrc_instantiate_prefab", *continuation_tools]
        if (evidence.get("schema") != "vrcforge.prepared-add-outfit.v1"
                or evidence.get("manageWardrobe") is not False
                or type(evidence.get("setupOutfit")) is not bool
                or type(evidence.get("unpackPrefab")) is not bool
                or [call[0] for call in calls] != expected_tools):
            raise ValueError("This Add Outfit plan has no proven scene-only installation footprint.")
        target = arguments["executionTarget"]
        avatar = target["avatar"]
        scene = target["scene"]
        facts = evidence["readFacts"]
        asset, owner, parent = facts["asset"], facts["avatar"], facts["parent"]
        bound_parent = target["object"] if target.get("scope") in {"object", "component"} else avatar
        name = evidence["outfitName"]
        if (not _hierarchy_path(name) or "/" in name
                or facts["outfitPath"] != parent["gameObjectPath"] + "/" + name
                or facts["outfitPath"] in parent["children"]
                or owner["globalObjectId"] != avatar["globalObjectId"]
                or owner["gameObjectPath"] != avatar["exactHierarchyPath"]
                or owner["scenePath"] != scene["assetPath"]
                or parent["scenePath"] != scene["assetPath"]
                or parent["globalObjectId"] != bound_parent["globalObjectId"]
                or Path(evidence["projectIdentity"]["projectPath"]).resolve() != project_root.resolve()
                or not asset["assetPath"].startswith("Assets/")
                or ".." in Path(asset["assetPath"]).parts
                or not asset["assetPath"].endswith(".prefab")
                or any(len(asset[key]) != 32 or any(c not in "0123456789abcdef" for c in asset[key])
                       for key in ("guid", "dependencyHash"))):
            raise ValueError("The sealed installation identity differs from its bound scene owner.")
        instantiate = calls[0][1]
        nonce = instantiate.get("approvedObjectReceiptNonce")
        if continuation_tools and (not isinstance(nonce, str) or len(nonce) != 64
                                   or any(c not in "0123456789abcdef" for c in nonce)):
            raise ValueError("The installation has no exact approved continuation nonce.")
        project = evidence["projectIdentity"]["projectPath"]
        expected_instantiate = {
            "projectPath": project, "assetPath": asset["assetPath"], "guid": asset["guid"],
            "parentPath": parent["gameObjectPath"], "name": name,
            "worldPositionStays": instantiate["worldPositionStays"],
            "expectedPrefabGuid": asset["guid"], "expectedAssetDependencyHash": asset["dependencyHash"],
            "expectedScenePath": scene["assetPath"], "expectedParentGlobalObjectId": parent["globalObjectId"],
            "expectedResultPath": facts["outfitPath"], "preview": False,
        }
        if continuation_tools:
            expected_instantiate.update(approvedObjectReceiptNonce=nonce,
                                        approvedContinuationTools=continuation_tools)
        if type(instantiate.get("worldPositionStays")) is not bool or instantiate != expected_instantiate:
            raise ValueError("The sealed installation calls do not match their exact scene-only effects.")
        if setup and calls[-1][1] != {
            "projectPath": project, "avatarPath": owner["gameObjectPath"],
            "outfitPath": facts["outfitPath"], "confirmSetup": True, "saveScene": True,
            "approvedObjectReceiptNonce": nonce,
        }:
            raise ValueError("The sealed installation calls do not match their exact scene-only effects.")
        if unpack:
            unpack_tool, unpack_request = calls[1]
            if unpack_tool != "vrc_unpack_prefab" or unpack_request != {
                "projectPath": project, "gameObjectPath": facts["outfitPath"],
                "expectedPrefabGuid": asset["guid"],
                "expectedAssetDependencyHash": asset["dependencyHash"],
                "expectedScenePath": scene["assetPath"],
                "mode": unpack_request.get("mode"), "preview": False,
                "approvedObjectReceiptNonce": nonce,
            } or unpack_request.get("mode") not in {"outermost", "completely"}:
                raise ValueError("The sealed unpack call does not continue the exact instantiated prefab in its bound scene.")
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
        return reject(str(exc))
    result = prepare_scene_workflow_checkpoint(
        project_root, {"executionTarget": target}, checkpoint_target="vrcforge_instantiate_prefab",
        build_plan=lambda _tool, _args: [calls[0]], discover=discover, prepare=prepare,
    )
    if result.get("ok") is True:
        result["archiveScopeReason"] = "revalidated_scene_only_add_outfit_owner"
    return result


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
    # An object being reparented into an Avatar is, by definition, outside
    # the Avatar before the write: Core discovery then reports avatar=null.
    # Require the Avatar section only when this target actually carries one;
    # scene and object identity stay strictly validated either way.
    if avatar and scope in {"avatar", "object", "component"} and (
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
    if checkpoint_target == "vrcforge_create_component_feature":
        try:
            expected_project = Path(str(request.get("expectedProjectPath") or "")).resolve()
        except (OSError, RuntimeError, ValueError):
            expected_project = None
        if (scope not in {"object", "component"}
                or request.get("scenePath") != scene_path
                or request.get("expectedSceneGuid") != scene.get("guid")
                or request.get("expectedHostObjectId") != obj.get("globalObjectId")
                or expected_project != project_root.resolve()):
            return reject("The frozen component feature call does not match its exact scene, project, and host identities.")
    selector_error = _selector_error(checkpoint_target, request, scope, avatar, obj, discover=discover)
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
    *,
    discover=None,
) -> str:
    avatar_path = _hierarchy_path(avatar.get("exactHierarchyPath"))
    object_path = _hierarchy_path(obj.get("exactHierarchyPath"))
    if checkpoint_target == "vrcforge_add_modular_avatar_component":
        if (scope not in {"object", "component"}
                or not object_path or request.get("gameObjectPath") != object_path
                or not avatar_path or not (object_path == avatar_path or object_path.startswith(avatar_path + "/"))
                or request.get("avatarPath", avatar_path) != avatar_path
                or request.get("preview") is not False
                or type(request.get("saveScene")) is not bool):
            return "Modular Avatar configuration must select the exact freshly bound Avatar object."
        return ""
    if checkpoint_target == "vrcforge_create_component_feature":
        if (scope not in {"object", "component"}
                or not object_path
                or request.get("gameObjectPath") != object_path
                or request.get("preview") is not False
                or request.get("saveScene") is not True):
            return "Component feature creation must select the exact bound host in one saved scene."
        return ""
    if checkpoint_target == "vrcforge_set_gameobject_active":
        if (scope not in {"object", "component"}
                or not object_path or request.get("gameObjectPath") != object_path
                or not avatar_path or not (object_path == avatar_path or object_path.startswith(avatar_path + "/"))
                or set(request) != {"gameObjectPath", "active", "preview"}
                or type(request.get("active")) is not bool or request.get("preview") is not False):
            return "Visibility must select one exact freshly bound Avatar object with an explicit Boolean state."
        return ""
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
    if checkpoint_target == "vrcforge_reparent_gameobject":
        # Reparenting INTO an Avatar moves an object that lives outside it;
        # Core discovery for such a source object reports avatar=null, so the
        # destination is proven against a live Avatar discovery instead of the
        # (absent) target section. In-subtree destinations still validate
        # against the bound Avatar identity.
        new_parent = _hierarchy_path(request.get("newParentPath"))
        if not new_parent:
            return "Reparent requires an exact non-empty newParentPath."
        if avatar_path and (new_parent == avatar_path or new_parent.startswith(avatar_path + "/")):
            return ""
        if scope in {"object", "component"} and new_parent == object_path:
            return ""
        if not avatar:
            avatars = discover({"projectPath": str(project_root), "scope": "avatar"}).get("targets") or []
            exact_roots = [
                str((t.get("object") or t).get("exactHierarchyPath") or "")
                for t in avatars if isinstance(t, dict)
            ]
            if len(exact_roots) == 1 and new_parent == exact_roots[0]:
                return ""
        return "The reparent destination is outside the exact freshly bound Avatar or object."
    if checkpoint_target == "vrcforge_unpack_prefab":
        selected_path = _hierarchy_path(request.get("gameObjectPath"))
        if not selected_path or selected_path != (object_path or avatar_path):
            return "Unpack Prefab must select the exact freshly bound object."
        return ""
    if checkpoint_target == "vrcforge_instantiate_prefab" and scope == "scene":
        if request.get("parentPath") == "":
            return ""
        return "A scene-root prefab instance must target the exact bound scene without a parent."
    if checkpoint_target == "vrcforge_create_gameobject" and scope == "scene":
        if request.get("parentPath") == "":
            return ""
        return "A scene-root GameObject must target the exact bound scene without a parent."
    parent_path = _hierarchy_path(request.get("parentPath"))
    if not parent_path:
        return "Creating a scene object requires an exact non-empty bound parent."
    if scope in {"object", "component"} and parent_path == object_path:
        return ""
    if scope == "avatar" and parent_path == avatar_path:
        return ""
    return "The requested parent is outside the exact freshly bound Avatar or object."
