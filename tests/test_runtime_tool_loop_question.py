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


def _service(root: Path, dispatches: list[tuple[str, dict[str, object], str]]) -> AgentQuestionService:
    return AgentQuestionService(
        AgentQuestionPersistence(AgentQuestionPersistencePorts(
            log_path=lambda: root / "questions.jsonl",
            shared_state_lock=threading.RLock(),
            redact=lambda value: value,
        )),
        AgentQuestionScopePorts(
            normalize_path=lambda value: value.casefold(),
            summarize=lambda value, limit: " ".join(value.split())[:limit],
            redact_goal_persistence=lambda value: value,
        ),
        GoalQuestionResolutionPort(resolve=lambda _question_id, _prompt: None),
        RuntimeQuestionResolutionPort(
            resolve=lambda question_id, seed, prompt: (
                dispatches.append((question_id, dict(seed), prompt)) or {"status": "queued"}
            ),
        ),
    )


def _create(service: AgentQuestionService) -> dict[str, object]:
    return service.create({
        "sourceTool": "runtime_tool_loop",
        "kind": "decision",
        "blocking": True,
        "question": "Three rounds repeated the same read. Continue or stop?\nRecent call: read_tool_result",
        "options": [
            {"id": "continue", "label": "Continue", "value": "Continue"},
            {"id": "stop", "label": "Stop", "value": "Stop"},
        ],
        "sessionId": "session-1",
        "_runtimeTaskSeed": {
            "taskId": "task-1",
            "sessionId": "session-1",
            "clientTurnId": "turn-1",
            "_toolLoopPause": True,
        },
    })["question"]


def test_tool_loop_continue_dispatches_existing_task_seed(tmp_path: Path) -> None:
    dispatches: list[tuple[str, dict[str, object], str]] = []
    service = _service(tmp_path, dispatches)
    question = _create(service)

    answered = service.answer(str(question["questionId"]), {
        "sessionId": "session-1", "selectedOptionId": "continue",
    })

    assert answered["runtimeContinuation"]["status"] == "queued"
    assert len(dispatches) == 1
    question_id, seed, prompt = dispatches[0]
    assert question_id == question["questionId"]
    assert seed["_toolLoopPause"] is True
    assert seed["_authenticatedUserAnswer"] == "Continue"
    assert "same read" in prompt
    assert answered["question"]["runtimeContinuationStatus"] == "queued"


def test_tool_loop_stop_settles_without_dispatch(tmp_path: Path) -> None:
    dispatches: list[tuple[str, dict[str, object], str]] = []
    service = _service(tmp_path, dispatches)
    question = _create(service)

    stopped = service.answer(str(question["questionId"]), {
        "sessionId": "session-1", "selectedOptionId": "stop",
    })

    assert stopped["question"]["status"] == "answered"
    assert stopped["question"]["runtimeContinuationStatus"] == "settled_without_dispatch"
    assert dispatches == []


@pytest.mark.parametrize("option_id", ["custom", "skip", "other"])
def test_tool_loop_rejects_custom_skip_and_unlisted_answers(tmp_path: Path, option_id: str) -> None:
    service = _service(tmp_path, [])
    question = _create(service)

    with pytest.raises(AgentQuestionServiceError, match="Choose Continue or Stop"):
        service.answer(str(question["questionId"]), {
            "sessionId": "session-1", "selectedOptionId": option_id, "answer": "anything",
        })


@pytest.mark.parametrize("seed", [None, {"_toolLoopPause": False}, {"taskId": "ordinary"}])
def test_runtime_tool_loop_source_requires_owned_pause_seed(tmp_path: Path, seed: object) -> None:
    service = _service(tmp_path, [])
    params = {
        "sourceTool": "runtime_tool_loop", "question": "Continue or stop?",
        "options": [
            {"id": "continue", "label": "Continue"},
            {"id": "stop", "label": "Stop"},
        ],
    }
    if seed is not None:
        params["_runtimeTaskSeed"] = seed
    with pytest.raises(AgentQuestionServiceError, match="owned pause seed"):
        service.create(params)


def test_ordinary_question_keeps_custom_reply_and_skip_behavior(tmp_path: Path) -> None:
    dispatches: list[tuple[str, dict[str, object], str]] = []
    service = _service(tmp_path, dispatches)
    question = service.create({"question": "Ordinary question?", "sessionId": "session-1"})["question"]
    answer = service.answer(str(question["questionId"]), {
        "sessionId": "session-1", "selectedOptionId": "custom", "answer": "A free-form answer",
    })
    assert answer["question"]["status"] == "answered"
    assert answer["question"]["answer"] == "A free-form answer"
