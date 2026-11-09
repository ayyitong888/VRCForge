from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry
from tests.test_native_runtime_gateway import run, setup_gateway
from tests.test_sub_agent_tasks import _wait_for_status


def test_manual_report_reaches_existing_native_conversation_once_after_adoption(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path / "tasks",
        roles=[SubAgentRole("review", "Review", "Read and report.")],
        handlers={"review": lambda _task, _cancel: {
            "ok": True, "summaryText": "Found useful evidence.",
            "finalReport": "The controller uses the verified face parameter.",
        }},
    )
    task = registry.create_task(
        role="review", task="Inspect the face controller", display_name="Reader",
        parent_chat_id="owning-chat", parent_session_id="native-session",
    )["task"]
    task_id = task["id"]
    _wait_for_status(registry, task_id, {"completed"})
    history = [{"role": "agent", "text": "Untrusted carrier text", "subAgentTaskId": task_id}]

    def configure(replies):
        gateway, model, _ = setup_gateway(tmp_path, [
            {"role": "assistant", "content": "Ready."} for _ in range(replies)
        ])
        gateway.bind_runtime_subagent_adoption(lambda key: registry.get_task(key, include_events=False))
        return gateway, model

    gateway, model = configure(4)
    run(gateway, tmp_path, chatId="owning-chat", clientTurnId="before-adoption", history=history)
    assert not any("verified face parameter" in str(message) for message in model.requests[0]["messages"])
    assert registry.merge_task(task_id, decision="adopted", chat_id="owning-chat")["ok"]

    run(gateway, tmp_path, chatId="other-chat", sessionId="other-session", clientTurnId="wrong-owner", history=history)
    assert not any("verified face parameter" in str(message) for message in model.requests[1]["messages"])
    run(gateway, tmp_path, chatId="owning-chat", sessionId="other-session", clientTurnId="wrong-session", history=history)
    assert not any("verified face parameter" in str(message) for message in model.requests[2]["messages"])

    run(gateway, tmp_path, chatId="owning-chat", clientTurnId="after-adoption", history=history * 2)
    messages = model.requests[3]["messages"]
    reports = [message for message in messages if "verified face parameter" in str(message)]
    assert len(reports) == 1 and reports[0]["role"] == "assistant"
    assert "Untrusted carrier text" not in str(messages)
    with gateway.runtime_planner.bind_turn({}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    snapshot = gateway.runtime_sessions.native_conversation("native-session", binding=binding)
    assert snapshot["adoptedSubAgentTaskIds"] == [task_id]
    assert snapshot["messages"][snapshot["activeTurnStart"] - 1] == reports[0]

    reopened, reopened_model = configure(2)
    run(reopened, tmp_path, chatId="owning-chat", clientTurnId="after-restart", history=history)
    assert sum("verified face parameter" in str(message) for message in reopened_model.requests[0]["messages"]) == 1
    state = reopened.runtime_sessions
    snapshot = state.native_conversation("native-session", binding=binding)
    state.replace_native_compacted_history(
        "native-session", binding=binding, expected_snapshot=snapshot, summary="Earlier report retained in summary.",
    )
    run(reopened, tmp_path, chatId="owning-chat", clientTurnId="after-compaction", history=history)
    assert "Earlier report retained" in str(reopened_model.requests[1]["messages"])
    assert "verified face parameter" not in str(reopened_model.requests[1]["messages"])
    registry.shutdown()


def test_automatically_delivered_report_cannot_be_manually_adopted(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path / "tasks",
        roles=[SubAgentRole("review", "Review", "Read and report.")],
        handlers={"review": lambda _task, _cancel: {"ok": True, "summaryText": "Report"}},
    )
    task_id = registry.create_task(
        role="review", task="Inspect", display_name="Reader",
        parent_chat_id="owning-chat", parent_session_id="native-session",
        params={"_taskSeed": {"sessionId": "native-session", "turnId": "original-turn"}},
    )["task"]["id"]
    task = _wait_for_status(registry, task_id, {"completed"})["task"]
    assert task["parentContinuationStatus"] in {"pending", "dispatching", "delivered", "interrupted"}
    for decision in ("adopted", "dismissed"):
        result = registry.merge_task(task_id, decision=decision, chat_id="owning-chat")
        assert not result["ok"]
        assert "parent continuation" in result["error"]
    registry.shutdown()
