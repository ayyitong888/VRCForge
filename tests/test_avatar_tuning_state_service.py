from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import pytest

from avatar_tuning_workflow_service import (
    AvatarTuningLiveContext,
    AvatarTuningPreparedPorts,
    AvatarTuningPreparedService,
    AvatarTuningStorePaths,
    AvatarTuningStorePorts,
    AvatarTuningStoreService,
    AvatarTuningUndoStore,
    PreparedFaceTuningState,
)
from prepared_unity_execution import prepared_call, prepared_evidence


class _Clock:
    def __init__(self) -> None:
        self._value = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(microseconds=1)
        return value


def _stores(tmp_path: Path) -> tuple[AvatarTuningStoreService, list[tuple[Any, ...]]]:
    logs: list[tuple[Any, ...]] = []
    service = AvatarTuningStoreService(
        AvatarTuningStorePorts(
            paths=AvatarTuningStorePaths(
                history=tmp_path / "state" / "tuning_history.json",
                presets=tmp_path / "state" / "tuning_presets.json",
                locks=tmp_path / "state" / "tuning_locks.json",
            ),
            lock=Lock(),
            current_avatar_path=lambda: "Avatar/Current",
            now_utc=_Clock(),
            emit_log=lambda *args: logs.append(args),
        )
    )
    return service, logs


def test_store_defaults_atomic_write_and_lock_schema_are_preserved(
    tmp_path: Path,
) -> None:
    stores, logs = _stores(tmp_path)

    assert stores.load_history() == {
        "type": "blendshape_tuning_history",
        "version": "0.1",
        "records": [],
    }
    assert stores.load_presets()["type"] == "blendshape_tuning_presets"
    assert stores.load_locks()["avatars"] == {}

    result = stores.update_locks(
        {
            "avatar_path": None,
            "locked_blendshapes": [
                {"renderer_path": "Face", "blendshape": "Smile"},
                {"rendererPath": "Face", "blendshapeName": "Smile"},
                {"rendererPath": "", "blendshapeName": "Blink"},
                {"rendererPath": "Face"},
            ],
        }
    )

    assert result == {
        "ok": True,
        "avatarPath": "Avatar/Current",
        "lockedBlendshapes": [
            {"rendererPath": "Face", "blendshapeName": "Smile"},
            {"rendererPath": "", "blendshapeName": "Blink"},
        ],
        "count": 2,
    }
    assert stores.load_locked_blendshapes("Avatar/Current") == result[
        "lockedBlendshapes"
    ]
    assert not (tmp_path / "state" / "tuning_locks.json.tmp").exists()
    payload = json.loads(
        (tmp_path / "state" / "tuning_locks.json").read_text(encoding="utf-8")
    )
    assert payload["type"] == "blendshape_tuning_locks"
    assert payload["version"] == "0.1"
    assert logs[-1][3] == {"avatarPath": "Avatar/Current", "count": 2}

    (tmp_path / "state" / "tuning_history.json").write_text(
        "not-json",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="not valid JSON"):
        stores.load_history()


def test_history_cap_and_preset_crud_keep_disk_fields_and_per_avatar_limit(
    tmp_path: Path,
) -> None:
    stores, _logs = _stores(tmp_path)
    for index in range(201):
        stores.save_history_record(
            {
                "id": f"hist-{index}",
                "avatar_name": "Avatar",
                "avatar_path": "Avatar/Path",
                "user_prompt": "make it softer",
                "provider": "Provider",
                "provider_id": "provider",
                "model": "model",
                "changes": [
                    {
                        "renderer_path": "Face",
                        "blendshape": "Smile",
                        "after": 42.0,
                    }
                ],
            }
        )
    records = stores.load_history()["records"]
    assert len(records) == 200
    assert records[0]["id"] == "hist-1"

    first = stores.create_preset(
        {
            "history_id": "hist-200",
            "name": "Soft",
            "tags": [" face ", ""],
            "description": " gentle ",
            "max_presets": 1,
        }
    )["preset"]
    second = stores.create_preset(
        {
            "history_id": "hist-200",
            "name": "Softer",
            "tags": [],
            "description": "",
            "max_presets": 1,
        }
    )["preset"]
    presets = stores.load_presets()["presets"]
    assert [item["id"] for item in presets] == [second["id"]]
    assert first["tags"] == ["face"]
    assert first["description"] == "gentle"
    assert first["apply_mode"] == "after_values"

    renamed = stores.rename_preset(second["id"], {"name": "Final"})["preset"]
    assert renamed["name"] == "Final"
    assert "updated_at" in renamed
    duplicate = stores.duplicate_preset(
        second["id"],
        {"name": None, "max_presets": 2},
    )["preset"]
    assert duplicate["source_preset_id"] == second["id"]
    stores.mark_preset_applied(duplicate["id"])
    applied = stores.find_preset(duplicate["id"])
    assert applied["apply_count"] == 1
    assert "last_applied_at" in applied
    deleted = stores.delete_preset(second["id"])
    assert deleted["deletedPresetId"] == second["id"]

    stores.mark_history_applied("hist-200")
    history = stores.find_history("hist-200")
    assert history["applied"] is True
    assert "last_applied_at" in history


def _prepared_service(
    tmp_path: Path,
) -> tuple[
    AvatarTuningPreparedService,
    AvatarTuningStoreService,
    AvatarTuningUndoStore,
    dict[str, Any],
]:
    stores, _logs = _stores(tmp_path)
    live: dict[str, Any] = {
        "weight": 10.0,
        "locks": [],
        "mock": False,
        "unity_calls": [],
        "verified": True,
        "finalize": [],
        "history_error": None,
        "history_artifacts": None,
    }

    def context(
        _arguments: dict[str, Any],
        _avatar_hint: str | None,
    ) -> AvatarTuningLiveContext:
        return AvatarTuningLiveContext(
            settings={"connection": "fake"},
            avatar_name="Avatar",
            avatar_path="Avatar/Path",
            allowed_targets={
                ("Face", "Smile"): {"currentWeight": live["weight"]},
                ("Face", "Blink"): {"currentWeight": 20.0},
            },
            locked_blendshapes=list(live["locks"]),
            using_mock_execute=bool(live["mock"]),
        )

    def invoke(
        settings: Any,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        live["unity_calls"].append((settings, tool_name, arguments))
        return {"status": "ok"}

    face_plan = {
        "summary": "face plan",
        "adjustments": [
            {
                "rendererPath": "Face",
                "blendshapeName": "Smile",
                "targetWeight": 55.0,
            }
        ],
    }

    def face_state(_arguments: dict[str, Any]) -> PreparedFaceTuningState:
        ctx = context({}, None)
        return PreparedFaceTuningState(
            context=ctx,
            plan=face_plan,
            direct_adjustments=list(face_plan["adjustments"]),
            change_preview=[
                {
                    "rendererPath": "Face",
                    "blendshapeName": "Smile",
                    "previousWeight": live["weight"],
                    "targetWeight": 55.0,
                }
            ],
            undo_items=[
                {
                    "rendererPath": "Face",
                    "blendshapeName": "Smile",
                    "targetWeight": live["weight"],
                }
            ],
            preview={"summary": "preview"},
            apply_payload="{}",
            export_source="fake-export",
        )

    def save_artifacts(*args: Any) -> dict[str, str]:
        live["finalize"].append(("artifacts", args))
        return {"json": "artifact.json"}

    def save_history(*args: Any) -> dict[str, str]:
        live["finalize"].append(("history", args))
        live["history_artifacts"] = args[-1]
        if live["history_error"]:
            raise RuntimeError(str(live["history_error"]))
        return {"id": "hist-face"}

    stacks: dict[str, list[list[dict[str, Any]]]] = {}
    undo = AvatarTuningUndoStore(stacks, Lock())
    service = AvatarTuningPreparedService(
        stores=stores,
        undo=undo,
        ports=AvatarTuningPreparedPorts(
            resolve_live_context=context,
            invoke_unity=invoke,
            serialize_result=lambda value: value,
            serialize_avatar=lambda ctx: {
                "avatarName": ctx.avatar_name,
                "avatarPath": ctx.avatar_path,
            },
            verify_live_changes=lambda _ctx, changes: [
                {**change, "verified": bool(live["verified"])}
                for change in changes
            ],
            remember_avatar=lambda _name, _path: None,
            prepare_face_state=face_state,
            face_adjustments_from_plan=lambda plan: (
                dict(plan),
                list(plan["adjustments"]),
            ),
            render_face_summary=lambda *_args: {"text": "done"},
            save_face_artifacts=save_artifacts,
            save_face_history=save_history,
        ),
    )
    return service, stores, undo, live


def test_manual_apply_binds_live_weight_locks_and_pushes_undo_after_fake_unity(
    tmp_path: Path,
) -> None:
    service, _stores_service, undo, live = _prepared_service(tmp_path)
    arguments = {
        "avatar": "Avatar/Path",
        "adjustments": [
            {
                "renderer_path": "Face",
                "blendshape_name": "Smile",
                "target_weight": 45.0,
            },
            {
                "renderer_path": "Face",
                "blendshape_name": "Missing",
                "target_weight": 10.0,
            },
        ],
    }

    prepared, preview = service.prepare_manual_apply(arguments, None)
    assert live["unity_calls"] == []
    assert preview["adjustmentCount"] == 1
    assert preview["skippedAdjustments"][0]["reason"] == "missing_blendshape"
    assert prepared_call(prepared)[1]["adjustments"][0]["targetWeight"] == 45.0
    assert prepared_evidence(prepared)["targetFacts"][0]["currentWeight"] == 10.0

    result = service.execute_manual_apply(prepared)

    assert result["ok"] is True
    assert result["undoDepth"] == 1
    assert len(live["unity_calls"]) == 1
    undo_items, evidence = undo.capture("Avatar/Path")
    assert undo_items[0]["targetWeight"] == 10.0
    assert evidence["undoDepth"] == 1


def test_manual_apply_drift_and_undo_cas_fail_before_fake_unity(tmp_path: Path) -> None:
    service, _stores_service, undo, live = _prepared_service(tmp_path)
    arguments = {
        "adjustments": [
            {
                "renderer_path": "Face",
                "blendshape_name": "Smile",
                "target_weight": 45.0,
            }
        ]
    }
    prepared, _preview = service.prepare_manual_apply(arguments, None)
    live["weight"] = 11.0
    with pytest.raises(RuntimeError, match="targetFacts"):
        service.execute_manual_apply(prepared)
    assert live["unity_calls"] == []

    live["weight"] = 10.0
    service.execute_manual_apply(prepared)
    undo_prepared, _undo_preview = service.prepare_manual_undo(
        {"avatar_path": "Avatar/Path"},
        None,
    )
    undo.push(
        "Avatar/Path",
        [
            {
                "rendererPath": "Face",
                "blendshapeName": "Smile",
                "targetWeight": 99.0,
            }
        ],
    )
    call_count = len(live["unity_calls"])
    with pytest.raises(RuntimeError, match="depth drifted"):
        service.execute_manual_undo(undo_prepared)
    assert len(live["unity_calls"]) == call_count

    with pytest.raises(RuntimeError, match="target_weight is invalid"):
        service.prepare_manual_apply(
            {
                "adjustments": [
                    {
                        "renderer_path": "Face",
                        "blendshape_name": "Smile",
                        "target_weight": 101,
                    }
                ]
            },
            None,
        )
    assert len(live["unity_calls"]) == call_count


def test_saved_history_record_and_lock_drift_block_before_fake_unity(
    tmp_path: Path,
) -> None:
    service, stores, _undo, live = _prepared_service(tmp_path)
    record = {
        "id": "hist-1",
        "avatar_name": "Avatar",
        "avatar_path": "Avatar/Path",
        "changes": [
            {
                "renderer_path": "Face",
                "blendshape": "Smile",
                "after": 60.0,
            }
        ],
    }
    stores.save_history_record(record)
    prepared, _preview = service.prepare_reapply_history(
        {"historyId": "hist-1"},
        None,
    )

    history_path = tmp_path / "state" / "tuning_history.json"
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    payload["records"][0]["changes"][0]["after"] = 61.0
    history_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="record drifted"):
        service.execute_reapply_history(prepared)
    assert live["unity_calls"] == []

    payload["records"][0]["changes"][0]["after"] = 60.0
    history_path.write_text(json.dumps(payload), encoding="utf-8")
    live["locks"] = [{"rendererPath": "Face", "blendshapeName": "Smile"}]
    with pytest.raises(RuntimeError, match="No valid saved history"):
        service.execute_reapply_history(prepared)
    assert live["unity_calls"] == []


def test_saved_history_success_marks_metadata_and_reports_readback_warning(
    tmp_path: Path,
) -> None:
    service, stores, _undo, live = _prepared_service(tmp_path)
    stores.save_history_record(
        {
            "id": "hist-1",
            "avatar_name": "Avatar",
            "avatar_path": "Avatar/Path",
            "changes": [
                {
                    "renderer_path": "Face",
                    "blendshape": "Smile",
                    "after": 60.0,
                }
            ],
        }
    )
    prepared, _preview = service.prepare_reapply_history(
        {"historyId": "hist-1"},
        None,
    )
    live["verified"] = False

    result = service.execute_reapply_history(prepared)

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["committedWithWarning"] is True
    assert result["historyRecord"]["applied"] is True
    assert result["readbackVerified"] is False
    assert len(live["unity_calls"]) == 1


def test_face_prepared_execution_revalidates_state_and_finalizes_after_commit(
    tmp_path: Path,
) -> None:
    service, _stores_service, _undo, live = _prepared_service(tmp_path)
    prepared, preview = service.prepare_face_tuning(
        {"avatar": "Avatar/Path", "instruction": "gentler smile"},
        None,
    )

    assert preview["adjustmentCount"] == 1
    assert live["unity_calls"] == []
    result = service.execute_face_tuning(prepared)
    assert result["ok"] is True
    assert result["readbackVerified"] is True
    assert result["summary"] == {"text": "done"}
    assert result["historyRecord"] == {"id": "hist-face"}
    assert [item[0] for item in live["finalize"]] == ["artifacts", "history"]
    assert live["history_artifacts"] == {"json": "artifact.json"}

    prepared_drift, _preview = service.prepare_face_tuning(
        {"avatar": "Avatar/Path", "instruction": "gentler smile"},
        None,
    )
    live["weight"] = 12.0
    call_count = len(live["unity_calls"])
    with pytest.raises(RuntimeError, match="target values"):
        service.execute_face_tuning(prepared_drift)
    assert len(live["unity_calls"]) == call_count


def test_face_history_failure_keeps_successful_artifacts_and_warns(
    tmp_path: Path,
) -> None:
    service, _stores_service, _undo, live = _prepared_service(tmp_path)
    prepared, _preview = service.prepare_face_tuning(
        {"avatar": "Avatar/Path", "instruction": "gentler smile"},
        None,
    )
    live["history_error"] = "disk full"

    result = service.execute_face_tuning(prepared)

    assert result["ok"] is True
    assert result["artifacts"] == {"json": "artifact.json"}
    assert result["historyRecord"] is None
    assert result["warnings"] == [
        "Post-apply metadata was not saved: disk full"
    ]
    assert [item[0] for item in live["finalize"]] == ["artifacts", "history"]
    assert live["history_artifacts"] == {"json": "artifact.json"}
    assert len(live["unity_calls"]) == 1
