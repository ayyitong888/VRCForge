from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import dashboard_server as host
import unity_editor_dialog_tools as dialogs
import unity_editor_window_probe as native


@pytest.mark.parametrize("reader", [dialogs.inspect_unity_editor_dialogs, native.probe_unity_reload_dialog])
@pytest.mark.parametrize("descriptor", ["stale", "missing", "invalid"])
def test_dialog_read_uses_live_project_process_before_core_is_ready(tmp_path, monkeypatch, reader, descriptor):
    path = tmp_path / "Library/VRCForge/mcp-core.json"
    path.parent.mkdir(parents=True)
    if descriptor != "missing":
        path.write_text(json.dumps({"processId": 118584}) if descriptor == "stale" else "{", encoding="utf-8")
    processes = [{"processId": 123152, "commandLine": f'Unity.exe -projectPath "{tmp_path}"'},
                 {"processId": 118584, "commandLine": f'Unity.exe -projectPath "{tmp_path}-other"'}]
    monkeypatch.setattr(host, "list_running_unity_processes", lambda **kw: processes)
    observed = []
    monkeypatch.setattr(native, "_enumerate_process_windows", lambda pid: observed.append(pid) or [])
    result = reader(tmp_path)
    assert result["available"] is True
    assert result["unityProcessId"] == 123152
    assert result["probeError"] is None
    assert observed == [123152]


@pytest.mark.parametrize("reader", [dialogs.inspect_unity_editor_dialogs, native.probe_unity_reload_dialog])
def test_dialog_read_does_not_report_stale_process_as_available(tmp_path, monkeypatch, reader):
    path = tmp_path / "Library/VRCForge/mcp-core.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"processId": 118584}), encoding="utf-8")
    monkeypatch.setattr(host, "list_running_unity_processes", lambda **kw: [])
    monkeypatch.setattr(native, "_enumerate_process_windows", lambda pid: pytest.fail("No live project process"))
    result = reader(tmp_path)
    assert result["available"] is False
    assert result["unityProcessId"] is None


@pytest.fixture
def process_snapshot(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Unity process discovery is Windows-only")
    project = tmp_path / "AssetImportWorker demo"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    (project / "ProjectSettings/ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )
    base_args = ["Unity.exe", "-projectPath", str(project)]
    processes = {
        101: base_args,
        102: [*base_args, "-adb2", "-batchMode", "-name", "AssetImportWorker0"],
        103: [*base_args, "-ADB2", "-batchmode", "-NAME", "AssetImportWorker1"],
    }
    monkeypatch.setattr(host, "_enumerate_windows_process_names", lambda **_: [
        {"pid": pid, "name": "Unity.exe"} for pid in processes
    ])
    monkeypatch.setattr(host, "psutil", SimpleNamespace(Process=lambda pid: SimpleNamespace(
        as_dict=lambda **_: {"pid": pid, "exe": "Unity.exe", "cmdline": processes[pid]}
    )))
    return project, processes


def test_dialog_discovery_ignores_import_workers_but_keeps_editor_ambiguity(process_snapshot):
    project, processes = process_snapshot
    processes[104] = ["Unity.exe", "-projectPath", str(project.with_name("Other project"))]
    assert native.resolve_unity_project_process_id(project) == 101

    # A second same-project Editor remains ambiguous, including in batch mode.
    for extra_args in ([], ["-batchmode"]):
        processes[105] = [*processes[101], *extra_args]
        with pytest.raises(ValueError, match="Multiple running Unity Editors"):
            native.resolve_unity_project_process_id(project)


def test_import_worker_alone_does_not_make_project_look_open(process_snapshot):
    project, processes = process_snapshot
    del processes[101]
    assert host.discover_running_unity_projects() == []
    assert host.KNOW_YOURSELF_READINESS._ports.list_running_unity_processes_strict() == []
    assert native.resolve_unity_project_process_id(project) is None
    assert host._checkpoint_restore_editor_is_definitely_closed(project) is False
