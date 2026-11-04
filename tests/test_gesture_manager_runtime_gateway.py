from __future__ import annotations

from types import SimpleNamespace
import pytest

import dashboard_server
from agent_approval_transactions import _gesture_manager_entry_is_pending


def test_gesture_status_keeps_spaced_and_plain_avatar_bindings_distinct(monkeypatch):
    paths = ["Scene/Avatar ", "Scene/Avatar"]
    calls = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda *_: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda settings, tool, arguments, **kwargs:
        calls.append(arguments) or {"gestureManager": {"managers": [{"avatarPath": path} for path in paths]}})
    monkeypatch.setattr(dashboard_server, "extract_tool_result_payload", lambda value: value)
    for path in paths:
        result = dashboard_server.gesture_manager_status_sync({"avatarPath": path})
        assert calls[-1]["avatarPath"] == path
        assert result["managerCount"] == 1
        assert result["managers"][0]["avatarPath"] == path


@pytest.mark.parametrize("manager_path", ["Scene/Avatar ", "Scene/Avatar"])
def test_pending_recovery_requires_exact_spaced_avatar_path(manager_path):
    from agent_approval_transactions import AgentApprovalTransactionService
    reads = []
    owner = SimpleNamespace(
        _project_lock_key=lambda value: value,
        _ports=SimpleNamespace(checkpoint=SimpleNamespace(
            active_apply_recoveries=lambda: reads.append(True) or [])),
    )
    result = AgentApprovalTransactionService.reconcile_gesture_manager_pending_recovery(owner,
        {"projectPath": "D:/Project", "avatarPath": "Scene/Avatar "},
        {"ok": True, "isPlayMode": True, "enterPlayModePending": False,
         "managers": [{"avatarPath": manager_path, "moduleConnected": True}]})
    assert result == []
    assert reads == ([True] if manager_path == "Scene/Avatar " else [])


def test_gesture_manager_status_forwards_bounded_parameter_selection(monkeypatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def invoke(_settings, tool_name: str, arguments: dict, **kwargs):
        calls.append((tool_name, arguments, kwargs))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "data": {
                    "gestureManager": {
                        "isPlayMode": True,
                        "managerCount": 1,
                        "managers": [
                            {
                                "avatarPath": "Scene/FinalAvatar",
                                "runtimeParameterCount": 341,
                                "returnedRuntimeParameterCount": 2,
                                "runtimeParameters": [
                                    {"name": "VelocityX", "value": 0.0},
                                    {"name": "VelocityZ", "value": 1.0},
                                ],
                            }
                        ],
                    }
                }
            },
        )

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)

    result = dashboard_server.gesture_manager_status_sync(
        {
            "projectPath": "D:/Project",
            "avatarPath": "Scene/FinalAvatar",
            "parameterNames": ["VelocityX", "VelocityZ"],
        }
    )

    assert result["ok"] is True
    assert result["managers"][0]["runtimeParameterCount"] == 341
    assert result["managers"][0]["returnedRuntimeParameterCount"] == 2
    assert calls == [
        (
            "vrc_capture_scene_view",
            {
                "statusOnly": True,
                "requirePlayMode": False,
                "avatarPath": "Scene/FinalAvatar",
                "includeGestureManagerParameters": False,
                "gestureManagerParameterNames": ["VelocityX", "VelocityZ"],
                "gestureManagerParameterPrefix": "",
            },
            {"preserve_tool_error": True},
        )
    ]


def test_gesture_manager_status_rejects_non_array_parameter_names_before_routing(monkeypatch) -> None:
    invoked = False

    def invoke(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("Unity must not be called for invalid external arguments")

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.gesture_manager_status_sync({"parameterNames": "VelocityZ"})

    assert result["ok"] is False
    assert result["errorCode"] == "gesture_manager_parameter_names_invalid"
    assert result["toolRoutingStarted"] is False
    assert result["mutationStarted"] is False
    assert result["commitState"] == "not_started"
    assert invoked is False


def test_gesture_manager_enter_play_mode_routes_one_exact_core_atom(monkeypatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def invoke(_settings, tool_name: str, arguments: dict, **kwargs):
        calls.append((tool_name, arguments, kwargs))
        return dashboard_server.McpResult(
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "data": {
                    "managerPath": "Scene/GestureManager",
                    "avatarPath": "Scene/FinalAvatar",
                    "enterPlayModePending": True,
                    "mutationStarted": True,
                    "committed": True,
                    "commitState": "enter_play_mode_requested",
                }
            },
        )

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)

    result = dashboard_server.gesture_manager_enter_play_mode_sync(
        {"projectPath": "D:/Project", "avatarPath": "Scene/FinalAvatar"}
    )

    assert result["ok"] is True
    assert result["enterPlayModePending"] is True
    assert calls == [
        (
            "vrc_gesture_manager_enter_play_mode",
            {"avatarPath": "Scene/FinalAvatar"},
            {"preserve_tool_error": True},
        )
    ]


def test_gesture_manager_pending_core_receipt_is_not_executed() -> None:
    result = {
        "ok": True,
        "isPlayMode": False,
        "moduleConnected": False,
        "enterPlayModePending": True,
        "commitState": "enter_play_mode_requested",
    }

    assert _gesture_manager_entry_is_pending(
        "vrcforge_gesture_manager_enter_play_mode", {"result": result}
    ) is True
    assert _gesture_manager_entry_is_pending("other_tool", {"result": result}) is False
    assert _gesture_manager_entry_is_pending(
        "vrcforge_gesture_manager_enter_play_mode",
        {"result": {**result, "moduleConnected": True, "isPlayMode": True}},
    ) is False


def test_gesture_manager_pending_real_mcp_envelope_keeps_scheduled_commit_state() -> None:
    envelope = {
        "structuredContent": {
            "ok": True,
            "status": "executed",
            "result": {
                "isPlayMode": False,
                "moduleConnected": False,
                "enterPlayModePending": True,
                "mutationStarted": True,
                "committed": True,
                "commitState": "enter_play_mode_requested",
            },
        }
    }

    assert _gesture_manager_entry_is_pending(
        "vrcforge_gesture_manager_enter_play_mode", envelope
    ) is True


@pytest.mark.parametrize("avatar_path", ["Scene/FinalAvatar", "Scene/FinalAvatar "])
def test_gesture_manager_enter_play_mode_finalizer_returns_only_connection_identity(monkeypatch, avatar_path) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "gesture_manager_status_sync",
        lambda _arguments: {
            "ok": True,
            "isPlayMode": True,
            "packageDetected": True,
            "packageVersion": "3.9.9",
            "managerCount": 1,
            "managers": [
                {
                    "managerPath": "Scene/GestureManager",
                    "avatarPath": avatar_path,
                    "moduleConnected": True,
                    "moduleType": "BlackStartX.GestureManager.Editor.Modules.Vrc3Module",
                    "menuTree": {"name": "must-not-leak"},
                    "runtimeParameters": [{"name": "must-not-leak"}],
                }
            ],
        },
    )

    result = dashboard_server.gesture_manager_enter_play_mode_finalize(
        {"projectPath": "D:/Project", "avatarPath": avatar_path},
        {},
        {"ok": True, "avatarPath": avatar_path},
    )

    expected = {
        "ok": True,
        "isPlayMode": True,
        "packageDetected": True,
        "packageVersion": "3.9.9",
        "managerCount": 1,
        "managerPath": "Scene/GestureManager",
        "avatarPath": avatar_path,
        "moduleConnected": True,
        "moduleType": "BlackStartX.GestureManager.Editor.Modules.Vrc3Module",
        "persistent": False,
        "sceneDirty": False,
        "mutationStarted": True,
        "committed": True,
        "commitState": "runtime_connected",
    }
    assert result == {**expected, "schema": "vrcforge.editor_state_completion.v1", "verified": True,
                      "transitionScheduled": False, "verificationRequired": False,
                      "identityVerification": {"verified": False, "status": "not_bound"},
                      "readback": {key: value for key, value in expected.items() if key not in {"persistent", "sceneDirty", "mutationStarted", "committed", "commitState"}}}
    assert "menuTree" not in result
    assert "runtimeParameters" not in result
    assert "task_continuation" not in result


def test_gesture_manager_enter_play_mode_finalizer_preserves_exact_connection_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "gesture_manager_status_sync",
        lambda _arguments: {
            "ok": True,
            "isPlayMode": True,
            "enterPlayModeErrorCode": "gesture_manager_module_connection_failed",
            "enterPlayModeError": "Gesture Manager rejected the selected avatar.",
            "managers": [],
        },
    )

    result = dashboard_server.gesture_manager_enter_play_mode_finalize(
        {"projectPath": "D:/Project", "avatarPath": "Scene/FinalAvatar"},
        {},
        {"ok": True, "avatarPath": "Scene/FinalAvatar"},
    )

    assert result["schema"] == "vrcforge.external_tool_error.v1"
    assert result["errorCode"] == "gesture_manager_module_connection_failed"
    assert result["error"] == "Gesture Manager rejected the selected avatar."
    assert result["failureLayer"] == "unity_core_gesture_manager"
    assert result["mutationStarted"] is True
    assert result["commitState"] == "partial"
