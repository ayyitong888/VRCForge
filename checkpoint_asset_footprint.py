"""Trusted exact Assets footprint validation and content witnesses; no write authority.

Callers own the project writer lock and checkpoint persistence. All source handles
are short-lived read handles using the existing process/file permissions.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
from typing import Any

KIND = "unity_project_asset_footprint"
MAX_PATHS = 16384
_is_reserved = getattr(os.path, "isreserved", lambda name: PureWindowsPath(name).is_reserved())


def is_asset_footprint(record: dict[str, Any]) -> bool:
    return ("assetFootprint" in record or "archiveAbsentFiles" in record or "archiveAbsentDirectories" in record
            or isinstance(record.get("checkpointScope"), dict) and record["checkpointScope"].get("kind") == KIND)


def _paths(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PATHS or (nonempty and not value):
        raise ValueError(f"{label} must be an explicit bounded path list.")
    seen: set[str] = set()
    for name in value:
        if (not isinstance(name, str) or not name.startswith("Assets/") or "\\" in name
                or ":" in name or any(part in {"", ".", ".."} for part in name.split("/"))
                or str(PurePosixPath(name)) != name
                or any(part.endswith((".", " ")) or _is_reserved(part)
                       or any(ord(char) < 32 or char in '<>"|?*' for char in part)
                       for part in name.split("/"))
                or name.casefold() in seen):
            raise ValueError(f"{label} contains a noncanonical or duplicate Assets path.")
        seen.add(name.casefold())
    return list(value)


def _regular_path(project: Path, name: str, *, directory: bool = False) -> Path:
    project = Path(os.path.abspath(project))
    target = project.joinpath(*PurePosixPath(name).parts)
    # lstat every existing ancestor before resolving; resolve alone hides links.
    for path in [*reversed(target.parents), target]:
        if not os.path.lexists(path):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Linked/reparse checkpoint path is forbidden: {name}")
        if path != target and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Checkpoint ancestor is not a directory: {name}")
        if path == target and not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ValueError(f"Checkpoint target has the wrong file type: {name}")
    if not target.resolve().is_relative_to(project.resolve()):
        raise ValueError(f"Checkpoint target escapes project: {name}")
    return target


def validate_asset_footprint(project_root: Path, record: dict[str, Any], *, preparing: bool = False) -> dict[str, Any]:
    project = Path(os.path.abspath(project_root))
    marker = record.get("assetFootprint")
    stored = record.get("checkpointScope")
    if not ((isinstance(marker, dict) and marker.get("schema") == "vrcforge.asset_checkpoint_footprint.v1")
            or isinstance(stored, dict) and stored.get("kind") == KIND):
        raise ValueError("Asset footprint requires its explicit trusted schema.")
    existing = _paths(record.get("archiveFiles"), "archiveFiles")
    absent = _paths(record.get("archiveAbsentFiles"), "archiveAbsentFiles")
    directories = _paths(record.get("archiveAbsentDirectories", []), "archiveAbsentDirectories")
    if not existing and not absent:
        raise ValueError("Asset footprint must identify at least one existing or absent file.")
    if len(existing) + len(absent) + len(directories) > MAX_PATHS:
        raise ValueError("Asset footprint exceeds the bounded path count.")
    files = {name.casefold() for name in [*existing, *absent]}
    if len(files) != len(existing) + len(absent) or files.intersection(name.casefold() for name in directories):
        raise ValueError("Asset footprint existing/absent files and directories overlap.")
    for name in [*existing, *absent, *directories]:
        if any(parent.as_posix().casefold() in files for parent in PurePosixPath(name).parents):
            raise ValueError("Asset footprint has a file as an ancestor.")
    for name in existing:
        target = _regular_path(project, name)
        if preparing and not target.is_file():
            raise ValueError(f"Checkpoint existing file is missing: {name}")
    for name in absent:
        target = _regular_path(project, name)
        if preparing and os.path.lexists(target):
            raise ValueError(f"Checkpoint absent file already exists: {name}")
    for name in directories:
        target = _regular_path(project, name, directory=True)
        if preparing and os.path.lexists(target):
            raise ValueError(f"Checkpoint absent directory already exists: {name}")
    scope = {"kind": KIND, "pathspecs": ["Assets"], "archiveFiles": existing,
             "archiveAbsentFiles": absent, "archiveAbsentDirectories": directories}
    if isinstance(marker, dict) and any(key in marker and marker[key] != scope[key]
                                       for key in ("archiveFiles", "archiveAbsentFiles", "archiveAbsentDirectories")):
        raise ValueError("Asset footprint differs from its trusted manifest.")
    if stored is not None and stored != scope:
        raise ValueError("Asset footprint metadata differs from its recorded checkpoint scope.")
    return scope


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Created asset changed while its witness was being read.")
    return digest.hexdigest()


def capture_created_asset_post_state(project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Return a checkpoint copy for its owner to append under the writer lock."""
    scope = validate_asset_footprint(project_root, record)
    state = {name: _sha256(_regular_path(project_root, name)) for name in scope["archiveAbsentFiles"]}
    return {**record, "createdAssetPostState": state}


def validate_created_asset_post_state(project_root: Path, record: dict[str, Any]) -> dict[str, Path]:
    scope = validate_asset_footprint(project_root, record)
    witness = record.get("createdAssetPostState")
    if not isinstance(witness, dict) or set(witness) != set(scope["archiveAbsentFiles"]):
        raise ValueError("Created asset rollback requires a complete post-write witness.")
    for name, expected in witness.items():
        if expected is not None and (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
            raise ValueError("Created asset rollback witness is invalid.")
        if _sha256(_regular_path(project_root, name)) != expected:
            raise ValueError(f"Created asset changed after its post-write witness; preserving all files: {name}")
    return {name: target for name in [*scope["archiveFiles"], *scope["archiveAbsentFiles"]]
            if (target := _regular_path(project_root, name)).is_file()}


def cleanup_absent_directories(project_root: Path, record: dict[str, Any]) -> None:
    scope = validate_asset_footprint(project_root, record)
    for name in sorted(scope["archiveAbsentDirectories"], key=lambda value: len(PurePosixPath(value).parts), reverse=True):
        target = _regular_path(project_root, name, directory=True)
        try:
            target.rmdir()
        except OSError:
            # Unknown nonempty/locked directories remain user-owned and intact.
            pass


def checkpoint_restore_file_scope(record: dict[str, Any]) -> list[str] | None:
    """Keep create-only restore preparation bounded instead of selecting all Assets."""
    if is_asset_footprint(record):
        scope = validate_asset_footprint(Path(str(record.get("projectRoot") or "")), record)
        return [*scope["archiveFiles"], *scope["archiveAbsentFiles"]]
    value = record.get("archiveFiles")
    items = value if isinstance(value, list) else [] if value is None else [value]
    return [text for item in items if (text := str(item or "").strip())] or None


def asset_footprint_safety_record(project_root: Path, checkpoint: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Capture the same bounded file domain before restore; no filesystem writes."""
    scope = validate_asset_footprint(project_root, checkpoint)
    all_files = [*scope["archiveFiles"], *scope["archiveAbsentFiles"]]
    existing = [name for name in all_files if _regular_path(project_root, name).is_file()]
    absent = [name for name in all_files if name not in existing]
    directories = [name for name in scope["archiveAbsentDirectories"]
                   if not _regular_path(project_root, name, directory=True).exists()]
    return {**record, "assetFootprint": {"schema": "vrcforge.asset_checkpoint_footprint.v1"},
            "archiveFiles": existing, "archiveAbsentFiles": absent, "archiveAbsentDirectories": directories}
