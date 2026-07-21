from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from background_goal_runtime import RuntimeLaneBudget
from sub_agent_tasks import (
    SUB_AGENT_LOG_SCHEMA,
    SUB_AGENT_MAX_CONCURRENT_HARD_LIMIT,
    SUB_AGENT_RESULT_SCHEMA,
    SubAgentRole,
    SubAgentTaskRegistry,
)


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


def _write_task_projection(tmp_path, task_id: str, status: str) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    snapshot = {
        "id": task_id,
        "role": "project_index_review",
        "displayName": "Manuka",
        "task": "scan",
        "parentChatId": "chat-recovery",
        "parentSessionId": "session-recovery",
        "projectPath": "ProjectA",
        "toolProfile": "read-only",
        "status": status,
        "createdAt": timestamp,
        "startedAt": timestamp if status != "queued" else "",
        "stoppedAt": "",
        "updatedAt": timestamp,
        "cancelRequested": status == "cancelling",
        "summary": "",
        "error": "",
        "eventCount": 2,
        "revision": 2,
        "retryOf": "",
        "handoffStatus": "",
        "handoffAt": "",
        "mergedAt": "",
        "mergedChatId": "",
        "mergeDecision": "",
        "resultAvailable": False,
        "resultUnavailable": False,
        "params": {},
    }
    event = {
        "schema": SUB_AGENT_LOG_SCHEMA,
        "timestamp": timestamp,
        "taskId": task_id,
        "event": "started",
        "revision": 2,
        "data": {},
        "task": snapshot,
    }
    (tmp_path / "sub-agent-events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")


def _write_result_sidecar(tmp_path, task_id: str, payload: dict) -> None:
    result_dir = tmp_path / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / f"{task_id}.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


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


def test_shared_runtime_lane_reserves_interactive_headroom(tmp_path):
    release = threading.Event()

    def handler(_payload: dict[str, object], _cancel_event: threading.Event) -> dict[str, object]:
        release.wait(3)
        return {"ok": True, "summaryText": "done"}

    lanes = RuntimeLaneBudget()
    assert lanes.acquire("background", "goal-a") is True
    assert lanes.acquire("background", "goal-b") is True
    assert lanes.acquire("background", "goal-c") is False
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": handler},
        max_concurrent=5,
        lane_budget=lanes,
    )
    task_ids: list[str] = []
    try:
        for index in range(3):
            created = registry.create_task(
                role="project_index_review",
                task=f"scan {index}",
                display_name=f"Worker {index}",
            )
            task_ids.append(created["task"]["id"])
        assert lanes.snapshot()["total"] == 5
        with pytest.raises(RuntimeError, match="Shared runtime concurrency limit"):
            registry.create_task(
                role="project_index_review",
                task="blocked only at total cap",
                display_name="Worker blocked",
            )

        assert lanes.release("goal-a") is True
        created = registry.create_task(
            role="project_index_review",
            task="interactive slot after one background release",
            display_name="Worker 4",
        )
        task_ids.append(created["task"]["id"])
        assert lanes.snapshot()["total"] == 5
    finally:
        release.set()
        for task_id in task_ids:
            _wait_for_status(registry, task_id, {"completed", "failed", "cancelled"})
        lanes.release("goal-b")

    assert lanes.snapshot()["total"] == 0


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


def test_startup_reconcile_can_be_deferred_and_runs_only_once(tmp_path):
    task_id = "sub_deferred_reconcile"
    _write_task_projection(tmp_path, task_id, "running")
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
        reconcile_on_init=False,
    )

    imported = registry.get_task(task_id)["task"]
    assert imported["status"] == "running"
    assert not [event for event in registry.recent_events() if event.get("event") == "interrupted"]

    assert registry.reconcile_startup() is True
    reconciled = registry.get_task(task_id)["task"]
    assert reconciled["status"] == "interrupted"
    assert registry.reconcile_startup() is False
    replayed = registry.get_task(task_id)["task"]
    assert replayed["revision"] == reconciled["revision"]
    assert len(
        [
            event
            for event in registry.recent_events()
            if event.get("taskId") == task_id and event.get("event") == "interrupted"
        ]
    ) == 1


def test_refresh_from_disk_observes_failed_event_written_after_registry_import(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def failing_handler(_payload, _cancel_event):
        entered.set()
        release.wait(2)
        raise RuntimeError("old owner failed")

    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    old_owner = SubAgentTaskRegistry(tmp_path, roles=roles, handlers={"project_index_review": failing_handler})
    task_id = old_owner.create_task(role="project_index_review", task="scan", display_name="Manuka")["task"]["id"]
    assert entered.wait(1)

    new_owner = SubAgentTaskRegistry(
        tmp_path,
        roles=roles,
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
        reconcile_on_init=False,
    )
    assert new_owner.get_task(task_id)["task"]["status"] == "running"

    release.set()
    assert _wait_for_status(old_owner, task_id, {"failed"})["task"]["status"] == "failed"
    assert new_owner.reconcile_startup(refresh_from_disk=True) is True
    refreshed = new_owner.get_task(task_id)["task"]
    assert refreshed["status"] == "failed"
    assert "old owner failed" in refreshed["error"]
    assert not [
        event
        for event in new_owner.recent_events()
        if event.get("taskId") == task_id and event.get("event") == "interrupted"
    ]


def test_refresh_from_disk_observes_cancelled_event_written_after_registry_import(tmp_path):
    entered = threading.Event()

    def cancellable_handler(_payload, cancel_event):
        entered.set()
        cancel_event.wait(2)
        return {"ok": True}

    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    old_owner = SubAgentTaskRegistry(tmp_path, roles=roles, handlers={"project_index_review": cancellable_handler})
    task_id = old_owner.create_task(role="project_index_review", task="scan", display_name="Manuka")["task"]["id"]
    assert entered.wait(1)

    new_owner = SubAgentTaskRegistry(
        tmp_path,
        roles=roles,
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
        reconcile_on_init=False,
    )
    assert new_owner.get_task(task_id)["task"]["status"] == "running"

    assert old_owner.cancel_task(task_id)["ok"] is True
    assert _wait_for_status(old_owner, task_id, {"cancelled"})["task"]["status"] == "cancelled"
    assert new_owner.reconcile_startup(refresh_from_disk=True) is True
    refreshed = new_owner.get_task(task_id)["task"]
    assert refreshed["status"] == "cancelled"
    assert refreshed["cancelRequested"] is True
    assert not [
        event
        for event in new_owner.recent_events()
        if event.get("taskId") == task_id and event.get("event") == "interrupted"
    ]


def test_refresh_from_disk_is_transactional_on_projection_read_error(tmp_path, monkeypatch):
    task_id = "sub_refresh_io_error"
    _write_task_projection(tmp_path, task_id, "running")
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
        reconcile_on_init=False,
    )
    original_task = registry.get_task(task_id, include_events=False)["task"]
    event_log = registry._event_log_path()
    original_read_text = Path.read_text

    def fail_event_log_read(path: Path, *args, **kwargs):
        if path == event_log:
            raise PermissionError("event log temporarily locked")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_event_log_read)
    with pytest.raises(PermissionError, match="temporarily locked"):
        registry.reconcile_startup(refresh_from_disk=True)

    assert registry.get_task(task_id, include_events=False)["task"] == original_task
    assert registry._startup_reconciled is False


def test_refresh_from_disk_rejects_live_local_workers(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def handler(_payload, _cancel_event):
        entered.set()
        release.wait(2)
        return {"ok": True}

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": handler},
        reconcile_on_init=False,
    )
    task_id = registry.create_task(role="project_index_review", task="scan", display_name="Manuka")["task"]["id"]
    assert entered.wait(1)
    try:
        with pytest.raises(RuntimeError, match="local workers are alive"):
            registry.reconcile_startup(refresh_from_disk=True)
        assert registry.get_task(task_id)["task"]["status"] == "running"
        assert registry._startup_reconciled is False
    finally:
        release.set()
        registry._threads[task_id].join(3)


@pytest.mark.parametrize("active_status", ["queued", "running", "cancelling"])
def test_restart_recovers_valid_orphan_result_sidecar_once(tmp_path, active_status):
    task_id = f"sub_orphan_{active_status}"
    _write_task_projection(tmp_path, task_id, active_status)
    result = {"ok": True, "summaryText": "recovered result", "details": {"finding": "kept"}}
    _write_result_sidecar(
        tmp_path,
        task_id,
        {
            "schema": SUB_AGENT_RESULT_SCHEMA,
            "taskId": task_id,
            "summary": "recovered result",
            "result": result,
        },
    )
    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    handlers = {"project_index_review": lambda _payload, _cancel_event: {"ok": True}}

    reopened = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    restored = reopened.get_task(task_id)["task"]
    assert restored["status"] == "completed"
    assert restored["resultAvailable"] is True
    assert restored["resultUnavailable"] is False
    assert restored["result"] == result
    assert restored["summary"] == "recovered result"
    assert restored["handoffStatus"] == "handoff_pending"
    assert restored["cancelRequested"] is False
    assert restored["error"] == ""
    recovered_events = [
        event
        for event in reopened.recent_events()
        if event.get("taskId") == task_id and event.get("event") == "recovered"
    ]
    assert len(recovered_events) == 1
    assert recovered_events[0]["data"]["previousStatus"] == active_status
    assert recovered_events[0]["data"]["terminalStatus"] == "completed"

    # The recovered full projection is replayable and does not append again.
    recovered_revision = restored["revision"]
    replayed = SubAgentTaskRegistry(tmp_path, roles=roles, handlers=handlers)
    replayed_task = replayed.get_task(task_id)["task"]
    assert replayed_task["status"] == "completed"
    assert replayed_task["result"] == result
    assert replayed_task["revision"] == recovered_revision
    assert len(
        [
            event
            for event in replayed.recent_events()
            if event.get("taskId") == task_id and event.get("event") == "recovered"
        ]
    ) == 1


@pytest.mark.parametrize("invalid_kind", ["schema", "foreign", "result", "json"])
def test_restart_rejects_invalid_or_foreign_orphan_result_sidecar(tmp_path, invalid_kind):
    task_id = f"sub_invalid_{invalid_kind}"
    _write_task_projection(tmp_path, task_id, "running")
    payload = {
        "schema": SUB_AGENT_RESULT_SCHEMA,
        "taskId": task_id,
        "summary": "must not recover",
        "result": {"ok": True},
    }
    if invalid_kind == "schema":
        payload["schema"] = "vrcforge.sub_agent_result.future"
    elif invalid_kind == "foreign":
        payload["taskId"] = "sub_someone_else"
    elif invalid_kind == "result":
        payload["result"] = ["not", "an", "object"]
    if invalid_kind == "json":
        result_dir = tmp_path / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / f"{task_id}.json").write_text('{"schema":', encoding="utf-8")
    else:
        _write_result_sidecar(tmp_path, task_id, payload)

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
    )
    task = registry.get_task(task_id)["task"]
    assert task["status"] == "interrupted"
    assert task["result"] is None
    assert task["resultAvailable"] is False
    assert task["handoffStatus"] == ""
    assert not [
        event
        for event in registry.recent_events()
        if event.get("taskId") == task_id and event.get("event") == "recovered"
    ]


def test_orphan_result_sidecar_does_not_override_existing_terminal_projection(tmp_path):
    task_id = "sub_already_failed"
    _write_task_projection(tmp_path, task_id, "failed")
    _write_result_sidecar(
        tmp_path,
        task_id,
        {
            "schema": SUB_AGENT_RESULT_SCHEMA,
            "taskId": task_id,
            "summary": "stale result",
            "result": {"ok": True},
        },
    )

    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
    )
    task = registry.get_task(task_id)["task"]
    assert task["status"] == "failed"
    assert task["result"] is None
    assert task["resultAvailable"] is False
    assert not [event for event in registry.recent_events() if event.get("event") == "recovered"]


def test_completed_event_append_failure_recovers_the_durable_sidecar(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def handler(_payload, _cancel_event):
        entered.set()
        release.wait(2)
        return {"ok": True, "summaryText": "durable success", "details": {"kept": True}}

    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    registry = SubAgentTaskRegistry(tmp_path, roles=roles, handlers={"project_index_review": handler})
    task_id = registry.create_task(
        role="project_index_review",
        task="scan",
        display_name="Manuka",
        parent_chat_id="chat-append-recovery",
    )["task"]["id"]
    assert entered.wait(1)
    original_append = registry._append_event_locked
    failed_once = False

    def fail_first_completed_append(entry):
        nonlocal failed_once
        if entry.get("event") == "completed" and not failed_once:
            failed_once = True
            raise OSError("transient completed append failure")
        return original_append(entry)

    monkeypatch.setattr(registry, "_append_event_locked", fail_first_completed_append)
    release.set()
    registry._threads[task_id].join(3)

    task = registry.get_task(task_id)["task"]
    assert failed_once is True
    assert task["status"] == "completed"
    assert task["resultAvailable"] is True
    assert task["result"]["details"] == {"kept": True}
    assert task["summary"] == "durable success"
    assert registry._result_path(task_id).exists()
    events = [event for event in registry.recent_events() if event.get("taskId") == task_id]
    assert [event["event"] for event in events] == ["created", "started", "recovered"]
    assert events[-1]["data"]["source"] == "result_sidecar_after_append_error"

    reopened = SubAgentTaskRegistry(
        tmp_path,
        roles=roles,
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
    )
    replayed = reopened.get_task(task_id)["task"]
    assert replayed["status"] == "completed"
    assert replayed["result"]["details"] == {"kept": True}


def test_persistent_completed_event_append_failure_stays_running_until_restart(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def handler(_payload, _cancel_event):
        entered.set()
        release.wait(2)
        return {"ok": True, "summaryText": "recover after restart"}

    roles = [SubAgentRole("project_index_review", "Project", "Read local project index.")]
    registry = SubAgentTaskRegistry(tmp_path, roles=roles, handlers={"project_index_review": handler})
    task_id = registry.create_task(role="project_index_review", task="scan", display_name="Manuka")["task"]["id"]
    assert entered.wait(1)
    original_append = registry._append_event_locked

    def fail_completion_projection(entry):
        if entry.get("event") in {"completed", "recovered"}:
            raise OSError("persistent completed append failure")
        return original_append(entry)

    monkeypatch.setattr(registry, "_append_event_locked", fail_completion_projection)
    release.set()
    registry._threads[task_id].join(3)

    stranded = registry.get_task(task_id)["task"]
    assert stranded["status"] == "running"
    assert stranded["resultAvailable"] is False
    assert registry._result_path(task_id).exists()
    assert not [
        event
        for event in registry.recent_events()
        if event.get("taskId") == task_id and event.get("event") in {"completed", "failed", "recovered"}
    ]

    reopened = SubAgentTaskRegistry(
        tmp_path,
        roles=roles,
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
    )
    recovered = reopened.get_task(task_id)["task"]
    assert recovered["status"] == "completed"
    assert recovered["result"] == {"ok": True, "summaryText": "recover after restart"}
    assert len(
        [
            event
            for event in reopened.recent_events()
            if event.get("taskId") == task_id and event.get("event") == "recovered"
        ]
    ) == 1


def test_transient_orphan_sidecar_read_error_is_retryable(tmp_path, monkeypatch):
    task_id = "sub_transient_sidecar_lock"
    _write_task_projection(tmp_path, task_id, "running")
    _write_result_sidecar(
        tmp_path,
        task_id,
        {
            "schema": SUB_AGENT_RESULT_SCHEMA,
            "taskId": task_id,
            "summary": "kept",
            "result": {"ok": True},
        },
    )
    registry = SubAgentTaskRegistry(
        tmp_path,
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": lambda _payload, _cancel_event: {"ok": True}},
        reconcile_on_init=False,
    )
    original_load = registry._load_result_sidecar_locked

    def fail_locked_sidecar(_task_id):
        raise PermissionError("sidecar temporarily locked")

    monkeypatch.setattr(registry, "_load_result_sidecar_locked", fail_locked_sidecar)
    with pytest.raises(PermissionError, match="temporarily locked"):
        registry.reconcile_startup()
    assert registry.get_task(task_id)["task"]["status"] == "running"

    monkeypatch.setattr(registry, "_load_result_sidecar_locked", original_load)
    assert registry.reconcile_startup() is True
    assert registry.get_task(task_id)["task"]["status"] == "completed"


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
