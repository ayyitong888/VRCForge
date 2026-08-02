"""Sealed ZIP extraction facts for a later approval-bound import handler.

This module intentionally does not know about FastAPI, the dashboard, or MCP.
It only prepares and executes a project-scoped create-new extraction transaction.
"""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from prepared_file_imports import (
    capture_directory,
    capture_regular_file,
    verify_directory,
    verify_regular_file,
)


MAX_ENTRIES = 50_000
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100.0
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _safe_member_path(raw: str) -> str:
    normalized = str(raw or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            ":" in part
            or "\x00" in part
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
            for part in path.parts
        )
    ):
        raise ValueError(f"Archive member path is unsafe: {raw}")
    return path.as_posix()


def _entry_manifest(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValueError("Archive entry count exceeds the extraction cap.")
    for info in infos:
        if info.is_dir():
            continue
        mode = (int(info.external_attr) >> 16) & 0xFFFF
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
            raise ValueError(f"Archive member is not a regular file: {info.filename}")
        if int(info.flag_bits) & 1:
            raise ValueError(f"Archive member is encrypted: {info.filename}")
        name = _safe_member_path(info.filename)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in seen:
            raise ValueError(f"Archive has duplicate member path: {name}")
        seen.add(key)
        size = max(0, int(info.file_size))
        compressed = max(0, int(info.compress_size))
        total += size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Archive uncompressed size exceeds the extraction cap.")
        if size > 0 and (compressed == 0 or size / compressed > MAX_COMPRESSION_RATIO):
            raise ValueError(f"Archive member compression ratio exceeds the extraction cap: {name}")
        digest = hashlib.sha256()
        bytes_read = 0
        with archive.open(info, "r") as handle:
            while chunk := handle.read(1024 * 1024):
                bytes_read += len(chunk)
                if bytes_read > size:
                    raise ValueError(f"Archive member exceeded its declared size: {name}")
                digest.update(chunk)
        if bytes_read != size:
            raise ValueError(f"Archive member size did not match its central-directory record: {name}")
        entries.append({
            "path": name,
            "zipPath": info.filename,
            "crc": int(info.CRC),
            "compressedSize": compressed,
            "compressionRatio": (float(size) / float(compressed)) if compressed else (0.0 if size == 0 else float("inf")),
            "size": size,
            "sha256": digest.hexdigest(),
        })
    keys = sorted(seen)
    for index, key in enumerate(keys):
        if index + 1 < len(keys) and keys[index + 1].startswith(f"{key}/"):
            raise ValueError("Archive contains a file/directory prefix collision.")
    if not entries:
        raise ValueError("Archive contains no importable files.")
    return entries


def _opened_source_matches(handle: Any, identity: dict[str, Any], expected_sha256: str) -> None:
    opened = os.fstat(handle.fileno())
    if (
        int(opened.st_dev) != int(identity.get("device", -1))
        or int(opened.st_ino) != int(identity.get("inode", -1))
        or int(opened.st_size) != int(identity.get("size", -1))
        or int(opened.st_mtime_ns) != int(identity.get("mtimeNs", -1))
    ):
        raise ValueError("Archive source handle identity drifted after approval.")
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Archive source content drifted after approval.")
    handle.seek(0)


def prepare_zip_extract(
    *, source: Path,
    project_root: Path,
    target_folder: str,
    target_root_name: str,
    allowed_suffixes: set[str] | None = None,
) -> dict[str, Any]:
    """Create approval-time facts for a ZIP that must extract under Assets/."""
    source_identity, source_sha256 = capture_regular_file(source, label="Archive source")
    project = Path(os.path.abspath(project_root.expanduser()))
    assets = project / "Assets"
    project_identity = capture_directory(project, label="Unity project")
    assets_identity = capture_directory(assets, label="Unity Assets")
    folder = PurePosixPath(str(target_folder).replace("\\", "/").strip().strip("/"))
    if len(folder.parts) < 2 or folder.parts[0] != "Assets" or folder.is_absolute() or any(part in {"", ".", ".."} for part in folder.parts):
        raise ValueError("Target folder must be under Assets/.")
    target_name = PurePosixPath(str(target_root_name).replace("\\", "/"))
    if len(target_name.parts) != 1 or _safe_member_path(target_name.as_posix()) != target_name.as_posix():
        raise ValueError("Target root name is invalid.")
    target_relative = folder / target_name
    target_root = project.joinpath(*target_relative.parts)
    try:
        target_root.relative_to(assets)
    except ValueError as exc:
        raise ValueError("Archive target escapes Unity Assets.") from exc
    if target_root.exists() or target_root.is_symlink():
        raise ValueError("Approval-bound archive target root already exists.")
    parents: list[dict[str, Any]] = []
    absent_parents: list[str] = []
    cursor = target_root.parent
    while cursor != assets:
        if cursor.exists() or cursor.is_symlink():
            parents.append(capture_directory(cursor, label="Archive target parent"))
        else:
            absent_parents.append(cursor.relative_to(project).as_posix())
        cursor = cursor.parent
    with source.open("rb") as source_handle:
        _opened_source_matches(source_handle, source_identity, source_sha256)
        with zipfile.ZipFile(source_handle) as archive:
            manifest = _entry_manifest(archive)
    verify_regular_file(source_identity, source_sha256, label="Archive source")
    normalized_suffixes = sorted({str(suffix).lower() for suffix in (allowed_suffixes or set())})
    if normalized_suffixes:
        for entry in manifest:
            if PurePosixPath(str(entry["path"])).suffix.lower() not in normalized_suffixes:
                raise ValueError(f"Archive member type is not allowed: {entry['path']}")
    return {
        "schema": "vrcforge.prepared-zip-extract.v1",
        "sourceIdentity": source_identity,
        "sourceSha256": source_sha256,
        "project": project_identity,
        "assets": assets_identity,
        "parentIdentities": parents,
        "absentParentRelativePaths": absent_parents,
        "targetRootRelativePath": target_relative.as_posix(),
        "allowedSuffixes": normalized_suffixes,
        "manifest": manifest,
    }


def _safe_parent(root: Path, target: Path, created_dirs: list[Path]) -> None:
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Archive member target escapes its owned root.") from exc
    chain: list[Path] = []
    cursor = target.parent
    while cursor != root:
        chain.append(cursor)
        cursor = cursor.parent
    for directory in reversed(chain):
        if directory.exists():
            if directory not in created_dirs:
                raise ValueError("A foreign archive extraction parent appeared during execution.")
            capture_directory(directory, label="Archive extraction parent")
            continue
        try:
            directory.mkdir()
            created_dirs.append(directory)
        except FileExistsError:
            raise ValueError("A foreign archive extraction parent appeared during execution.")


def _create_safe_root(assets: Path, root: Path, created_dirs: list[Path]) -> None:
    try:
        root.relative_to(assets)
    except ValueError as exc:
        raise ValueError("Archive target root escaped Unity Assets.") from exc
    chain: list[Path] = []
    cursor = root
    while cursor != assets:
        chain.append(cursor)
        cursor = cursor.parent
    for directory in reversed(chain):
        if directory.exists():
            capture_directory(directory, label="Archive target parent")
            if directory == root:
                raise ValueError("Approval-bound archive target root appeared after approval.")
            continue
        try:
            directory.mkdir()
            created_dirs.append(directory)
        except FileExistsError as exc:
            raise ValueError("An approval-bound absent archive directory appeared after approval.") from exc


def cleanup_owned_zip_extract(files: list[Path], directories: list[Path]) -> str:
    errors: list[str] = []
    for path in reversed(files):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"file cleanup failed: {exc}")
    for path in reversed(directories):
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"directory cleanup failed: {exc}")
    return "; ".join(errors)


def _single_target_name(raw: str) -> str:
    """Accept one exact, local temp-file name; never a caller-controlled path."""
    candidate = PurePosixPath(str(raw).replace("\\", "/"))
    if len(candidate.parts) != 1 or _safe_member_path(candidate.as_posix()) != candidate.as_posix():
        raise ValueError("Materialization target name is invalid.")
    return candidate.name


def prepare_zip_member_materialization(
    *,
    source: Path,
    temp_parent: Path,
    selected_members: list[dict[str, str]],
) -> dict[str, Any]:
    """Seal exact ZIP members for a later, create-new temp-file materialization.

    ``selected_members`` preserves queue order.  Each member needs its exact
    normalized archive ``path`` and an exact one-component ``targetName``.
    The raw central-directory name is carried from the manifest, rather than
    trusted again from the caller at execution time.
    """
    source_identity, source_sha256 = capture_regular_file(source, label="Archive source")
    parent_identity = capture_directory(temp_parent, label="Materialization temp parent")
    if not isinstance(selected_members, list) or not selected_members:
        raise ValueError("At least one archive member must be selected.")
    with source.open("rb") as source_handle:
        _opened_source_matches(source_handle, source_identity, source_sha256)
        with zipfile.ZipFile(source_handle) as archive:
            manifest = _entry_manifest(archive)
    by_path = {str(entry["path"]): entry for entry in manifest}
    selected: list[dict[str, Any]] = []
    used_paths: set[str] = set()
    used_targets: set[str] = set()
    for requested in selected_members:
        if not isinstance(requested, dict):
            raise ValueError("Selected archive member is invalid.")
        normalized = _safe_member_path(str(requested.get("path") or ""))
        entry = by_path.get(normalized)
        if entry is None or normalized in used_paths:
            raise ValueError("Selected archive member is absent or duplicated.")
        target_name = _single_target_name(str(requested.get("targetName") or ""))
        target_key = unicodedata.normalize("NFC", target_name).casefold()
        if target_key in used_targets:
            raise ValueError("Materialization target name is duplicated.")
        target = temp_parent / target_name
        if target.exists() or target.is_symlink():
            raise ValueError("Approval-bound materialization target already exists.")
        used_paths.add(normalized)
        used_targets.add(target_key)
        selected.append({
            "path": entry["path"],
            "zipPath": entry["zipPath"],
            "crc": entry["crc"],
            "compressedSize": entry["compressedSize"],
            "size": entry["size"],
            "sha256": entry["sha256"],
            "compressionRatio": entry["compressionRatio"],
            "targetName": target_name,
        })
    verify_regular_file(source_identity, source_sha256, label="Archive source")
    verify_directory(parent_identity, label="Materialization temp parent")
    return {
        "schema": "vrcforge.prepared-zip-materialization.v1",
        "sourceIdentity": source_identity,
        "sourceSha256": source_sha256,
        "tempParent": parent_identity,
        "manifest": manifest,
        "selected": selected,
    }


def _owned_file_record(path: Path, handle: Any, digest: str | None) -> dict[str, Any]:
    metadata = os.fstat(handle.fileno())
    return {
        "path": str(path.resolve()), "device": int(metadata.st_dev), "inode": int(metadata.st_ino),
        "size": int(metadata.st_size), "mtimeNs": int(metadata.st_mtime_ns),
        "sha256": digest,
    }


def cleanup_owned_zip_materialization(receipt: dict[str, Any]) -> str:
    """Explicitly remove only outputs whose original owned identity remains."""
    if not isinstance(receipt, dict) or receipt.get("schema") != "vrcforge.zip-materialization-receipt.v1":
        raise ValueError("Materialization cleanup receipt is invalid.")
    errors: list[str] = []
    for expected in reversed(receipt.get("ownedFiles") or []):
        path = Path(str(expected.get("path") or ""))
        try:
            identity, digest = capture_regular_file(path, label="Owned materialization output")
            if (
                identity.get("device") != expected.get("device")
                or identity.get("inode") != expected.get("inode")
                or identity.get("size") != expected.get("size")
                or identity.get("mtimeNs") != expected.get("mtimeNs")
                or (expected.get("sha256") is not None and digest != expected.get("sha256"))
            ):
                errors.append(f"owned output changed and was not deleted: {path}")
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"output cleanup failed: {exc}")
    return "; ".join(errors)


def execute_zip_member_materialization(facts: dict[str, Any]) -> dict[str, Any]:
    """Materialize sealed members in sealed order using one verified read handle."""
    if not isinstance(facts, dict) or facts.get("schema") != "vrcforge.prepared-zip-materialization.v1":
        raise ValueError("Prepared ZIP materialization facts are invalid.")
    source = verify_regular_file(facts["sourceIdentity"], facts["sourceSha256"], label="Archive source")
    parent = verify_directory(facts["tempParent"], label="Materialization temp parent")
    manifest = facts.get("manifest")
    selected = facts.get("selected")
    if not isinstance(manifest, list) or not isinstance(selected, list) or not selected:
        raise ValueError("Prepared ZIP materialization facts are invalid.")
    owned: list[dict[str, Any]] = []
    receipt = {"schema": "vrcforge.zip-materialization-receipt.v1", "ownedFiles": owned}
    try:
        with source.open("rb") as source_handle:
            _opened_source_matches(source_handle, facts["sourceIdentity"], facts["sourceSha256"])
            with zipfile.ZipFile(source_handle) as archive:
                if _entry_manifest(archive) != manifest:
                    raise ValueError("Archive central directory or member content drifted after approval.")
                manifest_by_path = {str(entry["path"]): entry for entry in manifest}
                seen_targets: set[str] = set()
                seen_paths: set[str] = set()
                for expected in selected:
                    if not isinstance(expected, dict):
                        raise ValueError("Prepared selected archive member is invalid.")
                    path = str(expected.get("path") or "")
                    canonical = manifest_by_path.get(path)
                    if canonical is None or path in seen_paths:
                        raise ValueError("Prepared selected archive member no longer matches the manifest.")
                    if any(expected.get(key) != canonical.get(key) for key in ("zipPath", "crc", "compressedSize", "size", "sha256", "compressionRatio")):
                        raise ValueError("Prepared selected archive member no longer matches the manifest.")
                    target_key = unicodedata.normalize("NFC", _single_target_name(str(expected.get("targetName") or ""))).casefold()
                    if target_key in seen_targets:
                        raise ValueError("Prepared materialization target is duplicated.")
                    seen_paths.add(path)
                    seen_targets.add(target_key)
                for expected in selected:
                    if not isinstance(expected, dict):
                        raise ValueError("Prepared selected archive member is invalid.")
                    target = parent / _single_target_name(str(expected.get("targetName") or ""))
                    if target.exists() or target.is_symlink():
                        raise ValueError("Approval-bound materialization target appeared after approval.")
                    digest = hashlib.sha256()
                    output_handle = target.open("xb")
                    record: dict[str, Any] | None = None
                    try:
                        with output_handle:
                            try:
                                with archive.open(str(expected["zipPath"]), "r") as input_handle:
                                    written = 0
                                    while chunk := input_handle.read(1024 * 1024):
                                        written += len(chunk)
                                        if written > int(expected["size"]):
                                            raise ValueError("Archive member exceeded its approved size.")
                                        output_handle.write(chunk)
                                        digest.update(chunk)
                            except Exception:
                                record = _owned_file_record(target, output_handle, None)
                                owned.append(record)
                                raise
                            opened_record = _owned_file_record(target, output_handle, digest.hexdigest())
                        identity, readback = capture_regular_file(target, label="Materialized archive member")
                        if identity.get("device") != opened_record["device"] or identity.get("inode") != opened_record["inode"]:
                            raise ValueError("Materialized archive member changed before readback.")
                        record = {**identity, "sha256": readback}
                        owned.append(record)
                    except Exception:
                        raise
                    if written != int(expected["size"]) or digest.hexdigest() != expected["sha256"]:
                        raise ValueError("Materialized archive member does not match approved facts.")
                    if record is None or record["sha256"] != expected["sha256"]:
                        raise ValueError("Materialized archive member changed before readback.")
        return {**receipt, "ok": True, "files": [item["path"] for item in owned]}
    except Exception as exc:
        cleanup_error = cleanup_owned_zip_materialization(receipt)
        if cleanup_error:
            raise RuntimeError(f"ZIP materialization failed; recovery cleanup also failed: {cleanup_error}") from exc
        raise


def execute_zip_extract(facts: dict[str, Any]) -> dict[str, Any]:
    """Revalidate sealed facts and extract only into the owned create-new root."""
    if not isinstance(facts, dict) or facts.get("schema") != "vrcforge.prepared-zip-extract.v1":
        raise ValueError("Prepared ZIP extraction facts are invalid.")
    source = verify_regular_file(facts["sourceIdentity"], facts["sourceSha256"], label="Archive source")
    project = verify_directory(facts["project"], label="Unity project")
    assets = verify_directory(facts["assets"], label="Unity Assets")
    for identity in facts.get("parentIdentities") or []:
        verify_directory(identity, label="Archive target parent")
    for relative_parent in facts.get("absentParentRelativePaths") or []:
        candidate = project.joinpath(*PurePosixPath(str(relative_parent)).parts)
        if candidate.exists() or candidate.is_symlink():
            raise ValueError("An approval-bound absent archive parent appeared after approval.")
    relative_root = PurePosixPath(str(facts["targetRootRelativePath"]))
    if relative_root.is_absolute() or any(part in {"", ".", ".."} for part in relative_root.parts):
        raise ValueError("Prepared archive target root is invalid.")
    root = project.joinpath(*relative_root.parts)
    try:
        root.relative_to(assets)
    except ValueError as exc:
        raise ValueError("Archive target root escaped Unity Assets.") from exc
    if root.exists() or root.is_symlink():
        raise ValueError("Approval-bound archive target root appeared after approval.")
    manifest = facts.get("manifest")
    if not isinstance(manifest, list):
        raise ValueError("Prepared ZIP extraction manifest is invalid.")
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        with source.open("rb") as source_handle:
            _opened_source_matches(source_handle, facts["sourceIdentity"], facts["sourceSha256"])
            with zipfile.ZipFile(source_handle) as archive:
                if _entry_manifest(archive) != manifest:
                    raise ValueError("Archive central directory or member content drifted after approval.")
                _create_safe_root(assets, root, created_dirs)
                for expected in manifest:
                    relative = str(expected["path"])
                    target = root.joinpath(*PurePosixPath(relative).parts)
                    try:
                        target.relative_to(root)
                    except ValueError as exc:
                        raise ValueError("Archive member target escaped its owned root.") from exc
                    _safe_parent(root, target, created_dirs)
                    digest = hashlib.sha256()
                    output_handle = target.open("xb")
                    created_files.append(target)
                    with output_handle:
                        with archive.open(str(expected["zipPath"]), "r") as input_handle:
                            bytes_written = 0
                            while chunk := input_handle.read(1024 * 1024):
                                bytes_written += len(chunk)
                                if bytes_written > int(expected["size"]):
                                    raise ValueError(f"Archive member exceeded its approved size: {relative}")
                                output_handle.write(chunk)
                                digest.update(chunk)
                    if bytes_written != int(expected["size"]) or digest.hexdigest() != expected["sha256"]:
                        raise ValueError(f"Archive member readback changed during extraction: {relative}")
                    _identity, readback_sha256 = capture_regular_file(target, label="Extracted archive member")
                    if readback_sha256 != expected["sha256"]:
                        raise ValueError(f"Archive member disk readback changed after extraction: {relative}")
        return {
            "ok": True,
            "targetRoot": str(root),
            "files": [str(path.relative_to(project).as_posix()) for path in created_files],
            "fileCount": len(created_files),
        }
    except Exception as exc:
        cleanup_error = cleanup_owned_zip_extract(created_files, created_dirs)
        if cleanup_error:
            raise RuntimeError(f"ZIP extraction failed; recovery cleanup also failed: {cleanup_error}") from exc
        raise
