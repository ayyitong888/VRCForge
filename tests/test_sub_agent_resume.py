from __future__ import annotations

import threading
import time

from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


def _wait_for_status(registry: SubAgentTaskRegistry, task_id: str, statuses: set[str]) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = registry.get_task(task_id)["task"]
        if value["status"] in statuses:
            return value
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {statuses}")


def _readonly_role() -> SubAgentRole:
    return SubAgentRole(
        id="custom_reviewer",
        title="Custom reviewer",
        description="A scoped child reviewer.",
        handler="llm_readonly",
        model="role-model",
        system_prompt="Role instructions.",
        max_steps=3,
        tools=("read_text", "search_text"),
    )


def test_resume_reuses_child_history_with_new_turn_and_does_not_redeliver_parent(tmp_path):
    calls: list[dict] = []
    parent_events: list[dict] = []
    resume_entered = threading.Event()
    release_resume = threading.Event()

    def handler(payload: dict, _cancel: threading.Event) -> dict:
        calls.append(payload)
        if payload["isResume"]:
            resume_entered.set()
            release_resume.wait(2)
        return {
            "ok": True,
            "summaryText": "child turn finished",
            "providerRoute": {"provider": "resolved-fixture", "model": "resolved-model"},
            "resumable": True,
        }

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[_readonly_role()],
        handlers={"custom_reviewer": handler},
        task_finished=parent_events.append,
    )
    created = registry.create_task(
        role="custom_reviewer",
        task="First task",
        display_name="Rin",
        parent_session_id="parent-session",
        params={
            "model": "frozen-model",
            "systemPrompt": "frozen instructions",
            "maxSteps": 99,
            "tools": ["search_text", "outside_role_scope"],
            "provider": "fixture-provider",
            "task": "untrusted params task",
            "_taskSeed": {"taskId": "parent-owner"},
        },
    )
    source_id = created["task"]["id"]
    source = _wait_for_status(registry, source_id, {"completed"})
    assert source["tools"] == ["search_text"]
    assert source["maxSteps"] == 12
    assert source["provider"] == "resolved-fixture"
    assert source["model"] == "resolved-model"
    assert source["canResume"] is True
    assert calls[0]["task"] == "First task"
    assert calls[0]["model"] == "frozen-model"
    assert calls[0]["systemPrompt"] == "frozen instructions"
    assert calls[0]["tools"] == ["search_text"]
    assert calls[0]["provider"] == "fixture-provider"
    assert calls[0]["clientTurnId"] == source_id
    assert calls[0]["isResume"] is False

    resumed_response = registry.resume_task(source_id, task="Continue using the saved findings.")
    assert resumed_response["ok"] is True
    resumed = resumed_response["task"]
    assert resumed["id"] != source_id
    assert resumed["resumeOf"] == source_id
    assert resumed["retryOf"] == ""
    assert resumed["childSessionId"] == source["childSessionId"]
    assert resumed["task"] == "Continue using the saved findings."
    assert resume_entered.wait(1)

    second_payload = calls[1]
    assert second_payload["isResume"] is True
    assert second_payload["clientTurnId"] == resumed["id"]
    assert second_payload["clientTurnId"] != calls[0]["clientTurnId"]
    assert second_payload["task"] == "Continue using the saved findings."
    assert second_payload["childSessionId"] == calls[0]["childSessionId"]
    assert second_payload["model"] == "resolved-model"
    assert second_payload["systemPrompt"] == calls[0]["systemPrompt"]
    assert second_payload["maxSteps"] == calls[0]["maxSteps"]
    assert second_payload["tools"] == calls[0]["tools"]
    assert second_payload["provider"] == "resolved-fixture"
    assert "_taskSeed" not in second_payload

    duplicate = registry.resume_task(source_id, task="Do not overlap the current child turn.")
    assert duplicate["ok"] is False
    assert "active turn" in duplicate["error"]
    assert registry.get_task(source_id)["task"]["canResume"] is False
    assert len(calls) == 2
    release_resume.set()
    final_resumed = _wait_for_status(registry, resumed["id"], {"completed"})
    assert final_resumed["canResume"] is True
    assert len(parent_events) == 1
    assert parent_events[0]["subAgentTaskId"] == source_id

    # Reopening the registry restores the lineage and same child ID; another
    # explicit resume starts one more unique host-owned turn in that session.
    reopened = SubAgentTaskRegistry(
        tmp_path,
        roles=[_readonly_role()],
        handlers={"custom_reviewer": handler},
        task_finished=parent_events.append,
    )
    restored = reopened.get_task(resumed["id"])["task"]
    assert restored["resumeOf"] == source_id
    assert restored["childSessionId"] == source["childSessionId"]
    assert restored["provider"] == "resolved-fixture"
    assert restored["model"] == "resolved-model"
    third = reopened.resume_task(resumed["id"], task="Continue after reopening the registry.")
    assert third["ok"] is True
    third_task = _wait_for_status(reopened, third["task"]["id"], {"completed"})
    assert third_task["childSessionId"] == source["childSessionId"]
    assert third_task["resumeOf"] == resumed["id"]
    assert calls[2]["clientTurnId"] == third["task"]["id"]
    assert calls[2]["task"] == "Continue after reopening the registry."
    assert len(parent_events) == 1


def test_retry_stays_a_fresh_task_and_resume_rejects_legacy_handlers(tmp_path):
    calls: list[dict] = []

    def handler(payload: dict, _cancel: threading.Event) -> dict:
        calls.append(payload)
        return {"ok": True, "summaryText": "done"}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[
            _readonly_role(),
            SubAgentRole("legacy", "Legacy", "Fixed handler."),
        ],
        handlers={"custom_reviewer": handler, "legacy": handler},
    )
    source_id = registry.create_task(
        role="custom_reviewer", task="Review", display_name="Rin"
    )["task"]["id"]
    source = _wait_for_status(registry, source_id, {"completed"})
    retried = registry.retry_task(source_id)
    assert retried["ok"] is True
    retry = retried["task"]
    assert retry["id"] != source_id
    assert retry["retryOf"] == source_id
    assert retry["resumeOf"] == ""
    assert retry["childSessionId"] != source["childSessionId"]
    _wait_for_status(registry, retry["id"], {"completed"})

    legacy_id = registry.create_task(role="legacy", task="Inspect", display_name="Rin")["task"]["id"]
    _wait_for_status(registry, legacy_id, {"completed"})
    assert registry.get_task(legacy_id)["task"]["canResume"] is False
    rejected = registry.resume_task(legacy_id, task="Resume legacy history.")
    assert rejected["ok"] is False
    assert "does not support history resume" in rejected["error"]


def test_child_without_native_history_is_not_advertised_or_resumed(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[_readonly_role()],
        handlers={"custom_reviewer": lambda _payload, _cancel: {
            "ok": True, "summaryText": "legacy route has no durable native history", "resumable": False,
        }},
    )
    source_id = registry.create_task(
        role="custom_reviewer", task="Review", display_name="Rin"
    )["task"]["id"]
    source = _wait_for_status(registry, source_id, {"completed"})
    assert source["canResume"] is False
    rejected = registry.resume_task(source_id, task="Continue")
    assert rejected["ok"] is False
    assert "no resumable native history" in rejected["error"]


def test_resume_uses_only_the_fresh_trusted_parent_seed(tmp_path):
    calls: list[dict] = []
    parent_events: list[dict] = []

    def handler(payload: dict, _cancel: threading.Event) -> dict:
        calls.append(payload)
        return {"ok": True, "summaryText": "done", "resumable": True}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[_readonly_role()],
        handlers={"custom_reviewer": handler},
        task_finished=parent_events.append,
    )
    old_seed = {"sessionId": "parent-session", "clientTurnId": "old-parent-turn"}
    source_id = registry.create_task(
        role="custom_reviewer", task="Original", display_name="Rin",
        parent_session_id="parent-session", params={"_taskSeed": old_seed},
    )["task"]["id"]
    _wait_for_status(registry, source_id, {"completed"})

    wrong_owner = registry.resume_task(
        source_id, task="Must not cross owners.",
        task_seed={"sessionId": "another-parent", "clientTurnId": "foreign-turn"},
    )
    assert wrong_owner["ok"] is False
    assert "different parent session" in wrong_owner["error"]

    fresh_seed = {"sessionId": "parent-session", "clientTurnId": "new-parent-turn"}
    resumed = registry.resume_task(source_id, task="Continue", task_seed=fresh_seed)
    assert resumed["ok"] is True
    _wait_for_status(registry, resumed["task"]["id"], {"completed"})

    deadline = time.monotonic() + 1
    while len(parent_events) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls[0]["_taskSeed"] == old_seed
    assert calls[1]["_taskSeed"] == fresh_seed
    assert [event["taskSeed"] for event in parent_events] == [old_seed, fresh_seed]
