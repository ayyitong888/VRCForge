from __future__ import annotations

import pytest

from unity_execution_plans_tuning import build_tuning_execution_plan


@pytest.mark.parametrize(
    ("target", "arguments", "expected"),
    [
        (
            "vrcforge_apply_clothing_fx",
            {"avatarPath": "Assets/Avatar.prefab", "items": [{"name": "Hat"}], "dryRun": False},
            [("vrc_apply_clothing_fx", {"avatarPath": "Assets/Avatar.prefab", "items": [{"name": "Hat"}]})],
        ),
        (
            "vrcforge_apply_parameter_optimization",
            {"avatar_path": "Assets/Avatar.prefab", "suggestions": [{"name": "Toggle"}], "dry_run": False},
            [
                (
                    "vrc_apply_parameter_optimization",
                    {"avatarPath": "Assets/Avatar.prefab", "suggestions": [{"name": "Toggle"}]},
                )
            ],
        ),
    ],
)
def test_builds_exact_pure_core_calls(target: str, arguments: dict, expected: list[tuple[str, dict]]) -> None:
    plan = build_tuning_execution_plan(target, arguments)

    assert plan == expected
    plan[0][1][next(key for key in plan[0][1] if key != "avatarPath")][0]["name"] = "mutated"
    assert arguments != plan[0][1]


@pytest.mark.parametrize(
    "target",
    [
        "vrcforge_apply_blendshapes",
        "vrcforge_undo_blendshapes",
        "vrcforge_run_face_tuning",
        "vrcforge_apply_shader_tuning",
        "vrcforge_restore_shader_tuning",
        "vrcforge_rollback_parameters",
        "vrcforge_reapply_tuning_history",
        "vrcforge_apply_tuning_preset",
        "vrcforge_reapply_shader_tuning_history",
        "vrcforge_apply_shader_tuning_preset",
    ],
)
def test_runtime_derived_handlers_fail_closed(target: str) -> None:
    with pytest.raises(ValueError, match="cannot be determined"):
        build_tuning_execution_plan(target, {})


@pytest.mark.parametrize(
    "arguments",
    [
        {"items": [{"name": "Hat"}], "dry_run": False},
        {"avatar_path": "Assets/A.prefab", "items": [{"name": "Hat"}]},
        {"avatar_path": "Assets/A.prefab", "avatarPath": "Assets/B.prefab", "items": [{"name": "Hat"}], "dry_run": False},
        {"avatar_path": "Assets/A.prefab", "items": [], "dry_run": False},
    ],
)
def test_clothing_plan_rejects_ambiguous_or_incomplete_raw_arguments(arguments: dict) -> None:
    with pytest.raises(ValueError):
        build_tuning_execution_plan("vrcforge_apply_clothing_fx", arguments)


def test_unknown_target_and_non_object_arguments_fail_closed() -> None:
    with pytest.raises(ValueError, match="no approved"):
        build_tuning_execution_plan("vrcforge_unknown_tuning", {})
    with pytest.raises(ValueError, match="arguments"):
        build_tuning_execution_plan("vrcforge_apply_clothing_fx", [])  # type: ignore[arg-type]
