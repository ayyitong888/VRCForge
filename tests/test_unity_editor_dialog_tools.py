from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

import unity_editor_dialog_tools as tools


def _windows(button_text="&Continue", body="Import selected assets?"):
    return [{"windowHandle": 101, "ownerWindow": 99, "title": "Unity", "className": "#32770",
             "visible": True, "enabled": True, "controls": [
                 {"windowHandle": 102, "className": "Static", "text": body,
                  "parentWindow": 101, "visible": True, "enabled": True},
                 {"windowHandle": 103, "className": "Button", "text": button_text,
                  "parentWindow": 101, "visible": True, "enabled": True, "buttonStyle": 1}]}]


@pytest.fixture
def context(tmp_path, monkeypatch):
    descriptor = tmp_path / "Library/VRCForge/mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(json.dumps({"processId": 4242}), encoding="utf-8")
    windows = _windows()
    monkeypatch.setattr(tools.native, "_enumerate_process_windows", lambda _pid: windows)
    click = Mock()
    monkeypatch.setattr(tools.native, "post_native_dialog_button_click", click, raising=False)
    source = {"approved": True, "projectPath": str(tmp_path), "checkpointReady": True,
              "checkpointId": "checkpoint-bound-to-original-operation", "sourceTool": "original-import",
              "operationId": "original-operation"}
    lookup = Mock(return_value=source)
    return tmp_path, windows, click, source, lookup


def _params(project):
    observed = tools.inspect_unity_editor_dialogs({"projectPath": str(project)})
    return {"projectPath": str(project), "dialogId": observed["dialogs"][0]["dialogId"],
            "buttonHandle": 103, "buttonText": observed["dialogs"][0]["buttons"][0]["text"],
            "sourceApprovalId": "approved-import"}


@pytest.mark.parametrize("caption", ["Save", "Import", "Overwrite", "Retry", "Continue", "OK", "Cancel", "保存", "覆盖"])
def test_common_buttons_use_observed_identity_instead_of_caption_allowlist(context, caption):
    project, windows, click, _source, lookup = context
    windows[0]["controls"][1]["text"] = caption
    params = _params(project)
    prepared, preview = tools.prepare_unity_editor_dialog_action(params, None, source_approval_lookup=lookup)
    click.side_effect = lambda *_args: windows.clear()
    result = tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    click.assert_called_once_with(4242, 101, 103, caption)
    assert result["ok"] and result["dialogClosed"]
    assert result["assetOperationVerified"] is False
    assert preview["dialogClickRollbackAvailable"] is False
    assert preview["expectedSource"]["checkpointId"] == "checkpoint-bound-to-original-operation"


def test_inspection_never_clicks_or_requests_approval(context):
    project, _windows_, click, _source, lookup = context
    result = tools.inspect_unity_editor_dialogs({"projectPath": str(project)})
    assert result["dialogs"][0]["text"] == "Unity\nImport selected assets?"
    assert result["dialogs"][0]["supported"] is True
    click.assert_not_called()
    lookup.assert_not_called()


@pytest.mark.parametrize("change", ["text", "button", "window", "hidden", "disabled", "parent", "truncated"])
def test_dialog_drift_or_unsupported_control_rejected_before_click(context, change):
    project, windows, click, _source, lookup = context
    prepared, _ = tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)
    if change == "text":
        windows[0]["controls"][0]["text"] = "Overwrite unrelated file?"
    elif change == "button":
        windows[0]["controls"][1]["text"] = "Delete"
    elif change == "window":
        windows[0]["windowHandle"] = 777
    elif change == "hidden":
        windows[0]["controls"][1]["visible"] = False
    elif change == "disabled":
        windows[0]["controls"][1]["enabled"] = False
    elif change == "parent":
        windows[0]["controls"][1]["parentWindow"] = 800
    else:
        windows[0]["controls"][0]["textTruncated"] = True
    with pytest.raises(tools.UnityEditorDialogError):
        tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    click.assert_not_called()


@pytest.mark.parametrize("field,value", [("approved", False), ("checkpointReady", False), ("checkpointId", "")])
def test_source_operation_cannot_bypass_existing_approval_or_checkpoint(context, field, value):
    project, _windows_, click, source, lookup = context
    source[field] = value
    with pytest.raises(tools.UnityEditorDialogError):
        tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)
    click.assert_not_called()


def test_source_checkpoint_identity_is_rechecked_before_click(context):
    project, _windows_, click, source, lookup = context
    prepared, _ = tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)
    source["checkpointId"] = "different-checkpoint"
    with pytest.raises(tools.UnityEditorDialogError, match="checkpoint changed"):
        tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    click.assert_not_called()


def test_missing_source_approval_explicitly_reports_unsupported_write_scope(context):
    project, _windows_, click, _source, _lookup = context
    with pytest.raises(tools.UnityEditorDialogError, match="unsupported-write-scope"):
        tools.prepare_unity_editor_dialog_action(_params(project), None)
    click.assert_not_called()


def test_custom_drawn_controls_are_visible_but_not_claimed_supported(context):
    project, windows, click, _source, lookup = context
    windows[0]["controls"][1]["className"] = "UnityGUIViewWndClass"
    observed = tools.inspect_unity_editor_dialogs(str(project))
    assert observed["dialogs"][0]["supported"] is False
    assert "custom-drawn" in observed["dialogs"][0]["limitation"]
    with pytest.raises(tools.UnityEditorDialogError):
        tools.prepare_unity_editor_dialog_action({"projectPath": str(project), "dialogId": observed["dialogs"][0]["dialogId"],
                                                 "buttonHandle": 103, "buttonText": "&Continue"}, None,
                                                source_approval_lookup=lookup)
    click.assert_not_called()


@pytest.mark.parametrize("style", [2, 3, 4, 9])
def test_checkbox_and_radio_are_not_push_buttons(context, style):
    project, windows, click, _source, _lookup = context
    windows[0]["controls"][1]["buttonStyle"] = style
    observed = tools.inspect_unity_editor_dialogs(str(project))
    assert observed["dialogs"][0]["supported"] is False
    assert observed["dialogs"][0]["buttons"][0]["nativeClickable"] is False
    click.assert_not_called()


def test_truncated_title_cannot_authorize_partial_dialog(context):
    project, windows, _click, _source, lookup = context
    windows[0]["titleTruncated"] = True
    with pytest.raises(tools.UnityEditorDialogError, match="unavailable or unsupported"):
        tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)


def test_click_dispatched_but_probe_lost_is_unknown_and_not_retryable(context, monkeypatch):
    project, windows, click, _source, lookup = context
    prepared, _ = tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)
    def lose_probe(*_args):
        monkeypatch.setattr(tools.native, "_enumerate_process_windows", Mock(side_effect=OSError("probe lost")))
    click.side_effect = lose_probe
    result = tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    assert result["ok"] is False and result["commitState"] == "unknown"
    assert result["retryable"] is False and result["buttonClicked"] is True
    click.assert_called_once()


def test_next_dialog_same_window_is_reported_changed_not_closed(context):
    project, windows, click, _source, lookup = context
    prepared, _ = tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)
    click.side_effect = lambda *_args: windows[0]["controls"][0].update(text="Import completed")
    result = tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    assert result["ok"] and result["dialogChanged"] and result["dialogClosed"] is False
    assert result["assetOperationVerified"] is False


def test_process_change_is_rejected_even_if_same_handles_reused(context):
    project, _windows_, click, _source, lookup = context
    prepared, _ = tools.prepare_unity_editor_dialog_action(_params(project), None, source_approval_lookup=lookup)
    (project / "Library/VRCForge/mcp-core.json").write_text(json.dumps({"processId": 999}), encoding="utf-8")
    with pytest.raises(tools.UnityEditorDialogError):
        tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    click.assert_not_called()


def test_dismiss_closes_exact_native_dialog_without_source_or_button(context, monkeypatch):
    project, windows, click, _source, lookup = context
    params = {k: v for k, v in _params(project).items() if k in {"projectPath", "dialogId"}}
    params["action"] = "dismiss"
    close = Mock(side_effect=lambda *_args: windows.clear())
    monkeypatch.setattr(tools.native, "post_native_dialog_close", close, raising=False)
    prepared, preview = tools.prepare_unity_editor_dialog_action(params, None, source_approval_lookup=lookup)
    result = tools.handle_unity_editor_dialog(prepared, source_approval_lookup=lookup)
    close.assert_called_once_with(4242, 101)
    click.assert_not_called()
    lookup.assert_not_called()
    assert result["ok"] and result["dialogClosed"]
    assert result["closeRequested"] and result["buttonClicked"] is False
    assert preview["checkpointAvailable"] is False


def test_dismiss_does_not_close_owned_custom_editor_window(context):
    project, windows, click, _source, lookup = context
    windows[0]["className"] = "UnityContainerWndClass"
    params = _params(project) | {"action": "dismiss"}
    with pytest.raises(tools.UnityEditorDialogError, match="native dialog"):
        tools.prepare_unity_editor_dialog_action(params, None, source_approval_lookup=lookup)
    click.assert_not_called()


def test_external_mcp_dismiss_uses_native_lane_without_core(context, monkeypatch, tmp_path):
    import jsonschema
    from agent_gateway import AgentGateway, canonical_unity_write_tool_input_schema
    project, windows, click, _source, lookup = context
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (project / folder).mkdir(exist_ok=True)
    args = {k: v for k, v in _params(project).items() if k in {"projectPath", "dialogId"}}
    args["action"] = "dismiss"
    jsonschema.validate(args, canonical_unity_write_tool_input_schema("vrcforge_handle_unity_editor_dialog"))
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "roslyn_full_auto"
    config.roslyn_risk_acknowledged = True
    gateway.save_config(config)
    monkeypatch.setattr(gateway, "resolve_execution_target_reference", lambda *a, **kw: pytest.fail("Modal dismissal must not call Core"))
    close = Mock(side_effect=lambda *_args: windows.clear())
    monkeypatch.setattr(tools.native, "post_native_dialog_close", close)
    gateway.approval_transactions.register_write_handler(
        "vrcforge_handle_unity_editor_dialog", "Dismiss exact native dialog.", "high",
        tools.handle_unity_editor_dialog, request_preparer=tools.prepare_unity_editor_dialog_action,
        pre_write_checkpoint_required=False)
    gateway.register_external_mcp_unity_tool("vrcforge_handle_unity_editor_dialog", "project")
    result = gateway.call_external_mcp_tool("vrcforge_handle_unity_editor_dialog", args)
    assert result["status"] == "executed", result
    close.assert_called_once_with(4242, 101)
    click.assert_not_called()


@pytest.mark.parametrize("readback", ["recovers", "deadline", "pid_changed"])
def test_dismiss_readback_replays_transient_native_probe_failure(context, monkeypatch, readback):
    project, windows, click, _source, lookup = context
    params = {k: v for k, v in _params(project).items() if k in {"projectPath", "dialogId"}}
    params["action"] = "dismiss"
    prepared, _ = tools.prepare_unity_editor_dialog_action(params, None)
    snapshot = tools.inspect_unity_editor_dialogs(params)
    failed = {**snapshot, "available": False, "dialogs": [],
              "probeError": {"code": "unity_editor_dialog_probe_failed", "message": "native window disappeared"}}
    closed = {**snapshot, "dialogs": []}
    if readback == "pid_changed":
        failed["unityProcessId"] = 999
    probe = Mock(side_effect=[snapshot, failed, closed] if readback != "deadline" else [snapshot, failed, failed])
    monkeypatch.setattr(tools, "inspect_unity_editor_dialogs", probe)
    close = Mock()
    monkeypatch.setattr(tools.native, "post_native_dialog_close", close)
    monkeypatch.setattr(tools.time, "sleep", lambda _: None)
    monkeypatch.setattr(tools.time, "monotonic", Mock(side_effect=[0, 0.5, 2.0]))
    result = tools.handle_unity_editor_dialog(prepared)
    close.assert_called_once_with(4242, 101)
    click.assert_not_called()
    assert result["retryable"] is False
    if readback == "recovers":
        assert result["ok"] and result["dialogClosed"]
        assert probe.call_count == 3
    else:
        assert result["ok"] is False and result["commitState"] == "unknown"
        assert probe.call_count == (2 if readback == "pid_changed" else 3)
