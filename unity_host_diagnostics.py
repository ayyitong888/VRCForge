"""On-demand host diagnostics; no Unity startup, mutation, or upload preflight gate."""
from __future__ import annotations

import os
import codecs
from pathlib import Path
import time
from typing import Any

import httpx


LOG_TOOL = "vrcforge_read_unity_log"
CRASH_TOOL = "vrcforge_get_last_unity_crash"
NETWORK_TOOL = "vrcforge_check_vrchat_connectivity"
PAGE_BYTES = 16000
API_URL = "https://api.vrchat.cloud/api/1/config"

_PAGE_PROPERTIES = {
    "offset": {"type": "integer", "minimum": 0, "description": "Byte offset from a returned continuation request. Omit for the latest log tail."},
    "contains": {"type": "string", "description": "Optional case-insensitive line filter, applied only to the returned window, not the whole file."},
}
INPUT_SCHEMAS = {
    LOG_TOOL: {"type": "object", "additionalProperties": False, "properties": _PAGE_PROPERTIES},
    CRASH_TOOL: {"type": "object", "additionalProperties": False, "properties": {
        **_PAGE_PROPERTIES,
        "crashName": {"type": "string", "description": "Folder name returned by this tool; omit for the most recently modified crash directory."},
        "log": {"type": "string", "enum": ["error.log", "Editor.log"], "description": "Crash text log to read; omit to prefer error.log."},
    }},
    NETWORK_TOOL: {"type": "object", "additionalProperties": False, "properties": {}},
}


def _local_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise ValueError("Unity host log discovery currently requires Windows LOCALAPPDATA.")
    return Path(value)


def _text_page(path: Path, params: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # Each call owns and closes its read handle. A live log may grow or rotate;
    # ranges describe this read, not a persistent snapshot or project identity.
    with path.open("rb") as stream:
        total = stream.seek(0, os.SEEK_END)
        offset = params.get("offset", max(0, total - PAGE_BYTES))
        if type(offset) is not int or offset < 0 or offset > total:
            return {"ok": False, "error": "Log offset is outside the current file; it may have rotated.", "totalBytes": total}
        stream.seek(offset)
        raw = stream.read(PAGE_BYTES)
    end = offset + len(raw)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    content = decoder.decode(raw, final=end == total)
    # Retain an incomplete UTF-8 code point for the next byte window.
    end -= len(decoder.getstate()[0])
    contains = str(params.get("contains") or "")
    if contains:
        content = "\n".join(line for line in content.splitlines() if contains.casefold() in line.casefold())
        arguments = {**arguments, "contains": contains}
    result: dict[str, Any] = {
        "ok": True, "path": str(path), "content": content,
        "totalBytes": total, "startByte": offset, "endByte": end,
        "complete": offset == 0 and end == total,
        "hasMore": end < total, "hasEarlier": offset > 0,
        "filterScope": "returned_window" if contains else "none",
        "source": "host_file", "projectAssociation": "not_inferred",
    }
    if end < total:
        result["nextRequest"] = {"tool": tool, "arguments": {**arguments, "offset": end}}
    if offset > 0:
        result["previousRequest"] = {"tool": tool, "arguments": {**arguments, "offset": max(0, offset - PAGE_BYTES)}}
    return result


def read_unity_log(params: dict[str, Any]) -> dict[str, Any]:
    """Read Unity's documented default Editor log, even when the Editor is down."""
    try:
        return _text_page(_local_data() / "Unity/Editor/Editor.log", params, LOG_TOOL, {})
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "hint": "This reads Unity's default Windows log. An Editor launched with -logFile uses that explicit path instead."}


def get_last_unity_crash(params: dict[str, Any]) -> dict[str, Any]:
    """Inspect existing crash text; binary dumps are listed, never interpreted."""
    try:
        root = _local_data() / "Temp/Unity/Editor/Crashes"
        name = str(params.get("crashName") or "")
        if name:
            if name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
                raise ValueError("crashName must be a returned directory name.")
            directory = root / name
        else:
            directory = max((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, default=None)
        if directory is None or not directory.is_dir():
            return {"ok": True, "found": False, "directory": str(root)}
        files = [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(directory.iterdir()) if p.is_file()]
        log = str(params.get("log") or ("error.log" if (directory / "error.log").is_file() else "Editor.log"))
        if log not in {"error.log", "Editor.log"}:
            raise ValueError("log must be error.log or Editor.log.")
        result: dict[str, Any] = {"ok": True, "found": True, "directory": str(directory), "files": files,
                                  "dumpAnalyzed": False, "projectAssociation": "not_inferred"}
        if (directory / log).is_file():
            result["log"] = _text_page(directory / log, params, CRASH_TOOL, {"crashName": directory.name, "log": log})
        return result
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def check_vrchat_connectivity(_params: dict[str, Any]) -> dict[str, Any]:
    """One unauthenticated request, bounded timeout, no retries or background work."""
    started = time.monotonic()
    result: dict[str, Any] = {"url": API_URL, "environment": "gateway", "unityUploadVerified": False}
    try:
        # Fixed public endpoint; no user URL, credentials, cookies, or redirects.
        # The call owns and closes the connection pool, using existing httpx.
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            with client.stream("GET", API_URL, headers={"User-Agent": "VRCForge connectivity diagnostic"}) as response:
                result.update(ok=True, reachable=True, httpStatus=response.status_code,
                              apiResponding=200 <= response.status_code < 300)
    except httpx.RequestError as exc:
        result.update(ok=False, reachable=False, errorType=type(exc).__name__, error=str(exc))
    result["elapsedMs"] = round((time.monotonic() - started) * 1000)
    return result


def register_host_diagnostic_tools(gateway: Any) -> None:
    gateway.register_tool(LOG_TOOL,
        "When to use: read the default Windows Unity Editor.log tail, including runtime errors, without a working Core. Follow previousRequest for earlier text or nextRequest for later text; contains filters only that window. When NOT to use: do not treat the global log as belonging to a selected project, clear Console, or assume custom -logFile output is here. Negative example: zero matching lines does not prove the whole log is error-free.",
        "read/debug", read_unity_log)
    gateway.register_tool(CRASH_TOOL,
        "When to use: inspect the latest existing Windows Unity crash directory and its error.log or Editor.log text; returned crashName/log/offset requests can read other windows. When NOT to use: do not induce a crash, modify files, or infer dump analysis or project identity. Negative example: a listed crash.dmp is not a decoded stack trace.",
        "read/debug", get_last_unity_crash)
    gateway.register_tool(NETWORK_TOOL,
        "When to use: diagnose suspected VRChat API connectivity trouble with one unauthenticated request from the Gateway. When NOT to use: do not run before every tool or upload, change proxy settings, log in, or treat this as proof Unity uses the same network/proxy. Negative example: HTTP 403 is a received HTTP response, not a connection timeout.",
        "read/debug", check_vrchat_connectivity, progress_diagnostic_fields=("elapsedMs",))
