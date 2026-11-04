from __future__ import annotations

import json

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
