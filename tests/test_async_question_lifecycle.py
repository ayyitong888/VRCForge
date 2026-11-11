from __future__ import annotations

import json
import threading

import pytest

from agent_gateway import AgentGateway
from tests.test_dashboard_server import bind_test_runtime_planner
from tests.test_native_runtime_continuations import _write_gateway, _run, _binding
from tests.test_native_runtime_gateway import call


def _question(gateway, tmp_path, **values):
    return gateway.questions.create({
        "question": "Which outfit should be fitted next?", "blocking": False,
        "sessionId": "async-session", "projectRoot": str(tmp_path),
        "_runtimeTaskSeed": {"sessionId": "async-session", "clientTurnId": "origin-cid",
                             "turnId": "origin-turn", "projectRoot": str(tmp_path), "objective": "Fit the wardrobe"},
        **values,
    })


def test_async_question_does_not_end_independent_runtime_work(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    reads = []
    gateway.register_tool("vrcforge_fixture_read", "When to use: read. When NOT to use: write.", "read/debug", lambda _p: reads.append(True) or {"ok": True, "summary": "independent evidence"})
    replies = iter([
        {"action": "skill", "skill_tool": "vrcforge_ask_user", "skill_params": {"question": "Which outfit next?", "blocking": False}},
        {"action": "skill", "skill_tool": "vrcforge_fixture_read", "skill_params": {}},
        {"action": "reply", "reply": "Independent inspection is complete; awaiting the outfit choice.", "completion_claim": {"satisfied": False}},
    ])
    bind_test_runtime_planner(gateway, lambda _p: {"text": json.dumps(next(replies))})
    result = gateway.runtime_message({"message": "Ask about the next outfit and inspect independently while waiting.", "sessionId": "async-session", "clientTurnId": "origin-cid", "projectRoot": str(tmp_path)})
    assert reads == [True]
    assert result["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id="async-session")["questions"][0]
    assert question["blocking"] is False
    assert question["originClientTurnId"] == "origin-cid"
    assert question["originTurnId"]


def test_returned_blocked_question_reply_is_recovered_from_durable_ledger(tmp_path):
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.register_tool(
        "vrcforge_ask_user",
        "When to use: ask. When NOT to use: otherwise.",
        "plan/preview",
        gateway.create_runtime_question,
    )
    replies = iter([
        {"text": json.dumps({
            "action": "skill",
            "skill_tool": "vrcforge_ask_user",
            "skill_params": {"question": "Which outfit next?", "blocking": False},
        })},
        {"text": json.dumps({
            "action": "reply",
            "reply": "The question is recorded; the task is waiting for your choice.",
            "completion_claim": {"satisfied": False},
        })},
    ])
    bind_test_runtime_planner(gateway, lambda _prompt: next(replies))
    original = gateway.runtime_message({
        "message": "Ask which outfit should be selected next.",
        "sessionId": "durable-question-session",
        "clientTurnId": "durable-question-cid",
        "projectRoot": str(tmp_path),
    })
    assert original["plan"]["nextStep"] == "needs_user_action"
    assert gateway.runtime_runs.list_runs(
        session_id="durable-question-session", client_turn_id="durable-question-cid"
    )["runs"][0]["status"] == "blocked"

    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    recovered = restarted.get_runtime_session(
        "durable-question-session", client_turn_id="durable-question-cid"
    )
    assert recovered["status"] == "completed"
    response = recovered["response"]
    assert response["ok"] is True
    assert response["sessionId"] == "durable-question-session"
    assert response["session_id"] == "durable-question-session"
    assert response["clientTurnId"] == "durable-question-cid"
    assert response["turnId"] == original["turnId"]
    assert response["turn_id"] == original["turnId"]
    assert response["observe"] == {}
    assert response["plan"]["shellNeeded"] is False
    assert response["plan"]["nextStep"] == "needs_user_action"
    if "taskCompletion" in original["plan"]:
        assert response["plan"]["taskCompletion"]["status"] == original["plan"]["taskCompletion"]["status"]
    else:
        assert response["plan"]["taskCompletion"]["status"] == ""
    assert response["plan"]["reply"] == original["plan"]["reply"]
    assert response["continuationSource"] == "foreground_completed"
    assert "steps" not in response
    question = restarted.questions.list(session_id="durable-question-session")["questions"][0]
    assert question["blocking"] is False
    assert question["originClientTurnId"] == "durable-question-cid"

    historical = {
        "sessionId": "historical-blocked-session",
        "clientTurnId": "historical-blocked-cid",
        "turnId": "historical-blocked-turn",
    }
    restarted.runtime_runs.append({**historical, "event": "runtime_turn_started", "status": "running"})
    restarted.runtime_runs.append({
        **historical,
        "event": "runtime_turn_completed",
        "status": "blocked",
        "planSummary": "summary is not a recoverable reply",
        "timeline": [{"kind": "assistant", "payload": {"summary": "partial timeline"}}],
    })
    historical_recovery = restarted.get_runtime_session(
        "historical-blocked-session", client_turn_id="historical-blocked-cid"
    )
    assert historical_recovery["status"] == "missing"
    assert "response" not in historical_recovery


def test_active_answer_steers_exact_turn_without_a_second_provider(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="origin-cid", turn_id="origin-turn")
    question = _question(gateway, tmp_path)["question"]
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda *_a: (_ for _ in ()).throw(AssertionError("must not dispatch a second provider")))
    response = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    continuation = response["runtimeContinuation"]
    assert continuation["mode"] == "steer"
    assert continuation["clientTurnId"] == "origin-cid"
    queued = gateway.runtime_sessions.drain_steer(session_id="async-session", client_turn_id="origin-cid")
    assert len(queued) == 1 and "Example Outfit" in queued[0]["message"]
    saved = gateway.questions.list(session_id="async-session", include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "queued", "accepted hot mailbox is not durable consumption"
    gateway._settle_async_question_steers([queued[0]["inputId"]], session_id="async-session", client_turn_id="origin-cid")
    saved = gateway.questions.list(session_id="async-session", include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "delivered"


def test_async_request_cannot_disable_visual_or_goal_gates(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    for extra in ({"kind": "visual_checkpoint"}, {"goalDeliveryId": "goal-delivery"}, {"blocking": True}):
        created = _question(gateway, tmp_path, **extra)
        assert created["question"]["blocking"] is True
        assert created["outcome"]["status"] == "needs_user_action"


def test_async_resume_claim_excludes_other_session_writers_through_finalization(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    state = gateway.runtime_sessions
    assert state.begin_turn(session_id="s", client_turn_id="first", turn_id="t1")
    assert not state.begin_turn(session_id="s", client_turn_id="answer", turn_id="t2", exclusive=True)
    state.finish_turn(session_id="s", client_turn_id="first", turn_id="t1")
    assert not state.begin_turn(session_id="s", client_turn_id="answer", turn_id="t2", exclusive=True)
    state.record_final_response(session_id="s", client_turn_id="first", status="completed", response={})
    assert state.begin_turn(session_id="s", client_turn_id="answer", turn_id="t2", exclusive=True)
    assert not state.begin_turn(session_id="s", client_turn_id="other", turn_id="t3")
    state.finish_turn(session_id="s", client_turn_id="answer", turn_id="t2")
    assert not state.begin_turn(session_id="s", client_turn_id="other", turn_id="t3")
    state.record_final_response(session_id="s", client_turn_id="answer", status="completed", response={})
    assert state.begin_turn(session_id="s", client_turn_id="other", turn_id="t3")


def test_answer_after_async_round_finishes_starts_one_new_turn(tmp_path):
    gateway, model = _write_gateway(tmp_path, [
        call("async-ask", "vrcforge_ask_user", {"question": "Which outfit next?", "blocking": False}),
        call("independent-read", "vrcforge_fixture_read", {}),
        {"role": "assistant", "content": "Independent work finished; waiting for the choice."},
        {"role": "assistant", "content": "I'll use Example Outfit."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    gateway.register_tool("vrcforge_fixture_read", "When to use: read. When NOT to use: write.", "read/debug", lambda _p: {"ok": True, "summary": "independent evidence retained"})
    first = _run(gateway, tmp_path)
    assert first["plan"]["nextStep"] == "needs_user_action"
    snapshot = gateway.runtime_sessions.native_conversation("native-continuation-session", binding=_binding(tmp_path))
    assert gateway.runtime_sessions._native_pending(snapshot) == set()
    question = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    latest_seed = gateway.questions._project(include_answered=True)[question["questionId"]]["runtimeTaskSeed"]
    independent_actions = [a for a in latest_seed["actions"] if a["tool"] == "vrcforge_fixture_read"]
    assert len(independent_actions) == 1 and independent_actions[0]["status"] == "completed"
    completed = threading.Event()
    gateway._runtime_turn_completed = lambda _result: completed.set()
    answer = gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert answer["runtimeContinuation"]["status"] == "queued"
    assert answer["question"]["answer"] == "Example Outfit"
    assert completed.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert gateway.list_runtime_followups(session_id="native-continuation-session") == []
    assert len(model.requests) == 4
    assert {"role": "user", "content": "Example Outfit"} in model.requests[-1]["messages"]
    [new_run] = gateway.runtime_runs.list_runs(session_id="native-continuation-session",
        client_turn_id=answer["runtimeContinuation"]["clientTurnId"])["runs"]
    assert new_run["turnId"] != first["turnId"]


def test_active_answer_steers_240_character_origin_without_truncation(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    origin = "turn-" + "x" * 235
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id=origin, turn_id="origin-turn")
    question = _question(gateway, tmp_path, _runtimeTaskSeed={"sessionId": "async-session", "clientTurnId": origin, "turnId": "origin-turn", "projectRoot": str(tmp_path)})["question"]
    result = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert result["runtimeContinuation"]["mode"] == "steer"
    assert result["runtimeContinuation"]["clientTurnId"] == origin
    assert len(gateway.runtime_sessions.drain_steer(session_id="async-session", client_turn_id=origin)) == 1


def test_saved_unclaimed_answer_is_sent_as_user_text_after_restart(tmp_path):
    session_id = "native-continuation-session"
    gateway, model = _write_gateway(tmp_path, [
        call("async-ask", "vrcforge_ask_user", {"question": "Which outfit next?", "blocking": False}),
        {"role": "assistant", "content": "Waiting for your choice."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    _run(gateway, tmp_path)
    [question] = gateway.questions.list(session_id=session_id)["questions"]
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    reply = gateway.questions.answer(question["questionId"], {"sessionId": session_id, "answer": "Example Outfit"})
    assert reply["question"]["runtimeContinuationStatus"] == "queued"
    assert len(model.requests) == 2

    restarted, restarted_model = _write_gateway(tmp_path, [{"role": "assistant", "content": "I'll use Example Outfit."}], [])
    completed = threading.Event()
    restarted._runtime_turn_completed = lambda _result: completed.set()
    assert restarted.questions.reconcile_runtime_continuations() == {"queued": 1, "interrupted": 0}
    assert completed.wait(5)
    assert restarted.shutdown_runtime_continuations(5)["ok"]
    assert len(restarted_model.requests) == 1
    assert {"role": "user", "content": "Example Outfit"} in restarted_model.requests[0]["messages"]
    assert restarted.questions.reconcile_runtime_continuations()["queued"] == 0


def test_real_native_answer_during_independent_read_has_one_execution_owner(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    gateway, model = _write_gateway(tmp_path, [
        call("async-ask", "vrcforge_ask_user", {"question": "Which outfit next?", "blocking": False}),
        call("independent-read", "vrcforge_fixture_read", {}),
        {"role": "assistant", "content": "Selected Example Outfit after the independent inspection."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    def read(_params):
        entered.set()
        assert release.wait(5)
        return {"ok": True, "summary": "independent inspection"}
    gateway.register_tool("vrcforge_fixture_read", "When to use: read. When NOT to use: write.", "read/debug", read)
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda *_a: (_ for _ in ()).throw(AssertionError("second execution owner")))
    results = []
    origin = "turn-" + "x" * 235
    runner = threading.Thread(target=lambda: results.append(_run(gateway, tmp_path, clientTurnId=origin)))
    runner.start()
    assert entered.wait(5)
    question = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    answer = gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert answer["runtimeContinuation"]["mode"] == "steer"
    assert answer["runtimeContinuation"]["clientTurnId"] == origin
    release.set()
    runner.join(5)
    assert not runner.is_alive() and results
    assert len(model.requests) == 3
    assert any(m.get("role") == "user" and "Example Outfit" in m.get("content", "") for m in model.requests[-1]["messages"])
    saved = gateway.questions.list(session_id="native-continuation-session", include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "delivered"
    assert results[0]["plan"]["nextStep"] != "needs_user_action", "answered questions must not be replayed as a stale completion blocker"


@pytest.mark.parametrize("terminal_visible", [True, False])
def test_answer_after_last_native_model_boundary_starts_a_new_turn(tmp_path, monkeypatch, terminal_visible):
    from agent_runtime_session_state import AgentRuntimeSessionState
    entered, release = threading.Event(), threading.Event()
    gateway, model = _write_gateway(tmp_path, [
        call("async-ask", "vrcforge_ask_user", {"question": "Which outfit next?", "blocking": False}),
        {"role": "assistant", "content": "Waiting for your choice."},
        {"role": "assistant", "content": "I'll use Example Outfit."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    original_finish = AgentRuntimeSessionState.finish_turn
    def final_boundary(state, **identity):
        if state is gateway.runtime_sessions:
            entered.set()
            assert release.wait(5)
        return original_finish(state, **identity)
    monkeypatch.setattr(AgentRuntimeSessionState, "finish_turn", final_boundary)
    if not terminal_visible:
        ledger_type = type(gateway._runtime_run_ledger)
        read_terminal = ledger_type.read_terminal_continuation
        monkeypatch.setattr(ledger_type, "read_terminal_continuation",
            lambda ledger, **identity: read_terminal(ledger, **identity) if release.is_set() else None)
    results = []
    origin = "turn-" + "z" * 235
    runner = threading.Thread(target=lambda: results.append(_run(gateway, tmp_path, clientTurnId=origin)))
    runner.start()
    assert entered.wait(5)
    question = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    completed = threading.Event()
    gateway._runtime_turn_completed = lambda _result: completed.set()
    try:
        answer = gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
        continuation = answer["runtimeContinuation"]
        assert continuation["status"] == ("queued" if terminal_visible else "steered")
    finally:
        release.set()
        runner.join(5)
    assert not runner.is_alive() and results
    assert completed.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    followups = gateway.list_runtime_followups(session_id="native-continuation-session", include_terminal=False)
    assert followups == []
    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    saved = restarted.questions.list(session_id="native-continuation-session", include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "delivered"
    saved_client_turn_id = saved["runtimeContinuationClientTurnId"]
    assert len(saved_client_turn_id) <= 240
    assert saved_client_turn_id.endswith(":question:" + question["questionId"])
    assert saved["answer"] == "Example Outfit"
    assert len(model.requests) == 3
    assert {"role": "user", "content": "Example Outfit"} in model.requests[-1]["messages"]


def test_answer_winning_before_seed_refresh_keeps_queued_handoff(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="origin-cid", turn_id="origin-turn")
    question = _question(gateway, tmp_path)["question"]
    gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    latest = {"sessionId": "async-session", "clientTurnId": "origin-cid", "projectRoot": str(tmp_path), "actions": [{"tool": "later-read", "status": "completed"}]}
    assert gateway.questions.refresh_pending_runtime_tasks(session_id="async-session", client_turn_id="origin-cid", task_seed=latest) == 0
    saved = gateway.questions._project(include_answered=True)[question["questionId"]]
    assert saved["runtimeContinuationStatus"] == "queued"
    assert saved["runtimeContinuationClientTurnId"] == "origin-cid"
    assert "Example Outfit" in saved["answer"]


def test_all_pending_async_questions_receive_latest_independent_obligations(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    gateway.register_tool("vrcforge_fixture_read", "When to use: read. When NOT to use: write.", "read/debug", lambda _p: {"ok": True})
    replies = iter([
        {"action": "skill", "skill_tool": "vrcforge_ask_user", "skill_params": {"question": "Which outfit?", "blocking": False}},
        {"action": "skill", "skill_tool": "vrcforge_ask_user", "skill_params": {"question": "Which color?", "blocking": False}},
        {"action": "skill", "skill_tool": "vrcforge_fixture_read", "skill_params": {}},
        {"action": "reply", "reply": "Independent inspection done."},
    ])
    bind_test_runtime_planner(gateway, lambda _p: {"text": json.dumps(next(replies))})
    gateway.runtime_message({"message": "Ask the two choices while inspecting independently", "sessionId": "async-session", "clientTurnId": "origin-cid", "projectRoot": str(tmp_path)})
    questions = gateway.questions._project(include_answered=True)
    assert len(questions) == 2
    for question in questions.values():
        seed = question["runtimeTaskSeed"]
        assert any(a["tool"] == "vrcforge_fixture_read" and a["status"] == "completed" for a in seed["actions"])
        assert seed["requestedArguments"]["question"] == question["question"]
        assert "runtimeContinuationStatus" not in question


def test_existing_durable_answer_spill_is_saved_without_dispatch(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    question = _question(gateway, tmp_path)["question"]
    input_id = "question-answer:" + question["questionId"]
    gateway._runtime_followup_queue.enqueue(session_id="async-session", client_turn_id=input_id, message="Example Outfit",
        envelope={"questionId": question["questionId"], "continuationSource": "question_answered"})
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda *_a: (_ for _ in ()).throw(AssertionError("must not duplicate durable dispatch")))
    result = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert result["runtimeContinuation"]["status"] == "settled_without_dispatch"
    assert gateway.list_runtime_followups(session_id="async-session", include_terminal=False) == []


def test_legacy_question_followup_cannot_start_a_new_turn_or_block_an_ordinary_message(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    lane = "conversation"
    stale = gateway._runtime_followup_queue.enqueue(
        session_id=lane, client_turn_id="question-answer:old-question", message="Saved answer",
    )
    normal = gateway.enqueue_runtime_followup({
        "sessionId": lane, "clientTurnId": "normal", "message": "A separate user message",
    })
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    assert [item["queueId"] for item in gateway.list_runtime_followups(session_id=lane, include_terminal=False)] == [normal["queueId"]]
    assert not gateway.claim_runtime_followups(session_id=lane, owner_id="app", queue_id=stale["queueId"])
    assert gateway.claim_runtime_followups(session_id=lane, owner_id="app", queue_id=normal["queueId"])[0]["clientTurnId"] == "normal"


def test_blocking_question_continuation_retains_chat_followup_lane(tmp_path):
    gateway, _model = _write_gateway(tmp_path, [
        call("ask-one", "vrcforge_ask_user", {"question": "Which outfit?"}),
        call("ask-two", "vrcforge_ask_user", {"question": "Which color?", "blocking": False}),
        {"role": "assistant", "content": "Waiting for color."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    _run(gateway, tmp_path, chatId="chat-owner")
    first = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    done = threading.Event()
    gateway._runtime_turn_completed = lambda _p: done.set()
    gateway.questions.answer(first["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert done.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    second = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    assert second["questionId"] != first["questionId"]
    saved = gateway.questions._project(include_answered=True)[second["questionId"]]
    assert saved["runtimeTaskSeed"]["followupLaneId"] == "chat-owner"
    assert saved["originClientTurnId"].endswith(":question:" + first["questionId"])


def test_blocking_question_continuation_preserves_chat_owner_for_later_delegate(tmp_path):
    gateway, _model = _write_gateway(tmp_path, [
        call("ask-before-delegate", "vrcforge_ask_user", {"question": "Which outfit next?"}),
        call("delegate-after-answer", "vrcforge_delegate_subagent", {
            "role": "project_index_review", "task": "Inspect the selected outfit files.",
        }),
        {"role": "assistant", "content": "The selected outfit review is underway."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    delegated = []
    gateway.register_tool(
        "vrcforge_delegate_subagent",
        "When to use: delegate one bounded read-only review. When NOT to use: delegate writes.",
        "plan/preview",
        lambda params: delegated.append(dict(params)) or {"ok": True, "status": "running", "taskId": "child-after-answer"},
    )
    _run(gateway, tmp_path, chatId="chat-owner")
    question = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    saved = gateway.questions._project(include_answered=True)[question["questionId"]]
    assert saved["runtimeTaskSeed"]["parentChatId"] == "chat-owner"

    done = threading.Event()
    gateway._runtime_turn_completed = lambda _payload: done.set()
    gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert done.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert delegated
    assert delegated[0]["_taskSeed"]["parentChatId"] == "chat-owner"


def test_answer_to_an_old_question_does_not_join_another_active_turn(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="different-active", turn_id="active-turn")
    question = _question(gateway, tmp_path)["question"]
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda *_a: (_ for _ in ()).throw(AssertionError("must not start another writer")))
    response = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert response["runtimeContinuation"]["status"] == "queued"
    saved = gateway.questions._project(include_answered=True)[question["questionId"]]
    assert saved["runtimeContinuationStatus"] == "queued"
    assert gateway.list_runtime_followups(session_id="async-session") == []
    assert gateway.runtime_sessions.drain_steer(session_id="async-session", client_turn_id="different-active") == []
    stopped = gateway.request_runtime_cancel({
        "sessionId": "async-session", "clientTurnId": response["runtimeContinuation"]["clientTurnId"],
    })
    assert stopped["status"] == "cancelled"
    assert gateway.runtime_sessions.owns_turn(
        session_id="async-session", client_turn_id="different-active", turn_id="active-turn",
    )
    saved = gateway.questions.list(session_id="async-session", include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "cancelled"
    assert saved["answer"] == "Example Outfit"
    assert gateway.shutdown_runtime_continuations(5)["ok"]


def test_answer_to_old_question_does_not_join_reused_client_turn_id(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="origin-cid", turn_id="new-turn")
    question = _question(gateway, tmp_path)["question"]
    answer = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert answer["runtimeContinuation"]["status"] == "queued"
    assert gateway.runtime_sessions.drain_steer(session_id="async-session", client_turn_id="origin-cid") == []
    assert gateway.shutdown_runtime_continuations(5)["ok"]


def test_answer_to_already_settled_blocking_native_call_starts_new_turn(tmp_path):
    gateway, model = _write_gateway(tmp_path, [
        call("question-1", "vrcforge_ask_user", {"question": "Which outfit?"}),
        {"role": "assistant", "content": "I'll use Example Outfit."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    original = _run(gateway, tmp_path)
    assert original["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    gateway.runtime_sessions.settle_native_call(
        "native-continuation-session", binding=_binding(tmp_path), call_id="question-1", content="The turn has moved on."
    )

    completed = threading.Event()
    gateway._runtime_turn_completed = lambda _result: completed.set()
    answer = gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert answer["runtimeContinuation"]["status"] == "queued"
    assert answer["question"]["answer"] == "Example Outfit"
    assert completed.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert len(model.requests) == 2
    [run] = gateway.runtime_runs.list_runs(
        session_id="native-continuation-session", client_turn_id=answer["runtimeContinuation"]["clientTurnId"],
    )["runs"]
    assert run["turnId"] != original["turnId"]


def test_skip_blocking_question_saves_without_resuming_and_allows_next_message(tmp_path):
    session_id = "native-continuation-session"
    gateway, model = _write_gateway(tmp_path, [
        call("question-1", "vrcforge_ask_user", {"question": "Which outfit?"}),
        {"role": "assistant", "content": "Starting the new request."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    original = _run(gateway, tmp_path)
    assert original["plan"]["nextStep"] == "needs_user_action"
    question_id = gateway.questions.list(session_id=session_id)["questions"][0]["questionId"]

    answer = gateway.questions.answer(question_id, {
        "sessionId": session_id, "selectedOptionId": "skip", "answer": "Skipped",
    })
    assert answer["runtimeContinuation"]["status"] == "settled_without_dispatch"
    assert answer["question"]["selectedOptionId"] == "skip"
    assert not gateway.questions.list(session_id=session_id)["questions"]
    assert gateway.questions.reconcile_runtime_continuations()["queued"] == 0
    assert len(model.requests) == 1
    assert not gateway.list_runtime_followups(session_id=session_id)

    _run(gateway, tmp_path, message="Start a different request", clientTurnId="manual-next-turn")
    assert len(model.requests) == 2
    results = [message for message in model.requests[-1]["messages"] if message.get("tool_call_id") == "question-1"]
    assert len(results) == 1
    assert json.loads(results[0]["content"])["status"] == "skipped"


@pytest.mark.parametrize("resolution", ["later", "uncertain", "pass", "fail"])
def test_visual_answer_to_returned_turn_starts_with_user_answer(tmp_path, resolution):
    session_id = "native-continuation-session"
    gateway, model = _write_gateway(tmp_path, [
        call("question-1", "vrcforge_ask_user", {
            "kind": "visual_checkpoint",
            "question": "Is the appearance correct?",
            "options": [{"id": value, "label": value.title(), "resolution": value}
                        for value in ("later", "uncertain", "pass", "fail")],
        }),
        {"role": "assistant", "content": "Starting the new request."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    original = _run(gateway, tmp_path)
    assert original["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id=session_id)["questions"][0]
    origin = question["originClientTurnId"]
    assert gateway.get_runtime_session(session_id, client_turn_id=origin)["status"] == "completed"

    completed = threading.Event()
    gateway._runtime_turn_completed = lambda _result: completed.set()
    answer = gateway.questions.answer(question["questionId"], {
        "sessionId": session_id, "selectedOptionId": resolution,
    })
    assert answer["runtimeContinuation"]["status"] == "queued"
    assert answer["question"]["status"] == "answered"
    assert answer["question"]["resolution"] == resolution
    assert completed.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert not gateway.list_runtime_followups(session_id=session_id)
    assert len(model.requests) == 2
    assert {"role": "user", "content": resolution.title()} in model.requests[-1]["messages"]
    results = [message for message in model.requests[-1]["messages"] if message.get("tool_call_id") == "question-1"]
    assert len(results) == 1
    assert json.loads(results[0]["content"])["status"] == "answered"


@pytest.mark.parametrize("resolution", ["later", "pass"])
def test_visual_answer_during_blocking_tool_never_starts_another_turn(tmp_path, resolution):
    session_id = "native-continuation-session"
    gateway, model = _write_gateway(tmp_path, [
        call("visual-ask", "vrcforge_ask_user", {
            "kind": "visual_checkpoint", "question": "Is the appearance correct?",
        }),
        {"role": "assistant", "content": "The original turn is ending."},
    ], [])
    during_tool = []

    def ask_and_answer(params):
        result = gateway.create_runtime_question(params)
        during_tool.append(gateway.questions.answer(result["question"]["questionId"], {
            "sessionId": session_id, "answer": resolution.title(), "resolution": resolution,
        }))
        return result

    gateway.register_tool(
        "vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", ask_and_answer,
    )
    _run(gateway, tmp_path)
    assert during_tool[0]["runtimeContinuation"]["status"] == "steered"
    assert gateway.shutdown_runtime_continuations(1)["ok"]
    assert len(model.requests) == 2
    assert gateway.list_runtime_followups(session_id=session_id) == []
    saved = gateway.questions.list(session_id=session_id, include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "delivered"
    assert not gateway.runtime_runs.list_runs(
        session_id=session_id, client_turn_id=saved["originClientTurnId"] + ":question:" + saved["questionId"],
    )["runs"]
    answer_seen = any(message.get("role") == "user" and "User answer: " + resolution.title() in message.get("content", "")
                      for message in model.requests[-1]["messages"])
    assert answer_seen
    native = gateway.runtime_sessions.native_conversation(session_id, binding=_binding(tmp_path))
    assert gateway.runtime_sessions._native_pending(native) == set()


def test_async_delivery_receipt_cannot_settle_other_session_question(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="origin-cid", turn_id="origin-turn")
    question = _question(gateway, tmp_path)["question"]
    gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    gateway._settle_async_question_steers(["question-answer:" + question["questionId"]], session_id="unrelated", client_turn_id="origin-cid")
    saved = gateway.questions._project(include_answered=True)[question["questionId"]]
    assert saved["runtimeContinuationStatus"] == "queued"
