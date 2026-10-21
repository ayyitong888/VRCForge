"""A native confirmation continues its pending writer, never a second writer."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
import unity_editor_dialog_tools as dialogs


def _project(path: Path) -> Path:
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir()
    (path / "Packages").mkdir()
    (path / "Library/VRCForge").mkdir(parents=True)
    (path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n", encoding="utf-8")
    (path / "Packages/manifest.json").write_text("{}", encoding="utf-8")
    (path / "Assets/Scene.unity").write_text("%YAML 1.1\n", encoding="utf-8")
    (path / "Assets/Scene.unity.meta").write_text("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", encoding="utf-8")
    (path / "Library/VRCForge/mcp-core.json").write_text(json.dumps({"processId": 4242}), encoding="utf-8")
    return path


def _approve(service, tool: str, args: dict) -> str:
    request = service.create_apply_request({"target_tool": tool, "arguments": args})
    approval_id = request["approval"]["id"]
    assert service.approve(approval_id)["ok"]
    return approval_id


@pytest.fixture
def pending_writer(tmp_path: Path, monkeypatch, request):
    native_caption = getattr(request, "param", None)
    if native_caption and os.name != "nt":
        pytest.skip("The native dialog integration requires Windows.")
    project = _project(tmp_path / "Project")
    if native_caption:
        (project / "Library/VRCForge/mcp-core.json").write_text(json.dumps({"processId": os.getpid()}), encoding="utf-8")
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    service = gateway.approval_transactions
    started, release = threading.Event(), threading.Event()
    parent_results, parent_errors = [], []
    native_title = f"VRCForge native modal fixture {tmp_path.name}"
    native_window = 0

    def writer(_args):
        started.set()
        native_result = None
        if native_caption:
            import ctypes
            native_result = ctypes.windll.user32.MessageBoxW(None, "Confirm the fixture's original approved import.", native_title, 1)
        assert release.wait(10), "The test must release its original writer."
        return {"ok": True, "saved": True, "nativeDialogResult": native_result}

    service.register_write_handler(
        "vrcforge_fixture_native_import", "Import fixture requiring confirmation.", "high", writer,
        pre_write_checkpoint_required=True,
        checkpoint_prepare_handler=lambda _root, _args: {"ok": True, "archiveAssetPaths": ["Assets/Scene.unity"]},
    )
    service.register_write_handler(
        "vrcforge_fixture_other_write", "Another ordinary write.", "high", lambda _args: {"ok": True},
        pre_write_checkpoint_required=False,
    )
    lookup = service.get_native_dialog_source_approval
    service.register_write_handler(
        "vrcforge_handle_unity_editor_dialog", "Continue one approved native confirmation.", "high",
        lambda args: dialogs.handle_unity_editor_dialog(args, source_approval_lookup=lookup),
        request_preparer=lambda args, preview: dialogs.prepare_unity_editor_dialog_action(
            args, preview, source_approval_lookup=lookup),
        pre_write_checkpoint_required=False,
    )
    windows = [{"windowHandle": 101, "ownerWindow": 99, "title": "Unity import", "className": "#32770",
                "visible": True, "enabled": True, "controls": [
                    {"windowHandle": 102, "className": "Static", "text": "Import the approved assets?",
                     "parentWindow": 101, "visible": True, "enabled": True},
                    {"windowHandle": 103, "className": "Button", "text": "Continue", "buttonStyle": 1,
                     "parentWindow": 101, "visible": True, "enabled": True}]}]
    click = None
    if not native_caption:
        monkeypatch.setattr(dialogs.native, "_enumerate_process_windows", lambda _pid: windows)
        click = Mock(side_effect=lambda *_args: windows.clear())
        monkeypatch.setattr(dialogs.native, "post_native_dialog_button_click", click)
    parent_id = _approve(service, "vrcforge_fixture_native_import", {"projectPath": str(project)})

    def run_parent():
        try:
            parent_results.append(service.apply_approved({"approvalId": parent_id}))
        except BaseException as exc:
            parent_errors.append(exc)

    thread = threading.Thread(target=run_parent, daemon=True)
    thread.start()
    if not started.wait(5):
        release.set()
        thread.join(5)
        pytest.fail(f"Original writer never started: {parent_errors!r} {parent_results!r}")
    try:
        if native_caption:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetDlgItem.restype = wintypes.HWND
            user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            user32.SetWindowTextW.restype = wintypes.BOOL
            deadline = time.monotonic() + 3
            while not native_window and time.monotonic() < deadline:
                native_window = next((w["windowHandle"] for w in dialogs.native._enumerate_process_windows(os.getpid())
                                      if w["title"] == native_title), 0)
                if not native_window:
                    time.sleep(0.01)
            assert native_window, "The test's own MessageBox did not appear."
            native_button = user32.GetDlgItem(native_window, 1)
            assert native_button and user32.SetWindowTextW(native_button, native_caption)
        yield gateway, service, project, parent_id, release, thread, click, parent_results
    finally:
        if native_caption and native_window:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            # Only this fixture-created HWND. MB_OKCANCEL permits WM_CLOSE.
            user32.PostMessageW(native_window, 0x0010, 0, 0)
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert not parent_errors, parent_errors


def _dialog_args(project: Path, parent_id: str) -> dict:
    inspected = dialogs.inspect_unity_editor_dialogs({"projectPath": str(project)})
    return {"projectPath": str(project), "dialogId": inspected["dialogs"][0]["dialogId"],
            "buttonHandle": 103, "buttonText": "Continue", "sourceApprovalId": parent_id}


def test_dialog_child_continues_pending_writer_and_retains_parent_lock(pending_writer):
    gateway, service, project, parent_id, release, thread, click, parent_results = pending_writer
    parent = gateway._approvals[parent_id]
    assert parent["status"] == "applying"
    checkpoint = parent["checkpoint"]
    assert checkpoint["status"] == "ready"
    assert Path(checkpoint["archivePath"]).is_file()
    assert checkpoint["archiveFiles"] == ["Assets/Scene.unity", "Assets/Scene.unity.meta"]
    proof = service.get_native_dialog_source_approval(parent_id)
    assert proof["approved"] is True and proof["checkpointReady"] is True
    assert proof["checkpointId"] == checkpoint["id"]

    child_id = _approve(service, "vrcforge_handle_unity_editor_dialog", _dialog_args(project, parent_id))
    result = service.apply_approved({"approvalId": child_id})
    assert result["ok"], result
    click.assert_called_once_with(4242, 101, 103, "Continue")
    assert result["result"]["checkpointId"] == checkpoint["id"]
    assert result["result"]["assetOperationVerified"] is False
    assert gateway._approvals[parent_id]["status"] == "applying"
    assert parent_id in gateway._in_flight_apply_writes
    assert child_id not in gateway._in_flight_apply_writes
    key = service._project_lock_key(str(project))
    assert service._project_write_locks[key].locked(), "The child must not release its parent's lock."

    other_id = _approve(service, "vrcforge_fixture_other_write", {"projectPath": str(project)})
    blocked = service.apply_approved({"approvalId": other_id})
    assert blocked["status"] == "blocked_concurrent_write"
    click.assert_called_once()
    repeated = service.apply_approved({"approvalId": child_id})
    assert repeated["ok"] is False and repeated["status"] == "applied"
    click.assert_called_once()
    release.set()
    thread.join(5)
    assert parent_results[0]["ok"], parent_results
    assert not service._project_write_locks[key].locked()
    assert service.get_native_dialog_source_approval(parent_id) is None


def test_dialog_cannot_reuse_source_after_original_operation_finishes(pending_writer):
    _gateway, service, project, parent_id, release, thread, click, _results = pending_writer
    child_id = _approve(service, "vrcforge_handle_unity_editor_dialog", _dialog_args(project, parent_id))
    release.set()
    thread.join(5)
    assert service.get_native_dialog_source_approval(parent_id) is None
    result = service.apply_approved({"approvalId": child_id})
    assert result["ok"] is False
    click.assert_not_called()


def test_child_dispatch_failure_does_not_unlock_pending_parent(pending_writer):
    gateway, service, project, parent_id, _release, _thread, click, _results = pending_writer
    child_id = _approve(service, "vrcforge_handle_unity_editor_dialog", _dialog_args(project, parent_id))
    click.side_effect = OSError("Native button dispatch failed")
    result = service.apply_approved({"approvalId": child_id})
    assert result["ok"] is False
    click.assert_called_once()
    assert gateway._approvals[parent_id]["status"] == "applying"
    assert parent_id in gateway._in_flight_apply_writes
    assert service._project_write_locks[service._project_lock_key(str(project))].locked()


def test_dialog_source_from_different_project_is_rejected(pending_writer, tmp_path):
    _gateway, service, _project_, parent_id, _release, _thread, click, _results = pending_writer
    other = _project(tmp_path / "DifferentProject")
    with pytest.raises(AgentGatewayError) as rejected:
        service.create_apply_request({"target_tool": "vrcforge_handle_unity_editor_dialog",
                                      "arguments": _dialog_args(other, parent_id)})
    assert isinstance(rejected.value.__cause__, dialogs.UnityEditorDialogError)
    assert "different Unity project" in str(rejected.value.__cause__)
    click.assert_not_called()


@pytest.mark.parametrize("change", ["checkpoint_missing", "checkpoint_failed", "not_in_flight", "not_applying"])
def test_source_requires_current_applying_parent_and_ready_checkpoint(pending_writer, change):
    gateway, service, project, parent_id, _release, _thread, click, _results = pending_writer
    parent = gateway._approvals[parent_id]
    if change == "checkpoint_missing":
        parent.pop("checkpoint")
    elif change == "checkpoint_failed":
        parent["checkpoint"]["status"] = "failed"
    elif change == "not_in_flight":
        gateway._in_flight_apply_writes.pop(parent_id)
    else:
        parent["status"] = "approved"
    assert service.get_native_dialog_source_approval(parent_id) is None
    with pytest.raises(AgentGatewayError) as rejected:
        service.create_apply_request({"target_tool": "vrcforge_handle_unity_editor_dialog",
                                      "arguments": _dialog_args(project, parent_id)})
    assert isinstance(rejected.value.__cause__, dialogs.UnityEditorDialogError)
    assert "not approved" in str(rejected.value.__cause__)
    click.assert_not_called()


@pytest.mark.parametrize("pending_writer", ["Save", "Import", "Overwrite"], indirect=True)
def test_real_windows_confirmation_continues_approved_parent(pending_writer):
    gateway, service, project, parent_id, release, thread, click, parent_results = pending_writer
    assert click is None, "This integration must use the actual native click primitive."
    snapshot = dialogs.inspect_unity_editor_dialogs({"projectPath": str(project)})
    observed = next(d for d in snapshot["dialogs"] if d["title"].startswith("VRCForge native modal fixture "))
    button = next(b for b in observed["buttons"] if b["text"] in {"Save", "Import", "Overwrite"})
    assert button["nativeClickable"] is True
    parent = gateway._approvals[parent_id]
    checkpoint_id = parent["checkpoint"]["id"]
    assert Path(parent["checkpoint"]["archivePath"]).is_file()
    child_id = _approve(service, "vrcforge_handle_unity_editor_dialog", {
        "projectPath": str(project), "dialogId": observed["dialogId"],
        "buttonHandle": button["buttonHandle"], "buttonText": button["text"], "sourceApprovalId": parent_id,
    })
    result = service.apply_approved({"approvalId": child_id})
    assert result["ok"], result
    assert result["result"]["dialogClosed"] is True
    assert result["result"]["checkpointId"] == checkpoint_id
    assert parent["status"] == "applying"
    lock = service._project_write_locks[service._project_lock_key(str(project))]
    assert lock.locked()
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert parent_results[0]["ok"], parent_results
    assert parent_results[0]["result"]["nativeDialogResult"] == 1
    assert not lock.locked()
