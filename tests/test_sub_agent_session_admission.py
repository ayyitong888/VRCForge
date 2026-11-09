import threading
import time

import pytest

from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


def _registry(tmp_path, handler):
    role = SubAgentRole("review", "Review", "Inspect fixtures.", handler="llm_readonly")
    return SubAgentTaskRegistry(tmp_path, [role], {role.id: handler})


def _initial_task(registry):
    task = registry.create_task(role="review", task="Inspect", display_name="Child")["task"]
    registry._threads[task["id"]].join(3)
    assert registry.get_task(task["id"])["task"]["canResume"]
    return task["id"]


def test_queued_resume_owns_session_and_handler_exception_releases_it(tmp_path, monkeypatch):
    entered = threading.Event()
    allow_start = threading.Event()
    calls = []

    def handler(payload, _cancel):
        calls.append(payload["task"])
        if payload["task"] == "Fails":
            raise ValueError("fixture failure")
        return {"ok": True, "resumable": True}

    registry = _registry(tmp_path, handler)
    source_id = _initial_task(registry)
    run_task = registry._run_task

    def gated_worker(*args):
        entered.set()
        assert allow_start.wait(3)
        run_task(*args)

    monkeypatch.setattr(registry, "_run_task", gated_worker)
    resumed = registry.resume_task(source_id, task="Fails")["task"]
    assert entered.wait(3)
    try:
        assert registry.get_task(resumed["id"])["task"]["status"] == "queued"
        assert not registry.get_task(source_id)["task"]["canResume"]
        assert registry.resume_task(source_id, task="Duplicate") == {
            "ok": False, "error": "this child session already has an active turn.",
        }
    finally:
        allow_start.set()
        registry._threads[resumed["id"]].join(3)
    assert registry.get_task(resumed["id"])["task"]["status"] == "failed"
    assert registry.get_task(source_id)["task"]["canResume"]
    recovered = registry.resume_task(source_id, task="Recover")["task"]
    registry._threads[recovered["id"]].join(3)
    assert calls == ["Inspect", "Fails", "Recover"]


def test_watchdog_holds_session_until_worker_returns(tmp_path):
    allow_exit = threading.Event()

    def handler(payload, _cancel):
        if payload["task"] == "Hung":
            assert allow_exit.wait(4)
        return {"ok": True, "resumable": True}

    registry = _registry(tmp_path, handler)
    initial = registry.create_task(
        role="review", task="Inspect", display_name="Child", params={"timeoutSeconds": 1},
    )["task"]["id"]
    registry._threads[initial].join(3)
    resumed = registry.resume_task(initial, task="Hung")["task"]
    try:
        registry._threads[resumed["id"]].join(3)
        failed = registry.get_task(resumed["id"])["task"]
        assert failed["errorCode"] == "sub_agent_task_timeout"
        assert not registry.get_task(initial)["task"]["canResume"]
    finally:
        allow_exit.set()
    deadline = time.monotonic() + 3
    while not registry.get_task(initial)["task"]["canResume"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert registry.get_task(initial)["task"]["canResume"]


def test_registry_worker_launch_failure_leaves_no_queued_turn_or_owner(tmp_path, monkeypatch):
    registry = _registry(tmp_path, lambda _payload, _cancel: {"ok": True, "resumable": True})
    source_id = _initial_task(registry)
    start = threading.Thread.start

    def fail_registry_launch(worker):
        if worker.name.startswith("vrcforge-sub-agent-"):
            raise RuntimeError("fixture launch failure")
        return start(worker)

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_registry_launch)
        with pytest.raises(RuntimeError, match="fixture launch failure"):
            registry.resume_task(source_id, task="Fails to start")
    assert registry.list_tasks()["count"] == 1
    assert registry.get_task(source_id)["task"]["canResume"]
    recovered = registry.resume_task(source_id, task="Recover")["task"]
    registry._threads[recovered["id"]].join(3)
    assert registry.get_task(recovered["id"])["task"]["status"] == "completed"
