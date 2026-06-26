from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from outfit_package_inspector import inspect_outfit_package


IMPORT_PLAN_SCHEMA = "vrcforge.outfit_import_plan.v1"
DEFAULT_TARGET_ROOT = "Assets/VRCForge/ImportedOutfits"
IMPORTABLE_COMPANION_EXTENSIONS = {".unitypackage", ".zip"}
SUPPORT_PACKAGE_PATTERNS = [
    r"material(?:s)?",
    r"mat(?:erial)?[_ -]?pack",
    r"matetial[_ -]?pack",
    r"texture(?:s)?",
    r"\btex(?:ture)?[_ -]?pack\b",
    r"\bcommon[_ -]?material\b",
    r"\bfirst[_ -]?import\b",
    r"\bshader(?:s)?\b",
    r"lil[_ -]?toon",
    r"poiyomi",
    r"arktoon",
    r"\buts2?\b",
    r"unlitwf",
    r"wf[_ -]?shader",
    r"\b\u5171\u901a\u30de\u30c6\u30ea\u30a2\u30eb\b",
]
NON_IMPORT_SUPPORT_PATTERNS = [r"\bpsd\b", r"\bphotoshop\b"]

# Built-in defaults. These are a *heuristic* name-matching seed for common public
# avatar bases, not an allow-list: any VRChat-standard avatar still imports. The
# active table is resolved through avatar_compatibility_aliases(), which merges
# these defaults with an optional user/community override file so new avatar bases
# can be added without editing code. See AVATAR_ALIAS_OVERRIDE_ENV below.
BUILTIN_AVATAR_COMPATIBILITY_ALIASES: dict[str, list[str]] = {
    "milltina": ["milltina", "miltina", "\u30df\u30eb\u30c6\u30a3\u30ca"],
    "manuka": ["manuka", "\u30de\u30cc\u30ab"],
    "sapphy": ["sapphy", "\u30b5\u30d5\u30a3\u30fc", "\u590f\u83f2"],
    "shinano": ["shinano", "\u3057\u306a\u306e"],
    "kikyo": ["kikyo", "\u6843"],
    "moe": ["moe", "\u840c"],
    "selestia": ["selestia"],
    "airi": ["airi"],
    "lime": ["lime"],
    "chiffon": ["chiffon"],
    "chocolat": ["chocolat", "\u30b7\u30e7\u30b3\u30e9"],
    "sio": ["sio"],
    "rurune": ["rurune"],
    "mizuki": ["mizuki"],
    "las yusha": ["lasyusha", "las yusha"],
    "karin": ["karin"],
    "mamehinata": ["mamehinata"],
}

# Optional override file: a JSON object mapping a canonical avatar key to a list of
# name aliases, either flat ({"myavatar": ["my avatar", "..."]}) or wrapped
# ({"avatars": {...}}). User/community entries are merged on top of the builtin
# defaults, so the table is data-driven and extensible without a code change.
AVATAR_ALIAS_OVERRIDE_ENV = "VRCFORGE_AVATAR_ALIAS_PATH"

# Backward-compatible name for any external importer; the live logic uses the
# merged result from avatar_compatibility_aliases().
AVATAR_COMPATIBILITY_ALIASES: dict[str, list[str]] = BUILTIN_AVATAR_COMPATIBILITY_ALIASES

_AVATAR_ALIAS_CACHE: dict[str, list[str]] | None = None
_AVATAR_ALIAS_CACHE_KEY: tuple[str, float] | None = None


def _coerce_alias_overrides(raw: Any) -> dict[str, list[str]]:
    """Normalize an override document into {canonical: [aliases]}; ignore junk."""
    if isinstance(raw, dict) and isinstance(raw.get("avatars"), dict):
        raw = raw["avatars"]
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, list[str]] = {}
    for key, value in raw.items():
        canonical = str(key or "").strip().casefold()
        if not canonical:
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            continue
        aliases = [str(item).strip() for item in value if str(item or "").strip()]
        # Always let the canonical key itself match as an alias.
        if canonical not in [a.casefold() for a in aliases]:
            aliases.append(canonical)
        if aliases:
            overrides[canonical] = aliases
    return overrides


def load_avatar_alias_overrides(path: str | os.PathLike[str] | None = None) -> dict[str, list[str]]:
    """Read the optional override file. Never raises: malformed/missing -> {}."""
    candidate = str(path or os.environ.get(AVATAR_ALIAS_OVERRIDE_ENV, "") or "").strip()
    if not candidate:
        return {}
    file_path = Path(candidate)
    if not file_path.is_file():
        return {}
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return _coerce_alias_overrides(raw)


def avatar_compatibility_aliases() -> dict[str, list[str]]:
    """Builtin defaults merged with the optional override file (cached by path+mtime)."""
    global _AVATAR_ALIAS_CACHE, _AVATAR_ALIAS_CACHE_KEY
    candidate = str(os.environ.get(AVATAR_ALIAS_OVERRIDE_ENV, "") or "").strip()
    mtime = 0.0
    if candidate:
        try:
            mtime = Path(candidate).stat().st_mtime
        except OSError:
            mtime = 0.0
    cache_key = (candidate, mtime)
    if _AVATAR_ALIAS_CACHE is not None and _AVATAR_ALIAS_CACHE_KEY == cache_key:
        return _AVATAR_ALIAS_CACHE
    merged: dict[str, list[str]] = {key: list(value) for key, value in BUILTIN_AVATAR_COMPATIBILITY_ALIASES.items()}
    for canonical, aliases in load_avatar_alias_overrides(candidate).items():
        existing = merged.get(canonical, [])
        merged[canonical] = list(dict.fromkeys([*existing, *aliases]))
    _AVATAR_ALIAS_CACHE = merged
    _AVATAR_ALIAS_CACHE_KEY = cache_key
    return merged


DEPENDENCY_RULES: list[dict[str, Any]] = [
    {
        "id": "liltoon",
        "label": "lilToon",
        "kind": "shader",
        "packageIds": ["jp.lilxyzw.liltoon"],
        "assetFolders": ["Assets/lilToon", "Assets/_lilToon"],
        "hintPatterns": [r"\blil\s*toon\b", r"\bliltoon\b", r"lilToon"],
        "bundledPatterns": [r"(^|/)Assets/(?:_)?lilToon(?:/|$)", r"(^|/)Packages/jp\.lilxyzw\.liltoon(?:/|$)"],
        "stage": "before_import",
    },
    {
        "id": "poiyomi",
        "label": "Poiyomi",
        "kind": "shader",
        "packageIds": [],
        "assetFolders": ["Assets/_PoiyomiShaders", "Assets/Poiyomi", "Assets/_Poiyomi Toon Shader"],
        "hintPatterns": [r"\bpoiyomi\b", r"\bpoiyomi\s*toon\b", r"(^|/)poi[_ -]?"],
        "bundledPatterns": [r"(^|/)Assets/(?:_)?Poiyomi", r"(^|/)Assets/_PoiyomiShaders(?:/|$)"],
        "stage": "before_import",
    },
    {
        "id": "modular_avatar",
        "label": "Modular Avatar",
        "kind": "addon",
        "packageIds": ["nadena.dev.modular-avatar"],
        "assetFolders": ["Packages/nadena.dev.modular-avatar", "Assets/ModularAvatar", "Assets/Modular Avatar"],
        "hintPatterns": [r"\bmodular\s*avatar\b", r"\bmodularavatar\b", r"\bnadena\.dev\.modular-avatar\b"],
        "bundledPatterns": [r"(^|/)Packages/nadena\.dev\.modular-avatar(?:/|$)", r"(^|/)Assets/Modular ?Avatar(?:/|$)"],
        "stage": "before_import",
    },
    {
        "id": "vrcfury",
        "label": "VRCFury",
        "kind": "addon",
        "packageIds": ["com.vrcfury.vrcfury"],
        "assetFolders": ["Packages/com.vrcfury.vrcfury", "Assets/VRCFury"],
        "hintPatterns": [r"\bvrcfury\b", r"\bcom\.vrcfury\.vrcfury\b"],
        "bundledPatterns": [r"(^|/)Packages/com\.vrcfury\.vrcfury(?:/|$)", r"(^|/)Assets/VRCFury(?:/|$)"],
        "stage": "before_import",
    },
]


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
    explicit_selected_package = bool((selected_unitypackage or "").strip())
    selected_package = select_entry(inspection.get("unityPackages"), selected_unitypackage)
    selected_prefab_entry = select_entry(inspection.get("prefabCandidates"), selected_prefab)
    source_type = str(source.get("type") or "")
    selected_package_path = (
        resolve_selected_unitypackage_path(source_path, source_type, str(selected_package.get("path") or ""))
        if selected_package
        else None
    )
    effective_base_avatar_name = (base_avatar_name or "").strip() or infer_avatar_name_from_project(project_root)
    package_order_preflight = build_package_order_preflight(
        inspection=inspection,
        source_path=source_path,
        selected_package=selected_package,
        selected_package_path=selected_package_path,
        project_root=project_root,
        base_avatar_name=effective_base_avatar_name,
        explicit_selected_package=explicit_selected_package,
    )
    compatibility_preflight = build_avatar_compatibility_preflight(
        inspection=inspection,
        source_path=source_path,
        selected_package=selected_package,
        base_avatar_name=effective_base_avatar_name,
    )
    dependency_preflight = build_dependency_preflight(
        inspection,
        project_root,
        source_path,
        package_order_preflight=package_order_preflight,
        compatibility_preflight=compatibility_preflight,
    )
    warnings.extend(dependency_preflight.get("warnings") or [])

    if selected_package:
        plan = build_unitypackage_plan(
            source_path=source_path,
            source_type=source_type,
            selected_package=selected_package,
            project_root=project_root,
            target_root=target_root,
            base_avatar_name=effective_base_avatar_name,
            inspection=inspection,
            warnings=warnings,
            dependency_preflight=dependency_preflight,
        )
    elif selected_prefab_entry:
        plan = build_loose_prefab_plan(
            source_path=source_path,
            selected_prefab=selected_prefab_entry,
            project_root=project_root,
            target_root=target_root,
            base_avatar_name=effective_base_avatar_name,
            inspection=inspection,
            warnings=warnings,
            dependency_preflight=dependency_preflight,
        )
    else:
        plan = build_manual_review_plan(
            source_path=source_path,
            project_root=project_root,
            target_root=target_root,
            inspection=inspection,
            warnings=warnings,
            dependency_preflight=dependency_preflight,
        )

    return {
        "ok": bool(plan.get("ok")),
        "schema": IMPORT_PLAN_SCHEMA,
        "preview": True,
        "plannedAt": utc_now(),
        "inspection": inspection,
        "dependencyPreflight": dependency_preflight,
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
    dependency_preflight: dict[str, Any],
) -> dict[str, Any]:
    package_entry_path = str(selected_package.get("path") or "").replace("\\", "/").strip("/")
    actual_package_path = resolve_selected_unitypackage_path(source_path, source_type, package_entry_path)
    package_order = dependency_preflight.get("packageOrder") if isinstance(dependency_preflight.get("packageOrder"), dict) else {}
    import_queue = package_order.get("importQueue") if isinstance(package_order.get("importQueue"), list) else []
    queue_can_apply = bool(import_queue) and not bool(package_order.get("requiresManualExtract"))
    needs_extract = actual_package_path is None and not queue_can_apply
    ready = not needs_extract and bool(dependency_preflight.get("readyForImport"))
    plan_warnings = list(warnings)
    expected_assets = expected_asset_paths_from_inspection(inspection)
    return {
        "id": f"outfit_import_{stable_slug(source_path.stem)}_{timestamp_compact()}",
        "kind": "unitypackage_container_manual_extract"
        if needs_extract
        else "unitypackage_import_sequence"
        if len(import_queue) > 1 or source_type == "zip"
        else "unitypackage_import",
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
            "importQueue": import_queue,
        },
        "selectedPrefab": first_prefab_path(inspection),
        "expectedAssetPaths": expected_assets[:500],
        "dependencyPreflight": dependency_preflight,
        "writeTarget": "vrcforge_import_outfit_package" if ready else "",
        "steps": [
            step("inspect", "read", "vrcforge_inspect_outfit_package", "Inspect package structure without reading paid asset payload bytes."),
            step("dependency_preflight", "read", "vrcforge_plan_outfit_import", "Check shader/addon dependency hints before importing the outfit package."),
            step(
                "dependency_repair",
                "manual",
                "",
                "Install missing shader/addon dependencies before importing this package.",
                enabled=not bool(dependency_preflight.get("readyForImport")),
            ),
            step("plan", "preview", "vrcforge_plan_outfit_import", "Show import plan, affected package entries, and safety requirements."),
            step("approval", "approval", "vrcforge_request_apply", "User approves the import request in VRCForge Desktop."),
            step("checkpoint", "checkpoint", "vrc_prepare_checkpoint", "Save scenes/assets and create a pre-import rollback checkpoint."),
            step("import", "write", "vrc_import_unitypackage", "Import the UnityPackage queue through Unity AssetDatabase in dependency order.", enabled=ready),
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
    dependency_preflight: dict[str, Any],
) -> dict[str, Any]:
    ready = source_path.is_dir() and bool(dependency_preflight.get("readyForImport"))
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
        "dependencyPreflight": dependency_preflight,
        "writeTarget": "vrcforge_import_outfit_package" if ready else "",
        "steps": [
            step("inspect", "read", "vrcforge_inspect_outfit_package", "Inspect loose prefab/material/texture paths without returning file contents."),
            step("dependency_preflight", "read", "vrcforge_plan_outfit_import", "Check shader/addon dependency hints before copying loose outfit assets."),
            step(
                "dependency_repair",
                "manual",
                "",
                "Install missing shader/addon dependencies before copying this outfit.",
                enabled=not bool(dependency_preflight.get("readyForImport")),
            ),
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
    dependency_preflight: dict[str, Any],
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
        "dependencyPreflight": dependency_preflight,
        "writeTarget": "",
        "steps": [
            step("inspect", "read", "vrcforge_inspect_outfit_package", "Inspect package structure."),
            step("manual", "manual", "", "User must choose a valid UnityPackage or prefab folder before import.", enabled=False),
        ],
        "warnings": plan_warnings,
        "error": "No importable UnityPackage or prefab candidate was found.",
    }


def build_dependency_preflight(
    inspection: dict[str, Any],
    project_root: Path | None,
    source_path: Path,
    package_order_preflight: dict[str, Any] | None = None,
    compatibility_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package_paths = dependency_scan_paths(inspection)
    source_tokens = [source_path.name, str((inspection.get("source") or {}).get("name") or "")]
    manifest_dependencies = read_project_manifest_dependencies(project_root)
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []

    for rule in DEPENDENCY_RULES:
        label = str(rule["label"])
        package_ids = [str(item) for item in rule.get("packageIds") or []]
        bundled_evidence = matching_evidence(package_paths, [str(item) for item in rule.get("bundledPatterns") or []])
        hint_evidence = matching_evidence([*package_paths, *source_tokens], [str(item) for item in rule.get("hintPatterns") or []])
        project_evidence = project_dependency_evidence(project_root, manifest_dependencies, rule)

        detected = bool(bundled_evidence or hint_evidence or project_evidence)
        if not detected:
            status = "not_detected"
            message = f"{label} was not detected from package pathnames."
            blocking = False
        elif bundled_evidence:
            status = "bundled"
            message = f"{label} appears to be bundled in the package."
            blocking = False
        elif project_evidence:
            status = "installed"
            message = f"{label} appears to be installed in the Unity project."
            blocking = False
        else:
            status = "missing"
            message = f"{label} is referenced by package pathnames but was not detected in the Unity project."
            blocking = str(rule.get("stage") or "") == "before_import"
            warnings.append(f"Install {label} before importing this outfit package.")

        entries.append(
            {
                "id": str(rule["id"]),
                "label": label,
                "kind": str(rule.get("kind") or "dependency"),
                "status": status,
                "message": message,
                "blockingBeforeImport": blocking,
                "stage": str(rule.get("stage") or "before_import"),
                "packageIds": package_ids,
                "evidence": {
                    "packagePathnames": bundled_evidence[:10],
                    "hints": hint_evidence[:10],
                    "project": project_evidence[:10],
                },
            }
        )

    package_order_preflight = package_order_preflight or {}
    compatibility_preflight = compatibility_preflight or {}
    warnings.extend(str(item) for item in package_order_preflight.get("warnings") or [])
    warnings.extend(str(item) for item in compatibility_preflight.get("warnings") or [])
    blocking_entries = [entry for entry in entries if entry.get("blockingBeforeImport") and entry.get("status") == "missing"]
    blocking_issue_count = len(blocking_entries)
    if package_order_preflight.get("blockingBeforeImport"):
        blocking_issue_count += 1
    if compatibility_preflight.get("blockingBeforeImport"):
        blocking_issue_count += 1
    detected_entries = [entry for entry in entries if entry.get("status") != "not_detected"]
    return {
        "schema": "vrcforge.outfit_dependency_preflight.v1",
        "checkedAt": utc_now(),
        "projectManifestChecked": bool(project_root),
        "readsAssetBinaryContents": False,
        "readyForImport": blocking_issue_count == 0,
        "blockingMissingCount": len(blocking_entries),
        "blockingIssueCount": blocking_issue_count,
        "detectedCount": len(detected_entries),
        "entries": entries,
        "packageOrder": package_order_preflight,
        "compatibility": compatibility_preflight,
        "warnings": warnings,
        "recommendedOrder": [
            "inspect_package_pathnames",
            "verify_shader_and_addon_dependencies",
            "verify_base_avatar_compatibility",
            "import_required_material_or_texture_packages_first",
            "install_missing_dependencies_with_user_confirmation",
            "import_unitypackage_or_loose_prefab",
            "run_validation",
            "rollback_if_needed",
        ],
    }


def dependency_scan_paths(inspection: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for package in inspection.get("unityPackages") or []:
        if isinstance(package, dict):
            path = str(package.get("path") or "").replace("\\", "/").strip("/")
            if path:
                paths.append(path)
            for item in package.get("pathnames") or []:
                if isinstance(item, dict):
                    nested = str(item.get("path") or "").replace("\\", "/").strip("/")
                    if nested:
                        paths.append(nested)
    for key in ("prefabCandidates", "textures", "materials", "models"):
        for item in inspection.get(key) or []:
            if isinstance(item, dict):
                path = str(item.get("path") or "").replace("\\", "/").strip("/")
                if path:
                    paths.append(path)
    return sorted(dict.fromkeys(paths), key=str.lower)


def build_package_order_preflight(
    inspection: dict[str, Any],
    source_path: Path,
    selected_package: dict[str, Any] | None,
    selected_package_path: Path | None,
    project_root: Path | None = None,
    base_avatar_name: str | None = None,
    explicit_selected_package: bool = False,
) -> dict[str, Any]:
    source = inspection.get("source") if isinstance(inspection.get("source"), dict) else {}
    source_type = str(source.get("type") or "")
    selected_rel = str((selected_package or {}).get("path") or "").replace("\\", "/").strip("/")
    import_queue: list[dict[str, Any]] = []
    warnings: list[str] = []

    if source_type == "unitypackage":
        import_queue.extend(companion_support_queue(source_path))
        import_queue.append(import_queue_item(source_path.name, "direct", "target", "Selected UnityPackage.", actual_path=source_path, selected=True))
    elif source_type == "folder":
        for entry in inspection.get("unityPackages") or []:
            if not isinstance(entry, dict):
                continue
            rel_path = str(entry.get("path") or "").replace("\\", "/").strip("/")
            if not rel_path:
                continue
            selected = bool(explicit_selected_package and selected_rel and rel_path == selected_rel)
            role = classify_package_role(rel_path, selected=selected)
            actual_path = (source_path / rel_path).resolve()
            import_queue.append(import_queue_item(rel_path, "folder", role, package_role_reason(role), actual_path=actual_path, selected=selected))
        if explicit_selected_package and selected_rel:
            import_queue = [item for item in import_queue if item.get("selected") or item.get("role") == "support"]
    elif source_type == "zip":
        import_queue.extend(companion_support_queue(source_path))
        for entry in inspection.get("unityPackages") or []:
            if not isinstance(entry, dict):
                continue
            rel_path = str(entry.get("path") or "").replace("\\", "/").strip("/")
            if not rel_path:
                continue
            selected = bool(explicit_selected_package and selected_rel and rel_path == selected_rel)
            role = classify_package_role(rel_path, selected=selected)
            import_queue.append(
                import_queue_item(
                    rel_path,
                    "zip",
                    role,
                    package_role_reason(role),
                    container_path=source_path,
                    selected=selected,
                )
            )
        if explicit_selected_package and selected_rel:
            import_queue = [item for item in import_queue if item.get("selected") or item.get("role") == "support"]
    elif selected_package_path is not None:
        import_queue.append(import_queue_item(selected_package_path.name, "direct", "target", "Selected UnityPackage.", actual_path=selected_package_path, selected=True))

    ordered, avatar_skipped = filter_queue_for_base_avatar(order_import_queue(import_queue), base_avatar_name)
    ordered, dependency_skipped = filter_installed_dependency_packages(ordered, project_root)
    for index, item in enumerate(ordered, start=1):
        item["order"] = index
    support_count = sum(1 for item in ordered if item.get("role") == "support")
    if support_count:
        warnings.append(f"Import {support_count} material/texture/shader support package(s) before the outfit package.")
    if source_type == "zip" and ordered:
        warnings.append("VRCForge will extract nested UnityPackage files to a local temp folder after approval, then import them in order.")
    if avatar_skipped:
        warnings.append(f"Skipped {len(avatar_skipped)} package(s) that appear to target a different avatar.")
    if dependency_skipped:
        labels = sorted({str(item.get("dependencyLabel") or "dependency") for item in dependency_skipped}, key=str.lower)
        warnings.append(f"Skipped {len(dependency_skipped)} already-installed dependency package(s): {', '.join(labels)}.")
    return {
        "schema": "vrcforge.outfit_package_order_preflight.v1",
        "sourceType": source_type,
        "selectedUnityPackage": selected_rel,
        "importQueue": ordered,
        "skippedPackages": avatar_skipped,
        "skippedInstalledSupportPackages": dependency_skipped,
        "skippedInstalledSupportCount": len(dependency_skipped),
        "importCount": len(ordered),
        "supportPackageCount": support_count,
        "requiresManualExtract": source_type == "zip" and not ordered,
        "blockingBeforeImport": source_type == "zip" and not ordered,
        "warnings": warnings,
    }


def companion_support_queue(source_path: Path) -> list[dict[str, Any]]:
    if not source_path.is_file():
        return []
    parent = source_path.parent
    selected_key = source_path.resolve().as_posix().casefold()
    items: list[dict[str, Any]] = []
    try:
        candidates = sorted(parent.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []
    for candidate in candidates:
        if not candidate.is_file() or candidate.resolve().as_posix().casefold() == selected_key:
            continue
        suffix = candidate.suffix.lower()
        if suffix not in IMPORTABLE_COMPANION_EXTENSIONS:
            continue
        if classify_package_role(candidate.name) != "support":
            continue
        if suffix == ".unitypackage":
            items.append(import_queue_item(candidate.name, "direct", "support", "Sibling support UnityPackage should be imported first.", actual_path=candidate))
        elif suffix == ".zip":
            nested = nested_unitypackage_entries(candidate)
            if nested:
                for rel_path in nested:
                    role = classify_package_role(rel_path)
                    items.append(
                        import_queue_item(
                            rel_path,
                            "zip",
                            "support" if role != "target" else role,
                            "Sibling support ZIP contains UnityPackage files that should be imported first.",
                            container_path=candidate,
                        )
                    )
    return items[:20]


def nested_unitypackage_entries(zip_path: Path) -> list[str]:
    try:
        import zipfile

        with zipfile.ZipFile(zip_path) as archive:
            names = [name.replace("\\", "/").strip("/") for name in archive.namelist() if name.lower().endswith(".unitypackage")]
    except Exception:
        return []
    return sorted(dict.fromkeys(name for name in names if name), key=str.lower)


def import_queue_item(
    path: str,
    source_type: str,
    role: str,
    reason: str,
    actual_path: Path | None = None,
    container_path: Path | None = None,
    selected: bool = False,
) -> dict[str, Any]:
    item = {
        "path": str(path).replace("\\", "/").strip("/"),
        "sourceType": source_type,
        "role": role,
        "reason": reason,
        "selected": selected,
    }
    if actual_path is not None:
        item["actualPackagePath"] = str(actual_path)
    if container_path is not None:
        item["containerPath"] = str(container_path)
    return item


def classify_package_role(path: str, selected: bool = False) -> str:
    normalized = path.replace("\\", "/")
    if selected:
        return "target"
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in SUPPORT_PACKAGE_PATTERNS) and not any(
        re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in NON_IMPORT_SUPPORT_PATTERNS
    ):
        return "support"
    return "target"


def package_role_reason(role: str) -> str:
    if role == "support":
        return "Material, texture, shader, or common support package should be imported before the outfit package."
    if role == "target":
        return "Avatar/outfit package is imported after support packages."
    return "Package order was inferred from file names."


def order_import_queue(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = "|".join(
            [
                str(item.get("sourceType") or ""),
                str(item.get("containerPath") or item.get("actualPackagePath") or ""),
                str(item.get("path") or ""),
            ]
        ).casefold()
        if key and key not in unique:
            unique[key] = dict(item)
    return sorted(
        unique.values(),
        key=lambda item: (
            0 if item.get("role") == "support" else 1,
            0 if item.get("selected") else 1,
            str(item.get("path") or "").casefold(),
        ),
    )


def filter_queue_for_base_avatar(items: list[dict[str, Any]], base_avatar_name: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base = canonical_avatar_name(base_avatar_name or "")
    if not base:
        return items, []
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        if item.get("role") == "support" or item.get("selected"):
            kept.append(item)
            continue
        detected = detect_avatar_aliases([str(item.get("path") or ""), str(item.get("actualPackagePath") or ""), str(item.get("containerPath") or "")])
        if detected and base not in detected:
            skipped_item = dict(item)
            skipped_item["detectedAvatarNames"] = sorted(detected)
            skipped.append(skipped_item)
            continue
        kept.append(item)
    return kept, skipped


def filter_installed_dependency_packages(items: list[dict[str, Any]], project_root: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if project_root is None:
        return items, []
    manifest_dependencies = read_project_manifest_dependencies(project_root)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        installed_rule = installed_dependency_rule_for_queue_item(item, project_root, manifest_dependencies)
        if not installed_rule:
            kept.append(item)
            continue
        skipped_item = dict(item)
        skipped_item["skipReason"] = "already_installed_dependency"
        skipped_item["dependencyId"] = str(installed_rule.get("id") or "")
        skipped_item["dependencyLabel"] = str(installed_rule.get("label") or "dependency")
        skipped_item["message"] = f"Skipped because {skipped_item['dependencyLabel']} is already installed in the Unity project."
        skipped.append(skipped_item)
    return kept, skipped


def installed_dependency_rule_for_queue_item(
    item: dict[str, Any],
    project_root: Path | None,
    manifest_dependencies: dict[str, Any],
) -> dict[str, Any] | None:
    if item.get("role") != "support":
        return None
    values = [
        str(item.get("path") or ""),
        str(item.get("actualPackagePath") or ""),
        str(item.get("containerPath") or ""),
    ]
    for rule in DEPENDENCY_RULES:
        dependency_package_hint = matching_evidence(values, [str(pattern) for pattern in rule.get("hintPatterns") or []])
        dependency_package_hint.extend(matching_evidence(values, [re.escape(str(package_id)) for package_id in rule.get("packageIds") or []]))
        if not dependency_package_hint:
            continue
        if project_dependency_evidence(project_root, manifest_dependencies, rule):
            return rule
    return None


def build_avatar_compatibility_preflight(
    inspection: dict[str, Any],
    source_path: Path,
    selected_package: dict[str, Any] | None,
    base_avatar_name: str | None,
) -> dict[str, Any]:
    evidence_values = [source_path.name, str((inspection.get("source") or {}).get("name") or "")]
    if selected_package:
        evidence_values.append(str(selected_package.get("path") or ""))
    evidence_values.extend(dependency_scan_paths(inspection))
    detected = detect_avatar_aliases(evidence_values)
    base_canonical = canonical_avatar_name(base_avatar_name or "")
    warnings: list[str] = []
    blocking = False

    if detected and base_canonical and base_canonical in detected:
        status = "matched"
        message = f"Package appears compatible with {base_avatar_name}."
    elif detected and base_canonical and base_canonical not in detected:
        status = "mismatch"
        message = f"Package appears to target {', '.join(sorted(detected))}, not {base_avatar_name}."
        blocking = True
        warnings.append(message)
    elif detected and not base_canonical:
        status = "needs_confirmation"
        message = f"Package target model appears to be {', '.join(sorted(detected))}; select the matching avatar before import."
        warnings.append(message)
    else:
        status = "unknown"
        message = "Package target avatar could not be inferred from pathnames."
        warnings.append("Confirm this package supports the selected avatar before running Setup Outfit.")

    return {
        "schema": "vrcforge.outfit_avatar_compatibility.v1",
        "status": status,
        "baseAvatarName": (base_avatar_name or "").strip(),
        "detectedAvatarNames": sorted(detected),
        "blockingBeforeImport": blocking,
        "message": message,
        "evidence": avatar_alias_evidence(evidence_values, detected),
        "warnings": warnings,
    }


def canonical_avatar_name(value: str) -> str:
    normalized = normalize_avatar_token(value)
    for canonical, aliases in avatar_compatibility_aliases().items():
        if any(normalize_avatar_token(alias) and normalize_avatar_token(alias) in normalized for alias in aliases):
            return canonical
    return normalized


def detect_avatar_aliases(values: list[str]) -> set[str]:
    detected: set[str] = set()
    for canonical, aliases in avatar_compatibility_aliases().items():
        for value in values:
            if avatar_alias_matches(value, aliases):
                detected.add(canonical)
                break
    return detected


def avatar_alias_evidence(values: list[str], detected: set[str]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}
    aliases_table = avatar_compatibility_aliases()
    for canonical in detected:
        aliases = aliases_table.get(canonical, [])
        matches = [value for value in values if avatar_alias_matches(value, aliases)]
        evidence[canonical] = sorted(dict.fromkeys(matches), key=str.lower)[:10]
    return evidence


def avatar_alias_matches(value: str, aliases: list[str]) -> bool:
    raw = (value or "").casefold()
    normalized = normalize_avatar_token(raw)
    if not raw and not normalized:
        return False
    for alias in aliases:
        alias_value = (alias or "").casefold()
        alias_normalized = normalize_avatar_token(alias_value)
        if not alias_normalized:
            continue
        if re.fullmatch(r"[a-z0-9]+", alias_normalized):
            if re.search(rf"(?<![a-z0-9]){re.escape(alias_normalized)}(?![a-z0-9])", raw):
                return True
            continue
        if alias_normalized in normalized:
            return True
    return False


def normalize_avatar_token(value: str) -> str:
    return re.sub(r"[\s_\-().\[\]{}]+", "", (value or "").casefold())


POST_IMPORT_VALIDATION_SCHEMA = "vrcforge.outfit_post_import_validation.v1"
# Unity renders a material magenta/pink when its shader reference is missing or the
# shader failed to compile (it falls back to Hidden/InternalErrorShader). These are
# the post-import signals that the outfit's shader/material support was not imported
# before the prefab.
ERROR_SHADER_TOKENS = ("hidden/internalerrorshader", "internalerror", "internal-error")


def _material_lookup(material: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in material:
            text = str(material.get(name) or "").strip()
            if text:
                return text
    return ""


def material_shader_is_magenta(shader_name: str) -> bool:
    """True when a shader name indicates a missing/error (magenta) material."""
    name = (shader_name or "").strip().casefold()
    if not name:
        return True
    return any(token in name for token in ERROR_SHADER_TOKENS)


def detect_magenta_materials(inventory: Any) -> list[dict[str, Any]]:
    """Flag materials whose shader is missing or the Unity internal error shader."""
    materials = inventory.get("materials") if isinstance(inventory, dict) else None
    if not isinstance(materials, list):
        materials = []
    flagged: list[dict[str, Any]] = []
    for material in materials:
        if not isinstance(material, dict):
            continue
        shader_name = _material_lookup(material, "shader_name", "shaderName")
        if not material_shader_is_magenta(shader_name):
            continue
        flagged.append(
            {
                "materialId": _material_lookup(material, "material_id", "materialId"),
                "materialName": _material_lookup(material, "material_name", "materialName"),
                "rendererPath": _material_lookup(material, "renderer_path", "rendererPath", "item_path"),
                "meshName": _material_lookup(material, "mesh_name", "meshName"),
                "slotIndex": material.get("slot_index", material.get("slotIndex", 0)),
                "shaderName": shader_name,
                "reason": "missing_shader_reference" if not shader_name else "internal_error_shader",
            }
        )
    return flagged


def build_post_import_outfit_validation(
    inventory: Any,
    base_avatar_name: str | None = None,
) -> dict[str, Any]:
    """Post-import check: did the outfit land with broken (magenta) shaders?

    This is the validation that was missing before — the planner only warned
    *before* import. After import, a magenta material almost always means the
    shader/material support package was not imported before the outfit prefab.
    """
    magenta = detect_magenta_materials(inventory)
    affected_renderers = sorted({item["rendererPath"] for item in magenta if item.get("rendererPath")})
    status = "ok" if not magenta else "magenta_detected"
    if magenta:
        message = (
            f"{len(magenta)} material(s) imported with a missing or error shader "
            "(they render magenta/pink in Unity)."
        )
        remediation = [
            "Import the shader package (lilToon / Poiyomi / etc.) the outfit needs first.",
            "Import the outfit's material/texture support package BEFORE the clothing prefab.",
            "Re-import the outfit prefab so its materials rebind to the now-present shader.",
            "Re-run this validation; magenta count should drop to zero.",
        ]
    else:
        message = "No magenta/missing-shader materials were detected after import."
        remediation = []
    return {
        "schema": POST_IMPORT_VALIDATION_SCHEMA,
        "status": status,
        "baseAvatarName": (base_avatar_name or "").strip(),
        "magentaCount": len(magenta),
        "magentaMaterials": magenta[:50],
        "affectedRenderers": affected_renderers[:50],
        # Advisory by default at the planner layer; the dashboard validation layer
        # raises it to a blocking Error finding so a broken outfit cannot pass quietly.
        "blocking": bool(magenta),
        "message": message,
        "remediation": remediation,
    }


def infer_avatar_name_from_project(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    return canonical_avatar_name(project_root.name)


def read_project_manifest_dependencies(project_root: Path | None) -> dict[str, Any]:
    if project_root is None:
        return {}
    manifest_path = project_root / "Packages" / "manifest.json"
    try:
        import json

        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else {}
    return dependencies if isinstance(dependencies, dict) else {}


def matching_evidence(values: list[str], patterns: list[str]) -> list[str]:
    evidence: list[str] = []
    for value in values:
        normalized = value.replace("\\", "/")
        for pattern in patterns:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                evidence.append(value)
                break
    return sorted(dict.fromkeys(evidence), key=str.lower)


def project_dependency_evidence(project_root: Path | None, manifest_dependencies: dict[str, Any], rule: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    lowered_manifest = {str(key).casefold(): value for key, value in manifest_dependencies.items()}
    for package_id in rule.get("packageIds") or []:
        value = lowered_manifest.get(str(package_id).casefold())
        if value is not None:
            evidence.append(f"Packages/manifest.json:{package_id}={value}")
    if project_root is not None:
        for package_id in rule.get("packageIds") or []:
            package_folder = project_root / "Packages" / str(package_id)
            if package_folder.exists():
                evidence.append(f"Packages/{package_id}")
        for folder in rule.get("assetFolders") or []:
            candidate = project_root / str(folder).replace("/", "\\")
            if candidate.exists():
                evidence.append(str(folder).replace("\\", "/"))
    return sorted(dict.fromkeys(evidence), key=str.lower)


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
