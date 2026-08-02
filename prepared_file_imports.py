"""Bounded, approval-time file identities for project-scoped imports.

No handle survives preparation: each operation owns its ``with`` scopes and
reopens files at execution after identity and digest validation.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from windows_import_handles import secure_cleanup_owned, secure_copy_create_new


_REPARSE_POINT = 0x0400


def _stat_identity(path: Path, *, kind: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{kind} is unavailable: {path}") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    if path.is_symlink() or attributes & _REPARSE_POINT:
        raise ValueError(f"{kind} may not be a symlink or reparse point: {path}")
    if kind.endswith("directory") and not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{kind} is not a directory: {path}")
    if kind.endswith("file") and not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{kind} is not a regular file: {path}")
    identity = {
        "path": os.path.abspath(path),
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "attributes": attributes,
    }
    # A directory's mtime changes when a sibling is created.  Its object
    # identity, not its mutable listing timestamp, is the approval boundary.
    if kind.endswith("file"):
        identity["size"] = int(metadata.st_size)
        identity["mtimeNs"] = int(metadata.st_mtime_ns)
    return identity


def capture_regular_file(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    """Capture a regular-file identity and content hash within one handle scope."""
    path_identity = _stat_identity(path, kind=f"{label} file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        identity = {
            "path": path_identity["path"],
            "device": int(opened.st_dev),
            "inode": int(opened.st_ino),
            "attributes": int(getattr(opened, "st_file_attributes", path_identity["attributes"]) or 0),
            "size": int(opened.st_size),
            "mtimeNs": int(opened.st_mtime_ns),
        }
        if identity != path_identity:
            raise ValueError(f"{label} changed while its read handle was opened.")
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        closed_over = os.fstat(handle.fileno())
        if (
            int(closed_over.st_dev) != identity["device"]
            or int(closed_over.st_ino) != identity["inode"]
            or int(closed_over.st_size) != identity["size"]
            or int(closed_over.st_mtime_ns) != identity["mtimeNs"]
        ):
            raise ValueError(f"{label} changed while it was read.")
    if _stat_identity(path, kind=f"{label} file") != identity:
        raise ValueError(f"{label} path identity changed while it was read.")
    return identity, digest.hexdigest()


def verify_regular_file(identity: dict[str, Any], expected_sha256: str, *, label: str) -> Path:
    path = Path(str(identity.get("path") or ""))
    actual, digest = capture_regular_file(path, label=label)
    if actual != identity or digest != expected_sha256:
        raise ValueError(f"{label} identity or content drifted after approval.")
    return path


def capture_directory(path: Path, *, label: str) -> dict[str, Any]:
    return _stat_identity(path, kind=f"{label} directory")


def verify_directory(identity: dict[str, Any], *, label: str) -> Path:
    path = Path(str(identity.get("path") or ""))
    if capture_directory(path, label=label) != identity:
        raise ValueError(f"{label} identity drifted after approval.")
    return path


def prepare_project_asset_target(project_root: Path, target_folder: str, filename: str) -> dict[str, Any]:
    """Bind a currently absent target under an exact existing Assets directory."""
    project = Path(os.path.abspath(project_root.expanduser()))
    assets = project / "Assets"
    project_identity = capture_directory(project, label="Unity project")
    assets_identity = capture_directory(assets, label="Unity Assets")
    relative_folder = PurePosixPath(str(target_folder).replace("\\", "/").strip().strip("/"))
    parts = relative_folder.parts
    if len(parts) < 2 or parts[0] != "Assets" or relative_folder.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Target folder must be under Assets/.")
    target_relative = relative_folder / filename
    target = project.joinpath(*target_relative.parts)
    try:
        target.relative_to(assets)
    except ValueError as exc:
        raise ValueError("Import target escapes Unity Assets.") from exc
    if target.exists() or target.is_symlink():
        raise ValueError("The approval-bound import target already exists.")
    parent_identities: list[dict[str, Any]] = []
    absent_parent_relative_paths: list[str] = []
    cursor = target.parent
    while cursor != assets:
        if cursor.exists() or cursor.is_symlink():
            parent_identities.append(capture_directory(cursor, label="Import target parent"))
        else:
            absent_parent_relative_paths.append(cursor.relative_to(project).as_posix())
        cursor = cursor.parent
    return {
        "project": project_identity,
        "assets": assets_identity,
        "parentIdentities": parent_identities,
        "absentParentRelativePaths": absent_parent_relative_paths,
        "targetRelativePath": target_relative.as_posix(),
    }


def _stable_identity(identity: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(identity.get("device", -1)),
        int(identity.get("inode", -1)),
        int(identity.get("attributes", -1)),
    )


def _create_safe_target_parent(project: Path, assets: Path, target: Path) -> tuple[Path, list[dict[str, Any]]]:
    try:
        target.relative_to(assets)
    except ValueError as exc:
        raise ValueError("Import target escaped Unity Assets.") from exc
    created: list[dict[str, Any]] = []
    parent = target.parent
    chain: list[Path] = []
    cursor = parent
    while cursor != assets:
        chain.append(cursor)
        cursor = cursor.parent
    try:
        for directory in reversed(chain):
            if directory.exists():
                capture_directory(directory, label="Import target parent")
                continue
            try:
                directory.mkdir()
                created.append(capture_directory(directory, label="Created import target parent"))
            except FileExistsError as exc:
                raise ValueError("An approval-bound absent import parent appeared after approval.") from exc
    except Exception as exc:
        cleanup_errors: list[str] = []
        for identity in reversed(created):
            directory = Path(str(identity.get("path") or ""))
            try:
                if _stable_identity(capture_directory(directory, label="Created import target parent")) != _stable_identity(identity):
                    cleanup_errors.append(f"directory cleanup refused foreign replacement: {directory}")
                    continue
                directory.rmdir()
            except FileNotFoundError:
                continue
            except OSError as cleanup_exc:
                cleanup_errors.append(f"directory cleanup failed: {cleanup_exc}")
        if cleanup_errors:
            raise RuntimeError(
                f"Import parent creation failed; recovery cleanup also failed: {'; '.join(cleanup_errors)}"
            ) from exc
        raise
    return parent, created


def copy_approved_file_create_new(
    *,
    source_identity: dict[str, Any],
    source_sha256: str,
    project_identity: dict[str, Any],
    assets_identity: dict[str, Any],
    parent_identities: list[dict[str, Any]],
    absent_parent_relative_paths: list[str],
    target_relative_path: str,
) -> tuple[Path, str, dict[str, Any]]:
    """Revalidate and copy into a single exact create-new project target."""
    source = Path(str(source_identity.get("path") or ""))
    project = verify_directory(project_identity, label="Unity project")
    assets = verify_directory(assets_identity, label="Unity Assets")
    for parent_identity in parent_identities:
        verify_directory(parent_identity, label="Import target parent")
    for relative_parent in absent_parent_relative_paths:
        candidate = project.joinpath(*PurePosixPath(relative_parent).parts)
        if candidate.exists() or candidate.is_symlink():
            raise ValueError("An approval-bound absent import parent appeared after approval.")
    relative_target = PurePosixPath(target_relative_path)
    if relative_target.is_absolute() or any(part in {"", ".", ".."} for part in relative_target.parts):
        raise ValueError("Approval-bound import target is invalid.")
    target = project.joinpath(*relative_target.parts)
    if target.exists() or target.is_symlink():
        raise ValueError("The approval-bound import target appeared after approval.")
    created_directories: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    target_identity: dict[str, Any] | None = None
    try:
        with source.open("rb") as source_handle:
            opened_source = os.fstat(source_handle.fileno())
            opened_source_identity = {
                "path": os.path.abspath(source),
                "device": int(opened_source.st_dev),
                "inode": int(opened_source.st_ino),
                "attributes": int(getattr(opened_source, "st_file_attributes", 0) or 0),
                "size": int(opened_source.st_size),
                "mtimeNs": int(opened_source.st_mtime_ns),
            }
            if opened_source_identity != source_identity:
                raise ValueError("Import source identity drifted after approval.")
            source_digest = hashlib.sha256()
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                source_digest.update(chunk)
            if source_digest.hexdigest() != source_sha256:
                raise ValueError("Import source content drifted after approval.")
            if os.fstat(source_handle.fileno()).st_mtime_ns != opened_source.st_mtime_ns:
                raise ValueError("Import source changed while it was verified.")
            if _stat_identity(source, kind="Import source file") != source_identity:
                raise ValueError("Import source path identity drifted after approval.")
            source_handle.seek(0)

            if os.name == "nt":
                copied_digest, ownership = secure_copy_create_new(
                    source_handle=source_handle,
                    source_sha256=source_sha256,
                    project=project,
                    assets=assets,
                    target=target,
                    project_identity=project_identity,
                    assets_identity=assets_identity,
                    parent_identities=parent_identities,
                    absent_parent_relative_paths=absent_parent_relative_paths,
                )
                closed_source = os.fstat(source_handle.fileno())
                if (
                    int(closed_source.st_dev) != int(opened_source.st_dev)
                    or int(closed_source.st_ino) != int(opened_source.st_ino)
                    or int(closed_source.st_size) != int(opened_source.st_size)
                    or int(closed_source.st_mtime_ns) != int(opened_source.st_mtime_ns)
                ):
                    cleanup_error = secure_cleanup_owned(target, ownership)
                    if cleanup_error:
                        raise RuntimeError(
                            f"Import source changed during copy; handle-bound target cleanup failed: {cleanup_error}"
                        )
                    raise ValueError("Import source changed while it was copied.")
                return target, copied_digest, ownership

            # Recheck every existing and newly created parent immediately before
            # create-new.  This keeps path traversal and parent ownership inside
            # the same source-handle lifetime as the copy.
            verify_directory(project_identity, label="Unity project")
            verify_directory(assets_identity, label="Unity Assets")
            for parent_identity in parent_identities:
                verify_directory(parent_identity, label="Import target parent")
            _parent, created_directories = _create_safe_target_parent(project, assets, target)
            for created_identity in created_directories:
                verify_directory(created_identity, label="Created import target parent")
            if target.exists() or target.is_symlink():
                raise ValueError("The approval-bound import target appeared before create-new.")

            target_handle = target.open("xb")
            with target_handle:
                opened_target = os.fstat(target_handle.fileno())
                target_identity = {
                    "path": os.path.abspath(target),
                    "device": int(opened_target.st_dev),
                    "inode": int(opened_target.st_ino),
                    "attributes": int(getattr(opened_target, "st_file_attributes", 0) or 0),
                }
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    target_handle.write(chunk)
                    digest.update(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            closed_source = os.fstat(source_handle.fileno())
            if (
                int(closed_source.st_dev) != int(opened_source.st_dev)
                or int(closed_source.st_ino) != int(opened_source.st_ino)
                or int(closed_source.st_size) != int(opened_source.st_size)
                or int(closed_source.st_mtime_ns) != int(opened_source.st_mtime_ns)
            ):
                raise ValueError("Import source changed while it was copied.")
        copied_digest = digest.hexdigest()
        if copied_digest != source_sha256:
            raise ValueError("Copied import bytes do not match the approval-bound source hash.")
        # Reopen and hash the target so the result is a readback, not only the
        # bytes seen while writing.
        readback_identity, readback_digest = capture_regular_file(target, label="Imported target")
        if target_identity is None or _stable_identity(readback_identity) != _stable_identity(target_identity) or readback_digest != source_sha256:
            raise ValueError("Imported target readback hash does not match the approval-bound source hash.")
        ownership = {
            "schema": "vrcforge.owned-import-output.v1",
            "targetIdentity": readback_identity,
            "targetSha256": readback_digest,
            "createdDirectories": created_directories,
        }
        return target, readback_digest, ownership
    except Exception as exc:
        if os.name == "nt":
            raise
        cleanup_errors: list[str] = []
        if target_identity is not None:
            cleanup_errors.append(cleanup_owned_import(
                target,
                {
                    "schema": "vrcforge.owned-import-output.v1",
                    "targetIdentity": target_identity,
                    "targetSha256": None,
                    "createdDirectories": created_directories,
                },
            ))
        else:
            cleanup_errors.append(cleanup_owned_import(
                None,
                {
                    "schema": "vrcforge.owned-import-output.v1",
                    "targetIdentity": None,
                    "targetSha256": None,
                    "createdDirectories": created_directories,
                },
            ))
        cleanup_errors = [error for error in cleanup_errors if error]
        if cleanup_errors:
            raise RuntimeError(
                f"Import copy failed; recovery cleanup also failed: {'; '.join(cleanup_errors)}"
            ) from exc
        raise


def cleanup_owned_import(target: Path | None, ownership: dict[str, Any]) -> str:
    """Remove only create-new outputs owned by this transaction; return an error string."""
    if target is None and not (ownership.get("createdDirectories") if isinstance(ownership, dict) else None):
        return ""
    if os.name == "nt":
        return secure_cleanup_owned(target, ownership)
    errors: list[str] = []
    if not isinstance(ownership, dict) or ownership.get("schema") != "vrcforge.owned-import-output.v1":
        return "owned import cleanup refused an invalid ownership receipt"
    target_identity = ownership.get("targetIdentity")
    if target is not None:
        try:
            if not target.exists() and not target.is_symlink():
                pass
            elif not isinstance(target_identity, dict):
                errors.append(f"target cleanup refused missing ownership identity: {target}")
            else:
                current_identity, current_digest = capture_regular_file(target, label="Owned import target")
                expected_digest = ownership.get("targetSha256")
                if _stable_identity(current_identity) != _stable_identity(target_identity):
                    errors.append(f"target cleanup refused foreign replacement: {target}")
                elif isinstance(expected_digest, str) and expected_digest and current_digest != expected_digest:
                    errors.append(f"target cleanup refused modified owned output: {target}")
                elif _stable_identity(_stat_identity(target, kind="Owned import target file")) != _stable_identity(target_identity):
                    errors.append(f"target cleanup refused raced replacement: {target}")
                else:
                    target.unlink()
        except ValueError as exc:
            errors.append(f"target cleanup refused unverifiable output: {exc}")
        except OSError as exc:
            errors.append(f"target cleanup failed: {exc}")
    created_directories = ownership.get("createdDirectories")
    if not isinstance(created_directories, list):
        return "; ".join(errors + ["owned import cleanup refused invalid directory ownership"])
    for identity in reversed(created_directories):
        if not isinstance(identity, dict):
            errors.append("directory cleanup refused invalid ownership identity")
            continue
        directory = Path(str(identity.get("path") or ""))
        try:
            if not directory.exists() and not directory.is_symlink():
                continue
            if _stable_identity(capture_directory(directory, label="Owned import directory")) != _stable_identity(identity):
                errors.append(f"directory cleanup refused foreign replacement: {directory}")
                continue
            directory.rmdir()
        except FileNotFoundError:
            continue
        except ValueError as exc:
            errors.append(f"directory cleanup refused unverifiable output: {exc}")
        except OSError as exc:
            errors.append(f"directory cleanup failed: {exc}")
    return "; ".join(errors)
