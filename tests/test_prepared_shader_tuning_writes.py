from __future__ import annotations

from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, build_prepared_execution_plan


def _state(*, after: float = 0.8, locked: list[str] | None = None) -> dict:
    changes = [{"material_id": "mat_skin", "semantic_property": "smoothness", "before": 0.2, "after": after}]
    core_arguments = {"avatarPath": "Scene/A", "changes": changes, "saveAssets": True}
    return {
        "settings": SimpleNamespace(),
        "avatarPath": "Scene/A",
        "scanArguments": {"avatarPath": "Scene/A", "outputPath": "", "refreshAssets": False},
        "coreArguments": core_arguments,
        "validatedChanges": changes,
        "skippedChanges": [],
        "warnings": [],
        "effectiveLocks": {"lockedMaterials": sorted(locked or []), "lockedProperties": []},
    }


def _arguments() -> dict:
    return {
        "avatar_path": "Scene/A",
        "inventory": {"untrusted": True},
        "changes": [{"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.8}],
    }


def _core_applied_result(*, before: float = 0.2, after: float = 0.8) -> dict:
    """Match the Core's per-change identity and before/after readback."""
    return {
        "appliedCount": 1,
        "applied": [
            {
                "material_id": "mat_skin",
                "semantic_property": "smoothness",
                "before": before,
                "after": after,
            }
        ],
        "skipped": [],
    }


def test_apply_preparer_seals_real_validated_core_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: _state())
    prepared, preview = dashboard_server.prepare_shader_material_apply_request(_arguments(), None)

    assert build_prepared_execution_plan(prepared) == [
        ("vrc_scan_avatar_materials", _state()["scanArguments"]),
        ("vrc_apply_material_tuning", _state()["coreArguments"]),
    ]
    assert preview["changeCount"] == 1


def test_apply_state_scans_current_inventory_and_ignores_caller_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    scanned = {"materials": [{"material_id": "live"}]}
    seen: dict[str, object] = {}
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", lambda _settings, _avatar: scanned)
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_locks", lambda _avatar: {"lockedMaterials": ["store_lock"], "lockedProperties": []})
    monkeypatch.setattr(dashboard_server, "apply_shader_category_overrides", lambda inventory, _overrides: inventory)

    def validate(*, plan, inventory, locked_materials, locked_properties):
        seen.update({"inventory": inventory, "lockedMaterials": locked_materials, "lockedProperties": locked_properties})
        return {"validatedChanges": [{"material_id": "live", "semantic_property": "smoothness", "before": 0.2, "after": 0.8}], "skippedChanges": [], "warnings": []}

    monkeypatch.setattr(dashboard_server, "validate_shader_material_tuning_plan", validate)
    state = dashboard_server._prepare_shader_tuning_apply_state(dashboard_server.ShaderMaterialApplyRequest(**_arguments()))
    assert seen["inventory"] is scanned
    assert "store_lock" in seen["lockedMaterials"]
    assert state["coreArguments"]["changes"][0]["material_id"] == "live"


def test_preview_shader_apply_uses_the_write_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def prepare(request, *, allow_empty=False):
        seen["request"] = request
        seen["allow_empty"] = allow_empty
        return {
            **_state(),
            "validatedChanges": [
                {"material_id": "mat_skin", "semantic_property": "outline_width", "after": 0.0}
            ],
            "skippedChanges": [{"warning": "Unknown material_id: stale"}],
            "warnings": ["Unknown material_id: stale"],
            "coreArguments": {
                "avatarPath": "Scene/A",
                "changes": [
                    {"material_id": "mat_skin", "semantic_property": "outline_width", "after": 0.0}
                ],
                "saveAssets": True,
            },
        }

    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", prepare)
    result = dashboard_server.preview_agent_shader_apply(
        {
            "avatar_path": "Scene/A",
            "changes": [
                {"material_id": "mat_skin", "semantic_property": "outline_width", "after": 0.0},
                {"material_id": "stale", "semantic_property": "outline_width", "after": 0.0},
            ],
        }
    )

    assert seen["allow_empty"] is True
    assert result["ok"] is True
    assert result["requestedChangeCount"] == 2
    assert result["changeCount"] == 1
    assert result["skippedChanges"] == [{"warning": "Unknown material_id: stale"}]
    assert result["applyPayload"]["params"] == prepare(None, allow_empty=True)["coreArguments"]


def test_apply_state_reports_skipped_validation_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", lambda _settings, _avatar: {"materials": []})
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_locks", lambda _avatar: {})
    monkeypatch.setattr(dashboard_server, "apply_shader_category_overrides", lambda inventory, _overrides: inventory)
    monkeypatch.setattr(
        dashboard_server,
        "validate_shader_material_tuning_plan",
        lambda **_kwargs: {
            "validatedChanges": [],
            "skippedChanges": [{"warning": "Unknown material_id: mat_stale"}],
            "warnings": ["Unknown material_id: mat_stale"],
        },
    )

    with pytest.raises(RuntimeError, match="Unknown material_id: mat_stale"):
        dashboard_server._prepare_shader_tuning_apply_state(
            dashboard_server.ShaderMaterialApplyRequest(
                avatar_path="Scene/A",
                changes=[
                    {"material_id": "mat_stale", "semantic_property": "outline_width", "after": 0.0}
                ],
            )
        )


def test_apply_rejects_live_validation_drift_before_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: _state())
    prepared, _ = dashboard_server.prepare_shader_material_apply_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: _state(after=0.7))
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()

    with pytest.raises(Exception, match="drifted"):
        dashboard_server.apply_shader_material_plan_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack == {}


def test_apply_uses_sealed_call_and_pushes_undo_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: state)
    prepared, _ = dashboard_server.prepare_shader_material_apply_request(_arguments(), None)
    calls: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(
        dashboard_server,
        "apply_shader_material_tuning_direct",
        lambda _settings, avatar, changes: calls.append((avatar, changes)) or _core_applied_result(),
    )
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()

    result = dashboard_server.apply_shader_material_plan_approved_sync(prepared)
    assert calls == [("Scene/A", state["coreArguments"]["changes"])]
    assert result["undoDepth"] == 1
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack["Scene/A"][0][0]["after"] == 0.2


def test_apply_core_failure_does_not_create_undo_or_history_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: state)
    prepared, _ = dashboard_server.prepare_shader_material_apply_request({**_arguments(), "history_id": "hist"}, None)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(RuntimeError("Core failed")))
    mark_calls: list[str] = []
    monkeypatch.setattr(dashboard_server, "mark_shader_tuning_history_applied", lambda value: mark_calls.append(value))
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()

    with pytest.raises(Exception, match="Core failed"):
        dashboard_server.apply_shader_material_plan_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack == {}
    assert mark_calls == []


def test_apply_core_error_payload_does_not_create_undo(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: state)
    prepared, _ = dashboard_server.prepare_shader_material_apply_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: {"ok": False, "error": "rejected"})
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()

    with pytest.raises(Exception, match="rejected"):
        dashboard_server.apply_shader_material_plan_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack == {}


def test_apply_partial_core_readback_requires_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: state)
    prepared, _ = dashboard_server.prepare_shader_material_apply_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: {"appliedCount": 0, "applied": [], "skipped": [{"warning": "drift"}]})
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()

    with pytest.raises(Exception, match="partial shader material write"):
        dashboard_server.apply_shader_material_plan_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack == {}


def test_apply_post_core_history_failure_is_committed_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(dashboard_server, "_prepare_shader_tuning_apply_state", lambda _request: state)
    prepared, _ = dashboard_server.prepare_shader_material_apply_request({**_arguments(), "history_id": "hist"}, None)
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: _core_applied_result())
    monkeypatch.setattr(dashboard_server, "mark_shader_tuning_history_applied", lambda _value: (_ for _ in ()).throw(OSError("disk full")))

    result = dashboard_server.apply_shader_material_plan_approved_sync(prepared)
    assert result["committed"] is True
    assert result["committedWithWarning"] is True
    assert "disk full" in result["warning"]


def test_restore_holds_top_on_failure_and_consumes_only_exact_peek(monkeypatch: pytest.MonkeyPatch) -> None:
    avatar = "Scene/A"
    top = [{"material_id": "mat_skin", "semantic_property": "smoothness", "after": 0.2}]
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack[avatar] = [top]
    prepared, _ = dashboard_server.prepare_shader_material_restore_request({"avatar_path": avatar}, None)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(RuntimeError("Core failed")))
    with pytest.raises(Exception, match="Core failed"):
        dashboard_server.restore_shader_material_plan_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack[avatar] == [top]

    calls: list[list[dict]] = []
    monkeypatch.setattr(
        dashboard_server,
        "apply_shader_material_tuning_direct",
        lambda _settings, _avatar, changes: calls.append(changes) or _core_applied_result(before=0.2, after=0.2),
    )
    result = dashboard_server.restore_shader_material_plan_approved_sync(prepared)
    assert calls == [top]
    assert result["undoDepth"] == 0
    assert dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack[avatar] == []


def test_restore_rejects_stack_drift_before_core(monkeypatch: pytest.MonkeyPatch) -> None:
    avatar = "Scene/A"
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack.clear()
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack[avatar] = [[{"material_id": "a", "semantic_property": "x", "after": 1}]]
    prepared, _ = dashboard_server.prepare_shader_material_restore_request({"avatar_path": avatar}, None)
    dashboard_server.DASHBOARD_RUNTIME.shader_undo_stack[avatar].append([{"material_id": "b", "semantic_property": "y", "after": 2}])
    monkeypatch.setattr(dashboard_server, "apply_shader_material_tuning_direct", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not run")))
    with pytest.raises(Exception, match="depth drifted"):
        dashboard_server.restore_shader_material_plan_approved_sync(prepared)


def test_shader_handlers_are_bound_to_prepared_execution() -> None:
    for target, preparer in (
        ("vrcforge_apply_shader_tuning", dashboard_server.prepare_shader_material_apply_request),
        ("vrcforge_restore_shader_tuning", dashboard_server.prepare_shader_material_restore_request),
    ):
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[target]  # noqa: SLF001
        assert handler.request_preparer is preparer
        assert handler.requires_approved_execution_context is True
        assert handler.approved_execution_plan_builder is dashboard_server.build_prepared_execution_plan


def test_shader_preparers_reject_caller_supplied_prepared_seal() -> None:
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_shader_material_apply_request({**_arguments(), PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_shader_material_restore_request({"avatar_path": "Scene/A", PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)
