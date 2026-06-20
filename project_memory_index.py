from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INDEX_SCHEMA = "vrcforge.project_memory_index.v1"
INDEXED_ROOTS = ("Assets", "Packages", "ProjectSettings")
EXCLUDED_DIR_NAMES = {
    ".git",
    ".vs",
    "Library",
    "Logs",
    "Obj",
    "Temp",
    "UserSettings",
    "__pycache__",
}
GUID_RE = re.compile(r"^\s*guid:\s*([A-Fa-f0-9]{32})\s*$", re.MULTILINE)


def scan_project_memory(project_path: str | Path, index_root: str | Path, max_files: int = 100000) -> dict[str, Any]:
    project_root = Path(project_path).expanduser().resolve()
    if not project_root.is_dir():
        return {
            "ok": False,
            "schema": INDEX_SCHEMA,
            "error": "Unity project path does not exist or is not a directory.",
            "projectId": stable_project_id(project_root),
            "projectName": project_root.name,
            "summary": empty_summary(),
            "privacy": privacy_policy(),
        }

    storage_root = Path(index_root).expanduser().resolve() / stable_project_id(project_root)
    storage_root.mkdir(parents=True, exist_ok=True)
    index_path = storage_root / "index.json"
    previous = read_index(index_path)
    previous_files = ensure_dict(previous.get("files"))

    scanned_at = utc_now()
    current_files: dict[str, dict[str, Any]] = {}
    added: list[str] = []
    modified: list[str] = []
    unchanged: list[str] = []
    guid_changes: list[dict[str, str]] = []
    hashes_computed = 0
    hashes_reused = 0
    truncated = False

    for rel_path, file_path in iter_project_files(project_root):
        if len(current_files) >= max_files:
            truncated = True
            break
        try:
            stat = file_path.stat()
        except OSError:
            continue
        previous_entry = ensure_dict(previous_files.get(rel_path))
        size = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
        if previous_entry.get("size") == size and previous_entry.get("mtimeNs") == mtime_ns and previous_entry.get("sha256"):
            digest = str(previous_entry.get("sha256"))
            hashes_reused += 1
        else:
            digest = sha256_file(file_path)
            hashes_computed += 1
        guid = read_meta_guid(file_path) if rel_path.endswith(".meta") else ""
        entry = {
            "path": rel_path,
            "size": size,
            "mtimeNs": mtime_ns,
            "sha256": digest,
            "category": classify_path(rel_path),
            "guid": guid,
        }
        current_files[rel_path] = entry
        if not previous_entry:
            added.append(rel_path)
        elif previous_entry.get("sha256") != digest:
            modified.append(rel_path)
            old_guid = str(previous_entry.get("guid") or "")
            if old_guid and guid and old_guid != guid:
                guid_changes.append({"path": rel_path, "oldGuid": old_guid, "newGuid": guid})
        else:
            unchanged.append(rel_path)

    deleted = sorted(path for path in previous_files if path not in current_files)
    added.sort()
    modified.sort()
    unchanged.sort()

    scanner_families = scanner_families_for_changes(added + modified + deleted)
    package_fingerprints = build_package_fingerprints(current_files)
    meta_guid_map = {entry["guid"]: path for path, entry in current_files.items() if entry.get("guid")}
    next_index = {
        "schema": INDEX_SCHEMA,
        "projectId": stable_project_id(project_root),
        "projectName": project_root.name,
        "projectRoot": str(project_root),
        "scannedAt": scanned_at,
        "files": current_files,
        "metaGuidMap": meta_guid_map,
        "packageFingerprints": package_fingerprints,
        "lastSummaries": ensure_dict(previous.get("lastSummaries")),
        "checkpointFiles": ensure_dict(previous.get("checkpointFiles")),
    }
    write_json(index_path, next_index)

    summary = {
        "firstScan": not bool(previous_files),
        "totalFiles": len(current_files),
        "unchangedFiles": len(unchanged),
        "addedFiles": len(added),
        "modifiedFiles": len(modified),
        "deletedFiles": len(deleted),
        "guidChangeCount": len(guid_changes),
        "hashesComputed": hashes_computed,
        "hashesReused": hashes_reused,
        "truncated": truncated,
        "changed": bool(added or modified or deleted or guid_changes),
        "scannerFamilies": scanner_families,
    }
    return {
        "ok": True,
        "schema": INDEX_SCHEMA,
        "projectId": stable_project_id(project_root),
        "projectName": project_root.name,
        "indexPath": str(index_path),
        "summary": summary,
        "changes": {
            "added": summarize_paths(added, current_files),
            "modified": summarize_paths(modified, current_files),
            "deleted": [{"path": path, "category": classify_path(path)} for path in deleted],
            "guidChanges": guid_changes,
        },
        "packageFingerprints": package_fingerprints,
        "metaGuidCount": len(meta_guid_map),
        "staleDataPolicy": "Cached summaries are advisory and must be revalidated before write plans.",
        "privacy": privacy_policy(),
    }


def stable_project_id(project_root: Path) -> str:
    normalized = str(project_root).replace("\\", "/")
    if os.name == "nt":
        normalized = normalized.lower()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:24]


def iter_project_files(project_root: Path) -> list[tuple[str, Path]]:
    results: list[tuple[str, Path]] = []
    for root_name in INDEXED_ROOTS:
        root = project_root / root_name
        if not root.exists():
            continue
        if root.is_file():
            results.append((root_name, root))
            continue
        for current_root, dir_names, file_names in os.walk(root):
            dir_names[:] = [name for name in dir_names if name not in EXCLUDED_DIR_NAMES]
            for file_name in file_names:
                file_path = Path(current_root) / file_name
                try:
                    rel_path = file_path.relative_to(project_root).as_posix()
                except ValueError:
                    continue
                results.append((rel_path, file_path))
    results.sort(key=lambda item: item[0].lower())
    return results


def classify_path(path: str) -> str:
    lower = path.lower()
    if lower == "packages/manifest.json":
        return "package_manifest"
    if lower == "packages/packages-lock.json":
        return "package_lock"
    if lower.endswith(".meta"):
        return "meta"
    if lower.endswith(".prefab"):
        return "prefab"
    if lower.endswith(".unity"):
        return "scene"
    if lower.endswith(".controller") or lower.endswith(".overridecontroller"):
        return "animator"
    if lower.endswith(".anim"):
        return "animation_clip"
    if lower.endswith(".asset"):
        if "vrcexpression" in lower or "expressions" in lower:
            return "expression_asset"
        return "asset"
    if lower.endswith(".mat"):
        return "material"
    if lower.endswith((".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr")):
        return "texture"
    if lower.endswith((".fbx", ".blend", ".obj")):
        return "model"
    if lower.startswith("packages/"):
        return "package"
    return "other"


def scanner_families_for_changes(paths: list[str]) -> list[str]:
    families: set[str] = set()
    for path in paths:
        category = classify_path(path)
        if category in {"package_manifest", "package_lock", "package"}:
            families.update({"packages", "doctor", "validation"})
        elif category in {"prefab", "scene", "meta"}:
            families.update({"avatar", "wardrobe", "validation"})
        elif category in {"animator", "animation_clip"}:
            families.update({"fx", "animation_bindings", "validation"})
        elif category == "expression_asset":
            families.update({"parameters", "menus", "validation"})
        elif category in {"material", "texture"}:
            families.update({"materials", "validation"})
        elif category == "model":
            families.update({"avatar", "materials", "validation"})
        elif category != "other":
            families.add("validation")
    return sorted(families)


def summarize_paths(paths: list[str], entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "category": str(ensure_dict(entries.get(path)).get("category") or classify_path(path)),
            "size": int(ensure_dict(entries.get(path)).get("size") or 0),
            "sha256": str(ensure_dict(entries.get(path)).get("sha256") or ""),
        }
        for path in paths
    ]


def build_package_fingerprints(entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fingerprints: dict[str, Any] = {}
    for path in ("Packages/manifest.json", "Packages/packages-lock.json"):
        entry = entries.get(path)
        if entry:
            fingerprints[path] = {"sha256": entry.get("sha256"), "size": entry.get("size")}
    return fingerprints


def read_meta_guid(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return ""
    match = GUID_RE.search(text)
    return match.group(1).lower() if match else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) and payload.get("schema") == INDEX_SCHEMA else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def empty_summary() -> dict[str, Any]:
    return {
        "firstScan": True,
        "totalFiles": 0,
        "unchangedFiles": 0,
        "addedFiles": 0,
        "modifiedFiles": 0,
        "deletedFiles": 0,
        "guidChangeCount": 0,
        "hashesComputed": 0,
        "hashesReused": 0,
        "truncated": False,
        "changed": False,
        "scannerFamilies": [],
    }


def privacy_policy() -> dict[str, Any]:
    return {
        "localOnly": True,
        "binaryAssetContentsReturned": False,
        "modelContextReceivesStructuralSummaryOnly": True,
        "paidAssetPolicy": "Only local file metadata, hashes, paths, and structural deltas are returned.",
    }


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
