import ctypes
from unittest.mock import Mock

import pytest

import unity_editor_window_probe as probe


def _win32(monkeypatch, body, *, timeout=False):
    api = Mock()
    api.EnumWindows.side_effect = lambda callback, data: callback(100, data)
    api.EnumChildWindows.side_effect = lambda hwnd, callback, data: callback(101, data)
    api.GetWindowThreadProcessId.side_effect = lambda hwnd, pid: setattr(pid._obj, "value", 42) or 1
    api.GetWindowTextLengthW.side_effect = lambda hwnd: 6 if hwnd == 101 else 25
    api.GetWindowTextW.side_effect = lambda hwnd, buffer, count: setattr(
        buffer, "value", ("Static" if hwnd == 101 else "Preprocess Callback Failed")[:count - 1]
    ) or 1
    api.GetClassNameW.side_effect = lambda hwnd, buffer, count: setattr(
        buffer, "value", "Edit" if hwnd == 101 else "#32770"
    ) or 1
    api.IsWindowVisible.return_value = True
    api.IsWindowEnabled.return_value = True
    api.GetWindow.return_value = 0
    api.GetParent.return_value = 100

    def send(hwnd, message, count, address, flags, timeout_ms, result):
        assert hwnd == 101 and message == 0x000D
        assert 0 < timeout_ms <= 1000
        if timeout:
            return 0
        text = body[:count - 1]
        source = ctypes.create_unicode_buffer(text)
        ctypes.memmove(address, source, ctypes.sizeof(source))
        result._obj.value = len(text)
        return 1

    api.SendMessageTimeoutW.side_effect = send
    monkeypatch.setattr(probe.ctypes, "WinDLL", lambda *_a, **_kw: api)
    return api


def test_cross_process_edit_reads_message_not_window_caption(monkeypatch):
    body = "VRCSDKPreprocessAvatarCallback 'BuildFrameworkOptimizeHook' reported a failure."
    api = _win32(monkeypatch, body)
    windows = probe._enumerate_process_windows(42)
    assert windows[0]["controls"][0]["text"] == body
    assert windows[0]["controls"][0]["textTruncated"] is False
    assert api.SendMessageTimeoutW.call_count == 1


def test_cross_process_control_timeout_does_not_return_caption_as_body(monkeypatch):
    _win32(monkeypatch, "unreadable", timeout=True)
    with pytest.raises(OSError, match="control text"):
        probe._enumerate_process_windows(42)


def test_cross_process_control_truncation_uses_message_length(monkeypatch):
    _win32(monkeypatch, "x" * 600)
    control = probe._enumerate_process_windows(42)[0]["controls"][0]
    assert control["text"] == "x" * 512
    assert control["textTruncated"] is True
