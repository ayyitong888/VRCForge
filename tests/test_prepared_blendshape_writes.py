from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    prepared_call,
    prepared_evidence,
)
from approved_unity_execution import (
    bind_approved_unity_execution,
    create_approved_unity_execution_plan,
    current_approved_unity_execution,
)
from test_avatar_tuning_state_service import _prepared_service


def _arguments() -> dict:
    return {
        "avatar": "Avatar/Path",
        "adjustments": [
            {
                "renderer_path": "Face",
                "blendshape_name": "Smile",
                "target_weight": 50,
                "previous_weight": 1,
            }
        ],
    }


def test_manual_owner_freezes_live_weight_without_second_export(tmp_path: Path) -> None:
    service, _stores, undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_manual_apply(_arguments(), None)

    assert build_prepared_execution_plan(prepared) == [
        (
            "vrc_apply_blendshapes",
            {
                "avatarPath": "Avatar/Path",
                "adjustments": [
                    {
                        "rendererPath": "Face",
                        "blendshapeName": "Smile",
                        "targetWeight": 50.0,
                    }
                ],
                "saveAssets": True,
            },
        )
    ]
    assert prepared_evidence(prepared)["undoItems"][0]["targetWeight"] == 10.0

    live["weight"] = 11.0
    service.execute_manual_apply(prepared)
    assert live["context_calls"] == 1
    assert len(live["unity_calls"]) == 1
    undo_items, _undo_evidence = undo.capture("Avatar/Path")
    assert undo_items[0]["targetWeight"] == 10.0


def test_manual_owner_verifies_in_fresh_read_context_after_approved_write(
    tmp_path: Path,
) -> None:
    service, stores, undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_manual_apply(_arguments(), None)

    def verify(_context, changes):
        # This models the live export verifier: it cannot use the one-use
        # approved write capability, but it can read independently after the
        # service switches to its fresh context.
        assert current_approved_unity_execution() is None
        return [{**change, "verified": True, "verificationStatus": "verified"} for change in changes]

    service = type(service)(
        stores=stores,
        undo=undo,
        ports=replace(service._ports, verify_live_changes=verify),
    )
    plan = create_approved_unity_execution_plan(
        {
            "lane": "approved_write",
            "approvalId": "approval-test",
            "checkpointId": "checkpoint-test",
            "projectRoot": str(tmp_path),
            "targetTool": "vrcforge_apply_blendshapes",
            "issuedAtUnixMs": 0,
            "expiresAtUnixMs": 4_000_000_000_000,
        },
        [
            ("vrc_apply_blendshapes", prepared_call(prepared)[1]),
        ],
    )

    with bind_approved_unity_execution(plan):
        result = service.execute_manual_apply(prepared)
        assert current_approved_unity_execution() is plan

    assert result["verifiedChanges"][0]["verified"] is True
    assert result["verifiedChanges"][0]["verificationStatus"] == "verified"
    assert len(live["unity_calls"]) == 1
    assert plan.consumed is False


def test_manual_owner_rejects_reserved_and_mock_input(tmp_path: Path) -> None:
    service, _stores, _undo, live = _prepared_service(tmp_path)
    with pytest.raises(RuntimeError, match="reserved"):
        service.prepare_manual_apply(
            {**_arguments(), PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}},
            None,
        )
    live["mock"] = True
    with pytest.raises(RuntimeError, match="preview-only"):
        service.prepare_manual_apply(_arguments(), None)


def test_undo_owner_keeps_stack_on_fake_unity_failure_then_consumes(tmp_path: Path) -> None:
    service, _stores, undo, live = _prepared_service(tmp_path)
    applied, _preview = service.prepare_manual_apply(_arguments(), None)
    service.execute_manual_apply(applied)
    undo_prepared, _preview = service.prepare_manual_undo(
        {"avatar_path": "Avatar/Path"},
        None,
    )

    live["unity_error"] = "Core failed"
    with pytest.raises(RuntimeError, match="Core failed"):
        service.execute_manual_undo(undo_prepared)
    assert undo.depth("Avatar/Path") == 1

    live["unity_error"] = None
    result = service.execute_manual_undo(undo_prepared)
    assert result["undoDepth"] == 0
    assert undo.depth("Avatar/Path") == 0
