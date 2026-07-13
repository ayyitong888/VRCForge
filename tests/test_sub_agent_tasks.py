from __future__ import annotations

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
    created = registry.create_task(role="project_index_review", task="scan", display_name="Manuka")
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

    # 终态一旦写入不再改写：重复合并（哪怕换决定）只回放既有状态。
    replay = registry.merge_task(task_id, decision="dismissed", chat_id="chat-2")
    assert replay["ok"] is True
    assert replay["message"] == "task already merged"
    assert replay["task"]["mergeDecision"] == "adopted"
    assert replay["task"]["mergedChatId"] == "chat-1"
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
    created = registry.create_task(role="project_index_review", task="scan", display_name="Kikyo")
    task_id = created["task"]["id"]
    payload = _wait_for_status(registry, task_id, {"completed", "failed"})
    assert payload["task"]["status"] == "completed"

    merged = registry.merge_task(task_id, decision="dismissed")
    assert merged["ok"] is True
    assert merged["task"]["mergeDecision"] == "dismissed"
    assert merged["task"]["mergedChatId"] == ""
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
