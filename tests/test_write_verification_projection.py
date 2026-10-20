from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import dashboard_server
from agent_tool_result_reader import bind_tool_result_context, read_tool_result
from agent_write_verification_summary import bounded_write_verification_summary
from runtime_planner_service import sanitize_planner_observation_text
from tests.test_native_runtime_gateway import call, finish, setup_gateway


RECEIPT = Path(__file__).parent / "fixtures" / "fx_write_verified_receipt.json"


def _receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def test_fx_producer_projects_persisted_scalar_and_never_requested_scalar(monkeypatch):
    receipt = _receipt()
    seen = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: {})

    def invoke(_settings, _tool, request, **_kwargs):
        seen.append(request)
        return dashboard_server.McpResult(0, "", "", deepcopy(receipt))

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    request = {
        "controllerPath": "Assets/Fixture/FX.controller",
        "action": "update_state",
        "layerName": "Left Hand",
        "stateName": "Fist",
        "speed": 999.0,
    }

    payload = dashboard_server.manage_fx_animator_sync(request)
    assert payload["writeVerification"]["readback"]["target"]["speed"] == 0.75
    assert payload["writeVerification"]["readback"]["target"]["speed"] != request["speed"]
    assert seen and seen[0]["speed"] == 999.0

    preview = dashboard_server.manage_fx_animator_sync(request, preview=True)
    assert "writeVerification" not in preview


def test_native_approval_resume_exposes_bounded_verification_and_reader_pointer(tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Packages").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )
    gateway, model, _ = setup_gateway(tmp_path, [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write", "vrcforge_manage_fx_animator", {
            "projectRoot": str(tmp_path),
            "controllerPath": "Assets/Fixture/FX.controller",
            "action": "update_state", "layerName": "Left Hand", "stateName": "Fist",
            "speed": 999.0,
        }),
        finish,
    ])
    calls = []

    def handler(arguments):
        calls.append(dict(arguments))
        raw = _receipt()
        raw["status"] = "applied"
        raw["writeVerification"] = bounded_write_verification_summary(raw, arguments)
        return raw

    gateway.register_tool(
        "vrcforge_manage_fx_animator",
        "When to use: apply an approved FX state update. When NOT to use: preview or inspect.",
        "write", handler, write=True,
    )
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _root: {"ok": True}
    gateway.approval_transactions.register_write_handler(
        "vrcforge_manage_fx_animator", "Apply one approved FX update.", "high", handler,
    )

    first = gateway.runtime_message({
        "message": "Update Left Hand Fist speed to 0.75",
        "sessionId": "fx-verification-session", "clientTurnId": "fx-verification-turn",
        "projectRoot": str(tmp_path), "maxAgenticTurns": 5,
    })
    approval_id = next(iter(gateway._approvals))
    approval = gateway._approvals[approval_id]
    assert first["write"]["status"] == "approval_pending"
    assert calls == []

    gateway.approval_transactions.approve(approval_id)
    applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    assert applied["status"] == "applied", applied
    assert "writeVerification" in json.dumps(applied)
    assert len(calls) == 1

    resumed = gateway.resume_runtime_task_after_approval(approval, applied)
    assert resumed["plan"]["nextStep"] in {"done", "completion_unverified"}
    assert len(calls) == 1
    resumed_request = model.requests[-1]
    tool_messages = [m for m in resumed_request["messages"] if m.get("role") == "tool"]
    write_message = next(m for m in tool_messages if m.get("tool_call_id") == "write")
    content = json.loads(write_message["content"])
    serialized = json.dumps(resumed_request)
    assert "writeVerification" in serialized, content
    assert "0.75" in serialized
    result_ref = next(
        step["resultRead"]["resultRef"] for step in resumed["steps"]
        if step.get("resultRead", {}).get("resultRef")
    )
    assert result_ref in serialized
    result_step = next(step for step in resumed["steps"]
                       if step.get("resultRead", {}).get("resultRef") == result_ref)

    with bind_tool_result_context(
        "fx-verification-session", "fx-verification-turn", str(tmp_path), resumed["steps"],
        snapshot_lookup=lambda _session, _turn, _project, ref:
            {"step": result_step, "verification": "current_turn"}
            if ref == result_ref else None,
    ):
        page = read_tool_result(
            {"resultRef": result_ref,
             "jsonPointer": "/readback/state/layers/1/machine/states/0/speed"},
            sanitize=sanitize_planner_observation_text,
        )
    assert page["items"][0]["value"] == 0.75
