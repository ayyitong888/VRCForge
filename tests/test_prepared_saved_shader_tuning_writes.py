from __future__ import annotations

from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, build_prepared_execution_plan


def _record(record_id: str = "hist") -> dict:
    return {
        "id": record_id,
        "avatar_path": "Scene/A",
        "changes": [{"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.8}],
    }


def _state() -> dict:
    changes = [{"material_id": "mat_skin", "semantic_property": "smoothness", "before": 0.2, "after": 0.8}]
    return {
        "settings": SimpleNamespace(),
        "avatarPath": "Scene/A",
        "coreArguments": {"avatarPath": "Scene/A", "changes": changes, "saveAssets": True},
        "validatedChanges": changes,
        "skippedChanges": [],
        "warnings": [],
        "effectiveLocks": {"lockedMaterials": [], "lockedProperties": []},
        "restoreSnapshot": [{"material_id": "mat_skin", "material_name": "", "semantic_property": "smoothness", "after": 0.2, "reason": "Restore previous material value."}],
    }


def _core_applied_result() -> dict:
    """Match the Core's per-change identity and before/after readback."""
    return {
        "appliedCount": 1,
        "applied": [
            {
                "material_id": "mat_skin",
                "semantic_property": "smoothness",
                "before": 0.2,
                "after": 0.8,
            }
        ],
        "skipped": [],
    }


def _install_history(monkeypatch: pytest.MonkeyPatch, record: dict | None = None) -> dict:
    live = record or _record()
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_history_store", lambda: {"records": [live]})
    monkeypatch.setattr(dashboard_server, "_prepare_saved_shader_tuning_state", lambda _request, _saved, _type: _state())
    return live


def test_history_preparer_seals_saved_identity_and_exact_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_history(monkeypatch)
    prepared, preview = dashboard_server.prepare_reapply_shader_tuning_history_request({"historyId": "hist"}, None)
    assert build_prepared_execution_plan(prepared) == [("vrc_apply_material_tuning", _state()["coreArguments"])]
    assert preview["targetTool"] == "vrcforge_reapply_shader_tuning_history"


def test_history_source_drift_rejected_before_core(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _install_history(monkeypatch)
    prepared, _ = dashboard_server.prepare_reapply_shader_tuning_history_request({"historyId": "hist"}, None)
    live["changes"][0]["after"] = 0.7
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    with pytest.raises(Exception, match="source record drifted"):
        dashboard_server.reapply_shader_tuning_history_approved_sync(prepared)


def test_history_live_validation_drift_rejected_before_core(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_history(monkeypatch)
    prepared, _ = dashboard_server.prepare_reapply_shader_tuning_history_request({"historyId": "hist"}, None)
    changed = _state()
    changed["coreArguments"] = {**changed["coreArguments"], "changes": [{**changed["coreArguments"]["changes"][0], "after": 0.7}]}
    monkeypatch.setattr(dashboard_server, "_prepare_saved_shader_tuning_state", lambda _request, _saved, _type: changed)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    with pytest.raises(Exception, match="Core arguments drifted"):
        dashboard_server.reapply_shader_tuning_history_approved_sync(prepared)


def test_history_core_failure_keeps_undo_and_metadata_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_history(monkeypatch)
    prepared, _ = dashboard_server.prepare_reapply_shader_tuning_history_request({"historyId": "hist"}, None)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(RuntimeError("Core failed")))
    mark_calls: list[str] = []
    monkeypatch.setattr(dashboard_server, "mark_shader_tuning_history_applied", lambda value: mark_calls.append(value))
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()
    with pytest.raises(Exception, match="Core failed"):
        dashboard_server.reapply_shader_tuning_history_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack == {}
    assert mark_calls == []


def test_preset_success_marks_linked_history_and_preset_after_core(monkeypatch: pytest.MonkeyPatch) -> None:
    preset = {**_record("preset"), "source_history_id": "hist"}
    history = _record("hist")
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_preset_store", lambda: {"presets": [preset]})
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_history_store", lambda: {"records": [history]})
    monkeypatch.setattr(dashboard_server, "_prepare_saved_shader_tuning_state", lambda _request, _saved, _type: _state())
    prepared, _ = dashboard_server.prepare_apply_shader_tuning_preset_request({"presetId": "preset"}, None)
    calls: list[str] = []
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: calls.append("core") or _core_applied_result())
    metadata: list[tuple[str, str]] = []
    monkeypatch.setattr(dashboard_server, "mark_shader_tuning_history_applied", lambda value: metadata.append(("history", value)))
    monkeypatch.setattr(dashboard_server, "mark_shader_tuning_preset_applied", lambda value: metadata.append(("preset", value)))
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()
    result = dashboard_server.apply_shader_tuning_preset_approved_sync(prepared)
    assert calls == ["core"]
    assert metadata == [("history", "hist"), ("preset", "preset")]
    assert result["undoDepth"] == 1


def test_preset_linked_history_drift_blocks_core(monkeypatch: pytest.MonkeyPatch) -> None:
    preset = {**_record("preset"), "source_history_id": "hist"}
    history = _record("hist")
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_preset_store", lambda: {"presets": [preset]})
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_history_store", lambda: {"records": [history]})
    monkeypatch.setattr(dashboard_server, "_prepare_saved_shader_tuning_state", lambda _request, _saved, _type: _state())
    prepared, _ = dashboard_server.prepare_apply_shader_tuning_preset_request({"presetId": "preset"}, None)
    history["changes"][0]["after"] = 0.5
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    with pytest.raises(Exception, match="history record drifted"):
        dashboard_server.apply_shader_tuning_preset_approved_sync(prepared)


def test_metadata_failure_returns_committed_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_history(monkeypatch)
    prepared, _ = dashboard_server.prepare_reapply_shader_tuning_history_request({"historyId": "hist"}, None)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: _core_applied_result())
    monkeypatch.setattr(dashboard_server, "mark_shader_tuning_history_applied", lambda _value: (_ for _ in ()).throw(OSError("disk full")))
    result = dashboard_server.reapply_shader_tuning_history_approved_sync(prepared)
    assert result["committed"] is True
    assert result["committedWithWarning"] is True


@pytest.mark.parametrize(
    ("preparer", "arguments"),
    [
        (dashboard_server.prepare_reapply_shader_tuning_history_request, {"historyId": "hist"}),
        (dashboard_server.prepare_apply_shader_tuning_preset_request, {"presetId": "preset"}),
    ],
)
def test_saved_shader_preparers_reject_reserved_seal(preparer, arguments: dict) -> None:
    with pytest.raises(RuntimeError, match="reserved"):
        preparer({**arguments, PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)


def test_saved_shader_handlers_are_bound_to_prepared_execution() -> None:
    for target, preparer in (
        ("vrcforge_reapply_shader_tuning_history", dashboard_server.prepare_reapply_shader_tuning_history_request),
        ("vrcforge_apply_shader_tuning_preset", dashboard_server.prepare_apply_shader_tuning_preset_request),
    ):
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[target]  # noqa: SLF001
        assert handler.request_preparer is preparer
        assert handler.requires_approved_execution_context is True
        assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan
