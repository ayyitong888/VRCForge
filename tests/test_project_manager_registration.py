from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bounded_process import BoundedProcessResult, run_bounded_process
import project_manager_registration as registration


def _result(stdout: str = "", *, returncode: int = 0) -> BoundedProcessResult:
    return BoundedProcessResult(returncode, stdout, "", False, False)


def test_skips_uninstalled_managers_even_when_their_tools_are_available(tmp_path, monkeypatch):
    (tmp_path / "UnityHub").mkdir()  # Settings can outlive the app.
    executables = {}
    for name in ("unity", "vpm", "vrc-get"):
        path = tmp_path / f"{name}.exe"
        path.touch()
        executables[name] = str(path)
    monkeypatch.setattr(registration, "_which", executables.get)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setattr(registration, "_hub_windows_install_location", lambda: None)

    def run(executable, *args):
        assert executable.stem == "vpm", args
        return _result(f"│ {tmp_path} │ 22 │ date │")

    monkeypatch.setattr(registration, "_run", run)
    assert registration.register_project_managers(tmp_path, package_managers=({"name": "vcc"},)) == [
        {"catalog": "vcc", "status": "registered"},
    ]


def test_registers_using_official_commands_and_compares_each_full_path(tmp_path, monkeypatch):
    project = tmp_path / "Avatar Project"
    project.mkdir()
    project = project.resolve()
    executables = {}
    for name in ("Unity Hub", "unity", "vpm", "vrc-get"):
        path = tmp_path / f"{name}.exe"
        path.touch()
        executables[name] = str(path)
    monkeypatch.setattr(registration, "_which", executables.get)
    calls = []
    installed = {"unity": [], "vrc-get": []}

    def run(executable, *args):
        name = executable.stem
        calls.append((name, *args))
        if name == "unity":
            if args == ("projects", "--all", "--format", "json", "--no-banner", "--non-interactive"):
                return _result(json.dumps({"data": {"items": [{"path": path} for path in installed[name]]}}))
            if args[:2] == ("projects", "add") and args[2] != "--help":
                installed[name].append(args[2])
            return _result()
        if name == "vpm":
            if args[:2] == ("add", "project"):
                return _result("Added project " + args[2])
            # The path contains spaces and can span table rows.
            return _result(f"│ {project.parent}{os.sep} │ 20 │ date │\n│ Avatar Project │ 22 │ time │")
        if args[-1] == "--help":
            return _result("Usage: vrc-get.exe vcc project add <PATH>")
        if args == ("vcc", "project", "list"):
            return _result("\n".join(f"  Path: {path}" for path in installed[name]))
        installed[name].append(args[3])
        return _result()

    monkeypatch.setattr(registration, "_run", run)
    assert registration.register_project_managers(project, package_managers=({"name": "vcc"}, {"name": "alcom"})) == [
        {"catalog": "unityHub", "status": "registered"},
        {"catalog": "vcc", "status": "registered"},
        {"catalog": "alcom", "status": "registered"},
    ]
    assert ("unity", "projects", "add", str(project), "--format", "json", "--no-banner", "--non-interactive") in calls
    assert ("vpm", "add", "project", str(project)) in calls
    assert ("vrc-get", "vcc", "project", "add", str(project)) in calls


def test_missing_cli_and_disabled_vrc_get_have_manual_handoffs(tmp_path, monkeypatch):
    project = tmp_path / "Avatar"
    project.mkdir()
    executable = tmp_path / "vrc-get.exe"
    executable.touch()
    hub = tmp_path / "Custom Hub"
    hub.mkdir()
    (hub / "Unity Hub.exe").touch()
    monkeypatch.setattr(registration, "_hub_windows_install_location", lambda: hub)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("UNITY_CLI_HOME", str(tmp_path))
    monkeypatch.setattr(registration, "_which", lambda name: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def run(_executable, *args):
        assert args == ("vcc", "project", "add", "--help")
        return _result("Usage: vrc-get.exe vcc [ARGS]...")

    monkeypatch.setattr(registration, "_run", run)
    assert registration.register_project_managers(
        project,
        package_managers=({"name": "vcc"}, {"name": "alcom"}, {"name": "vrc-get", "path": str(executable)}),
    ) == [
        {"catalog": "unityHub", "status": "manual_required", "reason": "official_cli_missing"},
        {"catalog": "vcc", "status": "manual_required", "reason": "official_cli_missing"},
        {"catalog": "alcom", "status": "manual_required", "reason": "official_cli_unsupported"},
    ]


def test_timeout_and_false_success_are_reported_without_blocking_next_manager(tmp_path, monkeypatch):
    project = tmp_path / "Avatar"
    project.mkdir()
    for name in ("vpm", "vrc-get"):
        (tmp_path / f"{name}.exe").touch()
    monkeypatch.setattr(registration, "_which", lambda name: str(tmp_path / f"{name}.exe"))
    monkeypatch.setenv("UNITY_CLI_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(registration, "_unity_hub_installed", lambda _: False)

    def run(executable, *args):
        if executable.stem == "vpm":
            raise subprocess.TimeoutExpired(args, 10)
        if args[-1] == "--help":
            return _result("Usage: vrc-get.exe vcc project add <PATH>")
        return _result(f"  Path: {project}-different\n")

    monkeypatch.setattr(registration, "_run", run)
    assert registration.register_project_managers(project, package_managers=({"name": "vcc"}, {"name": "alcom"})) == [
        {"catalog": "vcc", "status": "failed", "reason": "command_timeout"},
        {"catalog": "alcom", "status": "failed", "reason": "readback_failed"},
    ]


def test_vpm_table_keeps_long_cjk_paths_and_checks_exact_membership(tmp_path):
    project = tmp_path / "Space 项目" / "台本│Unity"
    pieces = (f"{tmp_path}{os.sep}Spa", f"ce 项目{os.sep}台本│Unity")
    table = "\n".join(f"│ {piece} │ 22 │ value │" for piece in pieces)
    assert registration._seen_in_vpm_list(table, project)
    assert not registration._seen_in_vpm_list(table, project.with_name("台本"))
    assert not registration._seen_in_vpm_list(table, project.with_name("台本│Unity 2"))


def test_bounded_process_reads_ansi_fallback_and_preserves_utf8_precedence(tmp_path):
    chinese = "测试项目"
    process = run_bounded_process(
        [sys.executable, "-c", f"import os; os.write(1, {chinese.encode('cp936')!r}); os.write(2, {chinese.encode('utf-8')!r})"],
        cwd=str(tmp_path),
        env=os.environ,
        timeout_seconds=10,
        fallback_encoding="cp936",
    )
    assert process.stdout == process.stderr == chinese
