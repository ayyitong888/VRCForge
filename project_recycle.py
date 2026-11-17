"""Move one closed Unity project to the Windows Recycle Bin."""
from __future__ import annotations

import ctypes as c
import os
from pathlib import Path
from uuid import UUID

from session_store_integrity import path_has_link_like_segment


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


def recycle_project_folder(project: Path) -> None:
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
    aborted = c.c_int()

    def guid(value: str):
        return (c.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)

    def call(obj, index: int, returns, types, *args):
        table = c.cast(obj, c.POINTER(c.POINTER(pointer)))[0]
        return c.WINFUNCTYPE(returns, pointer, *types)(table[index])(obj, *args)

    def check(hr: int) -> None:
        if hr < 0:
            raise OSError(f"Windows could not recycle the project (0x{hr & 0xffffffff:08X}).")

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
        check(call(operation, 18, result, (pointer, pointer), item, None))  # DeleteItem
        check(call(operation, 21, result, ()))  # PerformOperations
        check(call(operation, 22, result, (c.POINTER(c.c_int),), c.byref(aborted)))
        if aborted.value or project.exists():
            raise OSError("Windows did not move the project to the Recycle Bin.")
    finally:
        for obj in (item, operation):
            if obj:
                call(obj, 2, c.c_ulong, ())  # Release
        ole.CoUninitialize()
