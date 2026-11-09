"""Hard denials for directly recognizable destructive Agent Shell commands."""

from __future__ import annotations

import ntpath
import os
import re
import shlex
from collections.abc import Callable, Iterator
from pathlib import Path

from agent_command_safety import is_path_within, strip_quotes


_SEPARATORS = ";|&{}\r\n"
_STOP = {"taskkill", "stop-process", "kill", "killall", "pkill"}
_DISK = {"diskpart", "format", "format-volume", "clear-disk", "remove-partition", "deltree"}
_SHUTDOWN = {"shutdown", "stop-computer", "restart-computer"}
_REMOVE = {"remove-item", "del", "erase", "rd", "ri", "rm", "rmdir"}
_MOVE = {"move-item", "move", "mv", "rename-item", "ren", "rename"}
_COPY = {"copy-item", "copy", "cp", "xcopy"}
_CONTENT = {"clear-content", "set-content", "out-file", "tee-object"}
_LOCATION = {"cd", "chdir", "set-location", "pushd", "push-location"}
_PROJECT_DIRS = {"assets", "packages", "projectsettings"}
_PROJECT_FILES = {"manifest.json", "packages-lock.json"}
_PROFILE_FILES = {"chat-transcripts.json", "chat-projects.json", "agent_gateway.json"}


def _segments(command: str) -> Iterator[list[str]]:
    # Keep quoted data as a token. Only literal command positions are inspected;
    # this is not an interpreter for arbitrary PowerShell or external programs.
    lexer = shlex.shlex(command, posix=False, punctuation_chars=_SEPARATORS + "<>")
    lexer.whitespace = " \t"
    lexer.whitespace_split = True
    words: list[str] = []
    try:
        for token in lexer:
            if token and all(ch in _SEPARATORS for ch in token):
                if words:
                    yield words
                    words = []
            else:
                words.append(token)
    except ValueError:
        return
    if words:
        yield words


def _name(word: str) -> str:
    return ntpath.basename(strip_quotes(word)).casefold().removesuffix(".exe").removesuffix(".com")


def _targets(words: list[str]) -> Iterator[str]:
    for word in words:
        if word.startswith("-"):
            option, _, value = word.partition(":")
            if option.casefold() not in {"-path", "-literalpath", "-destination", "-filepath"}:
                continue
            word = value
        elif word.casefold() in {"/s", "/q", "/f", "/a", "/p"}:
            continue
        if word:
            yield strip_quotes(word).strip(",")


def _option_paths(words: list[str], *options: str) -> Iterator[str]:
    for index, word in enumerate(words):
        flag, sep, value = word.partition(":")
        if flag.casefold() in options:
            if not sep and index + 1 < len(words):
                value = words[index + 1]
            if value:
                yield strip_quotes(value)


def _git_command(args: list[str]) -> tuple[str, set[str]]:
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-"):
            return arg, set(args[index + 1:])
        index += 2 if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"} else 1
    return "", set()


def _protected_path(
    raw: str, cwd: Path, roots: tuple[Path, ...], find_project: Callable[[Path], Path | None], *, tree: bool,
) -> bool:
    wildcard = bool(re.search(r"[*?]", raw))
    literal = re.split(r"[*?]", raw, maxsplit=1)[0].replace("\\", os.sep)
    if "$" in literal or "%" in literal:
        # An expanded target is not an exact path for destructive tree actions.
        return tree
    try:
        path = (cwd / Path(literal or ".").expanduser()).resolve()
    except (OSError, RuntimeError, ValueError):
        return tree
    if path == Path(path.anchor) or any(is_path_within(root, path) for root in roots):
        return True
    names = {part.casefold() for part in path.parts}
    if names & {".git", ".vrcforge"} or path.name.casefold() in _PROFILE_FILES:
        return True
    project = find_project(path)
    if project is None:
        return False
    parts = tuple(part.casefold() for part in path.relative_to(project).parts)
    if not parts or parts[0] == "projectsettings":
        return True
    if parts[0] == "packages" and parts[-1] in _PROJECT_FILES:
        return True
    if parts[0] in _PROJECT_DIRS and (len(parts) == 1 or tree or wildcard or path.is_dir()):
        return True
    return parts[0] == "assets" and path.suffix.casefold() in {".unity", ".prefab", ".meta", ".controller"}


def destructive_shell_reason(
    command: str, cwd: Path, workspace_root: Path, registered_roots: tuple[str, ...],
    find_project: Callable[[Path], Path | None], *, _depth: int = 0,
) -> str:
    roots = (workspace_root, *(Path(root) for root in registered_roots))
    for words in _segments(command):
        head = _name(words[0])
        args = words[1:]
        flags = {strip_quotes(word).casefold() for word in args}
        if head in {"cmd", "pwsh", "powershell", "bash", "sh"}:
            if head in {"pwsh", "powershell"} and flags & {"-encodedcommand", "-enc", "-ec", "-e"}:
                return "Encoded PowerShell commands are blocked. Pass readable commands instead."
            for index, arg in enumerate(args[:-1]):
                if arg.casefold() in {"-command", "-c", "/c", "/k"}:
                    if _depth >= 3:
                        return "Too many nested shell commands. Pass the command directly."
                    tail = args[index + 1:]
                    script = strip_quotes(tail[0]) if len(tail) == 1 else " ".join(tail)
                    reason = destructive_shell_reason(
                        script, cwd, workspace_root, registered_roots, find_project, _depth=_depth + 1,
                    )
                    if reason:
                        return reason
                    break
        if head in _STOP:
            return "Direct process termination is blocked. Stop an Agent-owned process through Shell Process, or close Unity normally."
        if head in _DISK or head in _SHUTDOWN:
            return "Disk and computer shutdown commands are blocked in Agent Shell."
        if head == "robocopy" and flags & {"/mir", "/purge"}:
            return "Destructive directory mirroring is blocked. Copy the exact files you need."
        if head == "git":
            verb, opts = _git_command(args)
            if (verb == "reset" and "--hard" in opts) or (
                verb == "clean" and not any(
                    word == "--dry-run" or re.fullmatch(r"-[a-z]*n[a-z]*", word) for word in opts
                )
            ):
                return "Destructive Git cleanup is blocked. Inspect the changes and keep a copy before removing them manually."
        if head in _REMOVE | _MOVE and "-whatif" not in flags:
            tree = bool(flags & {"-recurse", "/s", "--recursive"}) or any(
                re.fullmatch(r"-[rfivd]*r[rfivd]*", word, re.I) for word in args
            )
            paths = list(_targets(args))
            if any(_protected_path(path, cwd, roots, find_project, tree=tree) for path in paths):
                return "This command would remove or move a protected project directory or file. Use the dedicated VRCForge tool for an exact asset."
        write_paths = [
            strip_quotes(words[i + 1]) for i, word in enumerate(words[:-1]) if word in {">", ">>"}
        ]
        if head in _CONTENT:
            paths = list(_option_paths(args, "-path", "-literalpath", "-filepath")) or list(_targets(args))[:1]
            write_paths.extend(paths)
        if head in _COPY and "-whatif" not in flags:
            targets = list(_targets(args))
            source = next(_option_paths(args, "-path", "-literalpath"), targets[0] if targets else "")
            destination = next(_option_paths(args, "-destination"), targets[1] if len(targets) > 1 else "")
            if source and destination:
                dest_path = cwd / destination.replace("\\", os.sep)
                write_paths.append(str(dest_path / ntpath.basename(source)) if dest_path.is_dir() else destination)
        if any(_protected_path(path, cwd, roots, find_project, tree=False) for path in write_paths):
            return "Shell cannot overwrite or clear a protected project or application file."
        if head in _LOCATION:
            paths = list(_targets([word for word in args if word.casefold() != "/d"]))
            if len(paths) == 1 and not any(ch in paths[0] for ch in "$%*?"):
                cwd = cwd / paths[0].replace("\\", os.sep)
    return ""
