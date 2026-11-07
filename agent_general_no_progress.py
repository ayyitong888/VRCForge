"""Turn-local tool progress detection over the last twenty settled calls."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import ntpath
import re
from typing import Any, Mapping

from agent_action_targets import action_target

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


LOOP_REMINDER = "最近几次调用结果没有变化，可能在原地打转。请检查写入是否生效，或换一种方法。"
LOOP_REASONS = {
    "tool_loop_repeated": "同一工具调用和结果已重复出现三次。",
    "tool_loop_no_progress": "连续十次工具调用都没有带来新的结果。",
    "tool_loop_write_unverified": "写入参数已改变，但同一对象的读回结果没有变化，写入可能未生效。",
}


@dataclass(frozen=True)
class _Call:
    action_id: str
    result_digest: str
    target: dict[str, Any]
    effect: str
    tool: str
    status: str
    summary: str


def tool_result_digest(result: Any) -> str:
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ToolLoopProgress:
    def __init__(self, project_root: str = "") -> None:
        self._project_root = project_root.replace("\\", "/")
        self._calls: deque[_Call] = deque(maxlen=20)
        self._unchanged = 0
        self.reminded = False

    def reset(self) -> None:
        self._calls.clear()
        self._unchanged = 0
        self.reminded = False

    def record(self, *, action_id: str, tool: str, arguments: Any, result_digest: str,
               status: str, summary: str, effect: str) -> str:
        self._unchanged = self._unchanged + 1 if any(
            row.result_digest == result_digest for row in self._calls
        ) else 0
        target = {key.casefold(): value for key, value in action_target(arguments).items()}
        if isinstance(arguments, Mapping):
            for name, value in arguments.items():
                key = str(name).replace("_", "").casefold()
                if (key.endswith("id") or key in {"object", "parametername", "blendshapename"}) and isinstance(value, str) and value:
                    target.setdefault(key, value)
        scope = target.pop("projectroot", target.pop("projectpath", self._project_root))
        if target and scope:
            target["projectroot"] = scope
        self._calls.append(_Call(action_id, result_digest, target, effect, tool, status, summary))
        if effect == "read" and target and self._unchanged_write():
            return "tool_loop_write_unverified"
        if sum(row.action_id == action_id and row.result_digest == result_digest for row in self._calls) >= 3:
            return "tool_loop_repeated"
        if self._unchanged >= 10:
            return "tool_loop_no_progress"
        return ""

    def _unchanged_write(self) -> bool:
        rows = list(self._calls)
        current = rows[-1]
        latest_write = None
        for index in range(len(rows) - 2, -1, -1):
            row = rows[index]
            if row.target != current.target:
                continue
            if row.effect == "write" and latest_write is None:
                latest_write = row
            elif row.effect == "read" and row.action_id == current.action_id:
                if latest_write is None or row.result_digest != current.result_digest:
                    return False
                earlier_write = next((prior for prior in reversed(rows[:index])
                                      if prior.effect == "write" and prior.target == current.target), None)
                return earlier_write is not None and earlier_write.action_id != latest_write.action_id
        return False

    def recent_calls(self) -> list[dict[str, str]]:
        return [{"tool": row.tool, "status": row.status, "summary": row.summary}
                for row in list(self._calls)[-5:]]
