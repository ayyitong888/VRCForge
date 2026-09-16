from __future__ import annotations

import subprocess
from pathlib import Path

from alcom_litedb_reader import read_alcom_litedb_projects


def test_reader_requires_absolute_database_and_helper_paths(tmp_path: Path) -> None:
    result = read_alcom_litedb_projects(Path("relative.db"), helper_path=Path("reader.exe"))
    assert result == {"status": "error", "projects": [], "error": "absolute_paths_required"}


def test_reader_returns_projects_and_keeps_process_bounded(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, '"C:\\\\Avatar"\n', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = read_alcom_litedb_projects(
        tmp_path / "db.liteDb",
        helper_path=tmp_path / "reader.exe",
        timeout_seconds=2.5,
    )
    assert result == {"status": "ready", "projects": ["C:\\Avatar"], "error": ""}
    assert calls[0]["command"] == [str(tmp_path / "reader.exe"), str(tmp_path / "db.liteDb")]
    assert calls[0]["timeout"] == 2.5
    assert calls[0]["stdin"] is subprocess.DEVNULL


def test_reader_surfaces_timeout_as_error(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("reader", 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = read_alcom_litedb_projects(tmp_path / "db.liteDb", helper_path=tmp_path / "reader.exe")
    assert result["status"] == "error"
    assert result["error"] == "reader_timeout"
