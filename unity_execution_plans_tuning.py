"""Pure, fail-closed Core-call plans for legacy tuning write handlers.

This module intentionally does not read dashboard state, files, or Unity.  A
handler can use it at approval time only when its raw approval arguments fully
determine the eventual Core write.  Everything else must remain unplanned
until its runtime-derived inputs are made explicit in the approval payload.
"""

from __future__ import annotations

from typing import Any


TUNING_EXECUTION_PLAN_TARGETS = frozenset(
    {
        "vrcforge_apply_clothing_fx",
        "vrcforge_apply_parameter_optimization",
    }
)


_RUNTIME_DERIVED_TARGETS = {
    "vrcforge_apply_blendshapes": (
        "blendshape validation depends on the live export, lock state, and current avatar selection"
    ),
    "vrcforge_undo_blendshapes": "undo adjustments come from the in-memory undo stack",
    "vrcforge_run_face_tuning": "the face plan and validated adjustments are generated at execution time",
    "vrcforge_apply_shader_tuning": (
        "validated shader changes depend on inventory and lock state at execution time"
    ),
    "vrcforge_restore_shader_tuning": "restore changes come from the in-memory shader undo stack",
    "vrcforge_rollback_parameters": "rollback parameter names come from the snapshot file",
    "vrcforge_reapply_tuning_history": "saved tuning history is loaded and revalidated at execution time",
    "vrcforge_apply_tuning_preset": "saved tuning preset is loaded and revalidated at execution time",
    "vrcforge_reapply_shader_tuning_history": (
        "saved shader history is loaded and revalidated at execution time"
    ),
    "vrcforge_apply_shader_tuning_preset": (
        "saved shader preset is loaded and revalidated at execution time"
    ),
}


def build_tuning_execution_plan(
    target_name: str, arguments: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Return the exact ordered Core calls, or reject an under-specified write.

    The returned argument objects are copies, so a caller cannot mutate the
    approval payload through the plan after it has been frozen.
    """
    if not isinstance(target_name, str) or not target_name.strip():
        raise ValueError("tuning write target is required")
    if not isinstance(arguments, dict):
        raise ValueError("tuning write arguments must be an object")

    target = target_name.strip()
    if target in _RUNTIME_DERIVED_TARGETS:
        raise ValueError(
            "exact Core call cannot be determined from raw approval arguments: "
            + _RUNTIME_DERIVED_TARGETS[target]
        )
    if target == "vrcforge_apply_clothing_fx":
        return [
            (
                "vrc_apply_clothing_fx",
                {
                    "avatarPath": _required_avatar_path(arguments),
                    "items": _required_object_list(arguments, "items"),
                },
            )
        ]
    if target == "vrcforge_apply_parameter_optimization":
        return [
            (
                "vrc_apply_parameter_optimization",
                {
                    "avatarPath": _required_avatar_path(arguments),
                    "suggestions": _required_object_list(arguments, "suggestions"),
                },
            )
        ]
    raise ValueError("tuning write target has no approved Core execution plan")


def _required_avatar_path(arguments: dict[str, Any]) -> str:
    # Pydantic accepts both the field name and its API alias.  Requiring exactly
    # one non-empty value avoids choosing between conflicting raw inputs.
    values = [
        value
        for key in ("avatar_path", "avatarPath")
        if (value := arguments.get(key)) is not None
    ]
    if len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
        raise ValueError("exact Core call requires one non-empty avatar_path or avatarPath")
    if arguments.get("dry_run", arguments.get("dryRun", True)) is not False:
        raise ValueError("exact Core call requires dry_run=false")
    return values[0]


def _required_object_list(arguments: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = arguments.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"exact Core call requires a non-empty {key} object list")
    return [dict(item) for item in value]
