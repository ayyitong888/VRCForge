from agent_runtime_event_projection import project_runtime_turn_event


def test_runtime_turn_projection_preserves_terminal_time_across_reprojection() -> None:
    payload = {
        "continuationSource": "foreground_completed",
        "sessionId": "session",
        "turnId": "turn",
        "completedAt": "2026-10-22T22:05:23.405718+00:00",
        "plan": {"nextStep": "done"},
        "timeline": [{"kind": "assistant", "timestamp": "2026-10-22T22:05:10Z"}],
    }
    projected = project_runtime_turn_event(payload)
    assert projected["completedAt"] == payload["completedAt"]
    assert project_runtime_turn_event(projected)["completedAt"] == payload["completedAt"]


def test_runtime_turn_projection_never_invents_completion_time() -> None:
    payload = {
        "continuationSource": "question_answered",
        "sessionId": "session",
        "turnId": "turn",
        "createdAt": "2026-10-22T22:05:23Z",
        "updatedAt": "2026-10-22T22:05:24Z",
        "plan": {"nextStep": "needs_user_action"},
        "timeline": [{"kind": "assistant", "timestamp": "2026-10-22T22:05:10Z"}],
    }
    assert "completedAt" not in project_runtime_turn_event(payload)


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


def test_runtime_turn_projection_preserves_bounded_context_usage() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "question_answered",
            "sessionId": "owner-session",
            "turnId": "question-terminal",
            "contextUsage": {
                "schema": "vrcforge.context_usage.v1",
                "exact": True,
                "provider": "chatgpt",
                "model": "gpt-6.1-sol",
                "inputTokens": 44773,
                "outputTokens": 148,
                "totalTokens": 44921,
                "requestCount": 3,
                "priorRequestCount": 20,
                "cacheReadTokens": 13824,
                "cacheUsageComplete": True,
                "coverage": {
                    "planner": {"requestCount": 2, "reportedRequestCount": 2, "complete": True},
                    "compaction": {"requestCount": 3, "complete": False, "unknown": "drop"},
                },
                "cacheCoverage": {
                    "requestCount": 3,
                    "read": {"requestCount": 3, "reportedRequestCount": 2, "complete": False},
                    "write": {"requestCount": 3, "reportedRequestCount": 3, "complete": True},
                },
                "inputTokens": {"wrong": "type"},
                "secret": "must-not-cross",
            },
            "plan": {"reply": "蓝色", "nextStep": "done"},
        }
    )

    assert projected is not None
    assert projected["contextUsage"]["totalTokens"] == 44921
    assert projected["contextUsage"]["priorRequestCount"] == 20
    assert projected["contextUsage"]["cacheReadTokens"] == 13824
    assert projected["contextUsage"]["coverage"]["planner"]["complete"] is True
    assert projected["contextUsage"]["coverage"]["planner"]["reportedRequestCount"] == 2
    assert projected["contextUsage"]["coverage"]["compaction"] == {"requestCount": 3, "complete": False}
    assert projected["contextUsage"]["cacheCoverage"]["requestCount"] == 3
    assert projected["contextUsage"]["cacheCoverage"]["read"]["complete"] is False
    assert "inputTokens" not in projected["contextUsage"]
    assert "secret" not in projected["contextUsage"]


def test_runtime_turn_projection_rejects_non_shell_or_unowned_events() -> None:
    assert project_runtime_turn_event({"continuationSource": "approval", "sessionId": "s", "turnId": "t"}) is None
    assert project_runtime_turn_event({"continuationSource": "shell_process_finished", "turnId": "t"}) is None


def test_approval_runtime_turn_uses_the_same_bounded_owner_projection() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "approval_finished",
            "sessionId": "owner-session",
            "turnId": "approval-terminal",
            "execution": {"private": "must-not-cross"},
            "plan": {
                "reply": "done",
                "nextStep": "done",
                "taskCompletion": {"status": "completed", "taskId": "task-1"},
            },
        }
    )

    assert projected is not None
    assert projected["continuationSource"] == "approval_finished"
    assert projected["plan"]["taskCompletion"]["taskId"] == "task-1"
    assert "execution" not in projected


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


def test_runtime_turn_projection_can_carry_only_the_safe_harness_receipt() -> None:
    receipt = {
        "schema": "vrcforge.agent_harness_journey_receipt.v1",
        "receiptId": "receipt-1",
        "signature": "a" * 64,
        "journey": {"schema": "vrcforge.agent_harness_journey.v1"},
    }
    projected = project_runtime_turn_event(
        {
            "continuationSource": "shell_process_finished",
            "sessionId": "owner-session",
            "turnId": "terminal-turn",
            "harnessJourneyReceipt": receipt,
            "result": {"stdout": "private"},
            "plan": {"nextStep": "done"},
        }
    )

    assert projected is not None
    assert projected["harnessJourneyReceipt"] == receipt
    assert "result" not in projected


def test_foreground_completion_projection_preserves_full_reply_and_safe_timeline() -> None:
    reply = "最终答复 " + ("x" * 12000)
    projected = project_runtime_turn_event(
        {
            "continuationSource": "foreground_completed",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "clientTurnId": "client-1",
            "plan": {"summary": "done", "reply": reply, "planner": "runtime", "nextStep": "done"},
            "timeline": [
                {"id": "plan", "sequence": 0, "timestamp": "2026-09-22T00:00:00Z", "kind": "planner", "payload": {"summary": "private planner summary"}},
                {"id": "final", "sequence": 1, "timestamp": "2026-09-22T00:00:01Z", "kind": "assistant", "payload": {"summary": reply}},
            ],
            "observe": {"secret": "drop"},
        }
    )
    assert projected is not None
    assert projected["plan"]["reply"] == reply
    assert projected["timeline"][0]["payload"]["summary"] == "private planner summary"
    assert projected["timeline"][1]["payload"]["summary"] == reply
    assert "observe" not in projected


def test_runtime_turn_projection_preserves_bounded_retry_terminal_metadata() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "foreground_completed",
            "sessionId": "owner-session",
            "turnId": "retry-terminal",
            "plan": {
                "nextStep": "planner_failed",
                "plannerFailure": {
                    "code": "provider_rate_limited",
                    "phase": "initial",
                    "transportPhase": "first_byte",
                    "retryable": True,
                    "retryAttempts": 5,
                    "retryDecision": "NO_MORE_RETRY",
                    "providerError": {
                        "statusCode": 429,
                        "type": "RateLimitError",
                        "code": "rate_limit_exceeded",
                        "streamStarted": False,
                        "message": "drop",
                        "body": {"headers": {"authorization": "drop"}},
                        "headers": {"x-retry-metadata": "drop"},
                        "stack": "drop",
                    },
                },
            },
        }
    )

    assert projected is not None
    assert projected["plan"]["plannerFailure"] == {
        "code": "provider_rate_limited",
        "phase": "initial",
        "transportPhase": "first_byte",
        "retryable": True,
        "retryAttempts": 5,
        "retryDecision": "NO_MORE_RETRY",
        "providerError": {
            "statusCode": 429,
            "type": "RateLimitError",
            "code": "rate_limit_exceeded",
            "streamStarted": False,
        },
    }


def test_runtime_turn_projection_preserves_started_stream_without_fabricating_retry() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "foreground_completed",
            "sessionId": "owner-session",
            "turnId": "stream-terminal",
            "plan": {
                "nextStep": "planner_failed",
                "plannerFailure": {
                    "code": "provider_request_failed",
                    "phase": "initial",
                    "retryable": False,
                    "providerError": {
                        "statusCode": 200,
                        "type": "StreamError",
                        "code": "stream_failed",
                        "streamStarted": True,
                    },
                },
            },
        }
    )

    assert projected is not None
    failure = projected["plan"]["plannerFailure"]
    assert failure["providerError"] == {
        "statusCode": 200,
        "type": "StreamError",
        "code": "stream_failed",
        "streamStarted": True,
    }
    assert "retryDecision" not in failure


def test_runtime_turn_projection_rejects_invalid_failure_fields_and_absent_failure_stays_absent() -> None:
    without_failure = project_runtime_turn_event(
        {
            "continuationSource": "foreground_completed",
            "sessionId": "owner-session",
            "turnId": "no-failure",
            "plan": {"nextStep": "done"},
        }
    )
    assert without_failure is not None
    assert "plannerFailure" not in without_failure["plan"]

    projected = project_runtime_turn_event(
        {
            "continuationSource": "foreground_completed",
            "sessionId": "owner-session",
            "turnId": "malicious-failure",
            "plan": {
                "plannerFailure": {
                    "code": "bad value with spaces",
                    "phase": {"nested": "drop"},
                    "transportPhase": "first_byte\nforged",
                    "retryable": "true",
                    "retryAttempts": -1,
                    "retryDecision": "RETRY_FOREVER",
                    "providerError": {
                        "statusCode": 700,
                        "type": "bad type with spaces",
                        "code": "../secret",
                        "streamStarted": "true",
                        "message": "drop",
                        "body": {"api_key": "drop"},
                        "headers": {"authorization": "drop"},
                        "stack": "drop",
                    },
                },
            },
        }
    )

    assert projected is not None
    assert "plannerFailure" not in projected["plan"]


def test_runtime_turn_projection_ignores_container_retry_decision_without_raising() -> None:
    projected = project_runtime_turn_event(
        {
            "continuationSource": "foreground_completed",
            "sessionId": "owner-session",
            "turnId": "container-failure",
            "plan": {"plannerFailure": {"retryDecision": {"NO_MORE_RETRY": True}}},
        }
    )

    assert projected is not None
    assert "plannerFailure" not in projected["plan"]
