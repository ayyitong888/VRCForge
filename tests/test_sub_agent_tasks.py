from __future__ import annotations

import threading
import time

from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


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
