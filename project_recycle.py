"""Remove one closed Unity project using the Windows Shell."""
from __future__ import annotations

import ctypes as c
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from session_store_integrity import path_has_link_like_segment

# COPYENGINE_E_RECYCLE_FORCE_NUKE, _SIZE_TOO_BIG and _BIN_NOT_FOUND (sherrors.h).
_RECYCLE_UNAVAILABLE = {0x80270036, 0x80270037, 0x8027003A}


class _RecycleUnavailable(OSError):
    pass


@dataclass(frozen=True)
class ProjectRemoval:
    mode: str
    cleanup_warning: str = ""


def validate_project(path: str, protected: tuple[Path, ...]) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate == Path(candidate.anchor) or ".." in candidate.parts:
        raise ValueError("Select an absolute Unity project folder.")
    if path_has_link_like_segment(candidate, Path(candidate.anchor)):
        raise ValueError("A linked Unity project folder cannot be deleted here.")
    project = candidate.resolve(strict=True)
    if any(root.resolve().is_relative_to(project) for root in protected):
        raise ValueError("This is a VRCForge program or data folder, or a parent of one.")
    markers = (project / "Assets", project / "Packages", project / "ProjectSettings")
    if (not all(p.is_dir() and not path_has_link_like_segment(p, project) for p in markers)
            or not (project / "ProjectSettings" / "ProjectVersion.txt").is_file()
            or not (project / "Packages" / "manifest.json").is_file()):
        raise ValueError("The folder is not a complete Unity project.")
    return project


def remove_project_folder(project: Path) -> ProjectRemoval:
    identity = project.stat()
    try:
        _recycle_project_folder(project)
        return ProjectRemoval("recycle")
    except _RecycleUnavailable:
        if not os.path.samestat(project.stat(), identity) or path_has_link_like_segment(project, Path(project.anchor)):
            raise OSError("The Unity project changed while Windows tried to recycle it.")

    # Commit removal of the original path in one same-volume rename, before
    # purging any file. A purge failure must not restore registrations for an
    # incomplete directory. The staging path is not a backup or recovery point.
    staging = project.parent / f".vrcforge-deleting-{uuid4().hex}"
    project.rename(staging)
    try:
        shutil.rmtree(staging)
    except OSError as exc:
        return ProjectRemoval("permanent", f"The project was removed, but Windows could not purge all files at {staging}. {exc}")
    return ProjectRemoval("permanent")


def _delete_progress_sink(result_status: list[int]):
    """Keep the actual per-item HRESULT when PerformOperations reports only an abort."""
    ptr, word, hr, string = c.c_void_p, c.c_uint32, c.c_long, c.c_wchar_p
    interfaces = {
        UUID("00000000-0000-0000-c000-000000000046").bytes_le,
        UUID("04b0f1a7-9490-44bc-96e1-4296a31252e2").bytes_le,
    }
    references = 1

    def add_ref(_self):
        nonlocal references
        references += 1
        return references

    def release(_self):
        nonlocal references
        references -= 1
        return references

    def query(self, iid, output):
        if c.string_at(iid, 16) in interfaces:
            output[0] = self
            add_ref(self)
            return 0
        output[0] = None
        return -2147467262  # E_NOINTERFACE

    def post_delete(_self, _flags, _item, status, _created):
        result_status.append(status & 0xffffffff)
        return 0

    def noop(*_args):
        return 0

    methods = [
        (hr, (ptr, c.POINTER(ptr)), query), (word, (), add_ref), (word, (), release),
        (hr, (), noop), (hr, (hr,), noop),  # StartOperations, FinishOperations
        (hr, (word, ptr, string), noop), (hr, (word, ptr, string, hr, ptr), noop),  # Rename
        (hr, (word, ptr, ptr, string), noop), (hr, (word, ptr, ptr, string, hr, ptr), noop),  # Move
        (hr, (word, ptr, ptr, string), noop), (hr, (word, ptr, ptr, string, hr, ptr), noop),  # Copy
        (hr, (word, ptr), noop), (hr, (word, ptr, hr, ptr), post_delete),
        (hr, (word, ptr, string), noop), (hr, (word, ptr, string, string, word, hr, ptr), noop),  # New
        (hr, (word, word), noop), (hr, (), noop), (hr, (), noop), (hr, (), noop),  # Progress/timers
    ]
    callbacks = [c.WINFUNCTYPE(returns, ptr, *args)(method) for returns, args, method in methods]
    vtable = (ptr * len(callbacks))(*(c.cast(callback, ptr) for callback in callbacks))
    return c.pointer(c.cast(vtable, ptr)), callbacks


def _recycle_project_folder(project: Path) -> None:
    if os.name != "nt" or c.windll.kernel32.GetDriveTypeW(c.c_wchar_p(project.anchor)) != 3:
        raise OSError("Project deletion requires a local Windows drive with a Recycle Bin.")

    # A single Shell item, owned by the calling thread's COM apartment. The
    # extended recycle flag requires recycling rather than permanent deletion.
    ole = c.OleDLL("ole32")
    shell = c.OleDLL("shell32")
    result = c.c_long
    pointer = c.c_void_p
    operation = pointer()
    item = pointer()

    def guid(value: str):
        return (c.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)

    def call(obj, index: int, returns, types, *args):
        table = c.cast(obj, c.POINTER(c.POINTER(pointer)))[0]
        return c.WINFUNCTYPE(returns, pointer, *types)(table[index])(obj, *args)

    def check(hr: int) -> None:
        if hr < 0:
            raise OSError(f"Windows Recycle Bin operation failed (0x{hr & 0xffffffff:08X}).")

    ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    try:
        clsid = guid("3ad05575-8857-4850-9277-11b85bdb8e09")
        iid_operation = guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
        iid_item = guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
        ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid_operation), c.byref(operation))
        shell.SHCreateItemFromParsingName(c.c_wchar_p(str(project)), None, c.byref(iid_item), c.byref(item))
        silent, no_confirmation, no_error_ui = 0x0004, 0x0010, 0x0400
        recycle, early_failure, add_undo_record = 0x00080000, 0x00100000, 0x20000000
        flags = silent | no_confirmation | no_error_ui | recycle | early_failure | add_undo_record
        check(call(operation, 5, result, (c.c_uint32,), flags))  # SetOperationFlags
        item_results: list[int] = []
        sink, callbacks = _delete_progress_sink(item_results)
        check(call(operation, 18, result, (pointer, pointer), item, c.cast(sink, pointer)))  # DeleteItem
        outcome = call(operation, 21, result, ())  # PerformOperations
        if not project.exists():
            return
        failures = [status for status in item_results if status & 0x80000000]
        if not failures and outcome < 0:
            failures.append(outcome & 0xffffffff)
        if failures and all(status in _RECYCLE_UNAVAILABLE for status in failures):
            raise _RecycleUnavailable("Windows explicitly refused to recycle this project.")
        check(c.c_long(failures[0]).value if failures else outcome)
        raise OSError("Windows reported that recycling did not complete.")
    except _RecycleUnavailable:
        raise
    except OSError as exc:
        raise OSError(
            "Windows could not move this project to the Recycle Bin. The project may exceed the bin's size limit "
            f"or the bin may be unavailable on this drive. No permanent deletion was attempted. {exc}"
        ) from exc
    finally:
        for obj in (item, operation):
            if obj:
                call(obj, 2, c.c_ulong, ())  # Release
        ole.CoUninitialize()
