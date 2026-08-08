from __future__ import annotations

from pathlib import Path

import pytest

from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
)
from test_avatar_tuning_state_service import _prepared_service


def _arguments() -> dict:
    return {
        "avatar": "Avatar/Path",
        "instruction": "gentler smile",
        "mock_execute": False,
        "save_artifacts": False,
    }


def test_face_owner_seals_one_fake_call_and_defers_visual_proof(tmp_path: Path) -> None:
    service, _stores, undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_face_tuning(_arguments(), None)

    result = service.execute_face_tuning(prepared)

    assert len(build_prepared_execution_plan(prepared)) == 1
    assert len(live["unity_calls"]) == 1
    assert result["visualProof"]["status"] == "unavailable"
    assert result["readbackVerified"] is True
    assert undo.depth("Avatar/Path") == 1


def test_face_owner_blocks_drift_before_fake_unity(tmp_path: Path) -> None:
    service, _stores, undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_face_tuning(_arguments(), None)
    live["weight"] = 12.0

    with pytest.raises(RuntimeError, match="target values"):
        service.execute_face_tuning(prepared)
    assert live["unity_calls"] == []
    assert undo.depth("Avatar/Path") == 0


def test_face_owner_rejects_reserved_and_mock_input(tmp_path: Path) -> None:
    service, _stores, _undo, live = _prepared_service(tmp_path)
    with pytest.raises(RuntimeError, match="reserved"):
        service.prepare_face_tuning(
            {**_arguments(), PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}},
            None,
        )
    live["mock"] = True
    with pytest.raises(RuntimeError, match="preview-only"):
        service.prepare_face_tuning(_arguments(), None)


def test_face_owner_parses_string_mock_flag_before_live_state(tmp_path: Path) -> None:
    service, _stores, _undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_face_tuning(
        {**_arguments(), "mock_execute": "false"},
        None,
    )
    assert prepared["mock_execute"] == "false"

    context_calls = live["context_calls"]
    with pytest.raises(RuntimeError, match="preview-only"):
        service.prepare_face_tuning(
            {**_arguments(), "mock_execute": "true"},
            None,
        )
    assert live["context_calls"] == context_calls


def test_face_owner_keeps_artifact_success_when_history_fails(tmp_path: Path) -> None:
    service, _stores, _undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_face_tuning(_arguments(), None)
    live["history_error"] = "disk full"

    result = service.execute_face_tuning(prepared)

    assert result["ok"] is True
    assert result["artifacts"] == {"json": "artifact.json"}
    assert result["historyRecord"] is None
    assert result["warnings"] == ["Post-apply metadata was not saved: disk full"]
