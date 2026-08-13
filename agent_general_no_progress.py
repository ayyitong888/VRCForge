"""Bounded semantic replay detection for projectless general Agent turns."""

from __future__ import annotations

import ntpath
import re
from typing import Any, Mapping


_DIRECTORY_COMMAND = re.compile(
    r"^\s*(?:dir|ls|gci|get-childitem)\b(?P<arguments>.*)$",
    re.IGNORECASE,
)
_QUOTED_PATH = re.compile(r'''["']([^"']+)["']''')
_PATH_OPTIONS = {"-path", "-literalpath"}
_IGNORED_OPTIONS = {"/a", "-force", "-name"}


def _normalize_windows_path(value: object, cwd: object = "") -> str:
    text = str(value or "").strip().strip('"\'')
    base = str(cwd or "").strip().strip('"\'')
    if not text:
        text = base or "."
    elif not ntpath.isabs(text) and base:
        text = ntpath.join(base, text)
    return ntpath.normcase(ntpath.normpath(text))


def _shell_directory_target(command: object, cwd: object = "") -> str:
    text = str(command or "").strip()
    if not text or any(marker in text for marker in ("|", ";", "&&", ">", "<")):
        return ""
    match = _DIRECTORY_COMMAND.match(text)
    if match is None:
        return ""
    arguments = str(match.group("arguments") or "").strip()
    if re.search(r"(?:^|\s)-(?:recurse|r)(?:\s|$)", arguments, re.IGNORECASE):
        return ""
    quoted = _QUOTED_PATH.findall(arguments)
    if quoted:
        return _normalize_windows_path(quoted[-1], cwd)
    tokens = [token for token in re.split(r"\s+", arguments) if token]
    candidates: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            candidates.append(token)
            skip_next = False
            continue
        lowered = token.casefold()
        if lowered in _PATH_OPTIONS:
            skip_next = True
            continue
        if lowered in _IGNORED_OPTIONS or token.startswith("-") or token.startswith("/"):
            continue
        candidates.append(token)
    return _normalize_windows_path(candidates[-1] if candidates else "", cwd)


def general_read_observation_key(
    *,
    kind: str,
    tool: str,
    arguments: Mapping[str, Any] | None,
) -> str:
    """Return one semantic key only for equivalent top-level directory reads."""

    values = dict(arguments or {})
    if str(kind or "").strip().casefold() == "skill" and str(tool or "").strip() == "vrcforge_list_directory":
        depth = values.get("maxDepth", values.get("max_depth", 1))
        try:
            if int(depth) != 1:
                return ""
        except (TypeError, ValueError):
            return ""
        target = _normalize_windows_path(values.get("path"), values.get("cwd"))
    elif str(kind or "").strip().casefold() == "shell":
        target = _shell_directory_target(values.get("command"), values.get("cwd"))
    else:
        return ""
    return f"directory_listing:{target}" if target else ""


__all__ = ["general_read_observation_key"]
