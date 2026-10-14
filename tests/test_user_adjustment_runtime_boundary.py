from __future__ import annotations

import json
import pytest
from agent_gateway import AgentGateway, AgentWriteHandler
from tests.test_dashboard_server import bind_test_runtime_planner


TOOL = "vrcforge_user_adjustment_handoff"


def receipt(root, **changes):
    data = {
        "schema": "vrcforge.user_adjustment_handoff.v1",
        "operation": "user_adjustment_prepare", "ok": True,
        "status": "awaiting_user", "preview": False, "verified": True,
        "mutationStarted": True, "commitState": "committed", "changed": True,
        "handoffId": "a" * 32, "projectPath": str(root),
        "readback": {"avatarGlobalObjectId": "avatar", "targetGlobalObjectId": "target",
                     "proxyGlobalObjectId": "proxy", "sceneGuid": "b" * 32, "scenePath": "Assets/test.unity"},
        **changes,
    }
    return {"structuredContent": {"success": True, "message": "Drag the proxy now.", "data": data}}


def scripted_gateway(tmp_path, monkeypatch, result=None):
    (tmp_path / "Assets").mkdir(exist_ok=True)
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    calls = []
    def respond(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return {"text": json.dumps({"action": "skill", "skill_tool": TOOL,
                "skill_params": {"mode": "transform", "projectPath": str(tmp_path)}, "summary": "prepare"})}
        return {"text": json.dumps({"action": "reply", "reply": "continued unexpectedly",
                                   "completion_claim": {"satisfied": False}})}
    bind_test_runtime_planner(gateway, respond)
    gateway.register_tool(TOOL, "when-to-use: test handoff. when-NOT-to-use: otherwise.",
                          "plan/preview", lambda p: result or receipt(tmp_path))
    return gateway, calls


def test_prepare_stops_before_second_model_request_and_parks(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    result = gateway.runtime_message({"message": "prepare test", "sessionId": "s",
                                      "clientTurnId": "c", "projectRoot": str(tmp_path)})
    assert len(calls) == 1
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert result["steps"][0]["outcome"]["status"] == "ok"


@pytest.mark.parametrize("changes", [{"preview": True}, {"ok": False}, {"verified": False}])
def test_preview_and_failed_receipts_do_not_create_boundary(tmp_path, monkeypatch, changes):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch, receipt(tmp_path, **changes))
    gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    assert gateway._runtime_session_state.user_adjustment("s", str(tmp_path)) is None


def test_handoff_cancels_only_owned_children_and_shell(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    cancelled = []
    owners = []
    def tasks():
        context = gateway._runtime_session_state.stream_context()
        return [{"id": "owned", "status": "running", "sessionId": "s", "parentTurnId": context["turnId"], "parentClientTurnId": "c"},
                {"id": "foreign", "status": "running", "sessionId": "other", "parentTurnId": context["turnId"], "parentClientTurnId": "c"},
                {"id": "old", "status": "running", "sessionId": "s", "parentTurnId": "old", "parentClientTurnId": "old"}]
    gateway.bind_runtime_subagent_control(list_tasks=tasks, cancel_task=lambda task: cancelled.append(task))
    monkeypatch.setattr(type(gateway.shell), "cancel_owner", lambda self, owner: owners.append(owner) or [])
    result = gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    assert cancelled == ["owned"]
    assert owners == [gateway._runtime_shell_owner(result["turnId"], "c", "s")]


def test_missing_fresh_reader_blocks_provider_and_other_scope_is_unaffected(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    result = gateway.runtime_message({"message": "finished dragging", "sessionId": "s", "clientTurnId": "d", "projectRoot": str(tmp_path)})
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert len(calls) == 1
    assert gateway._runtime_session_state.user_adjustment("other", str(tmp_path)) is None
    assert gateway._runtime_session_state.user_adjustment("s", str(tmp_path / "other")) is None


def test_finalize_receipt_without_handoff_id_closes_only_exact_bound_handoff(tmp_path, monkeypatch):
    gateway, _ = scripted_gateway(tmp_path, monkeypatch)
    gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    completed = receipt(tmp_path, operation="user_adjustment_finalize", status="finalized")
    completed["structuredContent"]["data"].pop("handoffId")
    for identity, closed in [("wrong", False), ("a" * 32, True)]:
        assert gateway._accept_user_adjustment_receipt(completed, {"arguments": {"handoffId": identity}}, session_id="s", project_root=str(tmp_path), turn_id="finish", client_turn_id="finish") is None
        assert gateway._runtime_session_state.user_adjustment("s", str(tmp_path))["closed"] is closed


def test_next_user_turn_reads_exact_handoff_before_provider(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    reads = []
    def preview(args, unused):
        assert len(calls) == 1
        assert args["arguments"]["handoffId"] == "a" * 32
        assert args["arguments"]["targetGlobalObjectId"] == "target"
        assert args["arguments"]["action"] == "finalize"
        reads.append(args)
        return args, {"schema": "vrcforge.user_adjustment_handoff_approval.v1", "operation": "user_adjustment_finalize",
                      "requiresExplicitUserApproval": True, "previewDigest": "c" * 64,
                      "projectPath": str(tmp_path), "target": {"handoffId": "a" * 32,
                      "avatarGlobalObjectId": "avatar", "targetGlobalObjectId": "target", "proxyGlobalObjectId": "proxy",
                      "sceneGuid": "b" * 32, "scenePath": "Assets/test.unity"}}
    gateway._write_handlers[TOOL] = AgentWriteHandler(TOOL, "handoff", "high", lambda _: None, request_preparer=preview)
    gateway.runtime_message({"message": "finished dragging", "sessionId": "s", "clientTurnId": "d", "projectRoot": str(tmp_path)})
    assert len(reads) == 1
    assert len(calls) == 2


def test_automatic_continuation_cannot_revive_handoff(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    result = gateway._runtime_message_impl({"message": "late child completion", "sessionId": "s", "clientTurnId": "late", "projectRoot": str(tmp_path)}, task_continuation={"source": "sub_agent"})
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["user_adjustment_finalize", "user_adjustment_abort"])
@pytest.mark.parametrize("closed", [False, True])
def test_foreign_handoff_completion_cannot_bypass_runtime_barrier(tmp_path, monkeypatch, operation, closed):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    stored = gateway._runtime_session_state.user_adjustment("s", str(tmp_path))
    gateway._runtime_session_state.set_user_adjustment("s", str(tmp_path), {**stored, "closed": closed})
    completed = receipt(tmp_path, operation=operation, status="finalized", handoffId="f" * 32)
    result = gateway._runtime_message_impl(
        {"message": "late completion", "sessionId": "s", "clientTurnId": "late", "projectRoot": str(tmp_path)},
        task_continuation={"source": "approval_finished", "execution": completed,
                           "context": {"clientTurnId": "c", "requestedArguments": {"arguments": {"handoffId": "f" * 32}}}},
    )
    assert gateway._runtime_session_state.user_adjustment("s", str(tmp_path))["closed"] is closed
    assert len(calls) == 1
    assert result["plan"]["nextStep"] == "await_user_instruction"


@pytest.mark.parametrize("failure", ["lookup", "cancel"])
def test_failed_owned_task_stop_must_not_invite_manual_adjustment(tmp_path, monkeypatch, failure):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    def tasks():
        if failure == "lookup":
            raise RuntimeError("task lookup unavailable")
        context = gateway._runtime_session_state.stream_context()
        return [{"id": "owned", "status": "running", "sessionId": "s",
                 "parentTurnId": context["turnId"], "parentClientTurnId": "c"}]
    def cancel_task(task):
        raise RuntimeError("task cancellation unavailable")
    gateway.bind_runtime_subagent_control(list_tasks=tasks, cancel_task=cancel_task)
    result = gateway.runtime_message({"message": "prepare test", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    assert len(calls) == 1
    assert result["steps"][0]["outcome"]["status"] == "ok"
    assert gateway._runtime_session_state.user_adjustment("s", str(tmp_path))["closed"] is False
    assert result["plan"]["handoffCancellation"]["subAgentCancelPartialFailure"] is True
    assert result["plan"]["completionGate"]["reason"] == "user_adjustment_stop_unconfirmed"
    assert "Finish the manual adjustment" not in result["plan"]["reply"]


def test_stop_retry_uses_original_owner_and_blocks_preview_until_recovered(tmp_path, monkeypatch):
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    attempts = []
    recovered = False
    def cancel(*, session_id, turn_id, client_turn_id, reason):
        attempts.append({"sessionId": session_id, "turnId": turn_id, "clientTurnId": client_turn_id, "reason": reason})
        return {"subAgentCancelPartialFailure": not recovered}
    monkeypatch.setattr(gateway, "_cancel_runtime_owned_work", cancel)
    gateway.runtime_message({"message": "prepare", "sessionId": "s", "clientTurnId": "c", "projectRoot": str(tmp_path)})
    reads = []
    def preview(args, unused):
        reads.append(args)
        stored = gateway._runtime_session_state.user_adjustment("s", str(tmp_path))
        return args, {"schema": "vrcforge.user_adjustment_handoff_approval.v1", "operation": "user_adjustment_finalize",
                      "requiresExplicitUserApproval": True, "previewDigest": "c" * 64,
                      "projectPath": str(tmp_path), "target": {**stored["receipt"]["readback"], "handoffId": "a" * 32}}
    gateway._write_handlers[TOOL] = AgentWriteHandler(TOOL, "handoff", "high", lambda _: None, request_preparer=preview)
    blocked = gateway.runtime_message({"message": "try again", "sessionId": "s", "clientTurnId": "d", "projectRoot": str(tmp_path)})
    assert blocked["plan"]["completionGate"]["reason"] == "user_adjustment_stop_unconfirmed"
    assert not reads and len(calls) == 1
    recovered = True
    gateway.runtime_message({"message": "try again", "sessionId": "s", "clientTurnId": "e", "projectRoot": str(tmp_path)})
    assert len(reads) == 1 and len(calls) == 2
    assert len(attempts) == 3
    assert {(p["turnId"], p["clientTurnId"]) for p in attempts} == {(attempts[0]["turnId"], "c")}


def test_production_approval_resume_parks_prepared_handoff_without_provider(tmp_path, monkeypatch):
    from agent_task_loop import AgentTaskLoop, approval_task_context, approval_completion
    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    arguments = {"projectPath": str(tmp_path), "arguments": {"mode": "transform"}}
    loop = AgentTaskLoop("prepare adjustment", session_id="s", turn_id="prepare-turn", client_turn_id="c", project_root=str(tmp_path))
    context = approval_task_context(loop.approval_seed(requested_tool=TOOL, requested_arguments=arguments), tool=TOOL, arguments=arguments)
    raw = receipt(tmp_path)
    completion = approval_completion(context, raw_result=raw, outcome={"status": "ok", "summary": "prepared", "verification": {"state": "verified", "checks": []}})
    result = gateway.resume_runtime_task_after_approval(
        {"id": "prepare-approval", "taskContext": context, "arguments": arguments},
        {"status": "applied", "result": raw, "taskCompletion": completion},
    )
    assert result is not None
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert not calls


@pytest.mark.parametrize("stop_failure", [None, "lookup", "cancel"])
def test_approval_handoff_stops_original_resources_without_cancelling_other_turns(tmp_path, monkeypatch, stop_failure):
    from agent_gateway import AgentGatewayError
    from agent_task_loop import AgentTaskLoop, approval_task_context, approval_completion

    gateway, calls = scripted_gateway(tmp_path, monkeypatch)
    cancelled = []
    shell_owners = []
    recovered = stop_failure is None
    original_owner = gateway._runtime_shell_owner("prepare-turn", "c", "s")
    tasks = [
        {"id": "owned", "status": "running", "sessionId": "s", "parentTurnId": "prepare-turn", "parentClientTurnId": "c"},
        {"id": "other-turn", "status": "running", "sessionId": "s", "parentTurnId": "unrelated", "parentClientTurnId": "other"},
        {"id": "foreign-session", "status": "running", "sessionId": "foreign", "parentTurnId": "prepare-turn", "parentClientTurnId": "c"},
        {"id": "foreign-client", "status": "running", "sessionId": "s", "parentTurnId": "prepare-turn", "parentClientTurnId": "foreign"},
    ]

    def list_tasks():
        if stop_failure == "lookup" and not recovered:
            raise RuntimeError("task lookup unavailable")
        return tasks

    def cancel_task(task):
        cancelled.append(task)
        if stop_failure == "cancel" and not recovered:
            return {"ok": False}
        return {"ok": True}

    gateway.bind_runtime_subagent_control(list_tasks=list_tasks, cancel_task=cancel_task)
    monkeypatch.setattr(type(gateway.shell), "cancel_owner", lambda self, owner: shell_owners.append(owner) or [])
    state = gateway._runtime_session_state
    assert state.begin_turn(session_id="s", turn_id="unrelated", client_turn_id="other")
    # The public endpoint must not gain a bypass for stale/partially matching IDs.
    with pytest.raises(AgentGatewayError) as rejected:
        gateway.request_runtime_cancel({"sessionId": "s", "turnId": "prepare-turn", "clientTurnId": "c"})
    assert rejected.value.status_code == 409
    assert not cancelled and not shell_owners

    arguments = {"projectPath": str(tmp_path), "arguments": {"mode": "transform"}}
    loop = AgentTaskLoop("prepare adjustment", session_id="s", turn_id="prepare-turn", client_turn_id="c", project_root=str(tmp_path))
    context = approval_task_context(loop.approval_seed(requested_tool=TOOL, requested_arguments=arguments), tool=TOOL, arguments=arguments)
    raw = receipt(tmp_path)
    completion = approval_completion(context, raw_result=raw, outcome={"status": "ok", "summary": "prepared", "verification": {"state": "verified", "checks": []}})
    result = gateway.resume_runtime_task_after_approval(
        {"id": "prepare-approval", "taskContext": context, "arguments": arguments},
        {"status": "applied", "result": raw, "taskCompletion": completion},
    )
    assert not calls
    assert result["plan"]["nextStep"] == "await_user_instruction"
    assert shell_owners == [original_owner]
    assert cancelled == ([] if stop_failure == "lookup" else ["owned"])
    handoff = state.user_adjustment("s", str(tmp_path))
    assert (handoff["turnId"], handoff["clientTurnId"]) == ("prepare-turn", "c")
    assert handoff["cancellation"]["subAgentCancelPartialFailure"] is bool(stop_failure)
    assert state.owns_turn(session_id="s", turn_id="unrelated", client_turn_id="other")
    assert not state.consume_cancel_request(session_id="s", turn_id="unrelated", client_turn_id="other")
    if stop_failure:
        assert result["plan"]["completionGate"]["reason"] == "user_adjustment_stop_unconfirmed"
        assert "Finish the manual adjustment" not in result["plan"]["reply"]
        recovered = True
        # Retry on a new user turn must still stop the original owner's resources.
        gateway.runtime_message({"message": "retry stop", "sessionId": "s", "clientTurnId": "retry", "projectRoot": str(tmp_path)})
        assert not calls  # No fresh-read handler: the provider remains blocked.
        assert shell_owners == [original_owner, original_owner]
        assert cancelled == (["owned"] if stop_failure == "lookup" else ["owned", "owned"])
        assert state.user_adjustment("s", str(tmp_path))["cancellation"]["subAgentCancelPartialFailure"] is False
        assert not state.consume_cancel_request(session_id="s", turn_id="unrelated", client_turn_id="other")
