"""Guarded, small-scope filesystem mutation tools for the General Agent.

These functions deliberately contain no policy about protected roots.  The
caller supplies ``path_guard``; it is invoked before every source/destination
mutation with the operation, capability, and current-project context.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


PathGuard = Callable[..., Any]

TOOL_EDIT_FILE = "edit_file"
TOOL_WRITE_FILE = "write_file"
TOOL_DELETE_PATH = "delete_path"
TOOL_MOVE_PATH = "move_path"
TOOL_APPLY_PATCH = "apply_patch"

GENERAL_AGENT_WRITE_TOOL_NAMES = (
    TOOL_EDIT_FILE,
    TOOL_WRITE_FILE,
    TOOL_DELETE_PATH,
    TOOL_MOVE_PATH,
    TOOL_APPLY_PATCH,
)
GENERAL_AGENT_WRITE_TOOLS = tuple(
    {
        "name": name,
        "description": f"{name}: guarded General Agent filesystem mutation. "
        "when-to-use: an explicit approved file operation is requested. "
        "when-NOT-to-use: reading, recursive deletion, Unity writes, or an "
        "operation without a path guard and capability context. Negative example: "
        "do not use a General write tool to modify a registered Unity project.",
    }
    for name in GENERAL_AGENT_WRITE_TOOL_NAMES
)

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")


def _path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _authorize(
    value: str | os.PathLike[str],
    *,
    operation: str,
    path_guard: PathGuard,
    capability: Any,
    current_project: Any,
) -> Path:
    if not callable(path_guard):
        raise PermissionError("a path_guard is required for General Agent writes")
    path = _path(value)
    try:
        decision = path_guard(
            path,
            operation=operation,
            capability=capability,
            current_project=current_project,
        )
    except TypeError:
        # Permit simple guards used by integrations while retaining context
        # for guards that support the formal callback contract.
        decision = path_guard(path, operation, capability, current_project)
    if decision is False or decision is None:
        raise PermissionError(f"path guard rejected {operation}: {path}")
    if isinstance(decision, (str, os.PathLike)):
        return _path(decision)
    return path


def _atomic_write(path: Path, data: bytes, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    if not path.parent.is_dir():
        raise FileNotFoundError(str(path.parent))
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(str(path))
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_file(
    path: str | os.PathLike[str],
    content: str | bytes,
    *,
    path_guard: PathGuard,
    capability: Any = None,
    current_project: Any = None,
    overwrite: bool = False,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Create a file atomically; refuses to overwrite unless requested."""
    target = _authorize(path, operation=TOOL_WRITE_FILE, path_guard=path_guard, capability=capability, current_project=current_project)
    data = content if isinstance(content, bytes) else content.encode(encoding)
    _atomic_write(target, data, overwrite=overwrite)
    return {"path": str(target), "operation": TOOL_WRITE_FILE, "bytes": len(data), "overwritten": overwrite}


def edit_file(
    path: str | os.PathLike[str],
    content: str | bytes,
    *,
    path_guard: PathGuard,
    capability: Any = None,
    current_project: Any = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Replace an existing regular file atomically."""
    target = _authorize(path, operation=TOOL_EDIT_FILE, path_guard=path_guard, capability=capability, current_project=current_project)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    data = content if isinstance(content, bytes) else content.encode(encoding)
    _atomic_write(target, data, overwrite=True)
    return {"path": str(target), "operation": TOOL_EDIT_FILE, "bytes": len(data)}


def delete_path(
    path: str | os.PathLike[str],
    *,
    path_guard: PathGuard,
    capability: Any = None,
    current_project: Any = None,
) -> dict[str, Any]:
    """Delete one file or an empty directory; never recursively deletes."""
    target = _authorize(path, operation=TOOL_DELETE_PATH, path_guard=path_guard, capability=capability, current_project=current_project)
    if target.is_dir():
        target.rmdir()
        kind = "directory"
    elif target.is_file():
        target.unlink()
        kind = "file"
    else:
        raise FileNotFoundError(str(target))
    return {"path": str(target), "operation": TOOL_DELETE_PATH, "kind": kind}


def move_path(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    path_guard: PathGuard,
    capability: Any = None,
    current_project: Any = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Move a path without replacing a destination unless explicitly enabled."""
    src = _authorize(source, operation=TOOL_MOVE_PATH, path_guard=path_guard, capability=capability, current_project=current_project)
    dst = _authorize(destination, operation=TOOL_MOVE_PATH, path_guard=path_guard, capability=capability, current_project=current_project)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if dst.exists() and not overwrite:
        raise FileExistsError(str(dst))
    if not dst.parent.is_dir():
        raise FileNotFoundError(str(dst.parent))
    if overwrite and dst.exists():
        if dst.is_dir() and src.is_dir():
            raise FileExistsError(str(dst))
        if dst.is_dir():
            raise IsADirectoryError(str(dst))
        dst.unlink()
    os.replace(src, dst) if overwrite else os.rename(src, dst)
    return {"source": str(src), "destination": str(dst), "operation": TOOL_MOVE_PATH, "overwritten": overwrite}


def _apply_unified_patch(original: str, patch: str) -> str:
    lines = original.splitlines(keepends=True)
    patch_lines = patch.splitlines(keepends=True)
    if patch_lines and patch_lines[-1].strip() == "\\ No newline at end of file":
        patch_lines.pop()
    if not patch_lines:
        raise ValueError("patch must contain at least one hunk")
    index = 0
    if patch_lines[0].startswith("--- "):
        if len(patch_lines) < 2 or not patch_lines[1].startswith("+++ "):
            raise ValueError("malformed unified patch headers")
        index = 2
    output: list[str] = []
    cursor = 0
    found = False
    while index < len(patch_lines):
        match = _HUNK.match(patch_lines[index].rstrip("\r\n"))
        if not match:
            raise ValueError("malformed patch hunk header")
        found = True
        old_start, old_count, new_start, new_count = [int(value or 1) for value in match.groups()]
        if old_start < 1 or new_start < 1 or old_start - 1 < cursor:
            raise ValueError("invalid patch hunk position")
        output.extend(lines[cursor : old_start - 1])
        cursor = old_start - 1
        consumed_old = consumed_new = 0
        index += 1
        while index < len(patch_lines) and not _HUNK.match(patch_lines[index].rstrip("\r\n")):
            item = patch_lines[index]
            if item.startswith("\\ No newline"):
                index += 1
                continue
            if not item or item[0] not in " +-":
                raise ValueError("malformed patch line")
            marker, text = item[0], item[1:]
            if marker in " -":
                if cursor >= len(lines) or lines[cursor] != text:
                    raise ValueError("patch context does not match file")
                cursor += 1
                consumed_old += 1
            if marker in " +":
                output.append(text)
                consumed_new += 1
            index += 1
        if consumed_old != old_count or consumed_new != new_count:
            raise ValueError("patch hunk line counts do not match")
    if not found:
        raise ValueError("patch must contain a hunk")
    output.extend(lines[cursor:])
    return "".join(output)


def apply_patch(
    path: str | os.PathLike[str],
    patch: str,
    *,
    path_guard: PathGuard,
    capability: Any = None,
    current_project: Any = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Apply a single-file unified patch; malformed patches fail closed."""
    target = _authorize(path, operation=TOOL_APPLY_PATCH, path_guard=path_guard, capability=capability, current_project=current_project)
    if not target.is_file() or not isinstance(patch, str):
        raise ValueError("apply_patch requires an existing file and string patch")
    original = target.read_text(encoding=encoding)
    updated = _apply_unified_patch(original, patch)
    _atomic_write(target, updated.encode(encoding), overwrite=True)
    return {"path": str(target), "operation": TOOL_APPLY_PATCH, "bytes": len(updated.encode(encoding))}


__all__ = [
    "GENERAL_AGENT_WRITE_TOOL_NAMES", "GENERAL_AGENT_WRITE_TOOLS",
    "edit_file", "write_file", "delete_path", "move_path", "apply_patch",
]
