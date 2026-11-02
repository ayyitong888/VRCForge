"""Question replies keep local source text but exclude credentials from model handoffs."""
import json
import threading

import pytest

from agent_question_service import (
    AgentQuestionPersistence, AgentQuestionPersistencePorts, AgentQuestionScopePorts,
    AgentQuestionService, GoalQuestionResolutionPort, RuntimeQuestionResolutionPort,
)


ANSWER = (
    "素材：https://example.invalid/author\n"
    "    E:/Packages/Creator/CityNoir\n\n"
    "api_key=fixture-question-api-value\n"
    "Authorization: Bearer fixture-question-bearer-value\n"
    "https://example.invalid/asset?access_token=fixture-query-value&page=2\n"
    "tokenCount=512\n继续，保留身体和面捕。"
)
SECRETS = ("fixture-question-api-value", "fixture-question-bearer-value", "fixture-query-value")


@pytest.mark.parametrize("mode", ["runtime", "goal", "reconcile", "idempotent_goal", "visual_feedback"])
def test_question_handoffs_filter_credentials_without_rewriting_local_source(tmp_path, mode):
    prompts, seeds = [], []

    def runtime(_question, seed, prompt):
        prompts.append(prompt)
        seeds.append(seed)
        return {"status": "queued"}

    service = AgentQuestionService(
        AgentQuestionPersistence(AgentQuestionPersistencePorts(
            log_path=lambda: tmp_path / "questions.jsonl", shared_state_lock=threading.RLock(),
            redact=lambda value: value,
        )),
        AgentQuestionScopePorts(normalize_path=lambda value: value, summarize=lambda value, limit: value[:limit],
                                redact_goal_persistence=lambda value: value),
        GoalQuestionResolutionPort(resolve=lambda _id, prompt: prompts.append(prompt) or {"ok": True}),
        RuntimeQuestionResolutionPort(resolve=runtime),
    )
    params = {"question": "素材在哪里？", "sessionId": "fixture-session"}
    if mode in {"goal", "idempotent_goal"}:
        params["goalDeliveryId"] = "fixture-goal"
    else:
        params["_runtimeTaskSeed"] = {"sessionId": "fixture-session", "taskId": "fixture-task"}
    if mode == "visual_feedback":
        params["kind"] = "visual_checkpoint"
    question = service.create(params)["question"]
    reply = {"answer": ANSWER, "sessionId": "fixture-session"}
    if mode == "visual_feedback":
        reply["resolution"] = "uncertain"
    service.answer(question["questionId"], reply)
    if mode == "reconcile":
        service.reconcile_runtime_continuations()
    if mode == "idempotent_goal":
        service.answer(question["questionId"], reply)

    assert len(prompts) == (2 if mode in {"reconcile", "idempotent_goal"} else 1)
    for prompt in prompts:
        assert not any(secret in prompt for secret in SECRETS)
        assert "素材：https://example.invalid/author\n    E:/Packages/Creator/CityNoir\n\n" in prompt
        assert "&page=2" in prompt
        assert "tokenCount=512\n继续，保留身体和面捕。" in prompt
    for seed in seeds:
        assert not any(secret in json.dumps(seed) for secret in SECRETS)
    # The model-facing correction must not rewrite existing local evidence policy.
    assert all(secret in (tmp_path / "questions.jsonl").read_text(encoding="utf-8") for secret in SECRETS)


@pytest.mark.parametrize("kind", ["decision", "visual_checkpoint"])
def test_answer_reaches_production_planner_model_port_without_credentials(tmp_path, kind):
    from agent_gateway import AgentGateway
    from tests.test_dashboard_server import bind_test_runtime_planner

    prompts = []
    answered_model_call = threading.Event()
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.register_tool(
        "vrcforge_ask_user",
        "when-to-use: request a fixture answer. when-NOT-to-use: do not modify assets.",
        "plan/preview", gateway.create_runtime_question,
    )

    def model(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"text": json.dumps({"action": "skill", "skill_tool": "vrcforge_ask_user",
                                       "skill_params": {"question": "素材在哪里？", "kind": kind}})}
        answered_model_call.set()
        return {"text": json.dumps({"action": "reply", "reply": "Answer received."})}

    bind_test_runtime_planner(gateway, model)
    first = gateway.runtime_message({"message": "Ask where the material is.", "sessionId": "wire-fixture",
                                     "clientTurnId": "wire-client", "projectRoot": str(tmp_path)})
    assert first["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id="wire-fixture")["questions"][0]
    try:
        reply = {"sessionId": "wire-fixture", "answer": ANSWER}
        if kind == "visual_checkpoint":
            reply["resolution"] = "uncertain"
        gateway.questions.answer(question["questionId"], reply)
        assert answered_model_call.wait(10), "the resumed production planner did not reach its model port"
        resumed_input = "\n".join(prompts[1:])
        assert not any(secret in resumed_input for secret in SECRETS)
        assert "https://example.invalid/author" in resumed_input
        assert "E:/Packages/Creator/CityNoir" in resumed_input
        assert "tokenCount=512" in resumed_input
    finally:
        assert gateway.shutdown_runtime_continuations(10)["ok"]
