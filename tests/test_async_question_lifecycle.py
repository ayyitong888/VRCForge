from __future__ import annotations

import json
import threading

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
        {"action": "reply", "reply": "Independent inspection is complete; awaiting the outfit choice."},
    ])
    bind_test_runtime_planner(gateway, lambda _p: {"text": json.dumps(next(replies))})
    result = gateway.runtime_message({"message": "Ask about the next outfit and inspect independently while waiting.", "sessionId": "async-session", "clientTurnId": "origin-cid", "projectRoot": str(tmp_path)})
    assert reads == [True]
    assert result["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id="async-session")["questions"][0]
    assert question["blocking"] is False
    assert question["originClientTurnId"] == "origin-cid"
    assert question["originTurnId"]


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


def test_parked_async_answer_uses_latest_native_history_including_independent_read(tmp_path):
    gateway, model = _write_gateway(tmp_path, [
        call("async-ask", "vrcforge_ask_user", {"question": "Which outfit next?", "blocking": False}),
        call("independent-read", "vrcforge_fixture_read", {}),
        {"role": "assistant", "content": "Independent work finished; waiting for the choice."},
        {"role": "assistant", "content": "Example Outfit selected; existing inspection retained."},
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
    done = threading.Event()
    gateway._runtime_turn_completed = lambda _payload: done.set()
    gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert done.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    messages = model.requests[-1]["messages"]
    assert any(m.get("tool_call_id") == "independent-read" for m in messages)
    assert any(m.get("role") == "user" and "Example Outfit" in m.get("content", "") for m in messages)
    assert len(model.requests) == 4


def test_active_answer_steers_240_character_origin_without_truncation(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    origin = "turn-" + "x" * 235
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id=origin, turn_id="origin-turn")
    question = _question(gateway, tmp_path, _runtimeTaskSeed={"sessionId": "async-session", "clientTurnId": origin, "turnId": "origin-turn", "projectRoot": str(tmp_path)})["question"]
    result = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert result["runtimeContinuation"]["mode"] == "steer"
    assert result["runtimeContinuation"]["clientTurnId"] == origin
    assert len(gateway.runtime_sessions.drain_steer(session_id="async-session", client_turn_id=origin)) == 1


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


def test_late_native_answer_spills_durably_before_question_delivery(tmp_path, monkeypatch):
    from agent_runtime_session_state import AgentRuntimeSessionState
    entered, release = threading.Event(), threading.Event()
    gateway, model = _write_gateway(tmp_path, [
        call("async-ask", "vrcforge_ask_user", {"question": "Which outfit next?", "blocking": False}),
        {"role": "assistant", "content": "Waiting for your choice."},
    ], [])
    gateway.register_tool("vrcforge_ask_user", "When to use: ask. When NOT to use: otherwise.", "plan/preview", gateway.create_runtime_question)
    original_finish = AgentRuntimeSessionState.finish_turn
    def final_boundary(state, **identity):
        if state is gateway.runtime_sessions:
            entered.set()
            assert release.wait(5)
        return original_finish(state, **identity)
    monkeypatch.setattr(AgentRuntimeSessionState, "finish_turn", final_boundary)
    results = []
    origin = "turn-" + "z" * 235
    runner = threading.Thread(target=lambda: results.append(_run(gateway, tmp_path, clientTurnId=origin)))
    runner.start()
    assert entered.wait(5)
    question = gateway.questions.list(session_id="native-continuation-session")["questions"][0]
    answer = gateway.questions.answer(question["questionId"], {"sessionId": "native-continuation-session", "answer": "Example Outfit"})
    assert answer["runtimeContinuation"]["mode"] == "steer"
    release.set()
    runner.join(5)
    assert not runner.is_alive() and results
    followups = gateway.list_runtime_followups(session_id="native-continuation-session", include_terminal=False)
    assert len(followups) == 1
    assert followups[0]["clientTurnId"] == "question-answer:" + question["questionId"]
    assert followups[0]["targetClientTurnId"] == origin
    assert followups[0]["questionId"] == question["questionId"]
    assert followups[0]["continuationSource"] == "question_answered"
    assert "Example Outfit" in followups[0]["message"]
    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    assert restarted.list_runtime_followups(session_id="native-continuation-session")[0]["message"] == followups[0]["message"]
    saved = restarted.questions.list(session_id="native-continuation-session", include_answered=True)["questions"][0]
    assert saved["runtimeContinuationStatus"] == "delivered"
    assert saved["deliveryMode"] == "followup" and saved["followupQueueId"] == followups[0]["queueId"]
    assert saved["runtimeContinuationClientTurnId"] == followups[0]["clientTurnId"]
    assert saved["runtimeContinuationTurnId"] == ""
    assert len(model.requests) == 2


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


def test_existing_durable_answer_spill_is_reused_after_restart_gap(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    question = _question(gateway, tmp_path)["question"]
    input_id = "question-answer:" + question["questionId"]
    gateway._runtime_followup_queue.enqueue(session_id="async-session", client_turn_id=input_id, message="Example Outfit",
        envelope={"questionId": question["questionId"], "continuationSource": "question_answered"})
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda *_a: (_ for _ in ()).throw(AssertionError("must not duplicate durable dispatch")))
    result = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert result["runtimeContinuation"]["deduped"]
    assert len(gateway.list_runtime_followups(session_id="async-session")) == 1


def test_repeated_async_question_continuation_retains_chat_followup_lane(tmp_path):
    gateway, _model = _write_gateway(tmp_path, [
        call("ask-one", "vrcforge_ask_user", {"question": "Which outfit?", "blocking": False}),
        {"role": "assistant", "content": "Waiting for outfit."},
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


def test_queue_backpressure_keeps_async_answer_queued_and_does_not_dispatch(tmp_path, monkeypatch):
    from agent_runtime_followup_queue import AgentRuntimeFollowupQueue
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="different-active", turn_id="active-turn")
    question = _question(gateway, tmp_path)["question"]
    monkeypatch.setattr(AgentRuntimeFollowupQueue, "enqueue", lambda *_a, **_k: {"accepted": False, "mode": "followup", "status": "backpressure"})
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda *_a: (_ for _ in ()).throw(AssertionError("must not start another writer")))
    response = gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    assert response["runtimeContinuation"]["ok"] is False
    saved = gateway.questions._project(include_answered=True)[question["questionId"]]
    assert saved["runtimeContinuationStatus"] == "queued"


def test_async_delivery_receipt_cannot_settle_other_session_question(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.runtime_sessions.begin_turn(session_id="async-session", client_turn_id="origin-cid", turn_id="origin-turn")
    question = _question(gateway, tmp_path)["question"]
    gateway.questions.answer(question["questionId"], {"sessionId": "async-session", "answer": "Example Outfit"})
    gateway._settle_async_question_steers(["question-answer:" + question["questionId"]], session_id="unrelated", client_turn_id="origin-cid")
    saved = gateway.questions._project(include_answered=True)[question["questionId"]]
    assert saved["runtimeContinuationStatus"] == "queued"
