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


def test_reload_activation_failure_never_posts_bm_click(monkeypatch):
    class FakeUser32:
        def __init__(self):
            self.IsWindow = _FakeWin32Function(lambda _hwnd: 1)
            self.GetParent = _FakeWin32Function(lambda _hwnd: 10)
            self.GetWindowThreadProcessId = _FakeWin32Function(
                lambda _hwnd, target: setattr(target._obj, "value", 99) or 12616
            )
            self.IsWindowVisible = _FakeWin32Function(lambda _hwnd: 1)
            self.IsWindowEnabled = _FakeWin32Function(lambda _hwnd: 1)
            self.GetClassNameW = _FakeWin32Function(lambda _hwnd, target, _size: setattr(target, "value", "button") or 6)
            self.GetWindowTextW = _FakeWin32Function(lambda _hwnd, target, _size: setattr(target, "value", "&Reload") or 7)
            self.SetForegroundWindow = _FakeWin32Function(lambda _hwnd: 0)
            self.GetForegroundWindow = _FakeWin32Function(lambda: 0)
            self.PostMessageW = _FakeWin32Function(lambda *_args: pytest.fail("BM_CLICK must not be posted"))

    fake = FakeUser32()
    monkeypatch.setattr(w.ctypes, "WinDLL", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(w.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(w.UnityReloadDialogError, match="could not be activated"):
        w._post_reload_button_click(99, 10, 11)


def test_reload_click_activates_exact_dialog_before_post_message(monkeypatch):
    events = []
    fake = SimpleNamespace(
        SetForegroundWindow=_FakeWin32Function(lambda hwnd: events.append(("activate", hwnd)) or 1),
        GetForegroundWindow=_FakeWin32Function(lambda: 111),
    )
    w.ctypes.set_last_error(0)
    monkeypatch.setattr(w.ctypes, "get_last_error", lambda: 0)
    w._activate_reload_dialog(fake, 111)
    assert events == [("activate", 111)]

@pytest.mark.parametrize('patch', [{'available':False}, {'probeError':{'code':'failed'}}, {'unityProcessId':999}])
def test_reload_postclick_unavailable_or_changed_identity_is_unknown(tmp_path,monkeypatch,patch):
 before={'available':True,'blocked':True,'unityProcessId':123,'dialog':{'windowHandle':4,'reloadButtonHandle':5,'visible':True,'enabled':True}}
 after={'available':True,'blocked':False,'unityProcessId':123,**patch}
 snapshots=iter([before,after]);monkeypatch.setattr(w,'probe_unity_reload_dialog',lambda p:next(snapshots));click=Mock();monkeypatch.setattr(w,'_post_reload_button_click',click)
 result=w.confirm_unity_reload_dialog({'projectPath':str(tmp_path),'confirmReload':True,'expectedUnityProcessId':123,'expectedWindowHandle':4,'expectedReloadButtonHandle':5})
 assert result['ok'] is False and result['commitState']=='unknown'
 assert result['reloadClicked'] is True and result.get('dialogClosed') is not True
 assert click.call_count==1

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
