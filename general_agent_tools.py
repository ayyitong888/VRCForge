"""Small, read-only filesystem tools for agent planning.

The functions intentionally operate on arbitrary local paths (including Windows
absolute paths). They never start processes and return bounded JSON-friendly data.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator


MAX_DEPTH = 32
MAX_COUNT = 2_000
MAX_READ_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_CHARS = 1_000_000
MAX_MATCH_LINE_CHARS = 2_000

_SENSITIVE_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "agent_gateway.json",
    "app-session-token",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
_SAFE_ENV_SUFFIXES = {".example", ".sample", ".template"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)\b\s*[=:]\s*)([\"']?)([^\r\n\"']+)([\"']?)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----[\s\S]*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_WINDOWS_PATH_START = re.compile(r"(?i)[a-z]:[\\/]")


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def _authorized_path(value: str | Path, allowed_roots: Iterable[str | Path]) -> Path:
    roots = [item for item in allowed_roots if str(item or "").strip()]
    if not roots:
        raise PermissionError("an authorized root is required")
    raw_value = Path(value)
    if not raw_value.is_absolute():
        existing_roots = [
            _lexical_absolute(item)
            for item in roots
            if _lexical_absolute(item).exists()
        ]
        if len(existing_roots) != 1:
            raise PermissionError("relative path requires exactly one authorized root; scope is ambiguous")
        candidate_lexical = existing_roots[0] / raw_value
    else:
        candidate_lexical = _lexical_absolute(value)

    if any(part.casefold() == ".vrcforge" for part in candidate_lexical.parts):
        raise PermissionError(".vrcforge internal path is not readable by the General Agent")

    for raw_root in roots:
        root_lexical = _lexical_absolute(raw_root)
        if not root_lexical.exists():
            continue
        try:
            relative = candidate_lexical.relative_to(root_lexical)
        except ValueError:
            continue
        if not candidate_lexical.exists():
            raise FileNotFoundError(str(candidate_lexical))
        current = root_lexical
        if _is_link_or_junction(current):
            raise PermissionError(f"authorized root is a link or reparse point: {root_lexical}")
        for part in relative.parts:
            current = current / part
            if _is_link_or_junction(current):
                raise PermissionError(f"path crosses a link or reparse point: {current}")
        try:
            resolved_root = root_lexical.resolve(strict=True)
            resolved_candidate = candidate_lexical.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError):
            raise PermissionError(f"path escapes its authorized root: {candidate_lexical}")
        return resolved_candidate
    raise PermissionError(f"path is outside every authorized root: {candidate_lexical}")


def _directory(value: str | Path, allowed_roots: Iterable[str | Path]) -> Path:
    path = _authorized_path(value, allowed_roots)
    if not path.is_dir():
        raise NotADirectoryError(str(path))
    return path


def _limit(value: int, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if value > maximum:
        raise ValueError(f"{name} exceeds the maximum of {maximum}")
    return value


def _sensitive_file(path: Path) -> bool:
    lowered = path.name.casefold()
    if lowered in _SENSITIVE_NAMES:
        return True
    if lowered.startswith(".env.") and not any(lowered.endswith(suffix) for suffix in _SAFE_ENV_SUFFIXES):
        return True
    return False


def _redact_sensitive_text(text: str) -> tuple[str, bool]:
    redacted = False

    def assignment(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}"

    text = _SECRET_ASSIGNMENT.sub(assignment, text)
    updated = _BEARER.sub("Bearer [REDACTED]", text)
    redacted = redacted or updated != text
    text = updated
    updated = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    redacted = redacted or updated != text
    return updated, redacted


def extract_explicit_local_roots(message: object, *, max_roots: int = 8) -> list[str]:
    """Resolve only existing Windows paths that the user explicitly wrote."""

    text = str(message or "")[:16_384]
    roots: list[str] = []
    seen: set[str] = set()
    for match in _WINDOWS_PATH_START.finditer(text):
        tail = text[match.start() :]
        boundary = len(tail)
        for marker in ('"', "'", "\r", "\n", "<", ">", "|", "?", "*", "，", "。", "；", "、"):
            index = tail.find(marker)
            if index >= 0:
                boundary = min(boundary, index)
        candidate_text = tail[:boundary].rstrip()
        selected: Path | None = None
        for end in range(len(candidate_text), 2, -1):
            value = candidate_text[:end].rstrip(" .,:;!！?？)]}）】")
            if not value:
                continue
            candidate = Path(value)
            try:
                if candidate.is_absolute() and candidate.exists():
                    selected = candidate.resolve(strict=True)
                    break
            except (OSError, RuntimeError):
                continue
        if selected is None or _is_link_or_junction(selected):
            continue
        key = os.path.normcase(str(selected))
        if key in seen:
            continue
        seen.add(key)
        roots.append(str(selected))
        if len(roots) >= max_roots:
            break
    return roots


def list_directory(
    path: str | Path,
    *,
    allowed_roots: Iterable[str | Path],
    max_depth: int = 1,
    max_count: int = 200,
) -> dict[str, Any]:
    """List entries below *path*, bounded by depth and total entry count."""
    max_depth = _limit(max_depth, "max_depth", MAX_DEPTH)
    max_count = _limit(max_count, "max_count", MAX_COUNT)
    root = _directory(path, allowed_roots)
    entries: list[dict[str, Any]] = []
    truncated = False

    def walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        try:
            children = sorted(directory.iterdir(), key=lambda item: (item.name.casefold(), item.name))
        except OSError:
            return
        for child in children:
            if len(entries) >= max_count:
                truncated = True
                return
            if _is_link_or_junction(child):
                kind = "other"
            elif child.name.casefold() == ".vrcforge":
                continue
            else:
                kind = "directory" if child.is_dir() else "file" if child.is_file() else "other"
            item: dict[str, Any] = {"name": child.name, "path": str(child), "type": kind}
            if kind == "file":
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    pass
            entries.append(item)
            if kind == "directory" and depth + 1 < max_depth:
                walk(child, depth + 1)
                if truncated:
                    return

    walk(root, 0)
    return {"path": str(root), "entries": entries, "truncated": truncated}


def read_text_file(
    path: str | Path,
    *,
    allowed_roots: Iterable[str | Path],
    max_bytes: int = 1_048_576,
    max_file_bytes: int | None = None,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Read UTF-8 text only; reject binary data and bound bytes/output."""
    if max_file_bytes is not None:
        max_bytes = max_file_bytes
    max_bytes = _limit(max_bytes, "max_bytes", MAX_READ_BYTES)
    if max_output_chars is not None:
        max_output_chars = _limit(max_output_chars, "max_output_chars", MAX_OUTPUT_CHARS)
    file_path = _authorized_path(path, allowed_roots)
    if not file_path.is_file():
        raise IsADirectoryError(str(file_path))
    if _sensitive_file(file_path):
        raise PermissionError(f"sensitive credential file is not readable by the General Agent: {file_path.name}")
    with file_path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    if b"\x00" in data:
        raise ValueError(f"binary file rejected: {file_path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"binary file rejected: {file_path}") from exc
    text, redacted = _redact_sensitive_text(text)
    if max_output_chars is not None and len(text) > max_output_chars:
        text = text[:max_output_chars]
        truncated = True
    return {"path": str(file_path), "text": text, "truncated": truncated, "bytes": len(data), "redacted": redacted}


def _iter_files(root: Path, max_depth: int) -> Iterator[Path]:
    def walk(directory: Path, depth: int) -> Iterator[Path]:
        try:
            children = sorted(directory.iterdir(), key=lambda item: (item.name.casefold(), item.name))
        except OSError:
            return
        for child in children:
            if _is_link_or_junction(child):
                continue
            if child.name.casefold() == ".vrcforge":
                continue
            if child.is_file():
                yield child
            elif child.is_dir() and depth + 1 < max_depth:
                yield from walk(child, depth + 1)

    yield from walk(root, 0)


def find_files(
    path: str | Path,
    *,
    allowed_roots: Iterable[str | Path],
    pattern: str = "*",
    max_depth: int = 8,
    max_count: int = 200,
) -> dict[str, Any]:
    """Find regular files matching a pathlib glob pattern."""
    max_depth = _limit(max_depth, "max_depth", MAX_DEPTH)
    max_count = _limit(max_count, "max_count", MAX_COUNT)
    root = _directory(path, allowed_roots)
    files: list[dict[str, Any]] = []
    truncated = False
    for candidate in _iter_files(root, max_depth):
        if not candidate.match(pattern):
            continue
        if len(files) >= max_count:
            truncated = True
            break
        files.append({"path": str(candidate), "name": candidate.name})
    return {"path": str(root), "files": files, "truncated": truncated}


def search_text(
    path: str | Path,
    query: str,
    *,
    allowed_roots: Iterable[str | Path],
    pattern: str = "*",
    max_depth: int = 8,
    max_count: int = 200,
    max_file_bytes: int = 1_048_576,
    case_sensitive: bool = True,
) -> dict[str, Any]:
    """Search UTF-8 text files and return bounded line matches."""
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    max_count = _limit(max_count, "max_count", MAX_COUNT)
    max_file_bytes = _limit(max_file_bytes, "max_file_bytes", MAX_READ_BYTES)
    files = find_files(
        path,
        allowed_roots=allowed_roots,
        pattern=pattern,
        max_depth=max_depth,
        max_count=min(MAX_COUNT, max_count * 10 + 1),
    )
    matches: list[dict[str, Any]] = []
    needle = query if case_sensitive else query.casefold()
    skipped_binary = 0
    for item in files["files"]:
        try:
            payload = read_text_file(item["path"], allowed_roots=allowed_roots, max_bytes=max_file_bytes)
        except (PermissionError, ValueError):
            skipped_binary += 1
            continue
        for number, line in enumerate(payload["text"].splitlines(), 1):
            haystack = line if case_sensitive else line.casefold()
            if needle not in haystack:
                continue
            if len(matches) >= max_count:
                return {"path": str(_lexical_absolute(path)), "matches": matches, "truncated": True, "skipped_binary": skipped_binary}
            matches.append({"path": item["path"], "line": number, "text": line[:MAX_MATCH_LINE_CHARS]})
    return {"path": str(_lexical_absolute(path)), "matches": matches, "truncated": files["truncated"], "skipped_binary": skipped_binary}


__all__ = ["extract_explicit_local_roots", "list_directory", "read_text_file", "find_files", "search_text"]
