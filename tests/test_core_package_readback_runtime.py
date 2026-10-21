from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import dashboard_server as d
import unity_status_service as s
import unity_editor_window_probe as w
from unity_mcp_core_client import UnityMcpCoreError


class _FakeWin32Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def _reload_user32(events, *, foreground_result=0, parent=10, process_id=99, label="&Reload", post_result=1, style=1, control_id=1, resolved_button=11):
    class FakeUser32:
        def __init__(self):
            self.IsWindow = _FakeWin32Function(lambda _hwnd: 1)
            self.GetParent = _FakeWin32Function(lambda _hwnd: parent)
            self.GetWindowThreadProcessId = _FakeWin32Function(
                lambda _hwnd, target: setattr(target._obj, "value", process_id) or 12616
            )
            self.IsWindowVisible = _FakeWin32Function(lambda _hwnd: 1)
            self.IsWindowEnabled = _FakeWin32Function(lambda _hwnd: 1)
            self.GetClassNameW = _FakeWin32Function(lambda _hwnd, target, _size: setattr(target, "value", "button") or 6)
            self.GetWindowTextW = _FakeWin32Function(lambda _hwnd, target, _size: setattr(target, "value", label) or 7)
            self.SetForegroundWindow = _FakeWin32Function(lambda hwnd: events.append(("foreground", hwnd)) or foreground_result)
            self.GetForegroundWindow = _FakeWin32Function(lambda: 0)
            self.GetDlgCtrlID = _FakeWin32Function(lambda _hwnd: control_id)
            self.GetDlgItem = _FakeWin32Function(lambda _hwnd, _control_id: resolved_button)
            self.GetWindowLongW = _FakeWin32Function(lambda _hwnd, _index: style)
            self.PostMessageW = _FakeWin32Function(lambda *args: events.append(("post", *args)) or post_result)

    return FakeUser32()


@pytest.mark.parametrize("foreground_result", [0, 1])
def test_reload_foreground_request_does_not_gate_exact_button_dispatch(monkeypatch, foreground_result):
    # Native inactive BM_CLICK may be ignored. Deliver the standard push-button
    # notification to its exact parent without requiring foreground ownership.
    events = []
    fake = _reload_user32(events, foreground_result=foreground_result)
    monkeypatch.setattr(w.ctypes, "WinDLL", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(w.ctypes, "get_last_error", lambda: 0)
    w._post_reload_button_click(99, 10, 11)
    assert events == [("post", 10, 0x111, 1, 11)]


@pytest.mark.parametrize("drift", [{"parent": 12}, {"process_id": 100}, {"label": "Ignore"}, {"style": 2}, {"style": 4}, {"control_id": 0}, {"resolved_button": 12}])
def test_reload_identity_drift_still_prevents_any_dispatch(monkeypatch, drift):
    events = []
    fake = _reload_user32(events, **drift)
    monkeypatch.setattr(w.ctypes, "WinDLL", lambda *_args, **_kwargs: fake)
    with pytest.raises(w.UnityReloadDialogError):
        w._post_reload_button_click(99, 10, 11)
    assert events == []


def test_reload_post_message_failure_remains_an_error(monkeypatch):
    events = []
    fake = _reload_user32(events, post_result=0)
    monkeypatch.setattr(w.ctypes, "WinDLL", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(w.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(w.UnityReloadDialogError, match="could not be clicked: Windows error 5"):
        w._post_reload_button_click(99, 10, 11)
    assert events == [("post", 10, 0x111, 1, 11)]


def test_native_dialog_probe_distinguishes_push_buttons_from_checkboxes(monkeypatch):
    labels = {10: "Unity", 11: "OK", 12: "Remember choice", 13: "Dialog body"}
    classes = {10: "#32770", 11: "Button", 12: "Button", 13: "Static"}
    fake = SimpleNamespace(
        EnumWindows=_FakeWin32Function(lambda callback, _value: callback(10, 0)),
        EnumChildWindows=_FakeWin32Function(lambda _hwnd, callback, _value: [callback(child, 0) for child in (11, 12, 13)] and 1),
        GetWindowThreadProcessId=_FakeWin32Function(lambda _hwnd, target: setattr(target._obj, "value", 99) or 100),
        GetWindowTextLengthW=_FakeWin32Function(lambda hwnd: len(labels[hwnd])),
        GetWindowTextW=_FakeWin32Function(lambda hwnd, target, _size: setattr(target, "value", labels[hwnd]) or len(labels[hwnd])),
        GetClassNameW=_FakeWin32Function(lambda hwnd, target, _size: setattr(target, "value", classes[hwnd]) or len(classes[hwnd])),
        IsWindowVisible=_FakeWin32Function(lambda _hwnd: 1),
        IsWindowEnabled=_FakeWin32Function(lambda _hwnd: 1),
        GetWindow=_FakeWin32Function(lambda *_args: 0),
        GetParent=_FakeWin32Function(lambda _hwnd: 10),
        GetWindowLongW=_FakeWin32Function(lambda hwnd, _index: {11: 0x50000001, 12: 0x50000003}[hwnd]),
    )
    monkeypatch.setattr(w.ctypes, "WinDLL", lambda *_args, **_kwargs: fake)
    result = w._enumerate_process_windows(99)
    controls = result[0]["controls"]
    assert controls[0]["buttonStyle"] == 1
    assert controls[1]["buttonStyle"] == 3
    assert "buttonStyle" not in controls[2]
    assert all(control["parentWindow"] == 10 for control in controls)

@pytest.mark.parametrize('patch', [{'available':False}, {'probeError':{'code':'failed'}}, {'unityProcessId':999}])
def test_reload_postclick_unavailable_or_changed_identity_is_unknown(tmp_path,monkeypatch,patch):
 before={'available':True,'blocked':True,'unityProcessId':123,'dialog':{'windowHandle':4,'reloadButtonHandle':5,'visible':True,'enabled':True}}
 after={'available':True,'blocked':False,'unityProcessId':123,**patch}
 snapshots=iter([before,after]);monkeypatch.setattr(w,'probe_unity_reload_dialog',lambda p:next(snapshots));click=Mock();monkeypatch.setattr(w,'_post_reload_button_click',click)
 result=w.confirm_unity_reload_dialog({'projectPath':str(tmp_path),'confirmReload':True,'expectedUnityProcessId':123,'expectedWindowHandle':4,'expectedReloadButtonHandle':5})
 assert result['ok'] is False and result['commitState']=='unknown'
 assert result['reloadClicked'] is True and result.get('dialogClosed') is not True
 assert click.call_count==1


def test_reload_postclick_timeout_preserves_dispatched_unknown_outcome(tmp_path, monkeypatch):
    snapshot = {
        "available": True, "blocked": True, "unityProcessId": 123,
        "dialog": {"windowHandle": 4, "reloadButtonHandle": 5, "visible": True, "enabled": True},
    }
    monkeypatch.setattr(w, "probe_unity_reload_dialog", lambda _project: snapshot)
    clock = iter((0.0, 3.0))
    monkeypatch.setattr(w.time, "monotonic", lambda: next(clock))
    click = Mock()
    monkeypatch.setattr(w, "_post_reload_button_click", click)
    result = w.confirm_unity_reload_dialog({
        "projectPath": str(tmp_path), "confirmReload": True,
        "expectedUnityProcessId": 123, "expectedWindowHandle": 4, "expectedReloadButtonHandle": 5,
    })
    assert result["ok"] is False and result["commitState"] == "unknown"
    assert result["reloadClicked"] is True and result["mutationStarted"] is True
    assert result["retryable"] is False and result.get("dialogClosed") is not True
    assert "did not close" in result["error"]
    click.assert_called_once_with(123, 4, 5)

@pytest.mark.parametrize('failure',[False,True])
def test_registered_unity_tools_enumerates_or_preserves_failure(tmp_path,monkeypatch,failure):
 settings=SimpleNamespace(unity_mcp_timeout_seconds=3,unity_project_path=str(tmp_path))
 svc=s.UnityStatusService(s.UnityStatusPorts(lambda:settings,lambda:str(tmp_path),str,lambda p:True,('vrc_a',)))
 client=Mock();client.core_info.return_value={'coreVersion':'current'}
 client.list_tools.return_value=[{'name':'vrc_a'},{'name':'vrc_b'}]
 if failure: client.list_tools.side_effect=UnityMcpCoreError('actual list unavailable')
 monkeypatch.setattr(s,'UnityMcpCoreClient',lambda *a,**k:client);monkeypatch.setattr(s,'probe_unity_reload_dialog',lambda p:{'blocked':False});monkeypatch.setattr(d,'UNITY_STATUS',svc);monkeypatch.setattr(d,'load_dashboard_settings',lambda p:settings)
 result=d.AGENT_GATEWAY._tools['vrcforge_unity_tools'].handler({'projectPath':str(tmp_path)})
 assert client.list_tools.call_count==1
 if failure: assert result['ok'] is False and 'actual list unavailable' in result['error']
 else: assert result['toolNames']==['vrc_a','vrc_b'] and result['totalTools']==2 and result['inspectionSkipped'] is False
 client.reset_mock();svc.build_unity_status_snapshot(settings);client.list_tools.assert_not_called()
