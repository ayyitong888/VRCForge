from agent_runtime_event_projection import project_runtime_turn_event


def test_shell_runtime_turn_projection_is_bounded_and_drops_internal_state() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "shell_process_finished",
            "sessionId": "owner-session",
            "turnId": "terminal-turn",
            "clientTurnId": "client-turn",
            "observe": {"secret": "must-not-cross"},
            "result": {"stdout": "private raw output"},
            "plan": {
                "summary": "finished",
                "reply": "done" * 3000,
                "planner": "runtime",
                "nextStep": "done",
                "taskCompletion": {
                    "status": "completed",
                    "taskId": "task-1",
                    "evidenceActionIds": ["action-1", "action-2", "action-3", "action-4"],
                },
            },
        }
    )

    assert projected is not None
    assert projected["sessionId"] == "owner-session"
    assert len(projected["plan"]["reply"]) == 6000
    assert projected["plan"]["taskCompletion"]["evidenceActionIds"] == [
        "action-1",
        "action-2",
        "action-3",
    ]
    assert "observe" not in projected
    assert "result" not in projected


def test_runtime_turn_projection_rejects_non_shell_or_unowned_events() -> None:
    assert project_runtime_turn_event({"continuationSource": "approval", "sessionId": "s", "turnId": "t"}) is None
    assert project_runtime_turn_event({"continuationSource": "shell_process_finished", "turnId": "t"}) is None


def test_sub_agent_runtime_turn_uses_the_same_bounded_owner_projection() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "sub_agent_finished",
            "sessionId": "owner-session",
            "turnId": "sub-agent-terminal",
            "result": {"private": "must-not-cross"},
            "plan": {"summary": "review complete", "reply": "continue", "nextStep": "done"},
        }
    )
    assert projected is not None
    assert projected["continuationSource"] == "sub_agent_finished"
    assert "result" not in projected
