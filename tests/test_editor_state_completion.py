import dashboard_server as ds


def test_gm_completion_has_independent_receipt(monkeypatch):
    monkeypatch.setattr(ds, "gesture_manager_status_sync", lambda args: {"ok": True, "isPlayMode": True, "managers": [{"avatarPath": "Scene/Avatar", "moduleConnected": True, "managerPath": "Scene/GM"}]})
    result = ds.gesture_manager_enter_play_mode_finalize({"projectPath": "D:/Project", "avatarPath": "Scene/Avatar"}, {}, {"ok": True})
    assert result.get("verified") is True
    assert result["readback"]["avatarPath"] == "Scene/Avatar"


def test_set_play_mode_has_independent_finalizer():
    assert callable(getattr(ds, "set_play_mode_finalize", None))


def test_play_mode_waits_for_requested_state(monkeypatch):
    states = iter([{"isPlayMode": False}, {"isPlayMode": True}])
    monkeypatch.setattr(ds, "capture_scene_view_status_direct", lambda *a, **k: next(states))
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda *a: None)
    monkeypatch.setattr(ds.time, "sleep", lambda seconds: None)
    result = ds.set_play_mode_finalize({"projectPath": "D:/Project", "isPlaying": True}, {}, {"ok": True})
    assert result["verified"] is True
    assert result["readback"] == {"isPlayMode": True, "requested": True}


def test_play_mode_unknown_never_verifies(monkeypatch):
    monkeypatch.setattr(ds, "GESTURE_MANAGER_ENTER_PLAY_MODE_TIMEOUT_SECONDS", -1)
    result = ds.set_play_mode_finalize({"projectPath": "D:/Project", "isPlaying": False}, {}, {"ok": True})
    assert result["ok"] is False
    assert result["commitState"] == "unknown"
    assert result.get("verified") is not True
    assert result["retryable"] is False


def test_bound_read_rejects_wrong_editor_before_poll(monkeypatch):
    import editor_state_completion as domain
    import pytest
    def reject(*args, **kwargs):
        raise ValueError("wrong project/editor PID")
    monkeypatch.setattr(domain, "validate_runtime_execution_target", reject)
    monkeypatch.setattr(ds, "gesture_manager_status_sync", lambda args: pytest.fail("must not poll another Editor"))
    with pytest.raises(ValueError, match="wrong project/editor PID"):
        ds.gesture_manager_enter_play_mode_finalize({"executionTarget": {}, "projectPath": "D:/Other", "avatarPath": "Avatar"}, {}, {"ok": True})


def test_bound_read_rejects_post_poll_editor_drift(monkeypatch):
    import editor_state_completion as domain
    import pytest
    calls = []
    def validate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("Editor changed")
        return {"editor": {"unityPid": 12}}
    monkeypatch.setattr(domain, "validate_runtime_execution_target", validate)
    monkeypatch.setattr(ds, "gesture_manager_status_sync", lambda args: {"ok": True, "isPlayMode": True, "managers": [{"avatarPath": "Avatar", "moduleConnected": True}]})
    with pytest.raises(ValueError, match="Editor changed"):
        ds.gesture_manager_enter_play_mode_finalize({"executionTarget": {}, "projectPath": "D:/Project", "avatarPath": "Avatar"}, {}, {"ok": True})


def test_bound_read_keeps_exact_transport_target(monkeypatch):
    import editor_state_completion as domain
    from operation_context import current_operation_context
    target = {"avatar": {"exactHierarchyPath": "Avatar"}}
    monkeypatch.setattr(domain, "validate_runtime_execution_target", lambda *a, **k: target)
    monkeypatch.setattr(domain, "execution_target_digest", lambda value: "digest")
    with domain.bound_editor_readback({"projectPath": "D:/Project", "executionTarget": target, "avatarPath": "Avatar"}):
        assert current_operation_context()["executionTarget"] == target
    assert current_operation_context() is None


def test_gm_delayed_connection_failure_is_preserved(monkeypatch):
    states = iter([{"ok": True, "isPlayMode": False}, {"ok": True, "enterPlayModeErrorCode": "late_failure", "enterPlayModeError": "Module rejected Avatar"}])
    monkeypatch.setattr(ds, "gesture_manager_status_sync", lambda args: next(states))
    monkeypatch.setattr(ds.time, "sleep", lambda seconds: None)
    result = ds.gesture_manager_enter_play_mode_finalize({"projectPath": "D:/Project", "avatarPath": "Avatar"}, {}, {"ok": True})
    assert result["errorCode"] == "late_failure"
    assert result.get("verified") is not True


def test_verified_editor_receipt_passes_external_completion(tmp_path):
    from test_mcp_write_transaction_contract import _gateway
    from editor_state_completion import verified_editor_receipt
    gateway = _gateway(tmp_path)
    name = "vrcforge_contract_editor_transition"
    gateway.approval_transactions.register_write_handler(name, "Editor transition", "high",
        lambda args: {"ok": True, "enterPlayModePending": True},
        verification_finalize_handler=lambda args, baseline, result: verified_editor_receipt({"isPlayMode": True}, result),
        pre_write_checkpoint_required=False)
    gateway.register_external_mcp_unity_tool(name, "avatar")
    proposal = gateway.call_external_mcp_tool(name, {})
    result = gateway.call_external_mcp_tool(name, {"confirmation": {**proposal["confirmation"], "decision": "approve"}})
    assert result["ok"] is True
    assert result["readbackState"] == "verified"
    assert result["commitState"] == "runtime_state_verified"
