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


@pytest.mark.parametrize("option_id", ["continue", "stop", "custom", "skip", "other"])
def test_retired_tool_loop_question_cannot_dispatch_or_record_an_answer(tmp_path: Path, option_id: str) -> None:
    dispatches: list[tuple[str, dict[str, object], str]] = []
    service = _service(tmp_path, dispatches)
    question = _create(service)

    before = service.log_path.read_bytes()
    assert question["retiredLoopPause"] is True
    assert service.list()["questions"] == []
    with pytest.raises(AgentQuestionServiceError, match="stopped") as error:
        service.answer(str(question["questionId"]), {
            "sessionId": "session-1", "selectedOptionId": option_id, "answer": "anything",
        })
    assert error.value.status_code == 409
    assert dispatches == []
    assert service.log_path.read_bytes() == before


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
