from __future__ import annotations

from types import SimpleNamespace

import dashboard_server


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


def test_gesture_manager_enter_play_mode_finalizer_returns_only_connection_identity(monkeypatch) -> None:
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
                    "avatarPath": "Scene/FinalAvatar",
                    "moduleConnected": True,
                    "moduleType": "BlackStartX.GestureManager.Editor.Modules.Vrc3Module",
                    "menuTree": {"name": "must-not-leak"},
                    "runtimeParameters": [{"name": "must-not-leak"}],
                }
            ],
        },
    )

    result = dashboard_server.gesture_manager_enter_play_mode_finalize(
        {"projectPath": "D:/Project", "avatarPath": "Scene/FinalAvatar"},
        {},
        {"ok": True, "avatarPath": "Scene/FinalAvatar"},
    )

    assert result == {
        "ok": True,
        "isPlayMode": True,
        "packageDetected": True,
        "packageVersion": "3.9.9",
        "managerCount": 1,
        "managerPath": "Scene/GestureManager",
        "avatarPath": "Scene/FinalAvatar",
        "moduleConnected": True,
        "moduleType": "BlackStartX.GestureManager.Editor.Modules.Vrc3Module",
        "persistent": False,
        "sceneDirty": False,
        "mutationStarted": True,
        "committed": True,
        "commitState": "runtime_connected",
    }
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
