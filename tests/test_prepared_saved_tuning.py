from __future__ import annotations

from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, build_prepared_execution_plan, prepared_evidence
from vrchat_blendshape_agent import SelectedAvatar


def _saved(item_id: str) -> dict:
    return {"id": item_id, "avatar_path": "Scene/A", "changes": [{"rendererPath": "Body", "blendshapeName": "Smile", "after": 50.0}]}


def _state() -> dict:
    avatar = SelectedAvatar("A", "Scene/A", "Main", 1, 1)
    return {"settings": SimpleNamespace(), "selectedAvatar": avatar, "exportSource": "unity", "usingMockExecute": False, "adjustments": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 50.0}], "undoItems": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 12.0}], "skipped": [], "evidence": {"avatarPath": "Scene/A", "targetFacts": [{"rendererPath": "Body", "blendshapeName": "Smile", "currentWeight": 12.0}], "locksSha256": dashboard_server.blendshape_evidence_sha256([])}}


@pytest.mark.parametrize(("source_type", "item_key", "target", "preparer"), [
    ("history", "historyId", "vrcforge_reapply_tuning_history", dashboard_server.prepare_reapply_tuning_history_request),
    ("preset", "presetId", "vrcforge_apply_tuning_preset", dashboard_server.prepare_apply_tuning_preset_request),
])
def test_saved_preparer_freezes_identity_and_exact_call(monkeypatch: pytest.MonkeyPatch, source_type: str, item_key: str, target: str, preparer) -> None:
    finder = "find_tuning_history_record" if source_type == "history" else "find_tuning_preset"
    monkeypatch.setattr(dashboard_server, finder, lambda item_id: _saved(item_id))
    monkeypatch.setattr(dashboard_server, "_prepare_saved_tuning_state", lambda *_args: _state())
    args = {item_key: "item-1", "mock_execute": False}
    prepared, _ = preparer(args, None)
    assert build_prepared_execution_plan(prepared) == [("vrc_apply_blendshapes", {"avatarPath": "Scene/A", "adjustments": _state()["adjustments"], "saveAssets": True})]
    assert prepared_evidence(prepared)[item_key] == "item-1"
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[target]  # noqa: SLF001
    assert handler.request_preparer is preparer
    assert handler.approved_execution_plan_builder is build_prepared_execution_plan


def test_history_drift_blocks_core_and_undo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "find_tuning_history_record", lambda item_id: _saved(item_id))
    monkeypatch.setattr(dashboard_server, "_prepare_saved_tuning_state", lambda *_args: _state())
    prepared, _ = dashboard_server.prepare_reapply_tuning_history_request({"historyId": "history-1", "mock_execute": False}, None)
    monkeypatch.setattr(dashboard_server, "find_tuning_history_record", lambda item_id: {**_saved(item_id), "changes": []})
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not be called")))
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()
    with pytest.raises(Exception, match="record drifted"):
        dashboard_server.reapply_tuning_history_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack == {}


def test_preset_executes_sealed_call_and_metadata_failure_is_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "find_tuning_preset", lambda item_id: _saved(item_id))
    monkeypatch.setattr(dashboard_server, "_prepare_saved_tuning_state", lambda *_args: _state())
    prepared, _ = dashboard_server.prepare_apply_tuning_preset_request({"presetId": "preset-1", "mock_execute": False}, None)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda _settings, tool, args: calls.append((tool, args)) or dashboard_server.McpResult(0, "", "", {}))
    monkeypatch.setattr(dashboard_server, "verify_live_blendshape_changes", lambda *_args: [{"verified": True}])
    monkeypatch.setattr(dashboard_server, "mark_tuning_preset_applied", lambda _item_id: (_ for _ in ()).throw(RuntimeError("disk full")))
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()
    result = dashboard_server.apply_tuning_preset_approved_sync(prepared)
    assert calls == build_prepared_execution_plan(prepared)
    assert result["warnings"] == ["Post-apply metadata was not saved: disk full"]
    assert result["readbackVerified"] is True
    assert result["committedWithWarning"] is True
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack["Scene/A"] == [prepared_evidence(prepared)["undoItems"]]


def test_mock_and_reserved_input_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "find_tuning_history_record", lambda item_id: _saved(item_id))
    monkeypatch.setattr(dashboard_server, "_prepare_saved_tuning_state", lambda *_args: _state())
    with pytest.raises(RuntimeError, match="preview-only"):
        dashboard_server.prepare_reapply_tuning_history_request({"historyId": "h", "mock_execute": True}, None)
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_reapply_tuning_history_request({"historyId": "h", "mock_execute": False, PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)
