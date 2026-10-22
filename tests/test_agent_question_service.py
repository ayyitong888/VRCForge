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
    RuntimeQuestionResolutionPort,
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


def test_visual_checkpoint_has_explicit_resolution_and_replays_defer(tmp_path: Path) -> None:
    resolutions: list[tuple[str, str]] = []
    service = _service(tmp_path, threading.RLock(), resolutions)
    values = {
        "kind": "visual_checkpoint",
        "question": "Does the avatar preview pass the visual gate?",
        "options": ["pass", "fail", "uncertain", "later"],
        "sessionId": "visual-session",
        "projectRoot": "D:/ProjectA",
        "goalDeliveryId": "visual-delivery",
        "sourceTool": "call_user",
    }
    created = service.create(values)
    duplicate = service.create(values)
    question_id = str(created["question"]["questionId"])
    assert created["question"]["kind"] == "visual_checkpoint"
    assert duplicate["deduplicated"] is True
    assert duplicate["question"]["questionId"] == question_id

    deferred = service.answer(
        question_id,
        {
            "sessionId": "visual-session",
            "projectRoot": "D:/ProjectA",
            "resolution": "uncertain",
            "answer": "The screenshot is ambiguous.",
        },
    )
    assert deferred["deferred"] is True
    assert deferred["question"]["status"] == "pending"
    assert deferred["question"]["resolution"] == "uncertain"
    assert resolutions == []
    assert '"event": "question_deferred"' in (tmp_path / "agent-questions.jsonl").read_text(encoding="utf-8")

    passed = service.answer(
        question_id,
        {
            "sessionId": "visual-session",
            "projectRoot": "D:/ProjectA",
            "resolution": "pass",
            "answer": "Confirmed by the user.",
        },
    )
    assert passed["question"]["status"] == "answered"
    assert passed["question"]["resolution"] == "pass"
    assert len(resolutions) == 1


def test_visual_checkpoint_rejects_inferred_or_unknown_resolution(tmp_path: Path) -> None:
    service = _service(tmp_path, threading.RLock(), [])
    created = service.create({"kind": "visual_checkpoint", "question": "Inspect the preview", "sessionId": "s"})
    feedback = service.answer(created["question"]["questionId"], {"sessionId": "s", "answer": "Looks okay"})
    assert feedback["feedback"] is True
    assert feedback["question"]["status"] == "pending"


def test_question_dedup_is_call_user_scoped_and_persists_source(tmp_path: Path) -> None:
    service = _service(tmp_path, threading.RLock(), [])
    params = {"question": "Confirm once", "sessionId": "s", "sourceTool": "call_user"}
    first = service.create(params)
    second = service.create(params)
    legacy = service.create({"question": "Confirm once", "sessionId": "s"})
    assert second["deduplicated"] is True
    assert legacy["question"]["questionId"] != first["question"]["questionId"]
    assert '"sourceTool": "call_user"' in (tmp_path / "agent-questions.jsonl").read_text(encoding="utf-8")


def test_visual_feedback_attachment_reaches_runtime_without_resolving_acceptance(tmp_path: Path) -> None:
    dispatched: list[tuple[str, dict, str]] = []
    service = AgentQuestionService(
        AgentQuestionPersistence(AgentQuestionPersistencePorts(
            log_path=lambda: tmp_path / "agent-questions.jsonl",
            shared_state_lock=threading.RLock(), redact=lambda value: value,
        )),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value,
            summarize=_summarize,
            redact_goal_persistence=lambda value: value,
        ),
        GoalQuestionResolutionPort(resolve=lambda _question_id, _prompt: None),
        RuntimeQuestionResolutionPort(
            resolve=lambda question_id, seed, prompt: dispatched.append((question_id, dict(seed), prompt)) or {"ok": True},
        ),
    )
    created = service.create({
        "kind": "visual_checkpoint", "sourceTool": "call_user",
        "question": "Inspect the preview", "sessionId": "s", "projectRoot": "D:/P",
        "_runtimeTaskSeed": {"sessionId": "s", "taskId": "task-1"},
    })
    feedback = service.answer(created["question"]["questionId"], {
        "sessionId": "s", "projectRoot": "D:/P", "answer": "Please inspect this image.",
        "attachments": [{"name": "preview.png", "payloadHash": "a" * 64, "payloadKind": "vault_file"}],
    })
    assert feedback["feedback"] is True
    assert feedback["question"]["status"] == "pending"
    assert dispatched[0][1]["_authenticatedUserAttachments"][0]["payloadHash"] == "a" * 64
    assert '"event": "question_feedback"' in (tmp_path / "agent-questions.jsonl").read_text(encoding="utf-8")
    replayed: list[dict] = []
    restarted = AgentQuestionService(
        service._persistence, service._scope, GoalQuestionResolutionPort(resolve=lambda _id, _prompt: None),
        RuntimeQuestionResolutionPort(resolve=lambda _id, seed, _prompt: replayed.append(dict(seed)) or {"ok": True}),
    )
    assert restarted.reconcile_runtime_continuations()["queued"] == 1
    assert replayed[0]["_authenticatedUserAttachments"][0]["payloadHash"] == "a" * 64
    assert service.claim_runtime_continuation(created["question"]["questionId"]) is True


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


def test_runtime_retry_for_one_question_does_not_interrupt_other_claimed_question(tmp_path: Path) -> None:
    dispatched: list[str] = []
    service = AgentQuestionService(
        AgentQuestionPersistence(AgentQuestionPersistencePorts(
            log_path=lambda: tmp_path / "agent-questions.jsonl",
            shared_state_lock=threading.RLock(), redact=lambda value: value,
        )),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value, summarize=_summarize,
            redact_goal_persistence=lambda value: value,
        ),
        GoalQuestionResolutionPort(resolve=lambda _id, _prompt: None),
        RuntimeQuestionResolutionPort(
            resolve=lambda question_id, _seed, _prompt: dispatched.append(question_id)
            or {"ok": True, "status": "queued"},
        ),
    )
    question_a = service.create({
        "kind": "visual_checkpoint", "sourceTool": "call_user", "question": "A?",
        "sessionId": "s", "projectRoot": "P", "_runtimeTaskSeed": {"taskId": "A"},
    })["question"]
    question_b = service.create({
        "kind": "visual_checkpoint", "sourceTool": "call_user", "question": "B?",
        "sessionId": "s", "projectRoot": "P", "_runtimeTaskSeed": {"taskId": "B"},
    })["question"]
    service.answer(question_a["questionId"], {"sessionId": "s", "answer": "more detail"})
    service.answer(question_b["questionId"], {"sessionId": "s", "answer": "more detail"})
    service.record_runtime_continuation(question_b["questionId"], "claimed")
    assert service.reconcile_runtime_continuation(question_a["questionId"])
    current = service._project(include_answered=True)
    assert current[question_b["questionId"]]["runtimeContinuationStatus"] == "claimed"
    assert dispatched == [question_a["questionId"], question_b["questionId"], question_a["questionId"]]


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
    call_user_schema = planner_tool_input_schema('vrcforge_call_user')
    assert 'question' in schema['properties']
    assert call_user_schema == schema
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


def test_runtime_answer_keeps_user_supplied_url_and_path_for_model_but_redacts_durable_copy(tmp_path: Path) -> None:
    from runtime_planner_service import sanitize_planner_observation_text

    continuation_prompts: list[str] = []
    answer = (
        "Documentation: https://example.invalid/items/example-outfit. "
        "Package directory: E:/Packages/Creator/CityNoir."
        " Authorization: Bearer test-token-123"
    )
    service = AgentQuestionService(
        AgentQuestionPersistence(
            AgentQuestionPersistencePorts(
                log_path=lambda: tmp_path / "agent-questions.jsonl",
                shared_state_lock=threading.RLock(),
                redact=lambda value: value,
            )
        ),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value,
            summarize=_summarize,
            redact_goal_persistence=lambda value: sanitize_planner_observation_text(
                value, None, preserve_whitespace=True,
            ),
        ),
        GoalQuestionResolutionPort(resolve=lambda _question_id, _prompt: None),
        RuntimeQuestionResolutionPort(
            resolve=lambda _question_id, _seed, prompt: continuation_prompts.append(prompt) or {"ok": True},
        ),
    )
    created = service.create({
        "question": "Where is the package documentation?",
        "sessionId": "session-example-outfit",
        "projectRoot": "E:/unity/TestAvatar",
        "_runtimeTaskSeed": {"sessionId": "session-example-outfit", "taskId": "task-example-outfit"},
    })

    answered = service.answer(
        str(created["question"]["questionId"]),
        {"sessionId": "session-example-outfit", "projectRoot": "E:/unity/TestAvatar", "answer": answer},
    )

    assert len(continuation_prompts) == 1
    assert "https://example.invalid/items/example-outfit" in continuation_prompts[0]
    assert "E:/Packages/Creator/CityNoir" in continuation_prompts[0]
    assert "test-token-123" not in continuation_prompts[0]
    assert "<redacted>" in continuation_prompts[0]
    assert "<path redacted>" in answered["question"]["answer"]
    durable = (tmp_path / "agent-questions.jsonl").read_text(encoding="utf-8")
    assert "https://example.invalid/items/example-outfit" not in durable
    assert "E:/Packages/Creator/CityNoir" not in durable
    assert "test-token-123" not in durable
    assert "<path redacted>" in durable


def test_queued_idempotent_retry_uses_resubmitted_answer_for_model_context(tmp_path: Path) -> None:
    from runtime_planner_service import sanitize_planner_observation_text

    continuation_prompts: list[str] = []
    answer = "Docs: https://example.invalid/items/creator. Package: E:/Packages/Creator/CityNoir."
    service = AgentQuestionService(
        AgentQuestionPersistence(
            AgentQuestionPersistencePorts(
                log_path=lambda: tmp_path / "agent-questions.jsonl",
                shared_state_lock=threading.RLock(),
                redact=lambda value: value,
            )
        ),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value,
            summarize=_summarize,
            redact_goal_persistence=lambda value: sanitize_planner_observation_text(
                value, None, preserve_whitespace=True,
            ),
        ),
        GoalQuestionResolutionPort(resolve=lambda _question_id, _prompt: None),
        RuntimeQuestionResolutionPort(
            resolve=lambda _question_id, _seed, prompt: continuation_prompts.append(prompt) or {"status": "queued"},
        ),
    )
    created = service.create({
        "question": "Where are the docs and package?",
        "sessionId": "session-retry",
        "projectRoot": "E:/unity/TestAvatar",
        "_runtimeTaskSeed": {"sessionId": "session-retry", "taskId": "task-retry"},
    })
    qid = str(created["question"]["questionId"])
    request = {"sessionId": "session-retry", "projectRoot": "E:/unity/TestAvatar", "answer": answer}

    service.answer(qid, request)
    retried = service.answer(qid, request)

    assert retried["idempotent"] is True
    assert len(continuation_prompts) == 2
    assert all("https://example.invalid/items/creator" in prompt for prompt in continuation_prompts)
    assert all("E:/Packages/Creator/CityNoir" in prompt for prompt in continuation_prompts)


def test_queued_idempotent_retry_requires_resubmission_if_saved_answer_redacted_location(tmp_path: Path) -> None:
    from runtime_planner_service import sanitize_planner_observation_text

    continuation_prompts: list[str] = []
    service = AgentQuestionService(
        AgentQuestionPersistence(
            AgentQuestionPersistencePorts(
                log_path=lambda: tmp_path / "agent-questions.jsonl",
                shared_state_lock=threading.RLock(),
                redact=lambda value: value,
            )
        ),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value,
            summarize=_summarize,
            redact_goal_persistence=lambda value: sanitize_planner_observation_text(
                value, None, preserve_whitespace=True,
            ),
        ),
        GoalQuestionResolutionPort(resolve=lambda _question_id, _prompt: None),
        RuntimeQuestionResolutionPort(
            resolve=lambda _question_id, _seed, prompt: continuation_prompts.append(prompt) or {"status": "queued"},
        ),
    )
    created = service.create({
        "question": "Where is the package?",
        "sessionId": "session-retry-no-answer",
        "projectRoot": "E:/unity/TestAvatar",
        "_runtimeTaskSeed": {"sessionId": "session-retry-no-answer", "taskId": "task-retry-no-answer"},
    })
    qid = str(created["question"]["questionId"])

    service.answer(qid, {
        "sessionId": "session-retry-no-answer",
        "projectRoot": "E:/unity/TestAvatar",
        "answer": "Package: E:/Packages/Creator/CityNoir.",
    })
    with pytest.raises(AgentQuestionServiceError, match="Resubmit the original answer"):
        service.answer(qid, {"sessionId": "session-retry-no-answer", "projectRoot": "E:/unity/TestAvatar"})

    assert len(continuation_prompts) == 1
