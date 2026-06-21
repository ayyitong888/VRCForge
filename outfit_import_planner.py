from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from outfit_package_inspector import inspect_outfit_package


IMPORT_PLAN_SCHEMA = "vrcforge.outfit_import_plan.v1"
DEFAULT_TARGET_ROOT = "Assets/VRCForge/ImportedOutfits"


def build_outfit_import_plan(
    package_path: str | Path,
    project_path: str | Path | None = None,
    target_folder: str | None = None,
    selected_unitypackage: str | None = None,
    selected_prefab: str | None = None,
    base_avatar_name: str | None = None,
    max_entries: int = 5000,
) -> dict[str, Any]:
    inspection = inspect_outfit_package(package_path, max_entries=max_entries)
    if not inspection.get("ok"):
        return {
            "ok": False,
            "schema": IMPORT_PLAN_SCHEMA,
            "preview": True,
            "error": inspection.get("error") or "Package inspection failed.",
            "inspection": inspection,
            "privacy": privacy_policy(),
        }

    source = inspection.get("source") if isinstance(inspection.get("source"), dict) else {}
    source_path = Path(str(source.get("path") or package_path)).expanduser().resolve()
    project_root = Path(project_path).expanduser().resolve() if project_path else None
    target_root = normalize_asset_folder(target_folder or default_target_folder(source_path))
    warnings = list(inspection.get("warnings") or [])
    selected_package = select_entry(inspection.get("unityPackages"), selected_unitypackage)
    selected_prefab_entry = select_entry(inspection.get("prefabCandidates"), selected_prefab)

    if selected_package:
        plan = build_unitypackage_plan(
            source_path=source_path,
            source_type=str(source.get("type") or ""),
            selected_package=selected_package,
            project_root=project_root,
            target_root=target_root,
            base_avatar_name=base_avatar_name,
            inspection=inspection,
            warnings=warnings,
        )
    elif selected_prefab_entry:
        plan = build_loose_prefab_plan(
            source_path=source_path,
            selected_prefab=selected_prefab_entry,
            project_root=project_root,
            target_root=target_root,
            base_avatar_name=base_avatar_name,
            inspection=inspection,
            warnings=warnings,
        )
    else:
        plan = build_manual_review_plan(
            source_path=source_path,
            project_root=project_root,
            target_root=target_root,
            inspection=inspection,
            warnings=warnings,
        )

    return {
        "ok": bool(plan.get("ok")),
        "schema": IMPORT_PLAN_SCHEMA,
        "preview": True,
        "plannedAt": utc_now(),
        "inspection": inspection,
        "plan": plan,
        "warnings": plan.get("warnings") or warnings,
        "privacy": privacy_policy(),
    }


def build_unitypackage_plan(
    source_path: Path,
    source_type: str,
    selected_package: dict[str, Any],
    project_root: Path | None,
    target_root: str,
    base_avatar_name: str | None,
    inspection: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    package_entry_path = str(selected_package.get("path") or "").replace("\\", "/").strip("/")
    actual_package_path = resolve_selected_unitypackage_path(source_path, source_type, package_entry_path)
    ready = actual_package_path is not None
    plan_warnings = list(warnings)
    if source_type == "zip":
        plan_warnings.append("Booth ZIP contains a UnityPackage; extract it first, then run this plan on the extracted .unitypackage.")
    expected_assets = expected_asset_paths_from_inspection(inspection)
    return {
        "id": f"outfit_import_{stable_slug(source_path.stem)}_{timestamp_compact()}",
        "kind": "unitypackage_import" if ready else "unitypackage_container_manual_extract",
        "ok": True,
        "readyToApply": ready,
        "requiresApproval": True,
        "requiresCheckpoint": True,
        "validationAfterApply": True,
        "rollbackProofRequired": True,
        "projectPath": str(project_root) if project_root else "",
        "targetFolder": target_root,
        "baseAvatarName": (base_avatar_name or "").strip(),
        "source": {
            "type": source_type,
            "path": str(source_path),
            "selectedUnityPackage": package_entry_path,
            "actualPackagePath": str(actual_package_path) if actual_package_path else "",
        },
        "selectedPrefab": first_prefab_path(inspection),
        "expectedAssetPaths": expected_assets[:500],
        "writeTarget": "vrcforge_import_outfit_package" if ready else "",
        "steps": [
            step("inspect", "read", "vrcforge_inspect_outfit_package", "Inspect package structure without reading paid asset payload bytes."),
            step("plan", "preview", "vrcforge_plan_outfit_import", "Show import plan, affected package entries, and safety requirements."),
            step("approval", "approval", "vrcforge_request_apply", "User approves the import request in VRCForge Desktop."),
            step("checkpoint", "checkpoint", "vrc_prepare_checkpoint", "Save scenes/assets and create a pre-import rollback checkpoint."),
            step("import", "write", "vrc_import_unitypackage", "Import the UnityPackage through Unity AssetDatabase.", enabled=ready),
            step("scan", "read", "vrcforge_find_assets", "Find imported prefab candidates after Unity refresh.", enabled=ready),
            step("setup", "write", "vrcforge_add_outfit", "Continue Setup Outfit and wardrobe binding as a separate supervised plan.", enabled=False),
            step("validation", "read", "vrcforge_run_validation_report", "Run validation after apply when validators are available.", enabled=ready),
            step("rollback", "write", "vrcforge_restore_checkpoint", "Rollback must remove imported assets if the user restores the checkpoint.", enabled=ready),
        ],
        "warnings": plan_warnings,
    }


def build_loose_prefab_plan(
    source_path: Path,
    selected_prefab: dict[str, Any],
    project_root: Path | None,
    target_root: str,
    base_avatar_name: str | None,
    inspection: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    ready = source_path.is_dir()
    plan_warnings = list(warnings)
    if not ready:
        plan_warnings.append("Single loose asset inputs need a folder context before VRCForge can safely copy related textures/materials.")
    expected_assets = loose_asset_paths_from_inspection(inspection)
    return {
        "id": f"outfit_import_{stable_slug(source_path.stem)}_{timestamp_compact()}",
        "kind": "loose_prefab_copy",
        "ok": True,
        "readyToApply": ready,
        "requiresApproval": True,
        "requiresCheckpoint": True,
        "validationAfterApply": True,
        "rollbackProofRequired": True,
        "projectPath": str(project_root) if project_root else "",
        "targetFolder": target_root,
        "baseAvatarName": (base_avatar_name or "").strip(),
        "source": {
            "type": "folder" if source_path.is_dir() else "file",
            "path": str(source_path),
            "selectedPrefab": str(selected_prefab.get("path") or ""),
        },
        "selectedPrefab": str(selected_prefab.get("path") or ""),
        "expectedAssetPaths": [f"{target_root}/{path}" for path in expected_assets[:500]],
        "writeTarget": "vrcforge_import_outfit_package" if ready else "",
        "steps": [
            step("inspect", "read", "vrcforge_inspect_outfit_package", "Inspect loose prefab/material/texture paths without returning file contents."),
            step("plan", "preview", "vrcforge_plan_outfit_import", "Show the copy plan and safety requirements."),
            step("approval", "approval", "vrcforge_request_apply", "User approves the copy/import request in VRCForge Desktop."),
            step("checkpoint", "checkpoint", "vrc_prepare_checkpoint", "Save scenes/assets and create a pre-import rollback checkpoint."),
            step("copy", "write", "vrcforge_import_outfit_package", f"Copy loose outfit files into {target_root}.", enabled=ready),
            step("refresh", "write", "vrc_refresh_asset_database", "Refresh Unity AssetDatabase after copied assets.", enabled=ready),
            step("setup", "write", "vrcforge_add_outfit", "Continue Setup Outfit and wardrobe binding as a separate supervised plan.", enabled=False),
            step("validation", "read", "vrcforge_run_validation_report", "Run validation after apply when validators are available.", enabled=ready),
            step("rollback", "write", "vrcforge_restore_checkpoint", "Rollback must remove copied loose assets if the user restores the checkpoint.", enabled=ready),
        ],
        "warnings": plan_warnings,
    }


def build_manual_review_plan(
    source_path: Path,
    project_root: Path | None,
    target_root: str,
    inspection: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    plan_warnings = list(warnings)
    plan_warnings.append("No importable UnityPackage or prefab candidate was found; user selection is required.")
    return {
        "id": f"outfit_import_{stable_slug(source_path.stem)}_{timestamp_compact()}",
        "kind": "manual_review",
        "ok": False,
        "readyToApply": False,
        "requiresApproval": True,
        "requiresCheckpoint": True,
        "validationAfterApply": True,
        "rollbackProofRequired": True,
        "projectPath": str(project_root) if project_root else "",
        "targetFolder": target_root,
        "source": {"path": str(source_path), "type": (inspection.get("source") or {}).get("type", "")},
        "selectedPrefab": "",
        "expectedAssetPaths": [],
        "writeTarget": "",
        "steps": [
            step("inspect", "read", "vrcforge_inspect_outfit_package", "Inspect package structure."),
            step("manual", "manual", "", "User must choose a valid UnityPackage or prefab folder before import.", enabled=False),
        ],
        "warnings": plan_warnings,
        "error": "No importable UnityPackage or prefab candidate was found.",
    }


def select_entry(entries: Any, selected_path: str | None) -> dict[str, Any] | None:
    candidates = [entry for entry in entries or [] if isinstance(entry, dict)]
    if not candidates:
        return None
    selected = (selected_path or "").replace("\\", "/").strip("/")
    if selected:
        for entry in candidates:
            if str(entry.get("path") or "").replace("\\", "/").strip("/") == selected:
                return entry
        return None
    return candidates[0]


def resolve_selected_unitypackage_path(source_path: Path, source_type: str, selected_package: str) -> Path | None:
    if source_type == "unitypackage":
        return source_path if source_path.is_file() else None
    if source_type == "folder":
        candidate = (source_path / selected_package).resolve()
        try:
            candidate.relative_to(source_path)
        except ValueError:
            return None
        return candidate if candidate.is_file() and candidate.suffix.lower() == ".unitypackage" else None
    return None


def expected_asset_paths_from_inspection(inspection: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for package in inspection.get("unityPackages") or []:
        if not isinstance(package, dict):
            continue
        for item in package.get("pathnames") or []:
            if isinstance(item, dict):
                path = str(item.get("path") or "").replace("\\", "/").strip("/")
                if path.startswith("Assets/"):
                    paths.append(path)
    if paths:
        return sorted(dict.fromkeys(paths), key=str.lower)
    return loose_asset_paths_from_inspection(inspection)


def loose_asset_paths_from_inspection(inspection: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("prefabCandidates", "textures", "materials", "models"):
        for item in inspection.get(key) or []:
            if isinstance(item, dict):
                path = str(item.get("path") or "").replace("\\", "/").strip("/")
                if path:
                    paths.append(path)
    return sorted(dict.fromkeys(paths), key=str.lower)


def first_prefab_path(inspection: dict[str, Any]) -> str:
    for item in inspection.get("prefabCandidates") or []:
        if isinstance(item, dict):
            return str(item.get("path") or "")
    for path in expected_asset_paths_from_inspection(inspection):
        if path.lower().endswith(".prefab"):
            return path
    return ""


def step(step_id: str, category: str, tool: str, description: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": step_id,
        "category": category,
        "tool": tool,
        "description": description,
        "enabled": enabled,
    }


def default_target_folder(source_path: Path) -> str:
    return f"{DEFAULT_TARGET_ROOT}/{stable_slug(source_path.stem) or 'outfit'}"


def normalize_asset_folder(value: str) -> str:
    normalized = (value or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        normalized = DEFAULT_TARGET_ROOT
    if not normalized.startswith("Assets/"):
        normalized = f"{DEFAULT_TARGET_ROOT}/{stable_slug(normalized)}"
    return normalized.rstrip("/")


def stable_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-._")
    return slug[:80] or "outfit"


def timestamp_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def privacy_policy() -> dict[str, Any]:
    return {
        "localOnly": True,
        "readsAssetBinaryContentsDuringPlan": False,
        "uploadsPaidAssets": False,
        "modelContextReceivesStructuralSummaryOnly": True,
        "writeRequiresApprovalCheckpointValidationRollback": True,
    }
