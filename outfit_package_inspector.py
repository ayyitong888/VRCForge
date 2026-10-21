from __future__ import annotations

import tarfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INSPECTION_SCHEMA = "vrcforge.outfit_package_inspection.v1"
LOOSE_PREFAB_EXTENSIONS = {".prefab"}
ASSEMBLY_DEFINITION_EXTENSIONS = {".asmdef"}
TEXTURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr"}
MATERIAL_EXTENSIONS = {".mat"}
MODEL_EXTENSIONS = {".fbx", ".blend", ".obj"}
MAX_NESTED_UNITYPACKAGE_BYTES = 256 * 1024 * 1024
MAX_UNITYPACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_UNITYPACKAGE_MEMBERS = 200_000


def is_single_fbx_folder(source_type: str, models: list[dict[str, Any]], *, model_count: int, truncated: bool) -> bool:
    """Shared inspection/planning fact; no prefab or model choice is invented."""
    return (
        source_type == "folder" and not truncated
        and len(models) == 1 and model_count == 1
        and str(models[0].get("path") or "").lower().endswith(".fbx")
    )


class _BoundedArchiveReader:
    def __init__(self, reader: Any, limit: int) -> None:
        self._reader = reader
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self._limit - self.bytes_read + 1
        data = self._reader.read(size)
        self.bytes_read += len(data)
        if self.bytes_read > self._limit:
            raise tarfile.ReadError("UnityPackage uncompressed byte limit exceeded")
        return data

    def tell(self) -> int:
        return self.bytes_read


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
    nested_pathnames: list[dict[str, Any]] = []
    nested_package_results: list[dict[str, Any]] = []
    nested_warnings: list[str] = []
    parsed_nested_count = 0
    nested_truncated = False
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
            if normalized.lower().endswith(".unitypackage"):
                if info.file_size > MAX_NESTED_UNITYPACKAGE_BYTES:
                    nested_warnings.append(
                        f"Skipped pathname inspection for oversized nested UnityPackage {normalized} ({info.file_size} bytes; limit {MAX_NESTED_UNITYPACKAGE_BYTES})."
                    )
                    nested_package_results.append({
                        "path": normalized,
                        "size": int(info.file_size),
                        "parsedPathnameCount": 0,
                        "pathnamesEvidenceComplete": False,
                        "inspectionSkipped": "nested_package_size_limit",
                    })
                    continue
                remaining = max(0, max_entries - len(nested_pathnames))
                try:
                    with archive.open(info, "r") as nested_file:
                        bounded_nested_file = _BoundedArchiveReader(
                            nested_file,
                            MAX_UNITYPACKAGE_UNCOMPRESSED_BYTES,
                        )
                        parsed = inspect_unitypackage_stream(
                            bounded_nested_file,
                            max_entries=remaining,
                            package_path=normalized,
                        )
                except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as exc:
                    nested_warnings.append(f"Could not inspect nested UnityPackage {normalized}: {exc}")
                    nested_package_results.append({
                        "path": normalized,
                        "size": int(info.file_size),
                        "parsedPathnameCount": 0,
                        "pathnamesEvidenceComplete": False,
                        "inspectionSkipped": "invalid_nested_package",
                    })
                    continue
                parsed_nested_count += 1
                nested_pathnames.extend(parsed["pathnamesEvidence"])
                nested_truncated = nested_truncated or parsed["truncated"]
                nested_warnings.extend(parsed["warnings"])
                nested_package_results.append({
                    "path": normalized,
                    "size": int(info.file_size),
                    "parsedPathnameCount": len(parsed["pathnamesEvidence"]),
                    "archiveMemberCount": parsed["archiveMemberCount"],
                    "pathnames": parsed["pathnamesEvidence"][:200],
                    "pathnamesEvidence": parsed["pathnamesEvidence"],
                    "pathnamesEvidenceCount": len(parsed["pathnamesEvidence"]),
                    "pathnamesEvidenceComplete": not parsed["truncated"],
                    "pathnamesReturnedCount": min(len(parsed["pathnamesEvidence"]), 200),
                    "pathnamesReturnedTruncated": len(parsed["pathnamesEvidence"]) > 200,
                    "unsafePathnameCount": parsed["unsafePathnameCount"],
                })
    result = build_container_result(
        path,
        "zip",
        entries,
        truncated,
        can_parse_unitypackage=parsed_nested_count > 0,
        unsafe_entry_count=unsafe_entry_count,
        duplicate_entry_count=duplicate_entry_count,
    )
    nested_entries = [
        file_entry(f"{item['packagePath']}!/{item['path']}", 0)
        for item in nested_pathnames
    ]
    for entry in nested_entries:
        target = {
            "prefab": "prefabCandidates",
            "texture": "textures",
            "material": "materials",
            "model": "models",
            "assembly_definition": "assemblyDefinitions",
        }.get(entry["category"])
        if target:
            result[target].append(entry)
    counts = Counter(item["category"] for item in nested_pathnames)
    result["summary"]["outerEntryCount"] = len(entries)
    result["summary"]["nestedUnityPackageCount"] = parsed_nested_count
    result["summary"]["nestedPathnameCount"] = len(nested_pathnames)
    result["summary"]["nestedPathnamesTruncated"] = nested_truncated
    result["summary"]["unsafePathnameCount"] = sum(
        int(package.get("unsafePathnameCount") or 0) for package in nested_package_results
    )
    result["summary"]["prefabCandidateCount"] += counts["prefab"]
    result["summary"]["textureCount"] += counts["texture"]
    result["summary"]["materialCount"] += counts["material"]
    result["summary"]["modelCount"] += counts["model"]
    result["summary"]["assemblyDefinitionCount"] += counts["assembly_definition"]
    result["summary"]["canParseUnityPackagePathnames"] = parsed_nested_count > 0
    result["unityPackages"] = nested_package_results or result["unityPackages"]
    result["prefabCandidates"] = result["prefabCandidates"][:200]
    result["textures"] = result["textures"][:200]
    result["materials"] = result["materials"][:200]
    result["models"] = result["models"][:200]
    result["assemblyDefinitions"] = result["assemblyDefinitions"][:200]
    if nested_warnings:
        result["warnings"].extend(nested_warnings)
    if nested_package_results:
        result["summary"]["assetCountScope"] = "outer ZIP assets plus parsed nested UnityPackage pathnames"
        result["summary"]["entryCountScope"] = "outer ZIP direct entries; nested pathnames counted separately"
    return result


def inspect_unitypackage(path: Path, max_entries: int) -> dict[str, Any]:
    with path.open("rb") as package_file:
        parsed = inspect_unitypackage_stream(
            _BoundedArchiveReader(package_file, MAX_UNITYPACKAGE_UNCOMPRESSED_BYTES),
            max_entries=max_entries,
            package_path=path.name,
        )
    pathnames = parsed["pathnamesEvidence"]
    truncated = parsed["truncated"]
    member_count = parsed["archiveMemberCount"]
    unsafe_pathname_count = parsed["unsafePathnameCount"]
    entries = [file_entry(item["path"], 0) for item in pathnames]
    result = build_container_result(path, "unitypackage", entries, truncated, can_parse_unitypackage=True)
    result["unityPackages"] = [
        {
            "path": path.name,
            "size": path.stat().st_size,
            "parsedPathnameCount": len(pathnames),
            "archiveMemberCount": member_count,
            "pathnames": pathnames[:200],
            "pathnamesEvidence": pathnames,
            "pathnamesEvidenceCount": len(pathnames),
            "pathnamesEvidenceComplete": not truncated,
            "pathnamesReturnedCount": min(len(pathnames), 200),
            "pathnamesReturnedTruncated": len(pathnames) > 200,
            "unsafePathnameCount": unsafe_pathname_count,
        }
    ]
    result["summary"]["unityPackageCount"] = 1
    result["summary"]["entryCount"] = len(pathnames)
    result["summary"]["importPlanKind"] = "unitypackage_import"
    result["summary"]["unsafePathnameCount"] = unsafe_pathname_count
    result["warnings"] = parsed["warnings"]
    return result


def inspect_unitypackage_stream(package_file: Any, max_entries: int, package_path: str) -> dict[str, Any]:
    pathnames: list[dict[str, Any]] = []
    truncated = False
    member_count = 0
    unsafe_pathname_count = 0
    uncompressed_bytes = 0
    byte_limited = False
    with tarfile.open(fileobj=package_file, mode="r|*") as archive:
        try:
            for member in archive:
                member_count += 1
                if member_count > MAX_UNITYPACKAGE_MEMBERS:
                    truncated = True
                    break
                uncompressed_bytes += max(0, int(member.size))
                if uncompressed_bytes > MAX_UNITYPACKAGE_UNCOMPRESSED_BYTES:
                    truncated = True
                    break
                if not member.isfile() or not member.name.replace("\\", "/").endswith("/pathname"):
                    continue
                if len(pathnames) >= max_entries:
                    truncated = True
                    break
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read(16385)
                if len(raw) > 16384:
                    unsafe_pathname_count += 1
                    continue
                name = raw.decode("utf-8", errors="replace").strip().replace("\\", "/")
                if name:
                    if not is_safe_unitypackage_pathname(name):
                        unsafe_pathname_count += 1
                        continue
                    pathnames.append({"packagePath": package_path, "path": name, "category": classify_asset_name(name)})
        except tarfile.ReadError as exc:
            if "byte limit exceeded" not in str(exc):
                raise
            truncated = True
            byte_limited = True
    warnings: list[str] = []
    if unsafe_pathname_count:
        warnings.append(f"Skipped {unsafe_pathname_count} unsafe or oversized UnityPackage pathname(s).")
    if truncated:
        warnings.append("UnityPackage pathname inspection reached a configured member, byte, or entry limit.")
    if byte_limited:
        warnings.append("UnityPackage pathname inspection stopped at the decompressed byte limit.")
    return {
        "pathnamesEvidence": pathnames,
        "archiveMemberCount": member_count,
        "unsafePathnameCount": unsafe_pathname_count,
        "truncated": truncated,
        "warnings": warnings,
    }


def is_safe_unitypackage_pathname(name: str) -> bool:
    normalized = name.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized or normalized.startswith("/"):
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    return all(part not in {".", ".."} for part in normalized.split("/"))


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
    assembly_definitions = [entry for entry in entries if entry["category"] == "assembly_definition"]
    textures = [entry for entry in entries if entry["category"] == "texture"]
    materials = [entry for entry in entries if entry["category"] == "material"]
    models = [entry for entry in entries if entry["category"] == "model"]
    warnings: list[str] = []
    if not unity_packages and prefabs:
        warnings.append("Loose prefab workflow requires explicit user confirmation before copying assets into the Unity project.")
    if textures and not prefabs and not unity_packages:
        warnings.append("Texture-only assets in an unpacked folder can be copied into the project; assigning them to materials is a separate supervised operation.")
    if not unity_packages and not prefabs and not textures and not materials and not models and not assembly_definitions:
        warnings.append("No UnityPackage or loose outfit assets were detected.")
    if unsafe_entry_count:
        warnings.append(f"Skipped {unsafe_entry_count} unsafe archive entr{'y' if unsafe_entry_count == 1 else 'ies'}.")
    if duplicate_entry_count:
        warnings.append(f"Skipped {duplicate_entry_count} duplicate archive entr{'y' if duplicate_entry_count == 1 else 'ies'}.")
    warnings.extend(warning for warning in (extra_warnings or []) if warning)
    single_fbx = is_single_fbx_folder(source_type, models, model_count=len(models), truncated=truncated)
    import_plan_kind = "unitypackage_import" if unity_packages else "loose_prefab_assets" if (prefabs or assembly_definitions or single_fbx) else "manual_review"
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
            "assemblyDefinitionCount": len(assembly_definitions),
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
        "assemblyDefinitions": assembly_definitions[:200],
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
    if suffix in ASSEMBLY_DEFINITION_EXTENSIONS:
        return "assembly_definition"
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
