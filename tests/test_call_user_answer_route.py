from __future__ import annotations

import asyncio
import time

from agent_gateway import AgentGateway
from dashboard_api_models import AgentQuestionAnswerRequest
import dashboard_server as server


def test_question_answer_route_preserves_visual_verdict_and_attachment(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    broadcasts = []

    async def broadcast(name, payload):
        broadcasts.append((name, payload))

    monkeypatch.setattr(server.EVENT_BUS, "broadcast", broadcast)
    question = gateway.call_user({
        "question": "Inspect this synthetic test preview", "kind": "visual_checkpoint",
        "sessionId": "fixture-chat", "projectRoot": str(tmp_path),
    })["question"]
    attachment = {
        "id": "fixture-image", "name": "preview.png", "type": "image/png",
        "payloadKind": "data_url",
        "dataUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK1cAAAAASUVORK5CYII=",
    }
    request = AgentQuestionAnswerRequest.model_validate({
        "answer": "The preview needs another check", "attachments": [attachment],
        "sessionId": "fixture-chat", "projectRoot": str(tmp_path),
    })
    result = asyncio.run(server.app_answer_agent_question(question["questionId"], request))
    assert result["question"]["status"] == "pending"
    assert result["question"]["attachments"][0]["dataUrl"] == attachment["dataUrl"]
    assert broadcasts[-1][0] == "agentQuestions"
    result = asyncio.run(server.app_answer_agent_question(question["questionId"],
        AgentQuestionAnswerRequest.model_validate({
            "answer": "Synthetic fixture passed", "resolution": "pass",
            "sessionId": "fixture-chat", "projectRoot": str(tmp_path),
        })))
    assert result["question"]["status"] == "answered"
    assert result["question"]["resolution"] == "pass"


def test_multiple_feedback_turns_keep_visual_gate_until_explicit_verdict(tmp_path):
    from tests.test_agent_question_runtime_continuation import _scripted_question
    gateway, first, question, prompts, results, completed = _scripted_question(
        tmp_path, question_params={"kind": "visual_checkpoint", "sourceTool": "call_user"},
    )
    try:
        for text in ["First detail: inspect the shoes", "Second detail: inspect the thighs"]:
            completed.clear()
            gateway.questions.answer(question["questionId"], {
                "answer": text, "sessionId": "runtime-session", "projectRoot": str(tmp_path),
            })
            assert completed.wait(5)
            assert results[-1]["plan"]["nextStep"] == "needs_user_action"
            assert results[-1]["task"]["status"] != "completed"
            assert gateway.questions.list(session_id="runtime-session")["questions"][0]["status"] == "pending"
            # Wait for the dispatcher's existing ownership to release before another human reply.
            for _ in range(100):
                if not gateway._question_continuations_inflight:
                    break
                time.sleep(.01)
        assert len(prompts) == 3
        assert results[0]["clientTurnId"] != results[1]["clientTurnId"]
        completed.clear()
        gateway.questions.answer(question["questionId"], {
            "answer": "Synthetic fixture passed", "resolution": "pass",
            "sessionId": "runtime-session", "projectRoot": str(tmp_path),
        })
        assert completed.wait(5)
        assert len(prompts) == 4
        assert gateway.questions.list(session_id="runtime-session")["questions"] == []
        assert results[-1]["task"]["taskId"] == first["task"]["taskId"]
    finally:
        assert gateway.shutdown_runtime_continuations(5)["ok"]
