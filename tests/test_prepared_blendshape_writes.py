from __future__ import annotations

from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    prepared_evidence,
)


def _state(*, current: float = 12.0, locked: list[dict] | None = None) -> dict:
    avatar = dashboard_server.SelectedAvatar("A", "Scene/A", "Main", 1, 1)
    return {
        "settings": SimpleNamespace(),
        "exportSource": "test",
        "usingMockExecute": False,
        "selectedAvatar": avatar,
        "validatedAdjustments": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 50.0}],
        "skippedAdjustments": [],
        "undoItems": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": current}],
        "evidence": {
            "avatarPath": "Scene/A",
            "targetFacts": [{"rendererPath": "Body", "blendshapeName": "Smile", "currentWeight": current}],
            "locksSha256": dashboard_server.blendshape_evidence_sha256(locked or []),
        },
    }


def _arguments() -> dict:
    return {"avatar": "Scene/A", "adjustments": [{"renderer_path": "Body", "blendshape_name": "Smile", "target_weight": 50, "previous_weight": 1}]}


def test_apply_preparer_freezes_real_weight_not_caller_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_manual_blendshape_state", lambda _request: _state(current=12.0))
    prepared, _ = dashboard_server.prepare_manual_blendshape_apply_request(_arguments(), None)

    assert build_prepared_execution_plan(prepared) == [("vrc_apply_blendshapes", {"avatarPath": "Scene/A", "adjustments": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 50.0}], "saveAssets": True})]
    assert prepared_evidence(prepared)["undoItems"][0]["targetWeight"] == 12.0


def test_apply_execution_rejects_drift_without_core_or_undo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_manual_blendshape_state", lambda _request: _state(current=12.0))
    prepared, _ = dashboard_server.prepare_manual_blendshape_apply_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "_prepare_manual_blendshape_state", lambda _request: _state(current=13.0))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not be called")))
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()

    with pytest.raises(Exception, match="drifted"):
        dashboard_server.apply_manual_blendshapes_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack == {}


def test_apply_execution_uses_sealed_call_and_pushes_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(dashboard_server, "_prepare_manual_blendshape_state", lambda _request: state)
    prepared, _ = dashboard_server.prepare_manual_blendshape_apply_request(_arguments(), None)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda _settings, tool, arguments: calls.append((tool, arguments)) or dashboard_server.McpResult(0, "", "", {}))
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()

    dashboard_server.apply_manual_blendshapes_approved_sync(prepared)
    assert calls == build_prepared_execution_plan(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack["Scene/A"] == [prepared_evidence(prepared)["undoItems"]]


def test_preparer_rejects_reserved_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_manual_blendshape_state", lambda _request: _state())
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_manual_blendshape_apply_request({**_arguments(), PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)


def test_apply_preparer_rejects_mock_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    state["usingMockExecute"] = True
    monkeypatch.setattr(dashboard_server, "_prepare_manual_blendshape_state", lambda _request: state)
    with pytest.raises(RuntimeError, match="preview-only"):
        dashboard_server.prepare_manual_blendshape_apply_request(_arguments(), None)


def test_undo_failure_keeps_stack_and_success_consumes_exact_top(monkeypatch: pytest.MonkeyPatch) -> None:
    avatar = "Scene/A"
    top = [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 12.0}]
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack[avatar] = [top]
    prepared, _ = dashboard_server.prepare_manual_blendshape_undo_request({"avatar_path": avatar}, None)
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "apply_blendshapes_direct", lambda *_args: (_ for _ in ()).throw(RuntimeError("Core failed")))
    with pytest.raises(Exception, match="Core failed"):
        dashboard_server.undo_manual_blendshapes_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack[avatar] == [top]

    calls: list[list[dict]] = []
    monkeypatch.setattr(dashboard_server, "apply_blendshapes_direct", lambda _settings, _avatar, adjustments: calls.append(adjustments) or dashboard_server.McpResult(0, "", "", {}))
    dashboard_server.undo_manual_blendshapes_approved_sync(prepared)
    assert calls == [top]
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack[avatar] == []


def test_blendshape_handlers_are_bound_to_prepared_execution() -> None:
    for target, preparer in (("vrcforge_apply_blendshapes", dashboard_server.prepare_manual_blendshape_apply_request), ("vrcforge_undo_blendshapes", dashboard_server.prepare_manual_blendshape_undo_request)):
        handler = dashboard_server.AGENT_GATEWAY._write_handlers[target]  # noqa: SLF001
        assert handler.request_preparer is preparer
        assert handler.requires_approved_execution_context is True
        assert handler.approved_execution_plan_builder is build_prepared_execution_plan
