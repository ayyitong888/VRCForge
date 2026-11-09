from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


def test_manual_retry_keeps_parent_scope_without_replaying_original_callback(tmp_path):
    calls = []
    delivered = []

    def handler(payload, _cancel):
        calls.append(payload)
        return {"ok": True, "summaryText": "Result", "resumable": True}

    role = SubAgentRole("review", "Review", "Inspect fixtures.", handler="llm_readonly")
    registry = SubAgentTaskRegistry(tmp_path, [role], {role.id: handler}, task_finished=delivered.append)
    seed = {"sessionId": "parent", "turnId": "turn", "clientTurnId": "client"}
    source = registry.create_task(
        role=role.id, task="Inspect", display_name="Child",
        parent_chat_id="chat", parent_session_id="parent",
        params={"_taskSeed": seed},
    )["task"]
    registry._threads[source["id"]].join(3)
    retried = registry.retry_task(source["id"])["task"]
    registry._threads[retried["id"]].join(3)

    assert retried["parentSessionId"] == source["parentSessionId"]
    assert retried["parentChatId"] == source["parentChatId"]
    assert retried["childSessionId"] != source["childSessionId"]
    assert retried["retryOf"] == source["id"]
    assert "_taskSeed" not in calls[1]
    assert [event["subAgentTaskId"] for event in delivered] == [source["id"]]
    assert registry.get_task(retried["id"])["task"]["parentContinuationStatus"] == ""
