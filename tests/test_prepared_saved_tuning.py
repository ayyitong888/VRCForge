from __future__ import annotations

import json
from pathlib import Path

import pytest

from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    prepared_evidence,
)
from test_avatar_tuning_state_service import _prepared_service


def _saved(item_id: str) -> dict:
    return {
        "id": item_id,
        "avatar_path": "Avatar/Path",
        "changes": [
            {
                "rendererPath": "Face",
                "blendshapeName": "Smile",
                "after": 50.0,
            }
        ],
    }


@pytest.mark.parametrize(
    ("source_type", "item_key", "target"),
    [
        ("history", "historyId", "vrcforge_reapply_tuning_history"),
        ("preset", "presetId", "vrcforge_apply_tuning_preset"),
    ],
)
def test_saved_owner_freezes_identity_and_exact_fake_call(
    tmp_path: Path,
    source_type: str,
    item_key: str,
    target: str,
) -> None:
    service, stores, _undo, _live = _prepared_service(tmp_path)
    saved = _saved("item-1")
    if source_type == "history":
        stores.save_history_record(saved)
        item_id = "item-1"
        prepared, _preview = service.prepare_reapply_history(
            {item_key: item_id, "mock_execute": "false"},
            None,
        )
    else:
        stores.save_history_record(saved)
        item_id = stores.create_preset(
            {"history_id": "item-1", "name": "Fixture"}
        )["preset"]["id"]
        prepared, _preview = service.prepare_apply_preset(
            {item_key: item_id, "mock_execute": "false"},
            None,
        )

    assert prepared_evidence(prepared)[item_key] == item_id
    assert build_prepared_execution_plan(prepared)[0][0] == "vrc_apply_blendshapes"
    assert _preview["targetTool"] == target


def test_saved_history_drift_blocks_fake_unity(tmp_path: Path) -> None:
    service, stores, undo, live = _prepared_service(tmp_path)
    stores.save_history_record(_saved("history-1"))
    prepared, _preview = service.prepare_reapply_history(
        {"historyId": "history-1"},
        None,
    )
    history_path = tmp_path / "state" / "tuning_history.json"
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    payload["records"][0]["changes"] = []
    history_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="record drifted"):
        service.execute_reapply_history(prepared)
    assert live["unity_calls"] == []
    assert undo.depth("Avatar/Path") == 0


def test_saved_owner_rejects_mock_and_reserved_input(tmp_path: Path) -> None:
    service, stores, _undo, _live = _prepared_service(tmp_path)
    stores.save_history_record(_saved("history-1"))
    with pytest.raises(RuntimeError, match="preview-only"):
        service.prepare_reapply_history(
            {"historyId": "history-1", "mock_execute": True},
            None,
        )
    with pytest.raises(RuntimeError, match="reserved"):
        service.prepare_reapply_history(
            {
                "historyId": "history-1",
                PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {},
            },
            None,
        )


def test_saved_owner_rejects_mock_before_reading_corrupt_store(tmp_path: Path) -> None:
    service, _stores, _undo, live = _prepared_service(tmp_path)
    history_path = tmp_path / "state" / "tuning_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="preview-only"):
        service.prepare_reapply_history(
            {"historyId": "missing", "mock_execute": "true"},
            None,
        )
    assert live["context_calls"] == 0
    assert live["unity_calls"] == []
