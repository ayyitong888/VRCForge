from __future__ import annotations

import threading
from pathlib import Path

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
