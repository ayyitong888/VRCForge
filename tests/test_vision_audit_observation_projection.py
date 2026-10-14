from __future__ import annotations

from runtime_planner_service import RuntimePlannerService


def _observation(result: dict[str, object]) -> str:
    return RuntimePlannerService.complete_model_information(
        None,
        {
            "tool": "vrcforge_vision_audit_multi",
            "actionId": "audit-action",
            "status": "executed",
            "result": result,
        },
    )


def test_multi_angle_nested_audit_details_reach_next_planner() -> None:
    output = _observation(
        {
            "ok": True,
            "overallStatus": "clipping",
            "coverageComplete": True,
            "visualVerified": False,
            "results": [
                {
                    "angle": "front",
                    "status": "clipping",
                    "audit": {
                        "summary": "Sleeve intersects the torso.",
                        "issues": ["Visible clipping at the left sleeve."],
                        "annotations": [
                            {
                                "label": "left sleeve",
                                "reason": "mesh intersection",
                                "severity": "medium",
                                "box": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                            }
                        ],
                    },
                }
            ],
        }
    )

    assert '"angle":"front"' in output
    assert '"status":"clipping"' in output
    assert "Sleeve intersects the torso." in output
    assert "Visible clipping at the left sleeve." in output
    assert "left sleeve" in output
    assert "mesh intersection" in output
    assert '"x":0.1' in output


def test_multi_angle_top_level_detail_shape_remains_supported() -> None:
    output = _observation(
        {
            "ok": True,
            "results": [
                {
                    "angle": "back",
                    "status": "pass",
                    "summary": "No clipping found.",
                    "issues": [],
                    "annotations": [],
                }
            ],
        }
    )

    assert "No clipping found." in output
    assert '"angle":"back"' in output
