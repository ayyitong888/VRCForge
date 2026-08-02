"""Approval-bound loose outfit file-copy primitive.

This module has no Unity or dashboard dependency.  It captures the exact
source manifest and exact absent project targets at approval time, then only
creates those files with ``xb`` at execution.  Failure cleanup is restricted
to outputs created by this call.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from prepared_file_imports import (
    capture_directory,
    capture_regular_file,
    cleanup_owned_import,
    copy_approved_file_create_new,
    verify_directory,
)


DEFAULT_ALLOWED_SUFFIXES = frozenset({
    ".prefab", ".mat", ".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr",
    ".fbx", ".blend", ".obj", ".asset", ".controller", ".anim",
})
DEFAULT_MAX_FILES = 5_000
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024


def _walk_regular_files(root: Path) -> Iterable[Path]:
    """Walk without following a symlink/junction, rejecting it instead."""
    capture_directory(root, label="Loose outfit source root")
    with os.scandir(root) as entries:
        for entry in sorted(entries, key=lambda item: item.name.lower()):
            path = Path(entry.path)
            if entry.is_symlink():
                raise ValueError(f"Loose outfit source contains a symlink or reparse point: {path}")
            if entry.is_dir(follow_symlinks=False):
                # capture_directory checks the Windows reparse attribute too.
                capture_directory(path, label="Loose outfit source directory")
                yield from _walk_regular_files(path)
            elif entry.is_file(follow_symlinks=False):
                yield path
            else:
                raise ValueError(f"Loose outfit source contains a non-regular entry: {path}")


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _manifest_paths(root: Path, allowed_suffixes: frozenset[str]) -> list[tuple[Path, str]]:
    selected: list[tuple[Path, str]] = []
    for path in _walk_regular_files(root):
        suffix = path.suffix.lower()
        if suffix == ".meta":
            continue
        if suffix not in allowed_suffixes:
            continue
        selected.append((path, _relative(path, root)))
        meta = path.with_name(path.name + ".meta")
        if meta.exists() or meta.is_symlink():
            # A present companion must itself be a regular, non-reparse file.
            capture_regular_file(meta, label="Loose outfit companion metadata")
            selected.append((meta, _relative(meta, root)))
    return sorted(selected, key=lambda item: item[1].lower())


def prepare_loose_outfit_import(
    *,
    source_root: Path,
    project_root: Path,
    target_folder: str,
    allowed_suffixes: frozenset[str] = DEFAULT_ALLOWED_SUFFIXES,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    source = Path(os.path.abspath(source_root.expanduser()))
    project = Path(os.path.abspath(project_root.expanduser()))
    if max_files < 1 or max_total_bytes < 1:
        raise ValueError("Loose outfit import limits must be positive.")
    source_identity = capture_directory(source, label="Loose outfit source root")
    project_identity = capture_directory(project, label="Unity project")
    assets_identity = capture_directory(project / "Assets", label="Unity Assets")
    normalized_folder = PurePosixPath(target_folder.replace("\\", "/").strip().strip("/"))
    if (normalized_folder.is_absolute() or len(normalized_folder.parts) < 2
            or normalized_folder.parts[0] != "Assets" or any(part in {"", ".", ".."} for part in normalized_folder.parts)):
        raise ValueError("Loose outfit target folder must be under Assets/.")
    selected = _manifest_paths(source, frozenset(value.lower() for value in allowed_suffixes))
    if not selected:
        raise ValueError("Loose outfit import has no allowed files.")
    if len(selected) > max_files:
        raise ValueError("Loose outfit import exceeds the file-count limit.")
    total_size = 0
    files: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    from prepared_file_imports import prepare_project_asset_target  # narrow dependency at plan time
    for path, relative in selected:
        identity, digest = capture_regular_file(path, label="Loose outfit source")
        total_size += int(identity["size"])
        if total_size > max_total_bytes:
            raise ValueError("Loose outfit import exceeds the total-size limit.")
        target_relative = (normalized_folder / PurePosixPath(relative)).as_posix()
        if target_relative.lower() in seen_targets:
            raise ValueError("Loose outfit import has duplicate target paths.")
        seen_targets.add(target_relative.lower())
        target = prepare_project_asset_target(project, normalized_folder.as_posix(), relative)
        files.append({"relativePath": relative, "sourceIdentity": identity, "sourceSha256": digest, "target": target})
    return {
        "schema": "vrcforge.prepared-loose-outfit-import.v1",
        "sourceRoot": source_identity,
        "project": project_identity,
        "assets": assets_identity,
        "targetFolder": normalized_folder.as_posix(),
        "maxFiles": max_files,
        "maxTotalBytes": max_total_bytes,
        "allowedSuffixes": sorted({str(value).lower() for value in allowed_suffixes}),
        "totalSize": total_size,
        "files": files,
    }


def execute_loose_outfit_import(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("schema") != "vrcforge.prepared-loose-outfit-import.v1":
        raise ValueError("Prepared loose outfit import plan is invalid.")
    source_root = verify_directory(plan.get("sourceRoot") or {}, label="Loose outfit source root")
    verify_directory(plan.get("project") or {}, label="Unity project")
    verify_directory(plan.get("assets") or {}, label="Unity Assets")
    files = plan.get("files")
    if not isinstance(files, list) or not files or len(files) > int(plan.get("maxFiles") or 0):
        raise ValueError("Prepared loose outfit import file manifest is invalid.")
    # Re-enumerate before any write.  Every planned file, including .meta, must
    # remain exactly present; a new eligible source is also a drift signal.
    raw_suffixes = plan.get("allowedSuffixes")
    if not isinstance(raw_suffixes, list) or not raw_suffixes:
        raise ValueError("Prepared loose outfit import suffix allowlist is invalid.")
    allowed_suffixes = frozenset(str(value).lower() for value in raw_suffixes)
    if sorted(allowed_suffixes) != raw_suffixes:
        raise ValueError("Prepared loose outfit import suffix allowlist is not canonical.")
    live = _manifest_paths(source_root, allowed_suffixes)
    planned_relatives = [str(item.get("relativePath") or "") for item in files if isinstance(item, dict)]
    if [relative for _path, relative in live] != planned_relatives:
        raise ValueError("Loose outfit import source manifest drifted after approval.")
    created: list[tuple[Path, dict[str, Any]]] = []
    copied: list[str] = []
    owned_parent_relatives: set[str] = set()
    owned_parent_identities: dict[str, dict[str, Any]] = {}
    try:
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Prepared loose outfit import item is invalid.")
            target, digest, ownership = copy_approved_file_create_new(
                source_identity=item.get("sourceIdentity") or {},
                source_sha256=str(item.get("sourceSha256") or ""),
                project_identity=item.get("target", {}).get("project") or {},
                assets_identity=item.get("target", {}).get("assets") or {},
                parent_identities=[
                    *(item.get("target", {}).get("parentIdentities") or []),
                    *owned_parent_identities.values(),
                ],
                absent_parent_relative_paths=[
                    relative
                    for relative in (item.get("target", {}).get("absentParentRelativePaths") or [])
                    if str(relative).replace("\\", "/") not in owned_parent_relatives
                ],
                target_relative_path=str(item.get("target", {}).get("targetRelativePath") or ""),
            )
            created.append((target, ownership))
            project_path = Path(str(item.get("target", {}).get("project", {}).get("path") or ""))
            for directory_identity in ownership.get("createdDirectories") or []:
                directory = Path(str(directory_identity.get("path") or ""))
                relative_directory = directory.relative_to(project_path).as_posix()
                owned_parent_relatives.add(relative_directory)
                owned_parent_identities[relative_directory] = directory_identity
            copied.append(str(item.get("target", {}).get("targetRelativePath") or ""))
        return {"ok": True, "copiedFiles": copied, "copiedFileCount": len(copied)}
    except Exception as exc:
        cleanup_errors: list[str] = []
        for target, ownership in reversed(created):
            error = cleanup_owned_import(target, ownership)
            if error:
                cleanup_errors.append(error)
        if cleanup_errors:
            raise RuntimeError(f"Loose outfit import failed; owned-output cleanup also failed: {'; '.join(cleanup_errors)}") from exc
        raise
