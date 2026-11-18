"""Register an existing Unity project through each manager's documented CLI."""

from __future__ import annotations

import json
import locale
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from bounded_process import BoundedProcessResult, run_bounded_process
from project_lifecycle_service import ProjectLifecycleError


def _executable(*candidates: str | Path | None) -> Path | None:
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_absolute() and (os.name != "nt" or path.suffix.lower() == ".exe") and path.is_file():
                return path
    return None


def _which(name: str) -> str | None:
    return shutil.which(name + ".exe" if os.name == "nt" else name)


def _run(executable: Path, *args: str, timeout_seconds: int = 10) -> BoundedProcessResult:
    return run_bounded_process(
        [str(executable), *args],
        cwd=str(executable.parent),
        env=os.environ,
        timeout_seconds=timeout_seconds,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        fallback_encoding=locale.getencoding() if os.name == "nt" else "",
    )


def _vpm_executable() -> Path | None:
    return _executable(_which("vpm"), Path.home() / ".dotnet" / "tools" / ("vpm.exe" if os.name == "nt" else "vpm"))


def _vrc_get_executable(managers: Iterable[Mapping[str, Any]]) -> Path | None:
    return _executable(_which("vrc-get"), *(entry.get("path") for entry in managers if entry.get("name") == "vrc-get"))


def _missing_vpm_packages(project: Path) -> list[str]:
    manifest_path = project / "Packages" / "vpm-manifest.json"
    if not manifest_path.is_file():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ProjectLifecycleError("The template has an invalid VPM manifest.")
    dependencies = manifest.get("dependencies", {})
    locked = manifest.get("locked", {})
    if not isinstance(dependencies, dict) or not isinstance(locked, dict):
        raise ProjectLifecycleError("The template has an invalid VPM manifest.")
    package_names = dependencies.keys() | locked.keys()
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", name) for name in package_names):
        raise ProjectLifecycleError("The template contains an invalid VPM package ID.")
    return sorted(name for name in package_names if not (project / "Packages" / name / "package.json").is_file())


def resolve_template_packages(project: Path, package_managers: Iterable[Mapping[str, Any]]) -> bool:
    """Restore the template's SDK packages before exposing the new project."""
    if not _missing_vpm_packages(project):
        return False
    vrc_get = _vrc_get_executable(package_managers)
    vpm = None if vrc_get else _vpm_executable()
    if not vrc_get and not vpm:
        raise ProjectLifecycleError("Install the official VPM CLI or vrc-get to restore this template's VRChat packages.")
    try:
        result = (
            _run(vrc_get, "resolve", "--project", str(project), timeout_seconds=120)
            if vrc_get else _run(vpm, "resolve", "project", str(project), timeout_seconds=120)
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectLifecycleError("VRChat package restoration timed out.") from exc
    missing = _missing_vpm_packages(project)
    if result.returncode or missing:
        detail = f" Missing packages: {', '.join(missing)}." if missing else ""
        raise ProjectLifecycleError(f"The package manager could not restore the VRChat template.{detail}")
    return True


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(path))


def _unity_list_membership(executable: Path, project: Path) -> bool | None:
    result = _run(executable, "projects", "--all", "--format", "json", "--no-banner")
    if result.returncode or result.stdout_truncated:
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(rows, dict):
        rows = rows.get("items")
    if not isinstance(rows, list):
        return None
    expected = _path_key(project)
    return any(
        isinstance(row, dict) and isinstance(row.get("path"), str) and _path_key(row["path"]) == expected
        for row in rows
    )


def _vpm_table_paths(stdout: str) -> Iterable[list[str]]:
    """Group the first column of the VPM CLI's wrapped project table by path."""
    parts: list[str] = []
    for line in stdout.splitlines():
        if not line.startswith("│") or not line.endswith("│"):
            continue
        columns = line[1:-1].rsplit("│", 2)
        if len(columns) != 3:
            continue
        part = columns[0].strip()
        if not part:
            continue
        if Path(part).is_absolute():
            if parts:
                yield parts
            parts = [part]
        elif parts:
            parts.append(part)
    if parts:
        yield parts


def _seen_in_vpm_list(stdout: str, project: Path) -> bool:
    expected = _path_key(project)
    for parts in _vpm_table_paths(stdout):
        remaining = expected
        for part in parts:
            # VPM's table can wrap either within a word or at whitespace.
            fragment = os.path.normcase(part)
            remaining = remaining.lstrip(" ")
            if not remaining.startswith(fragment):
                break
            remaining = remaining[len(fragment):]
        else:
            if not remaining:
                return True
    return False


def _seen_in_vrc_get_list(stdout: str, project: Path) -> bool:
    expected = _path_key(project)
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Path: ") and _path_key(stripped.removeprefix("Path: ")) == expected:
            return True
    return False


def _register_unity_hub(executable: Path, project: Path) -> tuple[str, str]:
    help_result = _run(executable, "projects", "add", "--help")
    if help_result.returncode:
        return "manual_required", "official_cli_unsupported"
    before = _unity_list_membership(executable, project)
    if before is None:
        return "failed", "readback_failed"
    if before:
        return "already_registered", ""
    if _run(executable, "projects", "add", str(project), "--format", "json", "--no-banner").returncode:
        return "failed", "command_failed"
    return ("registered", "") if _unity_list_membership(executable, project) else ("failed", "readback_failed")


def _register_vcc(executable: Path, project: Path) -> tuple[str, str]:
    result = _run(executable, "add", "project", str(project))
    if result.returncode:
        return "failed", "command_failed"
    after = _run(executable, "list", "projects")
    if after.returncode or after.stdout_truncated or not _seen_in_vpm_list(after.stdout, project):
        return "failed", "readback_failed"
    if any(line.strip() == "Project already exists in database" for line in result.stdout.splitlines()):
        return "already_registered", ""
    return "registered", ""


def _register_alcom(executable: Path, project: Path) -> tuple[str, str]:
    help_result = _run(executable, "vcc", "project", "add", "--help")
    # A build without experimental-vcc exposes a top-level help stub that exits 0.
    if help_result.returncode or not re.search(r"(?mi)^Usage:.*\bvcc project add\b", help_result.stdout):
        return "manual_required", "official_cli_unsupported"
    args = ("vcc", "project", "list")
    before = _run(executable, *args)
    if before.returncode or before.stdout_truncated:
        return "failed", "readback_failed"
    if _seen_in_vrc_get_list(before.stdout, project):
        return "already_registered", ""
    if _run(executable, "vcc", "project", "add", str(project)).returncode:
        return "failed", "command_failed"
    after = _run(executable, *args)
    if after.returncode or after.stdout_truncated or not _seen_in_vrc_get_list(after.stdout, project):
        return "failed", "readback_failed"
    return "registered", ""


def register_project_managers(
    project: Path,
    *,
    package_managers: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, str]]:
    """Register the approved project path and report the result for each manager."""
    manager_entries = tuple(package_managers)
    local_appdata = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    roaming_appdata = Path(os.environ.get("APPDATA") or Path.home())
    unity_cli_home = Path(os.environ.get("UNITY_CLI_HOME") or local_appdata / "Unity")
    specs = (
        (
            "unityHub", _register_unity_hub,
            _executable(_which("unity"), unity_cli_home / "bin" / "unity.exe", Path.home() / ".local" / "bin" / "unity"),
            (roaming_appdata / "UnityHub").is_dir() or (local_appdata / "UnityHub").is_dir(),
        ),
        (
            "vcc", _register_vcc,
            _vpm_executable(),
            any(entry.get("name") == "vcc" for entry in manager_entries),
        ),
        (
            "alcom", _register_alcom,
            _vrc_get_executable(manager_entries),
            any(entry.get("name") == "alcom" for entry in manager_entries),
        ),
    )
    results: list[dict[str, str]] = []
    for catalog, register, executable, app_present in specs:
        if executable is None:
            status, reason = ("manual_required", "official_cli_missing") if app_present else ("unavailable", "manager_not_found")
        else:
            try:
                status, reason = register(executable, project)
            except subprocess.TimeoutExpired:
                status, reason = "failed", "command_timeout"
            except OSError:
                status, reason = "failed", "command_failed"
            if not app_present and reason == "official_cli_unsupported":
                status, reason = "unavailable", "manager_not_found"
        row = {"catalog": catalog, "status": status}
        if reason:
            row["reason"] = reason
        results.append(row)
    return results
