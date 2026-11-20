from __future__ import annotations

import json
from agent_native_history_store import project_native_binding
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.test_native_runtime_gateway import NativeModel, NativeTurn, call, runtime_state, setup_gateway
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
from agent_gateway import AgentGateway
from runtime_planner_service import PlannerModelResult, RuntimePlannerService
from agent_runtime_native_turn import NativeRuntimeTurn
from agent_runtime_session_state import (
    NATIVE_RUNTIME_CONTEXT_PREFIX, NATIVE_RUNTIME_STATE_PREFIX, project_native_runtime_state_history,
)


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
    return project_native_binding(str(tmp_path))


def test_native_delegate_running_result_remains_pending_for_terminal_callback(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    binding = "fixture-provider-model-route"

    class Planner:
        @staticmethod
        def native_result_observation(item, *, project_context_active=False, retained_tool_definitions=None):
            return item

    turn = NativeRuntimeTurn(
        gateway.runtime_sessions,
        Planner(),
        "native-delegate-session",
        "turn-delegate",
        binding,
        "inspect the package",
        [],
        None,
        None,
        None,
    )
    turn.admit(call("delegate-running", "call_tool", {
        "tool": "vrcforge_delegate_subagent",
        "arguments": {"role": "outfit_package_inspection"},
    }), tool_names=["call_tool"])
    turn.next_receipt()
    turn._loop_state.append({
        "tool": "vrcforge_delegate_subagent",
        "status": "executed",
        "result": {"ok": True, "status": "running"},
        "outcome": {"status": "ok"},
    })

    turn.settle()

    snapshot = gateway.runtime_sessions.native_conversation(
        "native-delegate-session", binding=binding
    )
    assert snapshot is not None
    assert gateway.runtime_sessions._native_pending(snapshot) == {"delegate-running"}


@pytest.mark.parametrize("tool,result", [
    ("vrcforge_execute_unity_editor_menu", {
        "menuItemPath": "GameObject/Fixture/Setup", "selectionGlobalObjectIds": [],
        "triggered": True, "state": "invoked", "resultVerification": "pending",
        "mutationMayHaveStarted": True, "checkpointRecoveryRequired": False, "selectionRestored": False,
    }),
    ("vrcforge_setup_outfit", {"ok": True, "status": "running", "jobId": "a" * 32}),
])
def test_native_pending_dispatch_returns_receipt_before_post_tool_request(tmp_path, tool, result):
    from agent_tool_result_contract import normalize_agent_tool_result
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    class Planner:
        @staticmethod
        def native_result_observation(item, **_kwargs):
            return item
    turn = NativeRuntimeTurn(gateway.runtime_sessions, Planner(), "dispatch-session", "dispatch-turn",
        "binding", "Set up the fixture then inspect its result", [], None, None, None)
    turn.admit(call("setup-call", tool, {}), tool_names=[tool])
    turn.next_receipt()
    outcome = normalize_agent_tool_result(result, fallback_summary="Setup dispatched", write=True)
    assert outcome["status"] == "pending"
    turn._loop_state.append({"tool": tool, "status": "executed", "result": result, "outcome": outcome})

    turn.settle()

    snapshot = turn.snapshot()
    assert not gateway.runtime_sessions._native_pending(snapshot)
    tool_messages = [message for message in snapshot["messages"] if message.get("tool_call_id") == "setup-call"]
    assert len(tool_messages) == 1
    observation = json.loads(tool_messages[0]["content"])["observations"][0]
    assert observation["outcome"]["status"] == "pending"
    assert observation["result"] == result
    turn.persist_runtime_state({"role": "system", "content": "Current runtime state (data): " + json.dumps({
        "exposureLayer": "execution", "projectContextActive": True,
        "allowedRuntimeActions": ["reply"], "availableToolNames": ["inspect_fixture"],
    })})


def test_native_receipt_public_content_is_emitted_once_for_queued_calls(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    class Planner:
        @staticmethod
        def native_result_observation(item, *, project_context_active=False, retained_tool_definitions=None):
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
        def native_result_observation(item, *, project_context_active=False, retained_tool_definitions=None):
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
    receipt = call(call_id, "vrcforge_instantiate_prefab", {"name": "SampleAvatar_Alt"})
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


@pytest.mark.parametrize("reject", [False, True])
def test_native_approval_after_new_owner_message_keeps_exact_receipt(tmp_path, reject):
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
    from tests.test_native_runtime_gateway import run

    project = tmp_path / "UnityProject"
    proposal = call("older-write", "fixture_write", {"projectRoot": str(project), "value": "first"})
    proposal["tool_calls"].extend(call("older-unapproved", "fixture_write", {
        "projectRoot": str(project), "value": "must not replay",
    })["tool_calls"])
    correction = "A later correction: keep the current conversation and do not automatically run further writes."
    resume_started, resume_release = threading.Event(), threading.Event()

    def finish_resume(_request):
        resume_started.set()
        assert resume_release.wait(5)
        return {"role": "assistant", "content": "The earlier approval result was received."}

    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        proposal, {"role": "assistant", "content": "Correction acknowledged."},
        finish_resume,
    ])
    _set_mode(gateway, "approval")
    initial = run(gateway, tmp_path, projectRoot=str(project), message="Prepare the first operation.")
    approval_id = initial["approval_id"]
    binding = gateway._approvals[approval_id]["taskContext"]["_nativeConversation"]["binding"]
    later = run(
        gateway, tmp_path, projectRoot=str(project), clientTurnId="later-owner-input", message=correction,
        _humanUserMessageSource={
            "source": "app_user_message", "sessionId": initial["sessionId"],
            "clientTurnId": "later-owner-input", "projectRoot": str(project), "message": correction,
        },
    )
    assert later["plan"]["reply"] == "Correction acknowledged."
    assert gateway._approvals[approval_id]["status"] == "pending" and not invoked
    assert runtime_state(model.requests[-1])["pendingApprovals"] == [
        {"approvalId": approval_id, "tool": "fixture_write", "status": "awaiting_user_decision"},
    ]
    assert "ownedPending" not in gateway.runtime_observe("different-session", str(project))["approvalQueue"]
    old_results = {row["tool_call_id"]: json.loads(row["content"])
                   for row in model.requests[-1]["messages"] if row["role"] == "tool"}
    assert old_results["older-write"]["status"] == "approval_pending"
    assert old_results["older-unapproved"]["status"] == "not_executed"

    if reject:
        approval = gateway.approval_transactions.reject(approval_id)["approval"]
        execution = {}
        result = gateway.resume_runtime_task_after_approval(approval, execution, rejected=True)
    else:
        gateway.approval_transactions.approve(approval_id)
        execution = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
        approval = execution["approval"]
        assert execution["status"] == "applied"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(gateway.resume_runtime_task_after_approval, approval, execution)
            try:
                assert resume_started.wait(5)
                assert gateway.resume_runtime_task_after_approval(approval, execution) is None
            finally:
                resume_release.set()
            result = pending.result(timeout=5)
    assert result["resumedApprovalId"] == approval_id
    assert result["parentClientTurnId"] == initial["clientTurnId"]
    assert result["plan"]["nextStep"] == ("needs_user_action" if reject else "done")
    assert gateway.resume_runtime_task_after_approval(approval, execution, rejected=reject) is None
    assert [args["value"] for args in invoked] == ([] if reject else ["first"])
    latest = gateway.runtime_sessions.native_conversation(initial["sessionId"], binding=binding)
    messages = latest["messages"]
    human_correction = next(index for index, row in enumerate(messages) if row.get("content") == correction)
    receipt = next(index for index, row in enumerate(messages)
                   if "Earlier approval result" in str(row.get("content")))
    assert human_correction < receipt
    assert messages[receipt]["runtimeInputSource"] == "runtime"
    assert approval_id in messages[receipt]["content"] and "older-write" in messages[receipt]["content"]
    assert len(model.requests) == (2 if reject else 3)


@pytest.mark.parametrize("legacy_provider_switch", [False, True])
def test_native_approval_pending_survives_gateway_restart_and_reject_resume_pairs_same_call(
    tmp_path, monkeypatch, legacy_provider_switch,
):
    invoked = []
    gateway, _model = _write_gateway(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"value": "x"}),
    ], invoked)
    if legacy_provider_switch:
        # Recreate the storage key and approval freeze used by the previous release.
        monkeypatch.setattr(gateway, "_scoped_history_binding",
                            lambda session, project, context=None: gateway.runtime_planner.legacy_native_route(project)[0])
    initial = _run(gateway, tmp_path)
    approval_id = initial["approval_id"]
    restarted, model = _write_gateway(tmp_path, [{"role": "assistant", "content": "rejected after restart"}], invoked)
    if legacy_provider_switch:
        from contextlib import nullcontext
        from runtime_planner_service import PlannerTurnMetadata

        monkeypatch.setattr(restarted.runtime_planner._turn, "bind", lambda request: nullcontext(
            PlannerTurnMetadata(native_binding="new-provider-model-effort", provider_route="a" * 64)))
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


def test_old_question_history_keeps_answers_in_quoted_context_on_reopen():
    task = {"objective": "fixture", "priorAnswers": [{"answer": "prior answer"}]}
    stored = {"role": "system", "content": NATIVE_RUNTIME_STATE_PREFIX + json.dumps({
        "questionTask": task, "executionMode": "approval"})}
    projected = project_native_runtime_state_history([stored])
    assert [item["role"] for item in projected] == ["system", "user"]
    assert json.loads(projected[0]["content"].removeprefix(NATIVE_RUNTIME_STATE_PREFIX)) == {"executionMode": "approval"}
    assert json.loads(projected[1]["content"].removeprefix(NATIVE_RUNTIME_CONTEXT_PREFIX)) == {"questionTask": task}
    assert project_native_runtime_state_history(projected) == projected
    assert "prior answer" in stored["content"]


def test_model_request_restores_old_host_reminder_without_relabeling_identical_human_words(tmp_path):
    from agent_general_no_progress import LOOP_REMINDER

    gateway, model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Done"}] * 2)
    params = {"sessionId": "native-session", "projectRoot": str(tmp_path), "message": LOOP_REMINDER}
    gateway.runtime_message({**params, "clientTurnId": "first", "_humanUserMessageSource": {
        "source": "app_user_message", "sessionId": "native-session", "clientTurnId": "first",
        "projectRoot": str(tmp_path), "message": LOOP_REMINDER}})
    binding = gateway.runtime_sessions._native_conversations["native-session"]["binding"]
    gateway.runtime_sessions.append_native_user("native-session", binding=binding, message=LOOP_REMINDER, human=False, hidden=True)
    gateway.runtime_sessions.append_native_user("native-session", binding=binding, message="fixture feedback", human=False, hidden=True)
    gateway.runtime_message({**params, "message": "another task", "clientTurnId": "next"})
    messages = model.requests[-1]["messages"]
    assert [m["role"] for m in messages if m.get("content") == LOOP_REMINDER] == ["user", "system"]
    feedback = next(m for m in messages if "fixture feedback" in str(m.get("content")))
    assert '"source":"runtime"' in feedback["content"]


def test_question_handoff_recovers_earlier_scope_and_continues_after_prerequisite(tmp_path):
    session = "native-session"
    scope = {"sessionId": session, "projectRoot": str(tmp_path)}
    completed = threading.Event()
    results = []

    def next_action(request):
        assert "questionTask" not in runtime_state(request)
        context = [json.loads(item["content"][len(NATIVE_RUNTIME_CONTEXT_PREFIX):])
                   for item in request["messages"] if item["role"] == "user"
                   and isinstance(item.get("content"), str)
                   and item["content"].startswith(NATIVE_RUNTIME_CONTEXT_PREFIX)]
        task = next(item["questionTask"] for item in reversed(context) if "questionTask" in item)
        assert task["objective"] == "Continue configuring the project"
        assert task["priorAnswers"] == [
            {"question": "Which deliverables?", "answer": "Configure the avatar and all three outfits."}
        ]
        assert any("The dependency is installed" in message.get("content", "")
                   for message in request["messages"] if isinstance(message.get("content"), str))
        assert any("Runtime completion feedback" in message.get("content", "")
                   for message in request["messages"] if isinstance(message.get("content"), str))
        return call("next", "vrcforge_ask_user", {"question": "Which outfit starts enabled?"})

    gateway, model, reads = setup_gateway(tmp_path, [
        call("question", "vrcforge_ask_user", {"question": "Is the dependency installed?"}),
        call("prerequisite", "vrcforge_read_text_file", {"path": "a.txt"}),
        {"role": "assistant", "content": "The dependency is installed. The rest can be configured now."},
        next_action,
    ])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: for writes.",
                          "plan/preview", gateway.create_runtime_question)
    prior = gateway.questions.create({**scope, "question": "Which deliverables?"})["question"]
    gateway.questions.answer(prior["questionId"], {**scope, "answer": "Configure the avatar and all three outfits."})
    unrelated = gateway.questions.create({**scope, "sessionId": "other", "question": "Other?"})["question"]
    gateway.questions.answer(unrelated["questionId"], {**scope, "sessionId": "other", "answer": "No."})
    gateway._runtime_turn_completed = lambda payload: (results.append(payload), completed.set())
    first = gateway.runtime_message({**scope, "message": "Continue configuring the project", "clientTurnId": "first",
                                     "maxAgenticTurns": 6})
    assert first["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(**{"session_id": session, "project_root": str(tmp_path)})["questions"][0]
    gateway.questions.answer(question["questionId"], {**scope, "answer": "The dependency is installed. Continue."})
    assert completed.wait(5)
    assert len(model.requests) == 4 and len(reads) == 1
    assert results[-1]["plan"]["nextStep"] == "needs_user_action"
    assert gateway.questions.list(session_id=session)["questions"][0]["question"] == "Which outfit starts enabled?"


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
