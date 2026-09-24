from __future__ import annotations

import time
from pathlib import Path

import json
import re
import threading

import pytest

from agent_gateway import AgentGateway
from tests.test_dashboard_server import bind_test_runtime_planner


@pytest.mark.parametrize("with_secret", [False, True])
def test_gateway_question_answer_preserves_unicode_layout_and_redacts_secrets(tmp_path, with_secret):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    question = gateway.create_runtime_question({"question": "继续？", "sessionId": "layout"})["question"]
    text = "先看衣柜\n    保留缩进\n\n不要修改任何配置。"
    expected = text
    if with_secret:
        text += "\napi_key=synthetic-question-secret\n下一行保留。"
        expected += "\napi_key=<redacted>\n下一行保留。"
    result = gateway.questions.answer(question["questionId"], {"sessionId": "layout", "answer": text})
    assert result["question"]["answer"] == expected
    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    restored = restarted.questions.list(session_id="layout", include_answered=True)["questions"][0]
    assert restored["answer"] == expected
    assert expected in restarted.questions._continuation_prompt(restored)
    raw = (tmp_path / "audit" / "agent-questions.jsonl").read_text(encoding="utf-8")
    assert "synthetic-question-secret" not in raw


def test_runtime_question_identity_is_fail_closed(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    result = gateway.create_runtime_question({
        "question": "plain text",
        "sessionId": "caller-session",
        "projectRoot": str(tmp_path),
        "_runtimeTaskSeed": {"taskId": "forged", "sessionId": "forged"},
        "_runtimeTaskId": "forged",
    })
    assert result["question"]["options"] == []
    raw = (tmp_path / "audit" / "agent-questions.jsonl").read_text(encoding="utf-8")
    assert "runtimeTaskSeed" not in raw


def test_untrusted_question_cannot_queue_runtime_continuation(tmp_path: Path, monkeypatch) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    calls: list[str] = []
    monkeypatch.setattr(gateway, "_dispatch_runtime_task_after_question", lambda question_id, seed, prompt: calls.append(question_id) or {"ok": True})
    question = gateway.create_runtime_question({
        "question": "continue?",
        "sessionId": "session-a",
        "projectRoot": str(tmp_path),
        "_runtimeTaskLinkAuthority": object(),
        "_taskSeed": {"taskId": "task-a", "sessionId": "session-a", "projectRoot": str(tmp_path)},
    })["question"]
    # A caller cannot forge the private authority object; this remains a plain Question.
    assert "runtimeTaskSeed" not in (tmp_path / "audit" / "agent-questions.jsonl").read_text(encoding="utf-8")
    assert question["questionId"]

    gateway.questions.answer(question["questionId"], {"sessionId": "session-a", "projectRoot": str(tmp_path), "answer": "yes"})
    gateway.questions.answer(question["questionId"], {"sessionId": "session-a", "projectRoot": str(tmp_path), "answer": "again"})
    assert calls == []


def _scripted_question(tmp_path: Path, *, question_params=None, runtime_params=None, on_resume=None):
    completed = threading.Event()
    results: list[dict] = []

    def on_complete(payload):
        results.append(payload)
        completed.set()

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit", runtime_turn_completed=on_complete)
    gateway.register_tool(
        "vrcforge_ask_user",
        "when-to-use: ask a bounded user question. when-NOT-to-use: do not use otherwise.",
        "plan/preview",
        gateway.create_runtime_question,
    )
    prompts: list[str] = []

    def scripted(prompt: str) -> dict[str, object]:
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"text": json.dumps({
                "action": "skill",
                "skill_tool": "vrcforge_ask_user",
                "skill_params": {"question": "Continue?", **(question_params or {})},
                "summary": "wait for answer",
            })}
        if on_resume is not None:
            on_resume()
        return {"text": json.dumps({
            "action": "reply", "reply": "resumed",
            "completion_claim": {
                "satisfied": True,
                "evidence_action_ids": list(dict.fromkeys(re.findall(r"actionId=(action_[0-9a-f]+)", prompt))),
            },
        })}

    bind_test_runtime_planner(gateway, scripted)
    first = gateway.runtime_message({
        "message": "run scripted question",
        "sessionId": "runtime-session",
        "clientTurnId": "runtime-turn",
        "projectRoot": str(tmp_path),
        **(runtime_params or {}),
    })
    assert first["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id="runtime-session", project_root=str(tmp_path))["questions"][0]
    return gateway, first, question, prompts, results, completed


def _answer(gateway, question, tmp_path):
    return gateway.questions.answer(question["questionId"], {
        "sessionId": "runtime-session",
        "projectRoot": str(tmp_path),
        "answer": "yes",
    })


def test_scripted_runtime_message_question_answer_resumes_same_task(tmp_path: Path) -> None:
    gateway, first, question, prompts, results, completed = _scripted_question(tmp_path)
    _answer(gateway, question, tmp_path)
    assert completed.wait(5), "the actual resumed runtime did not deliver a terminal event"
    assert len(prompts) == 2
    assert "yes" in prompts[1]
    assert results[0]["task"]["taskId"] == first["task"]["taskId"]
    assert results[0]["task"]["objective"] == first["task"]["objective"]
    assert results[0]["sessionId"] == first["sessionId"]
    assert results[0]["continuationSource"] == "question_answered"
    assert results[0]["clientTurnId"] == "runtime-turn:question:" + question["questionId"]
    assert _answer(gateway, question, tmp_path)["idempotent"] is True
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert len(prompts) == 2
    assert len(results) == 1
    public = gateway.questions.list(session_id="runtime-session", include_answered=True)["questions"][0]
    assert public["status"] == "answered"
    assert public["runtimeContinuationStatus"] == "delivered"
    assert "runtimeTaskSeed" not in public
    events = gateway.runtime_runs.list_runtime_continuations(limit=10)
    assert len(events) == 1
    assert events[0]["continuationSource"] == "question_answered"


def test_stopped_question_cannot_resume_after_restart(tmp_path: Path) -> None:
    gateway, _, question, prompts, _, _ = _scripted_question(tmp_path)
    gateway.request_runtime_cancel({"sessionId": "runtime-session", "clientTurnId": "runtime-turn"})
    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    with pytest.raises(Exception, match="stopped"):
        _answer(restarted, question, tmp_path)
    assert len(prompts) == 1
    assert restarted.questions.list(session_id="runtime-session")["questions"] == []


def test_unclaimed_saved_answer_recovers_once(tmp_path: Path) -> None:
    gateway, first, question, _, _, _ = _scripted_question(tmp_path)
    gateway.shutdown_runtime_continuations(5)
    _answer(gateway, question, tmp_path)
    completed = threading.Event()
    results = []
    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit", runtime_turn_completed=lambda payload: (results.append(payload), completed.set()))
    bind_test_runtime_planner(restarted, lambda prompt: {"text": json.dumps({"action": "reply", "reply": "recovered"})})
    restarted.questions.reconcile_runtime_continuations()
    assert completed.wait(5)
    assert restarted.shutdown_runtime_continuations(5)["ok"]
    assert results[0]["task"]["taskId"] == first["task"]["taskId"]
    restarted.questions.reconcile_runtime_continuations()
    assert len(results) == 1


def test_claimed_answer_is_not_replayed_after_restart(tmp_path: Path) -> None:
    gateway, _, question, _, _, _ = _scripted_question(tmp_path)
    gateway.shutdown_runtime_continuations(5)
    _answer(gateway, question, tmp_path)
    assert gateway.questions.claim_runtime_continuation(question["questionId"])
    restarted = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    assert restarted.questions.reconcile_runtime_continuations() == {"queued": 0, "interrupted": 1}
    state = restarted.questions.list(include_answered=True)["questions"][0]
    assert state["status"] == "answered"
    assert state["runtimeContinuationStatus"] == "interrupted"
    assert not restarted.questions.claim_runtime_continuation(question["questionId"])
    events = restarted.runtime_runs.list_runtime_continuations(limit=10)
    assert len(events) == 1
    assert events[0]["plan"]["nextStep"] == "needs_user_action"


def test_goal_question_keeps_goal_owner_and_ignores_model_supplied_task_scope(tmp_path: Path) -> None:
    gateway, _, question, _, _, _ = _scripted_question(
        tmp_path,
        question_params={"sessionId": "forged", "projectRoot": "forged", "_taskSeed": {"taskId": "forged"}},
        runtime_params={"goalDeliveryId": "goal-delivery-owned"},
    )
    assert question["sessionId"] == "runtime-session"
    assert question["projectRoot"] == str(tmp_path)
    assert question["goalDeliveryId"] == "goal-delivery-owned"
    persisted = (tmp_path / "audit" / "agent-questions.jsonl").read_text(encoding="utf-8")
    assert "runtimeTaskSeed" not in persisted


def test_answer_returns_while_continuation_is_running_and_shutdown_owns_it(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    def wait_for_test_release():
        entered.set()
        assert release.wait(5)

    gateway, _, question, _, _, completed = _scripted_question(tmp_path, on_resume=wait_for_test_release)
    try:
        response = _answer(gateway, question, tmp_path)
        assert response["question"]["status"] == "answered"
        assert entered.wait(5)
        assert not completed.is_set()
        assert gateway.shutdown_runtime_continuations(0)["ok"] is False
        with pytest.raises(RuntimeError, match="work is active"):
            gateway.start_runtime_continuations()
    finally:
        release.set()
        assert gateway.shutdown_runtime_continuations(5)["ok"]
