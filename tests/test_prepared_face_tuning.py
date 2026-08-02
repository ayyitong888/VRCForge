from __future__ import annotations

from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY, build_prepared_execution_plan, prepared_evidence
from vrchat_blendshape_agent import BlendshapeAdjustment, BlendshapePlan, SelectedAvatar


def _state() -> dict:
    avatar = SelectedAvatar("A", "Scene/A", "Main", 1, 1)
    plan = BlendshapePlan(summary="smile", adjustments=[BlendshapeAdjustment(avatar_path="Scene/A", renderer_path="Body", blendshape_name="Smile", target_weight=50.0, reason="test", confidence=1.0)])
    changes = [{"avatarPath": "Scene/A", "rendererPath": "Body", "blendshapeName": "Smile", "previousWeight": 12.0, "targetWeight": 50.0, "delta": 38.0, "reason": "test", "confidence": 1.0}]
    return {
        "settings": SimpleNamespace(), "exportPayload": {"weight": 12.0}, "exportSource": "unity", "usingMockExecute": False,
        "selectedAvatar": avatar, "lockedBlendshapes": [], "plan": plan,
        "directAdjustments": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 50.0}],
        "changePreview": changes, "undoItems": [{"rendererPath": "Body", "blendshapeName": "Smile", "targetWeight": 12.0}],
        "referenceContext": None, "preview": {"ok": True}, "applyPayload": "{}",
        "evidence": {"avatarPath": "Scene/A", "targetFacts": [{"rendererPath": "Body", "blendshapeName": "Smile", "currentWeight": 12.0}], "locksSha256": dashboard_server.blendshape_evidence_sha256([]), "planSha256": dashboard_server.blendshape_evidence_sha256(plan.model_dump())},
    }


def _arguments() -> dict:
    return {"avatar": "Scene/A", "instruction": "smile", "mock_execute": False, "save_artifacts": False}


def test_preparer_freezes_one_call_and_never_accepts_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_face_tuning_state", lambda _request: _state())
    prepared, _ = dashboard_server.prepare_face_tuning_execution_request(_arguments(), None)
    assert build_prepared_execution_plan(prepared) == [("vrc_apply_blendshapes", {"avatarPath": "Scene/A", "adjustments": _state()["directAdjustments"], "saveAssets": True})]
    assert prepared_evidence(prepared)["undoItems"][0]["targetWeight"] == 12.0
    with pytest.raises(RuntimeError, match="preview-only"):
        dashboard_server.prepare_face_tuning_execution_request({**_arguments(), "mock_execute": True}, None)


def test_execution_uses_sealed_call_and_defers_visual_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_face_tuning_state", lambda _request: _state())
    prepared, _ = dashboard_server.prepare_face_tuning_execution_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "_revalidate_prepared_face_tuning", lambda _request, _evidence: (SimpleNamespace(), _state()["selectedAvatar"]))
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda _settings, tool, args: calls.append((tool, args)) or dashboard_server.McpResult(0, "", "", {}))
    monkeypatch.setattr(dashboard_server, "verify_live_blendshape_changes", lambda *_args: [{"verified": True}])
    monkeypatch.setattr(dashboard_server, "render_summary", lambda *_args: "done")
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()

    result = dashboard_server.run_face_tuning_approved_sync(prepared)
    assert calls == build_prepared_execution_plan(prepared)
    assert result["visualProof"]["status"] == "unavailable"
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack["Scene/A"] == [prepared_evidence(prepared)["undoItems"]]


def test_execution_drift_blocks_core_and_undo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_face_tuning_state", lambda _request: _state())
    prepared, _ = dashboard_server.prepare_face_tuning_execution_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "_revalidate_prepared_face_tuning", lambda *_args: (_ for _ in ()).throw(RuntimeError("target values drifted")))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not be called")))
    dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack.clear()
    with pytest.raises(Exception, match="drifted"):
        dashboard_server.run_face_tuning_approved_sync(prepared)
    assert dashboard_server.DASHBOARD_RUNTIME.manual_undo_stack == {}


def test_execution_ignores_volatile_export_timestamp_when_bound_facts_match(monkeypatch: pytest.MonkeyPatch) -> None:
    export_payload = {
        "generatedAtUtc": "later-than-approval",
        "avatars": [{
            "avatarName": "A", "avatarPath": "Scene/A", "sceneName": "Main",
            "renderers": [{"rendererPath": "Body", "blendshapes": [{"name": "Smile", "currentWeight": 12.0}]}],
        }],
    }
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "load_dashboard_export_payload", lambda *_args: (export_payload, "unity", False))
    monkeypatch.setattr(dashboard_server, "load_locked_blendshapes", lambda _avatar: [])
    settings, avatar = dashboard_server._revalidate_prepared_face_tuning(
        dashboard_server.build_agent_dashboard_request(_arguments()),
        _state()["evidence"],
    )
    assert settings is not None
    assert avatar.avatar_path == "Scene/A"


def test_post_apply_metadata_error_is_a_warning_not_a_retry_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_face_tuning_state", lambda _request: _state())
    prepared, _ = dashboard_server.prepare_face_tuning_execution_request(_arguments(), None)
    monkeypatch.setattr(dashboard_server, "_revalidate_prepared_face_tuning", lambda _request, _evidence: (SimpleNamespace(), _state()["selectedAvatar"]))
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: dashboard_server.McpResult(0, "", "", {}))
    monkeypatch.setattr(dashboard_server, "verify_live_blendshape_changes", lambda *_args: [{"verified": True}])
    monkeypatch.setattr(dashboard_server, "render_summary", lambda *_args: "done")
    monkeypatch.setattr(dashboard_server, "build_tuning_history_record", lambda **_kwargs: {})
    monkeypatch.setattr(dashboard_server, "save_tuning_history_record", lambda _record: (_ for _ in ()).throw(RuntimeError("disk full")))

    result = dashboard_server.run_face_tuning_approved_sync(prepared)
    assert result["ok"] is True
    assert result["warnings"] == ["Post-apply metadata was not saved: disk full"]


def test_reserved_seal_and_registration_are_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "_prepare_face_tuning_state", lambda _request: _state())
    with pytest.raises(RuntimeError, match="reserved"):
        dashboard_server.prepare_face_tuning_execution_request({**_arguments(), PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None)
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_run_face_tuning"]  # noqa: SLF001
    assert handler.request_preparer is dashboard_server.prepare_face_tuning_execution_request
    assert handler.approved_execution_plan_builder is build_prepared_execution_plan
