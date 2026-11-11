from __future__ import annotations

import asyncio
import pytest

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
    assert result["question"]["status"] == "answered"
    assert result["question"]["attachments"][0]["dataUrl"] == attachment["dataUrl"]
    assert broadcasts[-1][0] == "agentQuestions"
    another_question = gateway.call_user({
        "question": "Inspect the updated preview", "kind": "visual_checkpoint",
        "sessionId": "fixture-chat", "projectRoot": str(tmp_path),
    })["question"]
    result = asyncio.run(server.app_answer_agent_question(another_question["questionId"],
        AgentQuestionAnswerRequest.model_validate({
            "answer": "Synthetic fixture passed", "resolution": "pass",
            "sessionId": "fixture-chat", "projectRoot": str(tmp_path),
        })))
    assert result["question"]["status"] == "answered"
    assert result["question"]["resolution"] == "pass"


@pytest.mark.parametrize(("choice", "resolution"), [
    ("later", "later"), ("uncertain", "uncertain"), ("skip", ""),
    ("fail", "fail"), ("custom", ""), ("pass", "pass"),
])
def test_visual_answer_route_sends_each_non_skip_choice_after_origin_returns(tmp_path, monkeypatch, choice, resolution):
    from tests.test_agent_question_runtime_continuation import _scripted_question
    gateway, first, question, prompts, results, completed = _scripted_question(
        tmp_path, question_params={
            "kind": "visual_checkpoint", "sourceTool": "call_user",
            "options": [{"id": option, "label": option.title(), "resolution": option}
                        for option in ("pass", "fail", "uncertain", "later")],
        },
    )
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)

    async def ignore_broadcast(_name, _payload):
        pass

    monkeypatch.setattr(server.EVENT_BUS, "broadcast", ignore_broadcast)
    gateway.replace_agent_progress({
        "sessionId": "runtime-session", "projectRoot": str(tmp_path),
        "items": [{"id": "review", "title": "Confirm appearance", "completionChecks": [{
            "tool": "vrcforge_ask_user", "result": {"question": question["question"], "resolution": "pass"},
        }]}],
    })
    text = "请检查这件衣服的贴合情况" if choice == "custom" else "" if choice == "skip" else choice.title()
    request = AgentQuestionAnswerRequest.model_validate({
        "answer": text, "selectedOptionId": choice, "sessionId": "runtime-session",
        "projectRoot": str(tmp_path),
    })
    try:
        result = asyncio.run(server.app_answer_agent_question(question["questionId"], request))
        assert result["question"]["status"] == "answered"
        assert result["question"]["resolution"] == resolution
        assert result["question"]["selectedOptionId"] == choice
        assert result["question"]["answer"] == text
        if choice == "skip":
            assert result["runtimeContinuation"]["status"] == "settled_without_dispatch"
            assert not completed.is_set()
            assert results == []
        else:
            assert result["runtimeContinuation"]["status"] == "queued"
            assert completed.wait(5)
            assert results[0]["sessionId"] == first["sessionId"]
            assert results[0]["turnId"] != first["turnId"]
            assert text in prompts[1]
        assert gateway.questions.list(session_id="runtime-session")["questions"] == []
        [todo] = gateway.list_agent_progress(session_id="runtime-session")["items"]
        assert todo["status"] == ("completed" if choice == "pass" else "pending")
        repeated = asyncio.run(server.app_answer_agent_question(question["questionId"], request))
        assert repeated["idempotent"] is True
    finally:
        assert gateway.shutdown_runtime_continuations(5)["ok"]
    if choice == "skip":
        assert len(prompts) == 1
    else:
        assert len(results) == 1, "repeating the answer must not start a second turn"
