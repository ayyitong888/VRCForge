from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent_question_service import (
    AgentQuestionPersistence,
    AgentQuestionPersistencePorts,
    AgentQuestionScopePorts,
    AgentQuestionService,
    AgentQuestionServiceError,
    GoalQuestionResolutionPort,
)


def _summarize(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _service(root: Path, lock: threading.RLock, resolved: list[tuple[str, str]]) -> AgentQuestionService:
    return AgentQuestionService(
        AgentQuestionPersistence(
            AgentQuestionPersistencePorts(
                log_path=lambda: root / "agent-questions.jsonl",
                shared_state_lock=lock,
                redact=lambda value: value,
            )
        ),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value.strip().replace("/", "\\").casefold(),
            summarize=_summarize,
            redact_goal_persistence=lambda value: str(value).replace("secret", "[redacted]"),
        ),
        GoalQuestionResolutionPort(
            resolve=lambda question_id, continuation_prompt: resolved.append((question_id, continuation_prompt))
            or {"ok": True, "questionId": question_id}
        ),
    )


def test_question_service_is_a_narrow_independent_owner(tmp_path: Path) -> None:
    source = (Path(__file__).parents[1] / "agent_question_service.py").read_text(encoding="utf-8")
    for forbidden in ("from agent_gateway", "import agent_gateway", "AGENT_GATEWAY", "getattr(", "_host", "_impl", "sys.modules"):
        assert forbidden not in source

    service = _service(tmp_path, threading.RLock(), [])
    assert service.log_path == tmp_path / "agent-questions.jsonl"


def test_question_service_preserves_scope_and_answer_exactly_once(tmp_path: Path) -> None:
    shared_lock = threading.RLock()
    resolutions: list[tuple[str, str]] = []
    first = _service(tmp_path, shared_lock, resolutions)
    second = _service(tmp_path, shared_lock, resolutions)

    created = first.create(
        {
            "question": "Choose a proof",
            "options": [
                {"id": "a", "label": "Actual", "value": "Run actual proof"},
                {"id": "b", "label": "Browser", "value": "Run browser proof"},
            ],
            "sessionId": "session-a",
            "projectRoot": "D:/ProjectA",
            "goalDeliveryId": "delivery-a",
        }
    )
    question_id = str(created["question"]["questionId"])

    try:
        second.answer(question_id, {"sessionId": "session-b", "projectRoot": "D:/ProjectA"})
    except AgentQuestionServiceError as exc:
        assert exc.status_code == 404
        assert "session" in str(exc)
    else:  # pragma: no cover - the assertion above is the behavior under test.
        raise AssertionError("cross-session Question answer was accepted")

    answered = first.answer(
        question_id,
        {"sessionId": "session-a", "projectRoot": "d:\\projecta", "selectedOptionId": "a", "answer": "secret proof"},
    )
    repeated = second.answer(question_id, {"sessionId": "session-a", "projectRoot": "D:/ProjectA"})

    assert answered["question"]["answer"] == "[redacted] proof"
    assert repeated["idempotent"] is True
    assert len(resolutions) == 2  # Existing Goal resolution retries after a durable answer.
    records = [
        line
        for line in (tmp_path / "agent-questions.jsonl").read_text(encoding="utf-8").splitlines()
        if '"event": "question_answered"' in line
    ]
    assert len(records) == 1
    assert first.list(session_id="session-a", project_root="D:/ProjectA")["count"] == 0
    assert first.list(session_id="session-a", project_root="D:/ProjectA", include_answered=True)["count"] == 1


def test_question_service_allows_free_text_and_rejects_single_option_or_empty_answer(tmp_path: Path) -> None:
    service = _service(tmp_path, threading.RLock(), [])
    created = service.create({"question": "Describe the proof", "sessionId": "s"})
    question_id = str(created["question"]["questionId"])
    answered = service.answer(question_id, {"sessionId": "s", "answer": "A bounded answer"})
    assert answered["question"]["answer"] == "A bounded answer"

    with pytest.raises(AgentQuestionServiceError, match="at least two"):
        service.create({"question": "Pick one", "options": ["Only one"]})

    second = service.create({"question": "Answer required", "sessionId": "s"})
    with pytest.raises(AgentQuestionServiceError, match="answer is required"):
        service.answer(str(second["question"]["questionId"]), {"sessionId": "s"})


def test_answered_history_cannot_hide_pending_or_running_questions(tmp_path: Path) -> None:
    service = _service(tmp_path, threading.RLock(), [])
    pending = service.create({"question": "Still needs an answer", "sessionId": "s"})["question"]
    active = service.create({"question": "Continuing", "sessionId": "s"})["question"]
    service.answer(active["questionId"], {"answer": "yes", "sessionId": "s"})
    service.record_runtime_continuation(active["questionId"], "claimed")
    for number in range(8):
        old = service.create({"question": f"Already answered {number}", "sessionId": "s"})["question"]
        service.answer(old["questionId"], {"answer": "done", "sessionId": "s"})
        service.record_runtime_continuation(old["questionId"], "delivered")
    rows = service.list(session_id="s", limit=2, include_answered=True)["questions"]
    assert [row["questionId"] for row in rows] == [pending["questionId"], active["questionId"]]

@pytest.mark.parametrize('field,limit', [('question',1000), ('header',120)])
def test_question_rejects_oversize_text_without_silent_truncation(tmp_path, field, limit):
    service = _service(tmp_path, threading.RLock(), [])
    values = {'question': 'Which repair?', field: 'x' * (limit + 1)}
    with pytest.raises(AgentQuestionServiceError, match=field):
        service.create(values)
    assert not service.log_path.exists()


@pytest.mark.parametrize('field,limit', [('label',160), ('value',500), ('description',500), ('id',120)])
def test_question_rejects_oversize_choice_without_changing_decision(tmp_path, field, limit):
    service = _service(tmp_path, threading.RLock(), [])
    option = {'id':'first','label':'Reinstall','value':'Install the bundled plugin',field:'x' * (limit + 1)}
    with pytest.raises(AgentQuestionServiceError, match=field):
        service.create({'question':'Which repair?', 'options':[option, {'id':'later','label':'Later'}]})
    assert not service.log_path.exists()


def test_question_preserves_multiline_choices_and_has_model_schema(tmp_path):
    from runtime_planner_service import planner_tool_input_schema
    schema = planner_tool_input_schema('vrcforge_ask_user')
    assert 'question' in schema['properties']
    assert 'options' in schema['properties']
    option_schema = schema['properties']['options']['items']['anyOf'][1]
    assert {'id','label','description','value'} <= set(option_schema['properties'])
    service = _service(tmp_path, threading.RLock(), [])
    question = 'Choose a repair.\nYour files are kept.'
    description = 'Install the bundled plugin.\nKeep user tools and the wardrobe.'
    result = service.create({'question':question,'options':[{'id':'install','label':'Reinstall','description':description}, {'id':'later','label':'Later'}]})
    assert result['question']['question'] == question
    assert result['question']['options'][0]['description'] == description


def test_question_answer_preserves_multiline_and_rejects_oversize(tmp_path):
    service = _service(tmp_path, threading.RLock(), [])
    created = service.create({'question': 'Describe the limits', 'sessionId': 's'})
    qid = created['question']['questionId']
    with pytest.raises(AgentQuestionServiceError, match='answer'):
        service.answer(qid, {'sessionId': 's', 'answer': 'x' * 2001})
    assert service.list(session_id='s')['count'] == 1
    text = 'Keep the files.\n    Keep indentation.\n\nDo not edit the wardrobe.'
    result = service.answer(qid, {'sessionId': 's', 'answer': text})
    assert result['question']['answer'] == text
    assert text in service._continuation_prompt(result['question'])
