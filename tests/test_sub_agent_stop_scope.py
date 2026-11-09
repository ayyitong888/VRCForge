from sub_agent_tasks import SubAgentTask, SubAgentTaskRegistry


def test_stop_finds_old_active_children_and_only_matching_parent_turn(tmp_path):
    registry = SubAgentTaskRegistry(tmp_path, roles=[], handlers={})
    seed = {"turnId": "turn-a", "clientTurnId": "client-a"}
    tasks = [
        SubAgentTask("old-active", "role", "Child", "Inspect", status="running",
                     parent_session_id="parent", params={"_taskSeed": seed}, created_at="2000"),
        SubAgentTask("pending-delivery", "role", "Child", "Inspect", status="completed",
                     parent_session_id="parent", params={"_taskSeed": seed},
                     parent_continuation_status="pending"),
        SubAgentTask("other-turn", "role", "Child", "Inspect", status="queued",
                     parent_session_id="parent",
                     params={"_taskSeed": {**seed, "clientTurnId": "client-b"}}),
        SubAgentTask("other-session", "role", "Child", "Inspect", status="cancelling",
                     parent_session_id="unrelated", params={"_taskSeed": seed}),
        *(SubAgentTask(f"recent-{i}", "role", "Child", "Inspect", status="completed",
                       parent_session_id="parent", created_at="2026") for i in range(55)),
    ]
    registry._tasks.update((task.id, task) for task in tasks)
    assert all(task["id"] != "old-active" for task in registry.list_tasks()["tasks"])
    assert set(registry.active_parent_task_ids("parent")) == {
        "old-active", "pending-delivery", "other-turn",
    }
    assert set(registry.active_parent_task_ids(
        "parent", parent_turn_id="turn-a", parent_client_turn_id="client-a",
    )) == {"old-active", "pending-delivery"}
    assert registry.active_parent_task_ids("") == []
