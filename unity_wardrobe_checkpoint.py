"""Preview-bound wardrobe creation calls and their exact asset rollback footprint."""
from copy import deepcopy
from pathlib import Path, PurePosixPath

from checkpoint_asset_footprint import validate_asset_footprint
from prepared_unity_execution import build_prepared_execution_plan, install_prepared_calls, prepared_evidence
from wardrobe_outfit_workflow_service import build_create_wardrobe_core_calls, build_add_wardrobe_outfit_request

def _base_calls(arguments, operation):
    if operation == "create_wardrobe":
        return build_create_wardrobe_core_calls(arguments, False)
    if operation == "add_wardrobe_outfit":
        return [("vrc_add_wardrobe_outfit", build_add_wardrobe_outfit_request(arguments, False))]
    raise ValueError("Unsupported wardrobe checkpoint operation.")


def _plans(arguments, calls, preview):
    plans = []
    for tool, request in calls:
        query = {**arguments, **request, "preview": True}
        query.pop("expectedCheckpointAssetPaths", None)
        result = preview(tool, query)
        plan = result.get("plan") if isinstance(result, dict) else None
        if (not isinstance(result, dict) or result.get("ok") is not True or result.get("preview") is not True
                or not isinstance(plan, dict) or plan.get("action") != tool.removeprefix("vrc_")
                or plan.get("avatarPath") != request.get("avatarPath")):
            raise ValueError("The wardrobe primitive preview did not prove its exact Avatar effect.")
        paths = plan.get("checkpointAssetPaths")
        if not isinstance(paths, list) or not paths or len(paths) > 2048 or any(not isinstance(p, str) for p in paths):
            raise ValueError("The loaded Core has no complete wardrobe checkpoint paths; load the current Core.")
        plans.append(plan)
    return plans


def prepare_wardrobe_request(arguments, caller_preview, *, preview, operation="create_wardrobe"):
    """Freeze Core-owned output paths before the ordinary approval digest is made."""
    calls = _base_calls(arguments, operation)
    plans = _plans(arguments, calls, preview)
    sealed_calls = [(tool, {**request, "expectedCheckpointAssetPaths": plan["checkpointAssetPaths"]})
                    for (tool, request), plan in zip(calls, plans)]
    return install_prepared_calls(arguments, sealed_calls, {
        "schema": "vrcforge.prepared-" + operation.replace("_", "-") + ".v1", "plans": plans,
    }), {"ok": True, "preview": True, "plans": plans}


def prepare_wardrobe_checkpoint(project_root, arguments, *, discover, preview, prepare, operation="create_wardrobe"):
    """Revalidate the approved paths and snapshot existing and create-new outputs."""
    def reject(message):
        return {"ok": False, "blocking": True, "code": "checkpoint_scope_unresolved",
                "error": message, "mutationStarted": False, "saved": False}
    try:
        target = arguments["executionTarget"]
        avatar, project, scene, editor = (target[key] for key in ("avatar", "project", "scene", "editor"))
        if (target.get("schema") != "vrcforge.execution_target.v1" or target.get("scope") != "avatar"
                or target.get("ambiguous") is True or target.get("resolutionCandidateCount") != 1
                or not Path(project["root"]).is_absolute() or Path(project["root"]).resolve() != project_root.resolve()
                or not project.get("projectId") or not avatar.get("globalObjectId")
                or not editor.get("unityPid") or not editor.get("processStartTime") or not editor.get("coreInstanceId")
                or not scene.get("guid") or not scene.get("revision") or not scene.get("digest")):
            raise ValueError("Wardrobe creation requires one complete bound Avatar session.")
        query = {"projectPath": str(project_root), "scope": "avatar", "avatarGlobalObjectId": avatar["globalObjectId"]}
        def fresh():
            live = discover(query)
            if live.get("ok") is not True or live.get("targets") != [target]:
                raise ValueError("The approved wardrobe Avatar identity changed before checkpointing.")
        fresh()
        evidence = prepared_evidence(arguments)
        calls = build_prepared_execution_plan(arguments)
        original = deepcopy(arguments)
        original.pop("_vrcforge_prepared_unity_execution")
        base = _base_calls(original, operation)
        if (evidence.get("schema") != "vrcforge.prepared-" + operation.replace("_", "-") + ".v1"
                or [tool for tool, _ in calls] != [tool for tool, _ in base]):
            raise ValueError("The wardrobe operation requires its exact prepared Core plan.")
        if [(tool, {k: v for k, v in request.items() if k != "expectedCheckpointAssetPaths"})
                for tool, request in calls] != base:
            raise ValueError("The approved wardrobe calls differ from the frozen user request.")
        if any(request.get("avatarPath") != avatar.get("exactHierarchyPath") for _, request in calls):
            raise ValueError("A wardrobe call selects another Avatar.")
        plans = _plans(arguments, calls, preview)
        if plans != evidence.get("plans") or any(request.get("expectedCheckpointAssetPaths") != plan["checkpointAssetPaths"]
                for (_, request), plan in zip(calls, plans)):
            raise ValueError("The approved wardrobe output paths or source state changed.")
        fresh()
        paths = sorted({path for plan in plans for path in plan["checkpointAssetPaths"]})
        if any(PurePosixPath(path).suffix not in {".asset", ".controller", ".anim", ".unity"}
               or path.endswith(".unity") and path != scene["assetPath"] for path in paths):
            raise ValueError("The Core wardrobe footprint includes an unsupported owner or scene.")
        marker = {"schema": "vrcforge.asset_checkpoint_footprint.v1"}
        # Reuse canonical-path, type, containment and reparse-point checks before filesystem inspection.
        validate_asset_footprint(project_root, {"assetFootprint": marker, "archiveFiles": paths,
                                              "archiveAbsentFiles": [], "archiveAbsentDirectories": []})
        files, directories = set(paths), set()
        for name in paths:
            if not name.endswith(".meta"):
                files.add(name + ".meta")
                if (project_root / name).is_file() and not (project_root / (name + ".meta")).is_file():
                    raise ValueError("An existing wardrobe output asset lacks its Unity metadata.")
            parent = PurePosixPath(name).parent
            while parent.as_posix() != "Assets":
                absolute = project_root / parent.as_posix()
                if not absolute.exists():
                    directories.add(parent.as_posix())
                    files.add(parent.as_posix() + ".meta")
                elif not absolute.is_dir() or not Path(str(absolute) + ".meta").is_file():
                    raise ValueError("An existing wardrobe output folder lacks its valid Unity metadata.")
                parent = parent.parent
        existing = sorted(name for name in files if (project_root / name).is_file())
        absent = sorted(files - set(existing))
        footprint = {**marker, "archiveFiles": existing, "archiveAbsentFiles": absent,
                     "archiveAbsentDirectories": sorted(directories)}
        validate_asset_footprint(project_root, {"assetFootprint": footprint, **{k: v for k, v in footprint.items() if k != "schema"}}, preparing=True)
        result = prepare(project_root, checkpoint_files=sorted(files))
        if result.get("ok") is not True:
            return result
        return {**result, "assetFootprint": footprint, "archiveScopeReason": "revalidated_prepared_wardrobe_asset_footprint"}
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
        return reject(str(exc))
