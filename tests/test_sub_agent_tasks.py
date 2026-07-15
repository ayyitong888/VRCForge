from __future__ import annotations

import json
import threading
import time

from sub_agent_tasks import SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT, SubAgentRole, SubAgentTaskRegistry


def test_sub_agent_registry_runs_records_and_retries(tmp_path):
    calls: list[dict[str, object]] = []

    def handler(payload: dict[str, object], cancel_event: threading.Event) -> dict[str, object]:
        calls.append(payload)
        assert not cancel_event.is_set()
        return {"ok": True, "summaryText": f"indexed {payload.get('projectPath')}"}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": handler},
    )
    created = registry.create_task(role="project_index_review", task="scan", display_name="Manuka", project_path="ProjectA")
    task_id = created["task"]["id"]

    deadline = time.time() + 3
    payload = registry.get_task(task_id)
    while payload["task"]["status"] not in {"completed", "failed"} and time.time() < deadline:
        time.sleep(0.02)
        payload = registry.get_task(task_id)

    assert payload["task"]["status"] == "completed"
    assert payload["task"]["summary"] == "indexed ProjectA"
    assert payload["task"]["displayName"] == "Manuka"
    assert payload["task"]["events"]
    assert calls and calls[0]["projectPath"] == "ProjectA"

    retried = registry.retry_task(task_id, display_name="Kikyo")
    assert retried["ok"] is True
    assert retried["task"]["displayName"] == "Kikyo"


def test_sub_agent_registry_cancel_sets_event(tmp_path):
    entered = threading.Event()
    released = threading.Event()

    def handler(_payload: dict[str, object], cancel_event: threading.Event) -> dict[str, object]:
        entered.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        released.set()
        return {"ok": True, "summaryText": "should not complete"}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("validation_triage", "Validation", "Read-only validation.")],
        handlers={"validation_triage": handler},
    )
    created = registry.create_task(role="validation_triage", task="validate", display_name="Rindo")
    task_id = created["task"]["id"]
    assert entered.wait(1)

    cancelled = registry.cancel_task(task_id)
    assert cancelled["ok"] is True
    assert cancelled["task"]["cancelRequested"] is True
    assert released.wait(1)

    deadline = time.time() + 3
    payload = registry.get_task(task_id)
    while payload["task"]["status"] not in {"cancelled", "failed"} and time.time() < deadline:
        time.sleep(0.02)
        payload = registry.get_task(task_id)

    assert payload["task"]["status"] == "cancelled"


def _wait_for_status(registry: SubAgentTaskRegistry, task_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    payload = registry.get_task(task_id)
    while payload["task"]["status"] not in statuses and time.time() < deadline:
        time.sleep(0.02)
        payload = registry.get_task(task_id)
    return payload


def test_merge_task_adopted_records_terminal_decision_and_event(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True, "summaryText": "done"}},
    )
    created = registry.create_task(
        role="project_index_review",
        task="scan",
        display_name="Manuka",
        parent_chat_id="chat-1",
    )
    task_id = created["task"]["id"]
    payload = _wait_for_status(registry, task_id, {"completed", "failed"})
    assert payload["task"]["status"] == "completed"
    assert payload["task"]["mergeDecision"] == ""

    merged = registry.merge_task(task_id, decision="adopted", chat_id="chat-1")
    assert merged["ok"] is True
    assert merged["task"]["mergeDecision"] == "adopted"
    assert merged["task"]["mergedChatId"] == "chat-1"
    assert merged["task"]["mergedAt"]
    merge_events = [event for event in registry.recent_events() if event.get("event") == "merged"]
    assert merge_events and merge_events[-1]["data"]["decision"] == "adopted"
    assert merge_events[-1]["data"]["chatId"] == "chat-1"

    # Repeating the same decision is idempotent; a conflicting decision is rejected.
    replay = registry.merge_task(task_id, decision="adopted", chat_id="chat-1")
    assert replay["ok"] is True
    assert replay["message"] == "task already merged"
    assert replay["task"]["mergeDecision"] == "adopted"
    assert replay["task"]["mergedChatId"] == "chat-1"
    conflict = registry.merge_task(task_id, decision="dismissed", chat_id="chat-1")
    assert conflict["ok"] is False
    assert "already adopted" in conflict["error"]
    assert len([event for event in registry.recent_events() if event.get("event") == "merged"]) == 1


def test_merge_task_rejects_non_completed_unknown_and_bad_decision(tmp_path):
    entered = threading.Event()

    def handler(_payload: dict[str, object], cancel_event: threading.Event) -> dict[str, object]:
        entered.set()
        cancel_event.wait(3)
        return {"ok": True}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("validation_triage", "Validation", "Read-only validation.")],
        handlers={"validation_triage": handler},
    )
    created = registry.create_task(role="validation_triage", task="validate", display_name="Rindo")
    task_id = created["task"]["id"]
    assert entered.wait(1)

    running_merge = registry.merge_task(task_id, decision="adopted")
    assert running_merge["ok"] is False
    assert "only completed" in running_merge["error"]

    missing = registry.merge_task("sub_missing", decision="adopted")
    assert missing["ok"] is False
    assert "not found" in missing["error"]

    bad_decision = registry.merge_task(task_id, decision="archived")
    assert bad_decision["ok"] is False
    assert "adopted or dismissed" in bad_decision["error"]

    registry.cancel_task(task_id)
    payload = _wait_for_status(registry, task_id, {"cancelled", "failed"})
    assert payload["task"]["status"] == "cancelled"
    cancelled_merge = registry.merge_task(task_id, decision="dismissed")
    assert cancelled_merge["ok"] is False
    assert "only completed" in cancelled_merge["error"]


def test_merge_task_dismissed_keeps_task_serialized_fields(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True, "summaryText": "done"}},
    )
    created = registry.create_task(
        role="project_index_review",
        task="scan",
        display_name="Kikyo",
        parent_chat_id="chat-2",
    )
    task_id = created["task"]["id"]
    payload = _wait_for_status(registry, task_id, {"completed", "failed"})
    assert payload["task"]["status"] == "completed"

    merged = registry.merge_task(task_id, decision="dismissed")
    assert merged["ok"] is True
    assert merged["task"]["mergeDecision"] == "dismissed"
    assert merged["task"]["mergedChatId"] == "chat-2"
    listed = registry.list_tasks()["tasks"]
    target = next(item for item in listed if item["id"] == task_id)
    assert target["mergeDecision"] == "dismissed"
    assert target["mergedAt"]


def test_sub_agent_registry_clamps_configured_concurrency_to_hard_limit(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
        max_concurrent=99,
    )

    assert registry.max_concurrent == SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT
    assert registry.list_tasks()["maxConcurrent"] == SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT


def test_completed_task_and_result_rehydrate_with_immutable_owner(tmp_path):
    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    handlers = {
        "project_index_review": lambda _payload, _cancel_event: {
            "ok": True,
            "summaryText": "durable result",
            "details": {"finding": "kept"},
        }
    }
    registry = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    created = registry.create_task(
        role="project_index_review",
        task="scan",
        display_name="Manuka",
        parent_chat_id="chat-a",
        parent_session_id="session-a",
        project_path="ProjectA",
    )
    task_id = created["task"]["id"]
    completed = _wait_for_status(registry, task_id, {"completed", "failed"})["task"]
    assert completed["status"] == "completed"
    assert completed["handoffStatus"] == "handoff_pending"
    assert completed["resultAvailable"] is True

    reopened = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    restored = reopened.get_task(task_id)["task"]
    assert restored["status"] == "completed"
    assert restored["result"]["details"] == {"finding": "kept"}
    assert restored["parentChatId"] == "chat-a"
    assert restored["parentSessionId"] == "session-a"
    assert restored["projectPath"] == "ProjectA"
    assert restored["revision"] == completed["revision"]

    wrong_chat = reopened.merge_task(task_id, decision="adopted", chat_id="chat-b")
    assert wrong_chat["ok"] is False
    assert "does not match" in wrong_chat["error"]


def test_retry_is_new_attempt_and_merge_revision_is_idempotent(tmp_path):
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True, "summaryText": "done"}},
    )
    created = registry.create_task(
        role="project_index_review",
        task="scan",
        display_name="Kikyo",
        parent_chat_id="chat-1",
        parent_session_id="session-1",
        project_path="ProjectA",
    )
    original_id = created["task"]["id"]
    original = _wait_for_status(registry, original_id, {"completed", "failed"})["task"]
    stale = registry.merge_task(
        original_id,
        decision="adopted",
        chat_id="chat-1",
        expected_revision=original["revision"] - 1,
    )
    assert stale["ok"] is False
    assert stale["currentRevision"] == original["revision"]
    merged = registry.merge_task(
        original_id,
        decision="adopted",
        chat_id="chat-1",
        expected_revision=original["revision"],
    )
    assert merged["ok"] is True
    replay = registry.merge_task(
        original_id,
        decision="adopted",
        chat_id="chat-1",
        expected_revision=original["revision"],
    )
    assert replay["ok"] is True
    assert replay["message"] == "task already merged"

    retried = registry.retry_task(original_id)
    assert retried["ok"] is True
    assert retried["task"]["id"] != original_id
    assert retried["task"]["retryOf"] == original_id
    assert retried["task"]["parentChatId"] == "chat-1"
    assert retried["task"]["parentSessionId"] == "session-1"
    assert retried["task"]["projectPath"] == "ProjectA"


def test_cancel_transition_wins_worker_completion_race(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def handler(_payload: dict[str, object], _cancel_event: threading.Event) -> dict[str, object]:
        entered.set()
        release.wait(2)
        return {"ok": True, "summaryText": "too late"}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("validation_triage", "Validation", "Read-only validation.")],
        handlers={"validation_triage": handler},
    )
    task_id = registry.create_task(role="validation_triage", task="validate", display_name="Rindo")["task"]["id"]
    assert entered.wait(1)
    cancelled = registry.cancel_task(task_id)
    assert cancelled["task"]["status"] == "cancelling"
    release.set()
    terminal = _wait_for_status(registry, task_id, {"completed", "cancelled", "failed"})["task"]
    assert terminal["status"] == "cancelled"
    assert terminal["result"] is None
    assert not [event for event in registry.recent_events() if event.get("taskId") == task_id and event.get("event") == "completed"]


def test_restart_reconciles_running_and_cancelling_tasks(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def handler(_payload: dict[str, object], cancel_event: threading.Event) -> dict[str, object]:
        entered.set()
        while not release.is_set() and not cancel_event.is_set():
            time.sleep(0.01)
        return {"ok": True}

    roles = [SubAgentRole("validation_triage", "Validation", "Read-only validation.")]
    handlers = {"validation_triage": handler}
    first = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    task_id = first.create_task(role="validation_triage", task="validate", display_name="Rindo")["task"]["id"]
    assert entered.wait(1)

    reopened = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    restored = reopened.get_task(task_id)["task"]
    assert restored["status"] == "interrupted"
    assert "process restart" in restored["error"]

    first.cancel_task(task_id)
    release.set()


def test_legacy_terminal_event_without_sidecar_is_marked_unavailable(tmp_path):
    path = tmp_path / "sub-agent-events.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(entry)
            for entry in (
                {
                    "schema": "vrcforge.sub_agent_lifecycle.v1",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "taskId": "sub_legacy",
                    "event": "created",
                    "data": {"role": "project_index_review", "task": "legacy scan"},
                },
                {
                    "schema": "vrcforge.sub_agent_lifecycle.v1",
                    "timestamp": "2026-01-01T00:00:01+00:00",
                    "taskId": "sub_legacy",
                    "event": "completed",
                    "data": {"summary": "legacy done"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
    )
    task = registry.get_task("sub_legacy")["task"]
    assert task["status"] == "completed"
    assert task["result"] is None
    assert task["resultUnavailable"] is True
    assert task["handoffStatus"] == "handoff_pending"


def test_event_append_failure_does_not_mutate_cancel_state(tmp_path, monkeypatch):
    entered = threading.Event()

    def handler(_payload: dict[str, object], cancel_event: threading.Event) -> dict[str, object]:
        entered.set()
        cancel_event.wait(2)
        return {"ok": True}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("validation_triage", "Validation", "Read-only validation.")],
        handlers={"validation_triage": handler},
    )
    task_id = registry.create_task(role="validation_triage", task="validate", display_name="Rindo")["task"]["id"]
    assert entered.wait(1)
    original_append = registry._append_event_locked

    def fail_append(_entry):
        raise OSError("disk full")

    monkeypatch.setattr(registry, "_append_event_locked", fail_append)
    try:
        registry.cancel_task(task_id)
        raise AssertionError("cancel should fail when the lifecycle event cannot be persisted")
    except OSError as exc:
        assert "disk full" in str(exc)
    current = registry.get_task(task_id)["task"]
    assert current["status"] == "running"
    assert current["cancelRequested"] is False

    monkeypatch.setattr(registry, "_append_event_locked", original_append)
    registry.cancel_task(task_id)


def test_new_events_survive_a_crash_truncated_jsonl_tail(tmp_path):
    path = tmp_path / "sub-agent-events.jsonl"
    path.write_text('{"schema":"vrcforge.sub_agent_lifecycle.v2"', encoding="utf-8")
    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    handlers = {"project_index_review": lambda _payload, _cancel_event: {"ok": True, "summaryText": "done"}}
    registry = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    task_id = registry.create_task(
        role="project_index_review",
        task="scan",
        display_name="Manuka",
        parent_chat_id="chat-1",
    )["task"]["id"]
    assert _wait_for_status(registry, task_id, {"completed", "failed"})["task"]["status"] == "completed"

    reopened = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    assert reopened.get_task(task_id)["task"]["status"] == "completed"
