"""Focused regression tests for the read-only agent BlendShape preview."""

from unittest.mock import Mock, patch

import pytest

import dashboard_server


@pytest.fixture
def live_export() -> dict:
    return {
        "avatars": [
            {
                "avatarName": "Avatar",
                "avatarPath": "Scene/Avatar",
                "sceneName": "Scene",
                "renderers": [
                    {
                        "rendererPath": "Scene/Avatar/Face",
                        "blendshapes": [{"name": "Smile", "currentWeight": 0.0}],
                    }
                ],
            }
        ]
    }


def _params(adjustments: list[dict]) -> dict:
    return {
        "projectPath": "C:/Unity/AvatarProject",
        "avatarPath": "Scene/Avatar",
        "adjustments": adjustments,
    }


def test_preview_rejects_empty_adjustments_before_loading_or_writing() -> None:
    with patch.object(dashboard_server, "load_dashboard_export_payload") as load_export:
        with pytest.raises(RuntimeError, match="No blendshape adjustments were provided"):
            dashboard_server._preview_agent_blendshape_adapter(_params([]))
    load_export.assert_not_called()


def test_preview_rejects_unknown_renderer_or_blendshape_from_live_export(live_export: dict) -> None:
    settings = Mock()
    with patch.object(dashboard_server, "load_dashboard_settings", return_value=settings), patch.object(
        dashboard_server,
        "load_dashboard_export_payload",
        return_value=(live_export, "unity_live_export", False),
    ), patch.object(dashboard_server, "render_manual_blendshape_payload_json") as render_payload:
        with pytest.raises(RuntimeError, match="target not found") as error:
            dashboard_server._preview_agent_blendshape_adapter(
                _params(
                    [
                        {
                            "rendererPath": "Scene/Avatar/Face",
                            "blendshapeName": "__DOES_NOT_EXIST__",
                            "targetWeight": 50,
                        }
                    ]
                )
            )
    assert "__DOES_NOT_EXIST__" in str(error.value)
    render_payload.assert_not_called()


def test_preview_accepts_existing_live_target_without_mutating(live_export: dict) -> None:
    settings = Mock()
    with patch.object(dashboard_server, "load_dashboard_settings", return_value=settings), patch.object(
        dashboard_server,
        "load_dashboard_export_payload",
        return_value=(live_export, "unity_live_export", False),
    ), patch.object(
        dashboard_server,
        "render_manual_blendshape_payload_json",
        return_value='{"tool":"vrc_apply_blendshapes"}',
    ) as render_payload:
        result = dashboard_server._preview_agent_blendshape_adapter(
            _params(
                [
                    {
                        "rendererPath": "Scene/Avatar/Face",
                        "blendshapeName": "Smile",
                        "targetWeight": 42,
                    }
                ]
            )
        )

    assert result["ok"] is True
    assert result["adjustmentCount"] == 1
    assert result["executionMode"] == "live-unity"
    render_payload.assert_called_once()
