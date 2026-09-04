"""Read-only workspace diff reporting.

Git children inherit local OS permissions, are scoped to the requested repository,
and are joined by subprocess.run with the existing timeout. This owner has no
server, credentials, background worker or project-write authority.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol


class WorkspaceGitRunner(Protocol):
    def __call__(self, root: Path, args: list[str], timeout_seconds: int = 10) -> dict[str, Any]: ...


def run_workspace_git(root: Path, args: list[str], timeout_seconds: int = 10) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": "git executable was not found.", "returncode": 127}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "git command timed out.", "returncode": 124}
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "returncode": proc.returncode,
    }


def parse_workspace_numstat(stdout: str) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions_raw, deletions_raw, path = parts[0], parts[1], "\t".join(parts[2:]).strip()
        binary = additions_raw == "-" or deletions_raw == "-"
        additions = 0 if binary else int(additions_raw or "0")
        deletions = 0 if binary else int(deletions_raw or "0")
        stats[path] = {"additions": additions, "deletions": deletions, "binary": binary}
    return stats


WORKSPACE_DIFF_PATCH_MAX_CHARS = 40000


def build_workspace_diff_summary(
    root: str = "",
    include_patch: bool = False,
    *,
    default_root: Path,
    git_runner: WorkspaceGitRunner,
    numstat_parser: Callable[[str], dict[str, dict[str, Any]]],
    patch_max_chars: int,
) -> dict[str, Any]:
    requested_root = Path(root).expanduser() if root.strip() else default_root
    try:
        requested_root = requested_root.resolve()
    except OSError as exc:
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "status": "missing",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": str(exc),
        }
    if not requested_root.exists():
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "status": "missing",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": "workspace root does not exist.",
        }

    rev_parse = git_runner(requested_root, ["rev-parse", "--show-toplevel"])
    if not rev_parse["ok"]:
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "status": "not_git",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": (rev_parse.get("stderr") or "workspace is not a git repository.").strip(),
        }

    git_root = Path(rev_parse["stdout"].strip()).resolve()
    status = git_runner(git_root, ["status", "--short"])
    numstat = git_runner(git_root, ["diff", "--numstat", "HEAD"])
    shortstat = git_runner(git_root, ["diff", "--shortstat", "HEAD"])
    branch = git_runner(git_root, ["branch", "--show-current"])
    patch = git_runner(git_root, ["diff", "--patch", "--stat", "HEAD"], timeout_seconds=15) if include_patch else {"ok": True, "stdout": "", "stderr": ""}
    if not status["ok"]:
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "gitRoot": str(git_root),
            "status": "error",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": (status.get("stderr") or "git status failed.").strip(),
        }

    numstat_by_path = numstat_parser(numstat["stdout"] if numstat["ok"] else "")
    files: list[dict[str, Any]] = []
    additions = 0
    deletions = 0
    for raw in [line for line in status["stdout"].splitlines() if line.strip()]:
        status_code = raw[:2].strip() or raw[:2]
        path = raw[3:].strip() if len(raw) > 3 else raw.strip()
        lookup_path = path.split(" -> ")[-1].strip()
        path_stats = numstat_by_path.get(lookup_path, {})
        file_additions = int(path_stats.get("additions") or 0)
        file_deletions = int(path_stats.get("deletions") or 0)
        additions += file_additions
        deletions += file_deletions
        files.append(
            {
                "status": status_code,
                "path": path,
                "raw": raw,
                "additions": file_additions,
                "deletions": file_deletions,
                "binary": bool(path_stats.get("binary")),
            }
        )

    patch_text = patch["stdout"] if include_patch and patch["ok"] else ""
    patch_truncated = len(patch_text) > patch_max_chars
    if patch_truncated:
        patch_text = patch_text[:patch_max_chars] + "\n\n[diff truncated]"

    return {
        "ok": True,
        "schema": "vrcforge.workspace_diff.v1",
        "requestedRoot": str(requested_root),
        "gitRoot": str(git_root),
        "branch": branch["stdout"].strip() if branch["ok"] else "",
        "status": "changed" if files else "clean",
        "fileCount": len(files),
        "additions": additions,
        "deletions": deletions,
        "files": files,
        "statusLines": [line for line in status["stdout"].splitlines() if line.strip()],
        "shortstat": shortstat["stdout"].strip() if shortstat["ok"] else "",
        "patch": patch_text,
        "patchTruncated": patch_truncated,
        "error": "" if numstat["ok"] and patch["ok"] else ((numstat.get("stderr") or "") + "\n" + (patch.get("stderr") or "")).strip(),
    }
