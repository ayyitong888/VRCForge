from types import SimpleNamespace
from unittest.mock import patch

import dashboard_server


def _export_payload() -> dict:
    return {
        "avatars": [
            {
                "avatarPath": "Scene/HeroAvatar",
                "renderers": [
                    {
                        "rendererPath": "Scene/HeroAvatar/Face",
                        "blendshapes": [{"name": "Smile", "currentWeight": 55.0}],
                    }
                ],
            }
        ]
    }


def test_postwrite_verification_accepts_serialized_selected_avatar() -> None:
    change_preview = [
        {
            "rendererPath": "Scene/HeroAvatar/Face",
            "blendshapeName": "Smile",
            "targetWeight": 55.0,
            "previousWeight": 10.0,
        }
    ]

    with patch.object(dashboard_server, "export_blendshapes", return_value=_export_payload()):
        verified = dashboard_server.verify_live_blendshape_changes(
            SimpleNamespace(),
            {
                "avatarName": "HeroAvatar",
                "avatarPath": "Scene/HeroAvatar",
                "sceneName": "Scene",
            },
            change_preview,
        )

    assert verified[0]["verified"] is True
    assert verified[0]["actualWeight"] == 55.0
    assert verified[0]["verificationStatus"] == "verified"


def test_postwrite_verification_rejects_missing_serialized_avatar_path() -> None:
    with patch.object(dashboard_server, "export_blendshapes", return_value=_export_payload()):
        verified = dashboard_server.verify_live_blendshape_changes(
            SimpleNamespace(),
            {"avatarName": "HeroAvatar"},
            [
                {
                    "rendererPath": "Scene/HeroAvatar/Face",
                    "blendshapeName": "Smile",
                    "targetWeight": 55.0,
                }
            ],
        )

    assert verified[0]["verified"] is False
    assert verified[0]["verificationStatus"] == "unreadable"
    assert verified[0]["verificationError"] == (
        "Selected avatar path is missing for live verification."
    )
