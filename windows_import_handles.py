"""Windows handle-bound create-new copy and owned-output cleanup.

Every handle is scoped to one exact prepared project path, owned by the
current copy/cleanup call, authenticated by the current Windows process token
and filesystem ACLs, and closed before the call returns.  Directory handles
omit delete sharing for their lifetime so a checked parent cannot be swapped
to a junction between validation and target creation/deletion.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from ctypes import wintypes
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


_IS_WINDOWS = os.name == "nt"
_REPARSE_POINT = 0x0400
_FILE_ATTRIBUTE_DIRECTORY = 0x0010
_FILE_ATTRIBUTE_NORMAL = 0x0080
_FILE_READ_ATTRIBUTES = 0x0080
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_DISPOSITION_INFO = 4


if _IS_WINDOWS:
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class WindowsImportHandleError(RuntimeError):
    pass


def _win_error(message: str) -> WindowsImportHandleError:
    return WindowsImportHandleError(f"{message} (winerror={ctypes.get_last_error()})")


def _identity(path: Path, *, directory: bool) -> dict[str, Any]:
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT:
        raise WindowsImportHandleError(f"Prepared path became a symlink or reparse point: {path}")
    if directory and not stat.S_ISDIR(metadata.st_mode):
        raise WindowsImportHandleError(f"Prepared directory is no longer a directory: {path}")
    if not directory and not stat.S_ISREG(metadata.st_mode):
        raise WindowsImportHandleError(f"Prepared file is no longer a regular file: {path}")
    result = {
        "path": os.path.abspath(path),
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "attributes": attributes,
    }
    if not directory:
        result.update({"size": int(metadata.st_size), "mtimeNs": int(metadata.st_mtime_ns)})
    return result


def _normalize_handle_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def _handle_path(handle: int) -> str:
    size = 512
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        written = _kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0)
        if written == 0:
            raise _win_error("Could not read prepared handle path")
        if written < size:
            return _normalize_handle_path(buffer.value)
        size = int(written) + 1
    raise WindowsImportHandleError("Prepared handle path exceeded the supported length.")


def _handle_info(handle: int) -> _ByHandleFileInformation:
    info = _ByHandleFileInformation()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise _win_error("Could not read prepared handle identity")
    return info


def _open_directory(path: Path, *, delete_access: bool) -> int:
    access = _FILE_READ_ATTRIBUTES | (_DELETE if delete_access else 0)
    handle = _kernel32.CreateFileW(
        str(path),
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _win_error(f"Could not hold prepared directory: {path}")
    try:
        info = _handle_info(handle)
        if not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY or info.dwFileAttributes & _REPARSE_POINT:
            raise WindowsImportHandleError(f"Prepared directory handle is invalid or reparse-backed: {path}")
        if _handle_path(handle) != os.path.normcase(os.path.abspath(path)):
            raise WindowsImportHandleError(f"Prepared directory handle escaped its exact path: {path}")
        return int(handle)
    except Exception:
        _kernel32.CloseHandle(handle)
        raise


def _close_handle(handle: int | None) -> None:
    if handle not in (None, 0, _INVALID_HANDLE_VALUE):
        _kernel32.CloseHandle(handle)


def _mark_delete(handle: int, *, label: str) -> None:
    disposition = _FileDispositionInformation(True)
    if not _kernel32.SetFileInformationByHandle(
        handle,
        _FILE_DISPOSITION_INFO,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise _win_error(f"Could not delete owned {label} by handle")


def _directory_chain(assets: Path, target_parent: Path) -> list[Path]:
    relative = target_parent.relative_to(assets)
    chain = [assets]
    cursor = assets
    for part in relative.parts:
        cursor = cursor / part
        chain.append(cursor)
    return chain


def _hold_prepared_directories(
    *,
    project: Path,
    assets: Path,
    target_parent: Path,
    project_identity: dict[str, Any],
    assets_identity: dict[str, Any],
    parent_identities: list[dict[str, Any]],
    absent_parent_relative_paths: list[str],
    create_absent: bool,
) -> list[dict[str, Any]]:
    expected = {
        os.path.normcase(os.path.abspath(Path(str(identity.get("path") or "")))): identity
        for identity in [project_identity, assets_identity, *parent_identities]
    }
    absent = {
        os.path.normcase(os.path.abspath(project.joinpath(*PurePosixPath(value).parts)))
        for value in absent_parent_relative_paths
    }
    chain = [project, *_directory_chain(assets, target_parent)]
    held: list[dict[str, Any]] = []
    created_records: list[dict[str, Any]] = []
    try:
        for path in chain:
            key = os.path.normcase(os.path.abspath(path))
            planned = expected.get(key)
            created = key in absent
            created_now = False
            if planned is None and not created:
                raise WindowsImportHandleError(f"Prepared directory chain contains an unapproved path: {path}")
            if created:
                if not create_absent:
                    planned = next(
                        (identity for identity in parent_identities if os.path.normcase(os.path.abspath(Path(str(identity.get("path") or "")))) == key),
                        None,
                    )
                    if planned is None:
                        raise WindowsImportHandleError(f"Owned directory receipt is incomplete: {path}")
                    created = True
                else:
                    if os.path.lexists(path):
                        raise WindowsImportHandleError(f"Approval-bound absent parent appeared: {path}")
                    path.mkdir()
                    created_now = True
                    created_record = {
                        "path": path,
                        "handle": None,
                        "identity": None,
                        "created": True,
                    }
                    created_records.append(created_record)
                    try:
                        created_record["handle"] = _open_directory(path, delete_access=True)
                        planned = _identity(path, directory=True)
                        created_record["identity"] = planned
                    except Exception:
                        raise
            handle = (
                created_records[-1]["handle"]
                if created_now
                else _open_directory(path, delete_access=created)
            )
            item = {"path": path, "handle": handle, "identity": planned, "created": created}
            held.append(item)
            if created_now:
                created_records[-1]["handle"] = handle
                item = created_records[-1]
                held[-1] = item
            actual = _identity(path, directory=True)
            if created_now:
                planned = actual
            if planned is None or actual != planned:
                raise WindowsImportHandleError(f"Prepared directory identity drifted: {path}")
            item["identity"] = actual
        return held
    except Exception as exc:
        cleanup_errors: list[str] = []
        for item in reversed(created_records):
            handle = item.get("handle")
            held_original_handle = handle not in (None, 0, _INVALID_HANDLE_VALUE)
            try:
                path = Path(item["path"])
                if handle in (None, 0, _INVALID_HANDLE_VALUE):
                    if not os.path.lexists(path):
                        continue
                    if item.get("identity") is None:
                        raise WindowsImportHandleError(
                            f"Could not prove identity of newly created directory: {path}"
                        )
                    handle = _open_directory(path, delete_access=True)
                    item["handle"] = handle
                if item.get("identity") is not None and _identity(path, directory=True) != item["identity"]:
                    raise WindowsImportHandleError(
                        f"Refusing to delete replaced prepared directory: {path}"
                    )
                if item.get("identity") is None and not held_original_handle:
                    raise WindowsImportHandleError(
                        f"Could not retain original handle for newly created directory: {path}"
                    )
                _mark_delete(handle, label="prepared import directory")
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve all recovery failures.
                cleanup_errors.append(f"{item['path']}: {cleanup_exc}")
            finally:
                _close_handle(item.get("handle"))
                item["handle"] = None
        for item in reversed(held):
            _close_handle(item.get("handle"))
            item["handle"] = None
        if cleanup_errors:
            raise WindowsImportHandleError(
                "Prepared parent creation failed; handle-bound cleanup also failed: "
                + "; ".join(cleanup_errors)
            ) from exc
        raise


def _close_directories(held: list[dict[str, Any]]) -> None:
    for item in reversed(held):
        _close_handle(item.get("handle"))


def _delete_created_directories(held: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item in reversed(held):
        if not item.get("created"):
            continue
        try:
            _mark_delete(item["handle"], label="import directory")
        except Exception as exc:  # noqa: BLE001 - recovery must report every retained path.
            errors.append(f"directory cleanup failed for {item['path']}: {exc}")
        finally:
            _close_handle(item["handle"])
            item["handle"] = None
    return errors


def _create_target_handle(target: Path) -> int:
    handle = _kernel32.CreateFileW(
        str(target),
        _GENERIC_READ | _GENERIC_WRITE | _DELETE,
        0,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _win_error(f"Could not create approval-bound target: {target}")
    info = _handle_info(handle)
    if info.dwFileAttributes & (_FILE_ATTRIBUTE_DIRECTORY | _REPARSE_POINT):
        _close_handle(handle)
        raise WindowsImportHandleError("Approval-bound target handle is not a regular file.")
    if _handle_path(handle) != os.path.normcase(os.path.abspath(target)):
        _close_handle(handle)
        raise WindowsImportHandleError("Approval-bound target handle escaped its exact path.")
    return int(handle)


def secure_copy_create_new(
    *,
    source_handle: BinaryIO,
    source_sha256: str,
    project: Path,
    assets: Path,
    target: Path,
    project_identity: dict[str, Any],
    assets_identity: dict[str, Any],
    parent_identities: list[dict[str, Any]],
    absent_parent_relative_paths: list[str],
) -> tuple[str, dict[str, Any]]:
    if not _IS_WINDOWS:
        raise WindowsImportHandleError("Windows handle-bound imports require Windows.")
    held = _hold_prepared_directories(
        project=project,
        assets=assets,
        target_parent=target.parent,
        project_identity=project_identity,
        assets_identity=assets_identity,
        parent_identities=parent_identities,
        absent_parent_relative_paths=absent_parent_relative_paths,
        create_absent=True,
    )
    target_handle: int | None = None
    target_fd: int | None = None
    try:
        target_handle = _create_target_handle(target)
        target_fd = msvcrt.open_osfhandle(target_handle, os.O_BINARY | os.O_RDWR)
        with os.fdopen(target_fd, "w+b", closefd=False) as target_file:
            digest = hashlib.sha256()
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                target_file.write(chunk)
                digest.update(chunk)
            target_file.flush()
            if not _kernel32.FlushFileBuffers(target_handle):
                raise _win_error("Could not flush approval-bound target")
            copied_digest = digest.hexdigest()
            if copied_digest != source_sha256:
                raise WindowsImportHandleError("Copied import bytes do not match the approval-bound source hash.")
            target_file.seek(0)
            readback = hashlib.sha256()
            for chunk in iter(lambda: target_file.read(1024 * 1024), b""):
                readback.update(chunk)
            if readback.hexdigest() != source_sha256:
                raise WindowsImportHandleError("Imported target handle readback hash does not match approval.")
            opened = os.fstat(target_fd)
            target_identity = {
                "path": os.path.abspath(target),
                "device": int(opened.st_dev),
                "inode": int(opened.st_ino),
                "attributes": int(getattr(opened, "st_file_attributes", 0) or 0),
                "size": int(opened.st_size),
                "mtimeNs": int(opened.st_mtime_ns),
            }
        ownership = {
            "schema": "vrcforge.owned-import-output.v2",
            "project": project_identity,
            "assets": assets_identity,
            "guardDirectories": [item["identity"] for item in held if not item["created"]],
            "createdDirectories": [item["identity"] for item in held if item["created"]],
            "targetIdentity": target_identity,
            "targetSha256": source_sha256,
        }
        os.close(target_fd)
        target_fd = None
        target_handle = None
        return source_sha256, ownership
    except Exception as exc:
        cleanup_errors: list[str] = []
        if target_handle is not None:
            try:
                _mark_delete(target_handle, label="import target")
            except Exception as cleanup_exc:  # noqa: BLE001
                cleanup_errors.append(f"target cleanup failed: {cleanup_exc}")
        if target_fd is not None:
            os.close(target_fd)
            target_fd = None
            target_handle = None
        elif target_handle is not None:
            _close_handle(target_handle)
            target_handle = None
        cleanup_errors.extend(_delete_created_directories(held))
        if cleanup_errors:
            raise WindowsImportHandleError(
                f"Import copy failed; handle-bound cleanup also failed: {'; '.join(cleanup_errors)}"
            ) from exc
        raise
    finally:
        if target_fd is not None:
            os.close(target_fd)
        elif target_handle is not None:
            _close_handle(target_handle)
        _close_directories(held)


def secure_cleanup_owned(target: Path | None, ownership: dict[str, Any]) -> str:
    if not _IS_WINDOWS:
        return "Windows handle-bound cleanup requires Windows"
    if target is None:
        return "" if not ownership.get("createdDirectories") else "owned target is missing while created directories remain"
    if ownership.get("schema") != "vrcforge.owned-import-output.v2":
        return "owned import cleanup refused an invalid ownership receipt"
    project_identity = ownership.get("project")
    assets_identity = ownership.get("assets")
    guard_directories = ownership.get("guardDirectories")
    created_directories = ownership.get("createdDirectories")
    target_identity = ownership.get("targetIdentity")
    expected_digest = ownership.get("targetSha256")
    if not all(isinstance(value, dict) for value in (project_identity, assets_identity, target_identity)) \
            or not isinstance(guard_directories, list) or not isinstance(created_directories, list) \
            or not isinstance(expected_digest, str):
        return "owned import cleanup refused an incomplete ownership receipt"
    project = Path(str(project_identity.get("path") or ""))
    assets = Path(str(assets_identity.get("path") or ""))
    parent_identities = [
        identity for identity in [*guard_directories, *created_directories]
        if isinstance(identity, dict)
        and os.path.normcase(os.path.abspath(Path(str(identity.get("path") or ""))))
        not in {os.path.normcase(os.path.abspath(project)), os.path.normcase(os.path.abspath(assets))}
    ]
    absent_relatives = [
        Path(str(identity.get("path") or "")).relative_to(project).as_posix()
        for identity in created_directories
        if isinstance(identity, dict)
    ]
    held: list[dict[str, Any]] = []
    target_handle: int | None = None
    target_fd: int | None = None
    try:
        held = _hold_prepared_directories(
            project=project,
            assets=assets,
            target_parent=target.parent,
            project_identity=project_identity,
            assets_identity=assets_identity,
            parent_identities=parent_identities,
            absent_parent_relative_paths=absent_relatives,
            create_absent=False,
        )
        if not os.path.lexists(target):
            return "; ".join(_delete_created_directories(held))
        target_handle = _kernel32.CreateFileW(
            str(target),
            _GENERIC_READ | _DELETE,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if target_handle == _INVALID_HANDLE_VALUE:
            return str(_win_error(f"target cleanup could not lock exact output: {target}"))
        if _handle_path(target_handle) != os.path.normcase(os.path.abspath(target)):
            return f"target cleanup refused escaped output: {target}"
        target_fd = msvcrt.open_osfhandle(target_handle, os.O_BINARY | os.O_RDONLY)
        with os.fdopen(target_fd, "rb", closefd=False) as target_file:
            opened = os.fstat(target_fd)
            actual_identity = {
                "path": os.path.abspath(target),
                "device": int(opened.st_dev),
                "inode": int(opened.st_ino),
                "attributes": int(getattr(opened, "st_file_attributes", 0) or 0),
                "size": int(opened.st_size),
                "mtimeNs": int(opened.st_mtime_ns),
            }
            digest = hashlib.sha256()
            for chunk in iter(lambda: target_file.read(1024 * 1024), b""):
                digest.update(chunk)
        if actual_identity != target_identity or digest.hexdigest() != expected_digest:
            return f"target cleanup refused foreign or modified replacement: {target}"
        _mark_delete(target_handle, label="import target")
        os.close(target_fd)
        target_fd = None
        target_handle = None
        directory_errors = _delete_created_directories(held)
        return "; ".join(directory_errors)
    except Exception as exc:  # noqa: BLE001 - cleanup reports recoverable state.
        return f"owned import cleanup failed: {exc}"
    finally:
        if target_fd is not None:
            os.close(target_fd)
        elif target_handle not in (None, _INVALID_HANDLE_VALUE):
            _close_handle(target_handle)
        _close_directories(held)
