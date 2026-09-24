from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_gateway import AgentGatewayError
from tests.test_native_runtime_gateway import call, finish, setup_gateway


def _project(root: Path) -> Path:
    project = root / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )
    return project


def _setup_write(project: Path, replies: list[object]):
    gateway, model, _ = setup_gateway(project, replies)
    handler_calls: list[dict[str, object]] = []

    def handler(arguments: dict[str, object]) -> dict[str, object]:
        handler_calls.append(dict(arguments))
        return {
            "ok": True,
            "status": "applied",
            "path": arguments["path"],
            "persistedReadback": True,
            "readback": {"text": "hello"},
        }

    gateway.register_tool(
        "fixture_write",
        "When to use: write one fixture file. When NOT to use: read or inspect only.",
        "write",
        handler,
        write=True,
    )
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _root: {"ok": True}
    gateway.approval_transactions.register_write_handler(
        "fixture_write", "Write one fixture file.", "high", handler
    )
    return gateway, model, handler_calls, project


def _write_replies(project: Path) -> list[object]:
    return [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call(
            "write",
            "fixture_write",
            {"projectRoot": str(project), "path": str(project / "Assets" / "out.txt"), "content": "hello"},
        ),
    ]


def test_native_pending_write_approval_apply_resume_readback_and_final_evidence(tmp_path: Path) -> None:
    project = _project(tmp_path)
    # Build the fixture once, then use the same native provider sequence for
    # the live gateway call and its approval continuation.
    gateway, model, handler_calls, _ = _setup_write(
        project,
        [
            *_write_replies(project),
            finish,
        ],
    )

    first = gateway.runtime_message(
        {
            "message": "write fixture",
            "sessionId": "native-approval-session",
            "clientTurnId": "native-approval-turn",
            "projectRoot": str(project),
            "maxAgenticTurns": 5,
        }
    )
    approval_id = next(iter(gateway._approvals))
    approval = gateway._approvals[approval_id]
    assert first["write"]["status"] == "approval_pending"
    assert approval["status"] == "pending"
    assert approval["taskContext"]["_nativeConversation"]["messages"][-1]["tool_calls"][0]["id"] == "write"
    assert handler_calls == []

    gateway.approval_transactions.approve(approval_id)
    applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    assert applied["ok"] is True
    assert applied["status"] == "applied"
    assert applied["result"]["persistedReadback"] is True
    assert applied["result"]["readback"] == {"text": "hello"}
    assert len(handler_calls) == 1

    resumed = gateway.resume_runtime_task_after_approval(approval, applied)
    assert resumed is not None
    assert resumed["plan"]["nextStep"] == "done"
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert [item["tool_call_id"] for item in snapshot["messages"] if item.get("role") == "tool"] == [
        "phase", "write", "final"
    ]
    write_results = [
        item for item in snapshot["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "write"
    ]
    assert len(write_results) == 1
    assert '"status": "completed"' in write_results[0]["content"]
    assert len(handler_calls) == 1
    assert len(model.requests) == 3


def test_native_revision_preserves_same_call_rejection_without_reexecuting_handler(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway, _model, handler_calls, _ = _setup_write(
        project,
        [
            *_write_replies(project),
            {"role": "assistant", "content": "The proposed write was rejected."},
        ],
    )
    gateway.runtime_message(
        {
            "message": "write fixture",
            "sessionId": "native-revision-session",
            "clientTurnId": "native-revision-turn",
            "projectRoot": str(project),
            "maxAgenticTurns": 5,
        }
    )
    approval_id = next(iter(gateway._approvals))
    approval = gateway._approvals[approval_id]
    revision = gateway.approval_transactions.request_approval_revision(
        approval_id, reason="Use a different target."
    )
    resumed = gateway.resume_runtime_task_after_approval(
        approval, revision, revision_requested=True
    )

    assert revision["ok"] is True
    assert revision["approval"]["status"] == "revision_requested"
    assert resumed is not None
    assert handler_calls == []
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert snapshot["messages"][-1] == {
        "role": "assistant",
        "content": "The proposed write was rejected.",
    }
    assert [item["tool_call_id"] for item in snapshot["messages"] if item.get("role") == "tool"] == [
        "phase", "write"
    ]


def test_native_pending_restart_approve_executes_once_and_resumes_from_durable_snapshot(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original, original_model, original_calls, _ = _setup_write(project, _write_replies(project))
    initial = original.runtime_message({
        "message": "write fixture",
        "sessionId": "native-restart-success",
        "clientTurnId": "before-restart",
        "projectRoot": str(project),
        "maxAgenticTurns": 5,
    })
    approval_id = initial["approval_id"]
    assert initial["write"]["status"] == "approval_pending"
    assert original_calls == []
    saved = original._approvals[approval_id]["taskContext"]["_nativeConversation"]

    # Recreate all runtime owners against the same files, without copying any
    # conversation, approval or task state from the original gateway.
    restarted, resumed_model, restarted_calls, _ = _setup_write(project, [finish])
    assert restarted.runtime_sessions._native_conversations == {}
    restored = restarted._approvals[approval_id]
    assert restored["status"] == "pending"
    assert restored["taskContext"]["_nativeConversation"] == saved
    assert restarted_calls == []
    assert "private-fixture-replay" not in json.dumps(restarted.approval_transactions.list_approvals())

    restarted.approval_transactions.approve(approval_id)
    applied = restarted.approval_transactions.apply_approved({"approval_id": approval_id})
    assert applied["ok"] is True and applied["status"] == "applied"
    assert applied["result"]["persistedReadback"] is True
    assert applied["result"]["readback"] == {"text": "hello"}
    assert len(restarted_calls) == 1 and original_calls == []

    resumed = restarted.resume_runtime_task_after_approval(restored, applied)
    assert resumed is not None and resumed["plan"]["nextStep"] == "done"
    assert len(original_model.requests) == 2
    assert len(resumed_model.requests) == 1
    messages = resumed_model.requests[0]["messages"]
    assert messages[:len(saved["messages"])] == saved["messages"]
    write_receipts = [item for item in messages if item.get("tool_call_id") == "write"]
    assert len(write_receipts) == 1
    result = json.loads(write_receipts[0]["content"])
    assert result["status"] == "completed"
    assert result["actionId"] == restored["taskContext"]["requestedActionId"]
    assert any(item.get("actionId") == result["actionId"] and item["outcome"]["status"] == "ok"
               for item in result["observations"])

    snapshot = restarted.runtime_sessions.native_conversation("native-restart-success", binding=saved["binding"])
    assert not restarted.runtime_sessions._native_pending(snapshot)
    assert [item["tool_call_id"] for item in snapshot["messages"] if item.get("role") == "tool"] == [
        "phase", "write", "final"
    ]
    assert "private-fixture-replay" not in json.dumps(resumed)
    # Repeated delivery of the same terminal approval cannot execute or sample again.
    with pytest.raises(AgentGatewayError, match="already been settled") as duplicate:
        restarted.resume_runtime_task_after_approval(restored, applied)
    assert duplicate.value.status_code == 409
    assert len(restarted_calls) == 1 and len(resumed_model.requests) == 1
