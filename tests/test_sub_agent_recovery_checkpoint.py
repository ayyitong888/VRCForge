"""Compare crash recovery from the same durable sub-agent log."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import sub_agent_tasks
from sub_agent_tasks import SubAgentRole, SubAgentTask, SubAgentTaskRegistry


STAMP = "2026-01-01T00:00:00+00:00"
LOG_NAME = "sub-agent-events.jsonl"
CHECKPOINT_NAME = "sub-agent-recovery.json"
CRASH_EXIT_CODE = 47
TORN_ROW = b'{"schema":"vrcforge.sub_agent_lifecycle.v2","taskId":"torn-row"'
ROLES = [SubAgentRole("project_index_review", "Project", "Read local project index.")]


def _registry(path, callback=None, *, defer=False):
    return SubAgentTaskRegistry(
        path, roles=ROLES, handlers={}, task_finished=callback,
        reconcile_on_init=False, defer_projection_load=defer,
    )


def _write_task(registry, task_id, status, *, result=None):
    task = SubAgentTask(
        id=task_id, role=ROLES[0].id, display_name=task_id, task="scan",
        status=status, parent_chat_id="chat-owner", parent_session_id="session-owner",
        created_at=STAMP, started_at=STAMP, updated_at=STAMP,
        params={"_taskSeed": {"schema": "vrcforge.agent_task_loop.v2", "taskId": f"owner-{task_id}"}},
    )
    with registry._lock:
        registry._commit_task_event_locked(None, task, status, {}, result=result)


def _write_legacy(registry, task_id, event):
    with registry._lock:
        registry._append_event_locked({
            "taskId": task_id, "event": event, "timestamp": STAMP,
            "data": {"task": "legacy task", "summary": "legacy summary", "decision": "adopted"},
        })


def _make_crashed_log(path):
    log = path / LOG_NAME

    def crash_after_dispatch(event):
        assert event["subAgentTaskId"] == "dispatching"
        with log.open("ab") as stream:
            stream.write(TORN_ROW)
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(CRASH_EXIT_CODE)

    registry = _registry(path)
    _write_legacy(registry, "legacy", "completed")
    _write_task(registry, "pending", "completed", result={"ok": True, "summaryText": "retained result"})
    _write_task(registry, "delivered", "completed")
    assert registry.replay_parent_continuations(["delivered"]) == 1
    _write_task(registry, "dispatching", "completed")

    registry = _registry(path, crash_after_dispatch, defer=True)
    assert registry.reconcile_startup(refresh_from_disk=True, replay_parent_continuations=False)
    # These unfinished tasks and the result sidecar belong to the crashing process.
    _write_task(registry, "running", "running")
    _write_task(registry, "orphan-result", "running")
    with registry._lock:
        registry._write_result_sidecar_locked("orphan-result", {"ok": True, "summaryText": "orphan result"}, "orphan result")
    _write_legacy(registry, "legacy", "merged")
    registry.replay_parent_continuations(["dispatching"])
    raise AssertionError("the dispatched callback must terminate the subprocess")


def _run_crash_subprocess(path):
    root = Path(__file__).resolve().parents[1]
    script = (
        "import runpy, sys; from pathlib import Path; "
        "runpy.run_path(sys.argv[1])['_make_crashed_log'](Path(sys.argv[2]))"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, str(Path(__file__).resolve()), str(path)],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == CRASH_EXIT_CODE, completed.stdout + completed.stderr
    assert (path / LOG_NAME).read_bytes().endswith(TORN_ROW)


def _recover(path):
    callbacks = []
    registry = _registry(path, callbacks.append, defer=True)
    assert registry.reconcile_startup(refresh_from_disk=True, replay_parent_continuations=False)
    staged = registry.list_tasks(include_events=True)
    interrupted = registry.interrupted_parent_continuations()
    assert callbacks == []
    assert registry.replay_parent_continuations() == 3
    settled = registry.list_tasks(include_events=True)
    reopened = _registry(path, callbacks.append, defer=True)
    assert reopened.reconcile_startup(refresh_from_disk=True)
    assert reopened.list_tasks(include_events=True) == settled
    return staged, interrupted, callbacks, settled


def test_checkpoint_and_full_replay_agree_after_abrupt_dispatch_exit(tmp_path, monkeypatch):
    incremental = tmp_path / "incremental"
    full = tmp_path / "full"
    _run_crash_subprocess(incremental)
    original = (incremental / LOG_NAME).read_bytes()
    checkpoint = json.loads((incremental / CHECKPOINT_NAME).read_text(encoding="utf-8"))
    offset = checkpoint["offset"]
    assert 0 < offset < len(original) and original[offset - 1:offset] == b"\n"
    assert b'"event": "parent_continuation_dispatching"' in original[offset:]
    shutil.copytree(incremental, full, ignore=shutil.ignore_patterns(CHECKPOINT_NAME))

    seen_legacy_events = {incremental: [], full: []}
    project_legacy = SubAgentTaskRegistry._project_legacy_event

    def track_legacy(self, entry, tasks):
        seen_legacy_events[self.artifact_dir].append(entry["event"])
        return project_legacy(self, entry, tasks)

    monkeypatch.setattr(sub_agent_tasks, "utc_now", lambda: STAMP)
    monkeypatch.setattr(SubAgentTaskRegistry, "_project_legacy_event", track_legacy)
    accelerated = _recover(incremental)
    assert accelerated == _recover(full)
    assert seen_legacy_events == {incremental: ["merged"], full: ["completed", "merged"]}

    staged, interrupted, callbacks, settled = accelerated
    tasks = {task["id"]: task for task in staged["tasks"]}
    assert {key: (task["status"], task["parentContinuationStatus"]) for key, task in tasks.items()} == {
        "pending": ("completed", "pending"),
        "delivered": ("completed", "delivered"),
        "dispatching": ("completed", "interrupted"),
        "running": ("interrupted", "pending"),
        "orphan-result": ("completed", "pending"),
        "legacy": ("completed", ""),
    }
    assert tasks["pending"]["result"]["summaryText"] == "retained result"
    assert tasks["orphan-result"]["result"]["summaryText"] == "orphan result"
    assert tasks["legacy"]["mergeDecision"] == "adopted"
    assert [notice["subAgentTaskId"] for notice in interrupted] == ["dispatching"]
    assert [event["subAgentTaskId"] for event in callbacks] == ["pending", "running", "orphan-result"]
    assert all(task["parentContinuationStatus"] != "pending" for task in settled["tasks"])
    assert (incremental / LOG_NAME).read_bytes() == (full / LOG_NAME).read_bytes()


def test_replaced_log_invalidates_checkpoint_even_at_the_same_size(tmp_path):
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    _write_legacy(_registry(original), "before", "completed")
    _write_legacy(_registry(replacement), "after_", "completed")
    assert _registry(original, defer=True).reconcile_startup(refresh_from_disk=True)
    assert (original / CHECKPOINT_NAME).is_file()
    assert (original / LOG_NAME).stat().st_size == (replacement / LOG_NAME).stat().st_size
    os.replace(replacement / LOG_NAME, original / LOG_NAME)

    reopened = _registry(original, defer=True)
    assert reopened.reconcile_startup(refresh_from_disk=True)
    assert [(task["id"], task["status"]) for task in reopened.list_tasks()["tasks"]] == [("after_", "completed")]


def test_valid_final_row_without_newline_is_projected_but_not_checkpointed(tmp_path):
    _write_legacy(_registry(tmp_path), "before", "completed")
    assert _registry(tmp_path, defer=True).reconcile_startup(refresh_from_disk=True)
    checkpoint = tmp_path / CHECKPOINT_NAME
    offset = json.loads(checkpoint.read_text(encoding="utf-8"))["offset"]
    final_row = json.dumps({"taskId": "no-newline", "event": "failed", "timestamp": STAMP}).encode()
    with (tmp_path / LOG_NAME).open("ab") as stream:
        stream.write(final_row)

    reopened = _registry(tmp_path, defer=True)
    assert reopened.reconcile_startup(refresh_from_disk=True)
    assert reopened.get_task("no-newline")["task"]["status"] == "failed"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["offset"] == offset
