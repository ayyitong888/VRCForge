"""Private native replay must not escape the legacy Agent approval boundary."""

import json

import pytest

from agent_gateway import AgentGatewayError
from agent_runtime_native_turn import NativeRuntimeTurn
from tests.test_native_runtime_continuations import _run, _write_gateway
from tests.test_native_runtime_gateway import call


def pending_native_approval(tmp_path):
    invoked = []
    gateway, _ = _write_gateway(tmp_path, [
        call("phase-private", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-private", "fixture_write", {"value": "x"}),
    ], invoked)
    gateway.register_tool(
        "vrcforge_apply_approved", "Apply a previously approved write operation.",
        "supervised-write", gateway.approval_transactions.apply_approved, write=True,
    )
    result = _run(gateway, tmp_path)
    assert result["write"]["status"] == "approval_pending"
    assert not invoked
    return gateway, result["approval_id"]


@pytest.mark.parametrize("decision", ["reject", "approve"])
def test_legacy_agent_terminal_approval_response_does_not_expose_native_snapshot(tmp_path, decision):
    gateway, approval_id = pending_native_approval(tmp_path)
    gateway.approval_transactions.reject(approval_id)

    # Both /api/agent/approvals/{id}/approve and /reject return this payload
    # directly. Unlike the App routes, they do not add a public projection.
    response = getattr(gateway.approval_transactions, decision)(approval_id)

    assert response["ok"] is False
    assert "private-fixture-replay" not in json.dumps(response)


def test_direct_apply_is_hidden_and_its_operation_resource_excludes_approval(tmp_path):
    gateway, approval_id = pending_native_approval(tmp_path)
    for dispatch in (gateway.call_tool, gateway.call_external_mcp_tool):
        with pytest.raises(AgentGatewayError) as error:
            dispatch("vrcforge_apply_approved", {"approval_id": approval_id})
        assert error.value.status_code == 404

    # Exercise publication with the real internal pending-approval result.
    # No handler is run because the proposal has not been approved.
    result = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    assert "private-fixture-replay" in json.dumps(result)
    receipt = gateway.publish_mcp_tool_result_resource(
        "vrcforge_apply_approved", {"approval_id": approval_id}, result,
        source_mode="internal_agent",
    )
    public_resource = gateway.read_mcp_resource(receipt["uri"])
    assert "private-fixture-replay" not in json.dumps(public_resource)


def test_stale_native_continuation_cannot_settle_a_later_turn_call(tmp_path):
    gateway, _ = _write_gateway(tmp_path, [], [])
    state = gateway.runtime_sessions
    state.begin_native_turn("session", binding="route", turn_id="A", message="First task")
    state.append_native_assistant(
        "session", binding="route", message=call("call-A", "fixture_write", {"value": "A"}),
    )
    old_snapshot = state.native_conversation("session", binding="route")
    state.settle_native_call(
        "session", binding="route", call_id="call-A", content='{"status":"completed"}',
    )
    state.begin_native_turn("session", binding="route", turn_id="B", message="Second task")
    state.append_native_assistant(
        "session", binding="route", message=call("call-B", "fixture_write", {"value": "B"}),
    )
    before = state.native_conversation("session", binding="route")
    assert state._native_pending(before) == {"call-B"}

    with pytest.raises(ValueError, match="earlier user turn"):
        NativeRuntimeTurn(
            state, gateway.runtime_planner, "session", "resume-A", "route", "First task", [],
            {"_nativeConversation": old_snapshot, "requestedActionId": "action-A"},
            {"status": "completed"}, {},
        )

    after = state.native_conversation("session", binding="route")
    assert state._native_pending(after) == {"call-B"}
    assert after == before


@pytest.mark.parametrize("new_binding", ["another-provider", ""])
def test_provider_change_records_terminal_action_without_replaying_private_history(tmp_path, new_binding):
    gateway, _ = _write_gateway(tmp_path, [], [])
    state = gateway.runtime_sessions
    state.begin_native_turn("session", binding="old-provider", turn_id="A", message="First task")
    state.append_native_assistant("session", binding="old-provider", message=call("write-A", "fixture_write", {"value": "A"}))
    saved = state.native_conversation("session", binding="old-provider")
    with pytest.raises(ValueError, match="settings changed"):
        NativeRuntimeTurn(
            state, gateway.runtime_planner, "session", "resume-A", new_binding, "First task", [],
            {"_nativeConversation": saved, "requestedActionId": "action-A"}, {"status": "completed"}, {},
        )
    settled = state.native_conversation("session", binding="old-provider")
    assert not state._native_pending(settled)
    assert settled["messages"][-1]["tool_call_id"] == "write-A"
    state.begin_native_turn("session", binding="new-provider", turn_id="B", message="Continue", initial_history=[])
    assert "private-fixture-replay" not in json.dumps(state.native_conversation("session", binding="new-provider"))


def test_duplicate_native_continuation_does_not_sample_or_replace_settled_call(tmp_path):
    gateway, _ = _write_gateway(tmp_path, [], [])
    state = gateway.runtime_sessions
    state.begin_native_turn("session", binding="route", turn_id="A", message="First task")
    state.append_native_assistant("session", binding="route", message=call("write-A", "fixture_write", {"value": "A"}))
    saved = state.native_conversation("session", binding="route")
    state.settle_native_call("session", binding="route", call_id="write-A", content='{"status":"completed"}')
    before = state.native_conversation("session", binding="route")
    with pytest.raises(ValueError, match="already been settled"):
        NativeRuntimeTurn(
            state, gateway.runtime_planner, "session", "resume-A", "route", "First task", [],
            {"_nativeConversation": saved, "requestedActionId": "action-A"}, {"status": "completed"}, {},
        )
    assert state.native_conversation("session", binding="route") == before
