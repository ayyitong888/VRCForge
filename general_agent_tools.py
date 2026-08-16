"""Small, read-only filesystem tools for agent planning.

The functions intentionally operate on arbitrary local paths (including Windows
absolute paths). They never start processes and return bounded JSON-friendly data.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol
from urllib.parse import urlencode

import httpx


MAX_DEPTH = 32
MAX_COUNT = 2_000
MAX_READ_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_CHARS = 1_000_000
MAX_MATCH_LINE_CHARS = 2_000
WEB_DEFAULT_TIMEOUT = 10.0
WEB_MAX_TIMEOUT = 30.0
WEB_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
WEB_MAX_REDIRECTS = 3
WEB_MAX_SEARCH_RESULTS = 10

WEB_FETCH_TOOL_NAME = "web_fetch"
WEB_SEARCH_TOOL_NAME = "web_search"
GENERAL_AGENT_WEB_TOOL_METADATA = {
    WEB_FETCH_TOOL_NAME: {
        "name": WEB_FETCH_TOOL_NAME,
        "description": (
            "Fetch bounded text from a public URL when-to-use: use when the user needs "
            "content or metadata from a specific web page. when-NOT-to-use: do not use "
            "for local files, authenticated/private resources, or unrestricted downloads. "
            "Negative example: do not fetch a URL merely because it appears in quoted text."
        ),
        "write": False,
    },
    WEB_SEARCH_TOOL_NAME: {
        "name": WEB_SEARCH_TOOL_NAME,
        "description": (
            "Search the public web and return normalized results when-to-use: use when "
            "the user needs discovery by topic or keywords. when-NOT-to-use: do not use "
            "for private data, authenticated search, or a known URL (use web_fetch). "
            "Negative example: do not search when the user asked only for a local file check."
        ),
        "write": False,
    },
}


class _HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...


class _WebPageParser(HTMLParser):
    """Small dependency-free HTML title/text and search-result parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._parts: list[str] = []
        self.results: list[dict[str, str]] = []
        self._result: dict[str, str] | None = None
        self._result_depth = 0
        self._snippet_target: dict[str, str] | None = None
        self._snippet_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        classes = (attrs_map.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._result = {"title": "", "url": attrs_map.get("href") or "", "snippet": ""}
            self._result_depth = 1
        elif tag == "a" and "result__snippet" in classes and self.results:
            self._snippet_target = self.results[-1]
            self._snippet_depth = 1
        elif self._result is not None:
            self._result_depth += 1
        elif self._snippet_target is not None:
            self._snippet_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if self._result is not None:
            self._result_depth -= 1
            if self._result_depth <= 0:
                if self._result["title"] and self._result["url"]:
                    self.results.append(self._result)
                self._result = None
        elif self._snippet_target is not None:
            self._snippet_depth -= 1
            if self._snippet_depth <= 0:
                self._snippet_target = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or self._skip_depth:
            return
        if self._in_title:
            self.title += text
        self._parts.append(text)
        if self._result is not None:
            if not self._result["title"]:
                self._result["title"] = text
            else:
                self._result["snippet"] = (self._result["snippet"] + " " + text).strip()
        elif self._snippet_target is not None:
            self._snippet_target["snippet"] = (self._snippet_target["snippet"] + " " + text).strip()

    @property
    def text(self) -> str:
        return " ".join(self._parts)

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


def _web_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("timeout must be a positive number")
    if value > WEB_MAX_TIMEOUT:
        raise ValueError(f"timeout exceeds the maximum of {WEB_MAX_TIMEOUT:g} seconds")
    return float(value)


def _web_client(client: _HttpClient | None, timeout: float) -> tuple[_HttpClient, bool]:
    if client is not None:
        return client, False
    return httpx.Client(
        follow_redirects=True,
        max_redirects=WEB_MAX_REDIRECTS,
        timeout=timeout,
        headers={"User-Agent": "VRCForge-General-Agent/1.0"},
    ), True


def _response_bytes(response: Any, max_bytes: int) -> tuple[bytes, bool]:
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content)
    return bytes(content[:max_bytes]), len(content) > max_bytes


def _content_type(response: Any) -> str:
    headers = getattr(response, "headers", {}) or {}
    return str(headers.get("content-type", headers.get("Content-Type", ""))).split(";", 1)[0].strip().lower()


def web_fetch(
    url: str,
    *,
    client: _HttpClient | None = None,
    timeout: float = WEB_DEFAULT_TIMEOUT,
    max_bytes: int = WEB_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Fetch bounded public text/HTML/JSON with injectable client support."""
    if not isinstance(url, str) or not re.match(r"^https?://\S+$", url, re.IGNORECASE):
        raise ValueError("url must be an absolute http(s) URL")
    timeout = _web_timeout(timeout)
    max_bytes = _limit(max_bytes, "max_bytes", WEB_MAX_RESPONSE_BYTES)
    http, owned = _web_client(client, timeout)
    try:
        try:
            response = http.get(url, timeout=timeout)
        except Exception as exc:
            raise RuntimeError(f"web_fetch request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise RuntimeError(f"web_fetch returned HTTP {status}")
        content_type = _content_type(response)
        if content_type and not (
            content_type.startswith("text/")
            or content_type in {"application/json", "application/xml", "application/xhtml+xml"}
        ):
            raise ValueError(f"web_fetch does not support content type: {content_type}")
        raw, truncated = _response_bytes(response, max_bytes)
        text = raw.decode("utf-8", errors="replace")
        parser = _WebPageParser()
        if "html" in content_type or "xhtml" in content_type or "<html" in text[:512].lower():
            parser.feed(text)
            useful_text = parser.text
            title = parser.title.strip()
        else:
            useful_text = text
            title = ""
        return {
            "url": str(getattr(response, "url", url)),
            "status_code": status,
            "content_type": content_type or "text/plain",
            "title": title,
            "text": useful_text,
            "truncated": truncated,
            "bytes": len(raw),
        }
    finally:
        if owned:
            http.close()  # type: ignore[attr-defined]


def web_search(
    query: str,
    *,
    client: _HttpClient | None = None,
    timeout: float = WEB_DEFAULT_TIMEOUT,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search DuckDuckGo's public HTML endpoint without credentials."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    max_results = _limit(max_results, "max_results", WEB_MAX_SEARCH_RESULTS)
    if max_results == 0:
        return {"query": query, "results": [], "truncated": False}
    timeout = _web_timeout(timeout)
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query.strip()})
    http, owned = _web_client(client, timeout)
    try:
        try:
            response = http.get(url, timeout=timeout)
        except Exception as exc:
            raise RuntimeError(f"web_search request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise RuntimeError(f"web_search returned HTTP {status}")
        if _content_type(response) and "html" not in _content_type(response):
            raise ValueError("web_search provider returned non-HTML content")
        raw, truncated = _response_bytes(response, WEB_MAX_RESPONSE_BYTES)
        parser = _WebPageParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        results = parser.results[:max_results]
        return {"query": query.strip(), "results": results, "truncated": truncated or len(parser.results) > max_results}
    finally:
        if owned:
            http.close()  # type: ignore[attr-defined]


__all__ = [
    "extract_explicit_local_roots", "list_directory", "read_text_file", "find_files", "search_text",
    "web_fetch", "web_search", "WEB_FETCH_TOOL_NAME", "WEB_SEARCH_TOOL_NAME", "GENERAL_AGENT_WEB_TOOL_METADATA",
]
