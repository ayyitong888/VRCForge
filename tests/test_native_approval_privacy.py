from __future__ import annotations

import tempfile
from pathlib import Path

from agent_gateway import AgentGateway
from agent_task_loop import TASK_LOOP_SCHEMA, approval_task_context
from dashboard_server import public_approval_http_payload


def _seed() -> dict[str, object]:
    return {
        "schema": TASK_LOOP_SCHEMA,
        "objective": "continue after approval",
        "sessionId": "session-1",
        "requestedTool": "fixture_write",
        "requestedArguments": {"value": "ok"},
        "_nativeConversation": {
            "binding": {"provider": "fixture", "model": "test"},
            "turnId": "turn-1",
            "messages": [{"role": "assistant", "tool_calls": [{"id": "call-1"}]}],
        },
    }


def test_approval_context_deep_copies_private_native_snapshot() -> None:
    seed = _seed()
    context = approval_task_context(seed, tool="fixture_write", arguments={"value": "ok"})

    assert context is not None
    assert context["_nativeConversation"] == seed["_nativeConversation"]
    assert context["_nativeConversation"] is not seed["_nativeConversation"]
    seed["_nativeConversation"]["messages"][0]["tool_calls"][0]["id"] = "mutated"
    assert context["_nativeConversation"]["messages"][0]["tool_calls"][0]["id"] == "call-1"


def test_trusted_task_context_reads_by_approval_id_and_returns_copy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        gateway = AgentGateway(Path(directory) / "config.json", Path(directory) / "audit")
        native = _seed()["_nativeConversation"]
        gateway._approvals["approval-1"] = {
            "id": "approval-1",
            "status": "pending",
            "taskContext": {"schema": "vrcforge.task.approval.v1", "_nativeConversation": native},
        }

        context = gateway.approval_transactions.get_trusted_task_context("approval-1")

        assert context == gateway._approvals["approval-1"]["taskContext"]
        assert context is not gateway._approvals["approval-1"]["taskContext"]
        context["_nativeConversation"]["messages"][0]["tool_calls"][0]["id"] = "changed"
        assert native["messages"][0]["tool_calls"][0]["id"] == "call-1"
        assert gateway.approval_transactions.get_trusted_task_context("missing") is None


def test_public_approval_projection_removes_only_private_native_snapshot() -> None:
    payload = {
        "ok": True,
        "approval": {"id": "approval-1", "taskContext": {"_nativeConversation": {"messages": []}}},
        "execution": {"arguments": {"value": 1, "metadata": {"keep": True}}},
    }

    projected = public_approval_http_payload(payload)

    assert "_nativeConversation" not in projected["approval"]["taskContext"]
    assert projected["execution"]["arguments"] == payload["execution"]["arguments"]
    assert payload["approval"]["taskContext"]["_nativeConversation"]
