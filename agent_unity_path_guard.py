"""Cooperative path guard for Agent operations around Unity projects.

This is an injection seam for shared handlers, not a filesystem security
boundary.  It deliberately performs lexical Windows path checks only.
"""

from __future__ import annotations

import ntpath
from collections.abc import Iterable


UNITY_PROJECT_ACCESS = "unity_project_access"


class UnityPathGuardError(PermissionError):
    """Raised when an operation crosses a registered Unity project boundary."""


def normalize_path(value: object) -> str:
    """Normalize a Windows path without requiring that it already exists."""

    text = str(value or "").strip().strip('"').strip("'").replace("/", "\\")
    if not text:
        return ""
    normalized = ntpath.normpath(text)
    # ntpath preserves a root's trailing separator inconsistently; comparisons
    # below use equality or an explicit separator boundary, so trim it here.
    if len(normalized) > 1 and normalized.endswith("\\"):
        normalized = normalized.rstrip("\\")
    return normalized.casefold()


def path_is_within(path: object, root: object) -> bool:
    candidate = normalize_path(path)
    boundary = normalize_path(root)
    return bool(candidate and boundary and (candidate == boundary or candidate.startswith(boundary + "\\")))


def _command_references(command: object, root: str) -> bool:
    """Return whether a command text directly contains a root path.

    This intentionally does not parse shell syntax or resolve relative paths;
    the threat model is accidental direct references, not adversarial bypasses.
    """

    command_text = str(command or "").replace("/", "\\").casefold()
    marker = root.casefold()
    start = 0
    while True:
        index = command_text.find(marker, start)
        if index < 0:
            return False
        before = command_text[index - 1] if index else " "
        after_index = index + len(marker)
        after = command_text[after_index] if after_index < len(command_text) else " "
        # A path separator continues a path; punctuation/quotes/whitespace
        # delimit a direct path.  This rejects C:\\UnityProject2 for a
        # registered C:\\UnityProject while allowing --path=C:\\UnityProject.
        if (not (before.isalnum() or before in "_.-")) and (
            not (after.isalnum() or after in "_.-") or after == "\\"
        ):
            return True
        start = index + max(1, len(marker))


class UnityPathGuard:
    """Replaceable registry and authorization seam for shared operations."""

    def __init__(self, roots: Iterable[object] = (), *, current_root: object = "") -> None:
        self._roots: set[str] = set()
        self._current_root = ""
        self.replace_roots(roots)
        if current_root:
            self.set_current_root(current_root)

    @property
    def registered_roots(self) -> tuple[str, ...]:
        return tuple(sorted(self._roots))

    @property
    def current_root(self) -> str:
        return self._current_root

    def replace_roots(self, roots: Iterable[object]) -> tuple[str, ...]:
        normalized = {normalize_path(root) for root in roots}
        normalized.discard("")
        self._roots = normalized
        if self._current_root and self._current_root not in self._roots:
            self._current_root = ""
        return self.registered_roots

    replace_registered_roots = replace_roots

    def register_root(self, root: object) -> str:
        normalized = normalize_path(root)
        if not normalized:
            raise ValueError("Unity project root is required")
        self._roots.add(normalized)
        return normalized

    register_unity_root = register_root

    def unregister_root(self, root: object) -> bool:
        normalized = normalize_path(root)
        removed = normalized in self._roots
        self._roots.discard(normalized)
        if self._current_root == normalized:
            self._current_root = ""
        return removed

    def set_current_root(self, root: object) -> str:
        normalized = normalize_path(root)
        if normalized not in self._roots:
            raise ValueError("current Unity project root must be registered")
        self._current_root = normalized
        return normalized

    set_current_unity_root = set_current_root

    def clear_current_root(self) -> None:
        self._current_root = ""

    def is_read_allowed(self, path: object = "") -> bool:
        return True

    def is_write_allowed(self, path: object, *, capability: str | None = None) -> bool:
        candidate = normalize_path(path)
        if capability == UNITY_PROJECT_ACCESS:
            return bool(self._current_root and path_is_within(candidate, self._current_root))
        return not any(path_is_within(candidate, root) for root in self._roots)

    def authorize_write(self, path: object, *, capability: str | None = None) -> None:
        if not self.is_write_allowed(path, capability=capability):
            raise UnityPathGuardError(f"write target is outside the permitted Unity project scope: {path}")

    def is_shell_allowed(
        self, command: object = "", *, cwd: object = "", capability: str | None = None
    ) -> bool:
        if capability == UNITY_PROJECT_ACCESS:
            if not self._current_root:
                return False
            if cwd and not path_is_within(cwd, self._current_root):
                return False
            return not any(
                _command_references(command, root) for root in self._roots if root != self._current_root
            )
        if any(path_is_within(cwd, root) for root in self._roots):
            return False
        return not any(_command_references(command, root) for root in self._roots)

    def authorize_shell(
        self, command: object = "", *, cwd: object = "", capability: str | None = None
    ) -> None:
        if not self.is_shell_allowed(command, cwd=cwd, capability=capability):
            raise UnityPathGuardError("shell operation crosses a registered Unity project scope")


__all__ = [
    "UNITY_PROJECT_ACCESS",
    "UnityPathGuard",
    "UnityPathGuardError",
    "normalize_path",
    "path_is_within",
]
