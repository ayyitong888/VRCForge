from __future__ import annotations

import json
import hashlib
import ntpath
import threading
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.test_native_runtime_gateway import NativeModel, NativeTurn, call, setup_gateway
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
from agent_gateway import AgentGateway
from runtime_planner_service import PlannerModelResult, RuntimePlannerService
from agent_runtime_native_turn import NativeRuntimeTurn


def _write_gateway(tmp_path, replies, invoked):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    def handler(arguments):
        invoked.append(deepcopy(arguments))
        return {"ok": True, "status": "written"}

    gateway.register_tool(
        "fixture_write",
        "When to use: write the fixture. When NOT to use: when no write is requested.",
        "write/fixture",
        handler,
        write=True,
    )
    # The catalog entry makes the tool discoverable; the approval service's
    # write-handler registry is what creates a real pending approval.
    gateway.approval_transactions.register_write_handler(
        "fixture_write",
        "Write the fixture.",
        "medium",
        handler,
    )
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "approval"
    gateway.save_config(config)
    model = NativeModel(replies)
    planner = RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=model,
        turn=NativeTurn(),
    )
    gateway.bind_runtime_planner(planner)
    return gateway, model


def _run(gateway, tmp_path, **extra):
    return gateway.runtime_message({
        "message": "perform the fixture write",
        "sessionId": "native-continuation-session",
        "clientTurnId": "native-continuation-turn",
        "projectRoot": str(tmp_path),
        "maxAgenticTurns": 5,
        **extra,
    })


def _binding(tmp_path):
    return hashlib.sha256(
        ("fixture-provider-model-route\n" + ntpath.normcase(ntpath.normpath(str(tmp_path)))).encode()
    ).hexdigest()


def test_native_receipt_public_content_is_emitted_once_for_queued_calls(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    class Planner:
        @staticmethod
        def native_result_observation(item, *, project_context_active=False):
            return item

    turn = NativeRuntimeTurn(
        gateway.runtime_sessions,
        Planner(),
        "native-queued-content-session",
        "turn-queued-content",
        "fixture-provider-model-route",
        "inspect both files",
        [],
        None,
        None,
        None,
    )
    receipt = {
        "role": "assistant",
        "content": "I will inspect both files in order.",
        "tool_calls": [
            {"id": "read-one", "type": "function", "function": {"name": "read_one", "arguments": "{}"}},
            {"id": "read-two", "type": "function", "function": {"name": "read_two", "arguments": "{}"}},
        ],
    }
    turn.admit(receipt, tool_names=["read_one", "read_two"])

    first = turn.next_receipt()
    turn._loop_state.append({"tool": "read_one", "status": "executed", "outcome": {"status": "ok"}})
    turn.settle()
    second = turn.next_receipt()

    assert first["content"] == receipt["content"]
    assert second["content"] is None
    assert [call["id"] for call in first["tool_calls"] + second["tool_calls"]] == ["read-one", "read-two"]


def test_native_settles_approval_wrapper_when_inner_write_result_is_terminal_failure(tmp_path):
    """A blocked approved write must settle its native call with the real failure."""
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    binding = "fixture-provider-model-route"
    call_id = "instantiate-009"
    terminal_error = "A previous write did not finish cleanly."
    class Planner:
        @staticmethod
        def native_result_observation(item, *, project_context_active=False):
            return item

    turn = NativeRuntimeTurn(
        gateway.runtime_sessions,
        Planner(),
        "native-recovery-session",
        "turn-009",
        binding,
        "instantiate the prefab",
        [],
        None,
        None,
        None,
    )
    receipt = call(call_id, "vrcforge_instantiate_prefab", {"name": "Milltina_Oneesan"})
    turn.admit(receipt, tool_names=["vrcforge_instantiate_prefab"])
    turn.next_receipt()
    turn._loop_state.append({
        "tool": "vrcforge_instantiate_prefab",
        "status": "approval_pending",
        "result": {
            "ok": False,
            "status": "failed",
            "approval": {"status": "approved"},
            "error": terminal_error,
        },
        "outcome": {"status": "failed", "summary": terminal_error},
    })
    turn.settle({"nextStep": "needs_user_action"})
    snapshot = gateway.runtime_sessions.native_conversation(
        "native-recovery-session", binding=binding
    )
    assert snapshot is not None
    assert snapshot["binding"] == binding
    assert snapshot["turnId"] == "turn-009"
    assert gateway.runtime_sessions._native_pending(snapshot) == set()
    tool_messages = [m for m in snapshot["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == call_id
    observation = json.loads(tool_messages[0]["content"])["observations"][0]
    assert observation["outcome"]["status"] == "failed"
    assert observation["outcome"]["summary"] == terminal_error

    # Re-open the actual native history store.  The owner primitive must be
    # idempotent by the original call id and must not need a provider turn.
    restored_gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    restored = restored_gateway.runtime_sessions.native_conversation(
        "native-recovery-session", binding=binding
    )
    assert restored == snapshot
    with patch.object(restored_gateway, "runtime_message", side_effect=AssertionError("fresh turn forbidden")):
        with pytest.raises(ValueError, match="already settled"):
            restored_gateway.runtime_sessions.settle_native_call(
                "native-recovery-session",
                binding=binding,
                call_id=call_id,
                content=json.dumps({"status": "failed", "error": "duplicate"}),
            )
    assert restored_gateway.runtime_sessions.native_conversation(
        "native-recovery-session", binding=binding
    ) == restored


def test_native_approval_reject_keeps_call_id_and_never_runs_handler(tmp_path):
    invoked = []
    gateway, model = _write_gateway(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"value": "x"}),
        {"role": "assistant", "content": "rejected"},
    ], invoked)
    initial = _run(gateway, tmp_path)
    assert initial["write"]["status"] == "approval_pending"
    approval_id = initial["approval_id"]
    snapshot = gateway.runtime_sessions.native_conversation(
        "native-continuation-session", binding=_binding(tmp_path)
    )
    assert snapshot is not None
    assert gateway.runtime_sessions._native_pending(snapshot) == {"write-1"}
    assert any(message.get("role") == "assistant" and message.get("tool_calls", [{}])[0].get("id") == "write-1" for message in snapshot["messages"])
    gateway.approval_transactions.reject(approval_id)
    resumed = gateway.resume_runtime_task_after_approval(
        gateway._approvals[approval_id],
        {"status": "rejected", "taskCompletion": {"status": "failed", "outcome": {"status": "failed", "summary": "rejected"}}},
        rejected=True,
    )
    assert resumed is not None
    assert not invoked
    settled = gateway.runtime_sessions.native_conversation(
        "native-continuation-session", binding=_binding(tmp_path)
    )
    assert settled is not None
    settled_messages = [
        message for message in settled["messages"]
        if message.get("role") == "tool" and message.get("tool_call_id") == "write-1"
    ]
    assert len(settled_messages) == 1
    assert "rejected" in json.dumps(json.loads(settled_messages[0]["content"]))


def test_native_approval_pending_survives_gateway_restart_and_reject_resume_pairs_same_call(tmp_path):
    invoked = []
    gateway, _model = _write_gateway(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"value": "x"}),
    ], invoked)
    initial = _run(gateway, tmp_path)
    approval_id = initial["approval_id"]
    restarted, model = _write_gateway(tmp_path, [{"role": "assistant", "content": "rejected after restart"}], invoked)
    approval = restarted.approval_transactions.list_approvals()
    stored = next(item for item in approval if item["id"] == approval_id)
    assert stored["status"] == "pending"
    restarted.approval_transactions.reject(approval_id)
    resumed = restarted.resume_runtime_task_after_approval(
        stored,
        {"status": "rejected", "taskCompletion": {"status": "failed", "outcome": {"status": "failed", "summary": "rejected"}}},
        rejected=True,
    )
    assert resumed is not None
    assert not invoked
    binding = _binding(tmp_path)
    settled = restarted.runtime_sessions.native_conversation(
        "native-continuation-session", binding=binding
    )
    assert settled is not None
    settled_messages = [
        message for message in settled["messages"]
        if message.get("role") == "tool" and message.get("tool_call_id") == "write-1"
    ]
    assert len(settled_messages) == 1
    assert "rejected" in json.dumps(json.loads(settled_messages[0]["content"]))


def test_native_question_answer_continuation_keeps_pending_call_and_matches_answer(tmp_path):
    invoked = []
    completed = threading.Event()
    results = []
    gateway, model = _write_gateway(tmp_path, [call("question-1", "vrcforge_ask_user", {"question": "Continue?"}), {"role": "assistant", "content": "answered"}], invoked)
    gateway.register_tool(
        "vrcforge_ask_user",
        "When to use: ask a bounded user question. When NOT to use: do not use otherwise.",
        "plan/preview",
        gateway.create_runtime_question,
    )
    gateway._runtime_turn_completed = lambda payload: (results.append(payload), completed.set())
    first = _run(gateway, tmp_path)
    assert first["plan"]["nextStep"] == "needs_user_action"
    pending = gateway.runtime_sessions.native_conversation(
        "native-continuation-session", binding=_binding(tmp_path)
    )
    assert pending is not None
    assert gateway.runtime_sessions._native_pending(pending) == {"question-1"}
    question = gateway.questions.list(session_id="native-continuation-session", project_root=str(tmp_path))["questions"][0]
    response = gateway.questions.answer(question["questionId"], {
        "sessionId": "native-continuation-session", "projectRoot": str(tmp_path), "answer": "yes"
    })
    assert response["question"]["status"] == "answered"
    assert completed.wait(5)
    assert not invoked
    snapshot = gateway.runtime_sessions.native_conversation(
        "native-continuation-session", binding=_binding(tmp_path)
    )
    assert snapshot is not None
    settled_messages = [
        message for message in snapshot["messages"]
        if message.get("role") == "tool" and message.get("tool_call_id") == "question-1"
    ]
    assert len(settled_messages) == 1
    assert "yes" in json.dumps(json.loads(settled_messages[0]["content"]))
    assert results


def test_native_stop_cancels_only_matching_question_and_allows_same_session_new_turn(tmp_path):
    invoked = []
    gateway, model = _write_gateway(
        tmp_path,
        [
            call("question-a", "vrcforge_ask_user", {"question": "Stop A?"}),
            call("question-b", "vrcforge_ask_user", {"question": "Keep B?"}),
            {"role": "assistant", "content": "A continued after stop"},
        ],
        invoked,
    )
    gateway.register_tool(
        "vrcforge_ask_user",
        "When to use: ask a bounded user question. When NOT to use: do not use otherwise.",
        "plan/preview",
        gateway.create_runtime_question,
    )

    first = _run(gateway, tmp_path, clientTurnId="turn-a")
    assert first["plan"]["nextStep"] == "needs_user_action"
    question_a = gateway.questions.list(
        session_id="native-continuation-session", include_answered=True
    )["questions"][0]
    second = _run(
        gateway,
        tmp_path,
        sessionId="other-native-session",
        clientTurnId="turn-b",
    )
    assert second["plan"]["nextStep"] == "needs_user_action"
    question_b = gateway.questions.list(
        session_id="other-native-session", include_answered=True
    )["questions"][0]

    cancelled = gateway.request_runtime_cancel(
        {"sessionId": "native-continuation-session", "clientTurnId": "turn-a"}
    )
    assert cancelled["status"] == "cancel_requested"
    questions = gateway.questions.list(include_answered=True)["questions"]
    by_id = {question["questionId"]: question for question in questions}
    assert by_id[question_a["questionId"]]["status"] == "cancelled"
    assert by_id[question_b["questionId"]]["status"] == "pending"

    continued = _run(gateway, tmp_path, clientTurnId="turn-a-next")
    assert continued["plan"]["nextStep"] != "needs_user_action"
    assert not invoked
