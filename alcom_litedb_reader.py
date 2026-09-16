from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def read_alcom_litedb_projects(
    database_path: Path,
    *,
    helper_path: Path,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """Read one ALCOM LiteDB through the bundled read-only helper."""
    database = Path(database_path)
    helper = Path(helper_path)
    if not database.is_absolute() or not helper.is_absolute():
        return {"status": "error", "projects": [], "error": "absolute_paths_required"}
    try:
        completed = subprocess.run(
            [str(helper), str(database)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "projects": [], "error": "reader_timeout"}
    except OSError as exc:
        return {"status": "error", "projects": [], "error": f"reader_unavailable:{exc}"}
    if completed.returncode != 0:
        return {
            "status": "error",
            "projects": [],
            "error": (completed.stderr or "reader_failed").strip()[:512],
        }
    projects: list[str] = []
    try:
        for line in completed.stdout.splitlines():
            value = json.loads(line)
            if isinstance(value, str) and value.strip():
                projects.append(value.strip())
    except (TypeError, ValueError) as exc:
        return {"status": "error", "projects": [], "error": f"invalid_reader_output:{exc}"}
    return {"status": "ready" if projects else "empty", "projects": projects, "error": ""}
