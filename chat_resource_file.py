"""Authorize a user-clicked file from the persisted tool receipt, never from prose."""
from collections.abc import Mapping
from pathlib import Path
import os
import re
from urllib.parse import unquote


def authorize_chat_resource_file(*, path: str, session_id: str, client_turn_id: str,
                                 project_path: str, chat: Mapping, artifact_root: Path) -> dict[str, str]:
    if not session_id or not client_turn_id or chat.get("sessionId") != session_id:
        raise ValueError("File conversation identity does not match.")
    normalize = lambda value: os.path.normcase(os.path.abspath(value)) if value else ""
    if normalize(str(chat.get("projectPath") or "")) != normalize(project_path):
        raise ValueError("File conversation project does not match.")
    candidate = Path(path)
    if not candidate.is_absolute() or path.startswith(("\\\\", "//")) or any(ord(c) < 32 for c in path):
        raise ValueError("Unsupported file path.")
    if os.name == "nt" and ":" in path[2:]:
        raise ValueError("Unsupported alternate file stream.")
    target = candidate.resolve(strict=True)
    roots = [Path(artifact_root).resolve()]
    if project_path:
        roots.append(Path(project_path).resolve(strict=True))
    if not target.is_file() or not any(target.is_relative_to(root) for root in roots):
        raise ValueError("File is outside the conversation project and application artifacts.")

    def matches(value):
        if isinstance(value, str):
            # Exact structured tool result only; arbitrary Markdown text cannot authorize opening.
            try:
                return Path(value).is_absolute() and normalize(value) == normalize(str(candidate))
            except (ValueError, OSError):
                return False
        if isinstance(value, Mapping):
            return any(matches(item) for item in value.values())
        if isinstance(value, list):
            return any(matches(item) for item in value)
        return False

    for item in chat.get("items", []):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "streaming" and item.get("clientTurnId") == client_turn_id:
            events = item.get("timeline") or []
            if any(isinstance(event, Mapping) and event.get("kind") == "tool_result" and matches(event.get("payload")) for event in events):
                return {"path": str(target)}
        response = item.get("response") if item.get("type") == "agent" else None
        if not isinstance(response, Mapping) or str(response.get("sessionId") or response.get("session_id") or "") != session_id or response.get("clientTurnId") != client_turn_id:
            continue
        plan = response.get("plan") or {}
        if any(normalize(link) == normalize(str(candidate)) for link in markdown_file_targets(str(plan.get("reply") or ""))):
            return {"path": str(target)}
        steps = response.get("steps") or plan.get("steps") or []
        if any(isinstance(step, Mapping) and matches(step.get("result")) for step in steps):
            return {"path": str(target)}
        if any(isinstance(event, Mapping) and event.get("kind") == "tool_result" and matches(event.get("payload")) for event in response.get("timeline", [])):
            return {"path": str(target)}
    raise ValueError("File is not present in this conversation's tool receipt.")


def markdown_file_targets(text: str):
    """Extract local inline link destinations; prose and code do not grant authority."""
    # Assistant file links use CommonMark inline destinations, including angle-bracket
    # paths with spaces. Ignore code before inspecting link delimiters.
    text = re.sub(r"(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[^\n]*(?:\n|$)", "", text)
    text = re.sub(r"(`+)(.*?)\1", "", text, flags=re.S)
    for match in re.finditer(r"(?<!!)\[(?:[^\]\\]|\\.)*\]\(", text):
        start = match.end()
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text):
            continue
        if text[start] == "<":
            end = text.find(">", start + 1)
            if end < 0:
                continue
            raw = text[start + 1:end]
            tail = end + 1
        else:
            end, depth = start, 0
            while end < len(text):
                char = text[end]
                if char == "\\" and end + 1 < len(text) and text[end + 1] in "()":
                    end += 2
                    continue
                if char == "(":
                    depth += 1
                elif char == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif char.isspace() and depth == 0:
                    break
                end += 1
            raw, tail = text[start:end], end
        remainder = text[tail:]
        if not re.match(r"\s*(?:[\"'][^\n]*?[\"']\s*)?\)", remainder):
            continue
        raw = re.sub(r"\\([()])", r"\1", raw)
        path = unquote(raw)
        if path.lower().startswith("file:///"):
            path = path[8:]
        path = re.sub(r"^/([a-z]:[\\/])", r"\1", path, flags=re.I)
        path = re.sub(r":\d+(?::\d+)?$", "", path)
        if Path(path).is_absolute():
            yield path
