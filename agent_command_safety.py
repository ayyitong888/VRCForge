"""Shared command parsing and filesystem boundary helpers for agent services."""

from __future__ import annotations

import re
import shlex
from pathlib import Path


def tokenize_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return []


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def looks_like_absolute_path(value: str) -> bool:
    return bool(re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\)", value))


def normalize_filesystem_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    try:
        return Path(text).resolve().as_posix().lower()
    except (OSError, RuntimeError):
        return text.rstrip("/").lower()


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False
