"""Resolve the exact saved scene owner for a sealed blendshape write."""
from pathlib import Path
from typing import Any, Callable
from execution_target import scene_identity_matches


def prepare_blendshape_checkpoint(
    project_root: Path,
    arguments: dict[str, Any],
    *,
    build_plan: Callable,
    resolve_reference: Callable,
    discover: Callable,
    inspect_owner: Callable,
    prepare: Callable,
    required_scope: str,
) -> dict[str, Any]:
    def reject(message: str) -> dict[str, Any]:
        return {"ok": False, "blocking": True, "code": "checkpoint_scope_unresolved",
                "error": message, "mutationStarted": False, "saved": False}

    try:
        reference = arguments.get("executionTarget")
        if isinstance(reference, dict) and reference.get("schema") == "vrcforge.execution_target_ref.v1":
            target = resolve_reference(reference, project_root=str(project_root), required_scope=required_scope)
        elif isinstance(reference, dict) and reference.get("schema") == "vrcforge.execution_target.v1":
            target = reference
        else:
            return reject("The approved write has no valid session-bound ExecutionTarget reference.")
        calls = build_plan(arguments)
    except Exception as exc:  # References and seals are both fail-closed inputs.
        return reject(f"The approved blendshape identity or sealed plan is invalid: {exc}")

    if not isinstance(target, dict):
        return reject("The approved write has no complete ExecutionTarget identity.")
    project = target.get("project") or {}
    avatar = target.get("avatar") or {}
    scene = target.get("scene") or {}
    if (target.get("schema") != "vrcforge.execution_target.v1"
            or target.get("scope") not in {"avatar", "object", "component"}
            or target.get("ambiguous") is True
            or target.get("resolutionCandidateCount") not in (None, 1)
            or Path(str(project.get("root") or "")).resolve() != project_root.resolve()
            or not avatar.get("globalObjectId")
            or not avatar.get("exactHierarchyPath")
            or not scene.get("assetPath") or not scene.get("guid") or not scene.get("digest")):
        return reject("The approved write does not identify one complete Avatar and saved scene.")

    if (len(calls) != 1 or calls[0][0] != "vrc_apply_blendshapes"
            or not isinstance(calls[0][1], dict)):
        return reject("The sealed plan must contain exactly one blendshape apply call.")
    request = calls[0][1]
    scene_path = scene["assetPath"]
    avatar_path = avatar["exactHierarchyPath"]
    if (request.get("saveAssets") is not True or request.get("avatarPath") != avatar_path
            or not isinstance(scene_path, str) or not scene_path.startswith("Assets/")
            or not scene_path.lower().endswith(".unity") or "\\" in scene_path
            or any(part in {"", ".", ".."} for part in scene_path.split("/"))):
        return reject("The sealed blendshape call does not match the saved Avatar scene.")
    adjustments = request.get("adjustments")
    if not isinstance(adjustments, list) or not adjustments:
        return reject("The sealed blendshape call has no adjustments to checkpoint.")
    renderer_paths = sorted({
        item.get("rendererPath", item.get("renderer_path")) for item in adjustments
        if isinstance(item, dict) and isinstance(item.get("rendererPath", item.get("renderer_path")), str)
    })
    if len(renderer_paths) == 0 or any(
        (path != avatar_path and not path.startswith(avatar_path + "/")) or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        for path in renderer_paths
    ):
        return reject("Every adjusted renderer must be an exact descendant of the approved Avatar.")
    if sum(1 for item in adjustments if not isinstance(item, dict)
           or not isinstance(item.get("rendererPath", item.get("renderer_path")), str)):
        return reject("The sealed blendshape call contains an invalid renderer path.")

    discovered_rows: list[dict[str, Any]] = []
    offset = 0
    expected_total = None
    expected_hash = None
    while True:
        try:
            live = discover({
                "projectPath": str(project_root), "scope": "component",
                "avatarGlobalObjectId": avatar["globalObjectId"],
                "componentType": "UnityEngine.SkinnedMeshRenderer",
                "offset": offset, "maxItems": 128,
            })
        except Exception as exc:
            return reject(f"Fresh renderer discovery failed: {exc}")
        if live.get("ok") is False or live.get("success") is False:
            return {**live, "ok": False, "blocking": True,
                    "mutationStarted": False, "saved": False}
        rows = live.get("targets") if live.get("ok") is True else None
        if not isinstance(rows, list):
            return reject("Fresh renderer discovery returned no complete candidate page.")
        total = live.get("totalCandidateCount")
        candidate_hash = live.get("candidateSetHash")
        if type(total) is not int or total < 0 or not isinstance(candidate_hash, str) or not candidate_hash:
            return reject("Fresh renderer discovery omitted its complete-set identity.")
        if expected_total is None:
            expected_total, expected_hash = total, candidate_hash
        elif total != expected_total or candidate_hash != expected_hash:
            return reject("The renderer candidate set changed during checkpoint discovery.")
        if live.get("complete") is True and live.get("hasMore") is not True:
            discovered_rows.extend(rows)
            break
        if live.get("hasMore") is not True or not rows or offset + len(rows) >= 4096:
            return reject("Fresh renderer discovery was incomplete or exceeded its bounded scan.")
        discovered_rows.extend(rows)
        offset += len(rows)
    if len(discovered_rows) != expected_total:
        return reject("Fresh renderer discovery did not cover the complete candidate set.")

    for renderer_path in renderer_paths:
        matches = [row for row in discovered_rows if isinstance(row, dict)
                   if (row.get("object") or {}).get("exactHierarchyPath") == renderer_path
                   and (row.get("component") or {}).get("type") == "UnityEngine.SkinnedMeshRenderer"]
        if len(matches) != 1:
            return reject("Fresh discovery did not identify one exact adjusted renderer.")
        renderer = matches[0]
        obj = renderer.get("object") or {}
        component = renderer.get("component") or {}
        if (renderer.get("schema") != "vrcforge.execution_target.v1"
                or renderer.get("scope") != "component"
                or renderer.get("ambiguous") is True
                or renderer.get("resolutionCandidateCount") not in (None, 1)
                or not obj.get("globalObjectId") or not component.get("globalObjectId")
                or obj.get("exactHierarchyPath") != renderer_path
                or component.get("exactHierarchyPath") not in (None, renderer_path)
                or renderer.get("avatar") != avatar
                or not scene_identity_matches(renderer.get("scene") or {}, scene)
                or renderer.get("project") != project or renderer.get("editor") != target.get("editor")):
            return reject("The adjusted renderer is outside the approved Avatar scene identity.")
        # Read-only Core owner proof. It does not change the enabled property.
        owner = inspect_owner({
            "projectPath": str(project_root), "executionTarget": renderer,
            "gameObjectPath": renderer_path,
            "componentType": "UnityEngine.SkinnedMeshRenderer",
            "componentIndex": 0, "propertyPath": "enabled",
            "includeCheckpointScope": True,
        })
        if owner.get("ok") is False or owner.get("success") is False:
            return {**owner, "ok": False, "blocking": True,
                    "mutationStarted": False, "saved": False}
        scope = owner.get("checkpointScope") if owner.get("ok") is True else None
        expected = {
            "ownerKind": "scene", "scenePath": scene_path,
            "sceneGuid": scene["guid"], "sceneDigest": scene["digest"],
            "objectGlobalObjectId": obj.get("globalObjectId"),
            "componentGlobalObjectId": component.get("globalObjectId"),
            "componentType": "UnityEngine.SkinnedMeshRenderer", "componentIndex": 0,
            "propertyPath": "enabled", "archiveAssetPaths": [scene_path],
        }
        if (not isinstance(scope, dict) or scope.get("schema") != "vrcforge.component_property_checkpoint.v1"
                or any(scope.get(key) != value for key, value in expected.items())):
            return reject("Core did not prove the exact adjusted renderer's saved scene owner.")

    # The sealed call is unique and each renderer/owner was freshly revalidated.
    prepared = prepare(project_root, checkpoint_files=[scene_path])
    if prepared.get("ok") is not True:
        return prepared
    return {**prepared, "archiveAssetPaths": [scene_path],
            "archiveScopeReason": "revalidated_blendshape_renderer_scene_owners"}
