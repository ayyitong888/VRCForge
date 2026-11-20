"""Decision facts from a package inspection or import plan."""

import hashlib
import ntpath
from collections.abc import Mapping
from typing import Any

from outfit_package_inspector import INSPECTION_SCHEMA


OUTFIT_IMPORT_TOOL = "vrcforge_import_outfit_package"
OUTFIT_IMPORT_PLAN_TOOL = "vrcforge_plan_outfit_import"


def outfit_import_preflight_key(arguments: Mapping[str, Any]) -> tuple:
    """Match the exact same package selection and loose-file destination."""
    return (
        str(arguments.get("packagePath") or ""), str(arguments.get("selectedUnityPackage") or ""),
        str(arguments.get("selectedPrefab") or ""), str(arguments.get("baseAvatarName") or ""),
        str(arguments.get("dependencyMode") or "auto"), str(arguments.get("targetFolder") or ""),
        str(arguments.get("avatarTexturePaths") or ""),
    )


def package_preview_receipt(result: dict[str, Any]) -> dict[str, Any]:
    from outfit_import_planner import IMPORT_PLAN_SCHEMA

    schema = result.get("schema")
    if schema not in (INSPECTION_SCHEMA, IMPORT_PLAN_SCHEMA):
        return {}
    inspection = result if schema == INSPECTION_SCHEMA else (result.get("inspection") or {})
    summary = inspection.get("summary") or {}
    source = inspection.get("source") or {}
    packages = inspection.get("unityPackages") or []
    inventory = {key: summary[key] for key in (
        "unityPackageCount", "prefabCandidateCount", "materialCount", "textureCount", "modelCount", "entryCount",
        "archiveCount", "archiveUnityPackageCount",
    ) if type(summary.get(key)) is int}
    archive_count = inventory.get("archiveCount", 0)
    if archive_count:
        inventory["archiveCensusComplete"] = summary.get("archiveCensusComplete") is True
        inventory["unityPackageCountScope"] = summary.get("unityPackageCountScope")
        inventory["assetCountScope"] = summary.get("assetCountScope")
    nested = summary.get("nestedPathnamesTruncated")
    truncated = True if nested is True else summary.get("truncated")
    if type(truncated) is bool:
        inventory["truncated"] = truncated
    inventory["scope"] = "reported candidates in the supplied archive or folder"
    if (source.get("type") in {"unitypackage", "zip"} and packages
            and summary.get("truncated") is False and nested is not True
            and all(package.get("pathnamesEvidenceComplete") is True and not package.get("unsafePathnameCount") for package in packages)):
        inventory["missingMatchProvesAbsence"] = True
        inventory["absenceScope"] = "pathname candidates in the parsed UnityPackage inventory only"
    elif truncated is True or archive_count:
        inventory["missingMatchProvesAbsence"] = False
    receipt = {"inventory": inventory, "source": {
        key: source[key][:240] for key in ("name", "type") if isinstance(source.get(key), str)
    }}
    plan = result.get("plan") or {}
    plan_source = plan.get("source") or {}
    if plan_source.get("selectedUnityPackage"):
        receipt["source"]["selectedUnityPackage"] = plan_source["selectedUnityPackage"][:240]
    if isinstance(source.get("path"), str) and source["path"]:
        selection = (result.get("dependencyPreflight") or {}).get("packageOrder") or {}
        appearance_selection = (result.get("dependencyPreflight") or {}).get("materialAppearance") or {}
        scope = (source["path"], plan.get("projectPath", ""), plan_source.get("selectedUnityPackage", ""),
                 appearance_selection.get("selectedPrefabPath", ""), selection.get("dependencyMode", ""))
        receipt["source"]["scopeKey"] = hashlib.sha256(
            "\0".join(ntpath.normcase(ntpath.normpath(str(value or ""))) for value in scope).encode()
        ).hexdigest()[:32]
    queue = plan_source.get("importQueue")
    package_scopes = queue if queue is not None else packages
    if queue:
        receipt["importQueue"] = [{**(item.get("owner") or {}), "role": item.get("role"),
                                   "expectedAssetPathCount": len(item.get("expectedAssetPaths") or [])}
                                  for item in queue[:4]]
        receipt["importQueueCount"] = len(queue)
    limits = sorted({scope["scanLimit"] for scope in package_scopes if scope.get("scanLimit")})
    if limits:
        inventory["scanLimits"] = limits
    ready = plan.get("readyToApply")
    for key in ("kind", "expectedAssetPathsComplete", "expectedAssetPathCount", "requiresApproval", "requiresCheckpoint"):
        if key in plan:
            receipt[key] = plan[key]
    if type(ready) is bool:
        receipt["readyToApply"] = ready
        receipt["status"] = "ready" if ready else "blocked"
    preflight = result.get("dependencyPreflight") or {}
    appearance = preflight.get("materialAppearance")
    if isinstance(appearance, dict):
        receipt["materialAppearance"] = {
            key: appearance[key] for key in ("status", "scope", "prefabCount", "scannedPrefabCount", "bundledMaterialCount",
                                             "installedMaterialCount", "queuedMaterialCount", "unresolvedMaterialCount",
                                             "selectedPrefabPath", "companionSearchStatus")
            if key in appearance
        }
        sources = appearance.get("materialSources") or []
        if sources:
            receipt["materialAppearance"]["materialSources"] = [
                {key: value[:1] if key == "materialExamples" else value for key, value in owner.items()}
                for owner in sources[:3]
            ]
    warnings = [item[:300] for item in result.get("warnings") or [] if isinstance(item, str)]
    if ready is True and plan.get("writeTarget") == "vrcforge_import_outfit_package":
        receipt["nextTool"] = plan["writeTarget"]
        receipt["nextAction"] = "Submit the same package selection and dependency mode through the supervised approval flow."
    elif ready is False or truncated is True:
        if "input_bytes" in limits:
            receipt["blockingReasons"] = ["UnityPackage pathname inspection stopped at the scan input byte limit."]
            receipt["nextAction"] = "The scan hit its byte limit. Increasing maxEntries cannot resolve it. Preview a smaller complete export or a separate needed package."
        elif "archive_members" in limits:
            receipt["blockingReasons"] = ["The archive exceeded the UnityPackage member limit."]
            receipt["nextAction"] = "Preview a smaller complete UnityPackage export or select a separate needed package."
        elif "pathname_entries" in limits:
            receipt["blockingReasons"] = ["The scan reached the requested pathname entry limit."]
            receipt["nextTool"] = "vrcforge_plan_outfit_import" if ready is False else "vrcforge_inspect_outfit_package"
            receipt["nextAction"] = "Retry the same preview with a larger maxEntries."
        else:
            reasons = [entry["message"][:300] for entry in preflight.get("entries") or []
                       if entry.get("blockingBeforeImport") and isinstance(entry.get("message"), str)]
            compatibility = preflight.get("compatibility") or {}
            if compatibility.get("blockingBeforeImport") and isinstance(compatibility.get("message"), str):
                reasons.append(compatibility["message"][:300])
            if plan.get("expectedAssetPathsComplete") is False:
                reasons.append("The import queue lacks a complete, safe inventory of expected Assets paths.")
            receipt["blockingReasons"] = reasons[:5] or warnings[-5:]
            if ready is False:
                receipt["nextAction"] = "Resolve the listed blockers, then preview the import again."
    elif schema == INSPECTION_SCHEMA and archive_count:
        receipt["nextTool"] = "vrcforge_inspect_outfit_package"
        receipt["nextAction"] = "Read /archives for paths and listing status. Join the chosen ZIP path to the original folder packagePath and inspect that ZIP before planning its import."
    elif schema == INSPECTION_SCHEMA and result.get("ok") is True:
        receipt["nextTool"] = "vrcforge_plan_outfit_import"
    return receipt


def completed_package_import_receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Find this operation's successful receipt in the normal approval wrapper."""
    from agent_tool_result_contract import _views

    for view in _views(result):
        if view.get("ok") is True and type(view.get("importedPrefabCount")) is int:
            return {"status": "imported", **{
                key: view[key] for key in ("kind", "committed", "commitState", "importedPrefabCount", "importedMaterialCount",
                                           "importedTextureCount", "nextTool", "nextAction") if key in view
            }}
    return {}
