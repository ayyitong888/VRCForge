import pytest

from agent_runtime_input import RuntimeInputSource
from context_compaction import _normalize_entries, _Redactor
from cross_session_conversation_service import CrossSessionConversationService
from general_agent_tools import read_text_file
from tests.test_native_runtime_gateway import call, finish, setup_gateway


@pytest.mark.parametrize("source", ["app", "goal", "relay", "external", "approval", "question-wrapper", "question-reply"])
def test_only_actual_human_input_grants_scope_and_is_retained_as_user(tmp_path, source):
    project = tmp_path / "project"
    project.mkdir()
    reference_dir = tmp_path / "materials"
    reference_dir.mkdir()
    target = reference_dir / "source.txt"
    target.write_text("fixture", encoding="utf-8")
    client_id = "agent-forward-turn" if source == "relay" else "turn"
    message = f'Read "{target}"'
    marker = {
        "source": "app_user_message", "message": message,
        "sessionId": "session", "clientTurnId": client_id, "projectRoot": str(project),
    }
    params = {
        "message": message, "sessionId": "session", "clientTurnId": client_id,
        "projectRoot": str(project), "_projectContextActive": False,
        "history": [
            {"role": "user", "text": "Earlier human instruction"},
            {"role": "user", "text": "Earlier forwarded text", "authorRole": "agent_relay"},
        ],
    }
    if source in {"app", "question-reply", "relay"}:
        params["_humanUserMessageSource"] = marker
    if source == "goal":
        params["goalDeliveryId"] = "goal-delivery"
        params["_humanUserMessageSource"] = marker
    if source == "external":
        params["_runtimeExternalAgent"] = True
    gateway, _model, reads = setup_gateway(tmp_path, [
        call("read", "vrcforge_read_text_file", {"path": str(target)}),
        finish,
    ])
    def inspect(arguments):
        result = read_text_file(arguments["path"], allowed_roots=arguments["_generalAllowedRoots"])
        reads.append(result)
        return result

    gateway.register_tool("vrcforge_read_text_file", "Read scoped file.", "plan/preview", inspect)
    try:
        if source in {"approval", "question-wrapper", "question-reply"}:
            continuation = {"source": "approval_finished" if source == "approval" else "question_answered"}
            if source == "question-wrapper":
                continuation.update({"context": {"objective": "Earlier task"}, "questionId": "fixture-question"})
            with gateway.runtime_planner.bind_turn(params):
                gateway._runtime_message_impl(params, task_continuation=continuation)
        else:
            gateway.runtime_message(params, agent_name="external-agent" if source == "external" else "desktop-agent")
    finally:
        gateway.shutdown_runtime_continuations(0)
    stored = gateway.runtime_sessions._native_conversations["session"]
    snapshot = gateway.runtime_sessions.native_conversation("session", binding=stored["binding"])
    retained = snapshot["humanUserMessageIndices"]
    assert snapshot["messages"][0] == {"role": "user", "content": "Earlier human instruction"}
    assert snapshot["messages"][1]["content"] == RuntimeInputSource.AGENT_RELAY.model_text("Earlier forwarded text")
    assert snapshot["messages"][1]["runtimeInputSource"] == "agent_relay"
    assert 0 in retained and 1 not in retained
    actual = source in {"app", "question-reply"}
    assert (snapshot["activeTurnStart"] in retained) is actual
    expected_source = {
        "goal": RuntimeInputSource.GOAL, "relay": RuntimeInputSource.AGENT_RELAY,
        "external": RuntimeInputSource.EXTERNAL_AGENT,
    }.get(source, RuntimeInputSource.USER if actual else RuntimeInputSource.RUNTIME)
    assert snapshot["messages"][snapshot["activeTurnStart"]]["content"] == expected_source.model_text(message)
    assert len(reads) == int(actual)
    if actual:
        assert reads[0]["text"] == "fixture"


@pytest.mark.parametrize("question", [False, True], ids=["relay", "authenticated-answer"])
def test_live_steer_marks_runtime_context_without_losing_the_real_answer(tmp_path, question):
    runtime_text = "Context issued by the runtime."
    actual_answer = "My actual answer."
    input_id = "question-answer:fixture" if question else "agent-forward-fixture"

    def first_proposal(_request):
        if question:
            submitted = gateway.runtime_sessions.submit_steer(
                session_id="session", target_client_turn_id="turn", input_id=input_id,
                message=runtime_text, authenticated_answer=actual_answer,
            )
        else:
            submitted = gateway.submit_runtime_steer({
                "sessionId": "session", "targetClientTurnId": "turn",
                "clientTurnId": input_id, "message": runtime_text,
            })
        assert submitted["accepted"]
        return call("discarded", "vrcforge_read_text_file", {"path": "not-used.txt"})

    gateway, model, reads = setup_gateway(tmp_path, [first_proposal, finish])
    try:
        gateway.runtime_message({
            "message": "Begin.", "sessionId": "session", "clientTurnId": "turn",
            "projectRoot": str(tmp_path), "_humanUserMessageSource": {
                "source": "app_user_message", "message": "Begin.", "sessionId": "session",
                "clientTurnId": "turn", "projectRoot": str(tmp_path),
            },
        })
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert not reads
    source = RuntimeInputSource.RUNTIME if question else RuntimeInputSource.AGENT_RELAY
    wrapped = source.model_text(runtime_text)
    assert any(item.get("content") == wrapped for item in model.requests[1]["messages"])
    snapshot = gateway.runtime_sessions._native_conversations["session"]
    human = [snapshot["messages"][index]["content"] for index in snapshot["humanUserMessageIndices"]]
    assert human == (["Begin.", actual_answer] if question else ["Begin."])


def test_compaction_keeps_explicit_relay_provenance_and_does_not_change_typed_identical_text():
    text = "A message that looks identical in transcription."
    rows = _normalize_entries([
        {"role": "user", "text": text, "authorRole": "agent_relay"},
        {"role": "user", "text": text},
    ], _Redactor())
    assert rows == [{"role": "runtime", "text": text}, {"role": "user", "text": text}]


def test_cross_session_target_history_recovers_goal_from_its_paired_receipt():
    text = "Same text in automation and in a later human message."
    history = CrossSessionConversationService._history_before({"items": [
        {"id": "goal-user-delivery", "type": "user", "text": text},
        {"id": "goal-user-user-claimed", "type": "user", "text": text},
        {"id": "goal-answer", "type": "agent", "response": {
            "goalDeliveryId": "delivery", "plan": {"reply": "Checked."},
        }},
        {"id": "current", "type": "user", "clientTurnId": "target-turn", "text": "Relay payload"},
    ]}, "target-turn")
    assert history == [
        {"role": "user", "text": text, "authorRole": "goal"},
        {"role": "user", "text": text},
        {"role": "assistant", "text": "Checked."},
    ]
