from __future__ import annotations

import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INSPECTION_SCHEMA = "vrcforge.outfit_package_inspection.v1"
LOOSE_PREFAB_EXTENSIONS = {".prefab"}
TEXTURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr"}
MATERIAL_EXTENSIONS = {".mat"}
MODEL_EXTENSIONS = {".fbx", ".blend", ".obj"}


def inspect_outfit_package(path_value: str | Path, max_entries: int = 5000) -> dict[str, Any]:
    source_path = Path(path_value).expanduser().resolve()
    if not source_path.exists():
        return {
            "ok": False,
            "schema": INSPECTION_SCHEMA,
            "error": "Package path does not exist.",
            "source": source_summary(source_path, "missing"),
            "privacy": privacy_policy(),
        }
    try:
        if source_path.is_dir():
            result = inspect_folder(source_path, max_entries=max_entries)
        elif source_path.suffix.lower() == ".zip":
            result = inspect_zip(source_path, max_entries=max_entries)
        elif source_path.suffix.lower() == ".unitypackage":
            result = inspect_unitypackage(source_path, max_entries=max_entries)
        else:
            result = inspect_single_file(source_path)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return {
            "ok": False,
            "schema": INSPECTION_SCHEMA,
            "error": str(exc),
            "source": source_summary(source_path, "unknown"),
            "privacy": privacy_policy(),
        }
    result["inspectedAt"] = utc_now()
    result["privacy"] = privacy_policy()
    return result


def inspect_single_file(path: Path) -> dict[str, Any]:
    category = classify_asset_name(path.name)
    return {
        "ok": category != "unsupported",
        "schema": INSPECTION_SCHEMA,
        "source": source_summary(path, category),
        "summary": {
            "unityPackageCount": 1 if category == "unitypackage" else 0,
            "prefabCandidateCount": 1 if category == "prefab" else 0,
            "textureCount": 1 if category == "texture" else 0,
            "materialCount": 1 if category == "material" else 0,
            "modelCount": 1 if category == "model" else 0,
            "entryCount": 1,
            "truncated": False,
            "importPlanKind": "manual_loose_asset" if category != "unsupported" else "unsupported",
        },
        "unityPackages": [file_entry(path.name, path.stat().st_size)] if category == "unitypackage" else [],
        "prefabCandidates": [file_entry(path.name, path.stat().st_size)] if category == "prefab" else [],
        "textures": [file_entry(path.name, path.stat().st_size)] if category == "texture" else [],
        "materials": [file_entry(path.name, path.stat().st_size)] if category == "material" else [],
        "models": [file_entry(path.name, path.stat().st_size)] if category == "model" else [],
        "warnings": [] if category != "unsupported" else ["Unsupported package input. Expected .unitypackage, Booth .zip, folder, prefab, or texture/material assets."],
    }


def inspect_folder(path: Path, max_entries: int) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    truncated = False
    skipped_symlinks = 0
    for file_path in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: str(item).lower()):
        if len(entries) >= max_entries:
            truncated = True
            break
        if file_path.is_symlink():
            skipped_symlinks += 1
            continue
        try:
            relative = file_path.relative_to(path).as_posix()
            entries.append(file_entry(relative, file_path.stat().st_size))
        except OSError:
            continue
    return build_container_result(
        path,
        "folder",
        entries,
        truncated,
        can_parse_unitypackage=False,
        extra_warnings=[f"Skipped {skipped_symlinks} symlinked file(s)." if skipped_symlinks else ""],
    )


def inspect_zip(path: Path, max_entries: int) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    truncated = False
    unsafe_entry_count = 0
    duplicate_entry_count = 0
    seen_paths: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for index, info in enumerate(sorted(archive.infolist(), key=lambda item: item.filename.lower())):
            if info.is_dir():
                continue
            if index >= max_entries:
                truncated = True
                break
            normalized = normalize_archive_name(info.filename)
            if not is_safe_archive_path(normalized):
                unsafe_entry_count += 1
                continue
            lower = normalized.lower()
            if lower in seen_paths:
                duplicate_entry_count += 1
                continue
            seen_paths.add(lower)
            entries.append(file_entry(normalized, int(info.file_size)))
    return build_container_result(
        path,
        "zip",
        entries,
        truncated,
        can_parse_unitypackage=False,
        unsafe_entry_count=unsafe_entry_count,
        duplicate_entry_count=duplicate_entry_count,
    )


def inspect_unitypackage(path: Path, max_entries: int) -> dict[str, Any]:
    pathnames: list[dict[str, Any]] = []
    truncated = False
    member_count = 0
    unsafe_pathname_count = 0
    with tarfile.open(path, mode="r:*") as archive:
        members = archive.getmembers()
        pathname_members = [member for member in members if member.isfile() and member.name.replace("\\", "/").endswith("/pathname")]
        for member in sorted(pathname_members, key=lambda item: item.name.lower()):
            if len(pathnames) >= max_entries:
                truncated = True
                break
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            raw = extracted.read(16384)
            name = raw.decode("utf-8", errors="replace").strip().replace("\\", "/")
            if name:
                if not is_safe_archive_path(normalize_archive_name(name)):
                    unsafe_pathname_count += 1
                    continue
                pathnames.append({"path": name, "category": classify_asset_name(name)})
        member_count = len(members)
    entries = [file_entry(item["path"], 0) for item in pathnames]
    result = build_container_result(path, "unitypackage", entries, truncated, can_parse_unitypackage=True)
    result["unityPackages"] = [
        {
            "path": path.name,
            "size": path.stat().st_size,
            "parsedPathnameCount": len(pathnames),
            "archiveMemberCount": member_count,
            "pathnames": pathnames[:200],
        }
    ]
    result["summary"]["unityPackageCount"] = 1
    result["summary"]["entryCount"] = len(pathnames)
    result["summary"]["importPlanKind"] = "unitypackage_import"
    result["summary"]["unsafePathnameCount"] = unsafe_pathname_count
    result["warnings"] = [f"Skipped {unsafe_pathname_count} unsafe UnityPackage pathname(s)." if unsafe_pathname_count else ""]
    result["warnings"] = [warning for warning in result["warnings"] if warning]
    return result


def build_container_result(
    path: Path,
    source_type: str,
    entries: list[dict[str, Any]],
    truncated: bool,
    can_parse_unitypackage: bool,
    unsafe_entry_count: int = 0,
    duplicate_entry_count: int = 0,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    unity_packages = [entry for entry in entries if entry["category"] == "unitypackage"]
    prefabs = [entry for entry in entries if entry["category"] == "prefab"]
    textures = [entry for entry in entries if entry["category"] == "texture"]
    materials = [entry for entry in entries if entry["category"] == "material"]
    models = [entry for entry in entries if entry["category"] == "model"]
    warnings: list[str] = []
    if not unity_packages and prefabs:
        warnings.append("Loose prefab workflow requires explicit user confirmation before copying assets into the Unity project.")
    if textures and not prefabs and not unity_packages:
        warnings.append("Texture-only input cannot be installed as an outfit without user-selected target materials.")
    if not unity_packages and not prefabs and not textures and not materials and not models:
        warnings.append("No UnityPackage or loose outfit assets were detected.")
    if unsafe_entry_count:
        warnings.append(f"Skipped {unsafe_entry_count} unsafe archive entr{'y' if unsafe_entry_count == 1 else 'ies'}.")
    if duplicate_entry_count:
        warnings.append(f"Skipped {duplicate_entry_count} duplicate archive entr{'y' if duplicate_entry_count == 1 else 'ies'}.")
    warnings.extend(warning for warning in (extra_warnings or []) if warning)
    import_plan_kind = "unitypackage_import" if unity_packages else "loose_prefab_assets" if prefabs else "manual_review"
    return {
        "ok": True,
        "schema": INSPECTION_SCHEMA,
        "source": source_summary(path, source_type),
        "summary": {
            "unityPackageCount": len(unity_packages),
            "prefabCandidateCount": len(prefabs),
            "textureCount": len(textures),
            "materialCount": len(materials),
            "modelCount": len(models),
            "entryCount": len(entries),
            "truncated": truncated,
            "importPlanKind": import_plan_kind,
            "canParseUnityPackagePathnames": can_parse_unitypackage,
            "unsafeEntryCount": unsafe_entry_count,
            "duplicateEntryCount": duplicate_entry_count,
        },
        "unityPackages": unity_packages[:200],
        "prefabCandidates": prefabs[:200],
        "textures": textures[:200],
        "materials": materials[:200],
        "models": models[:200],
        "warnings": warnings,
    }


def file_entry(name: str, size: int) -> dict[str, Any]:
    normalized = normalize_archive_name(name)
    return {
        "path": normalized,
        "name": Path(normalized).name,
        "size": int(size),
        "category": classify_asset_name(normalized),
    }


def classify_asset_name(name: str) -> str:
    lower = name.lower()
    suffix = Path(lower).suffix
    if suffix == ".unitypackage":
        return "unitypackage"
    if suffix in LOOSE_PREFAB_EXTENSIONS:
        return "prefab"
    if suffix in TEXTURE_EXTENSIONS:
        return "texture"
    if suffix in MATERIAL_EXTENSIONS:
        return "material"
    if suffix in MODEL_EXTENSIONS:
        return "model"
    return "unsupported"


def source_summary(path: Path, source_type: str) -> dict[str, Any]:
    summary = {"type": source_type, "name": path.name, "path": str(path)}
    try:
        if path.exists() and path.is_file():
            summary["size"] = path.stat().st_size
    except OSError:
        pass
    return summary


def normalize_archive_name(name: str) -> str:
    return name.replace("\\", "/").strip("/")


def is_safe_archive_path(name: str) -> bool:
    normalized = normalize_archive_name(name)
    if not normalized or normalized.startswith("/") or normalized.startswith("\\"):
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    parts = [part for part in normalized.split("/") if part]
    return all(part not in {".", ".."} for part in parts)


def privacy_policy() -> dict[str, Any]:
    return {
        "localOnly": True,
        "readsArchiveDirectory": True,
        "readsUnityPackagePathnames": True,
        "readsAssetBinaryContents": False,
        "uploadsPaidAssets": False,
        "modelContextReceivesStructuralSummaryOnly": True,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
