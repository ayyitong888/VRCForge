from __future__ import annotations

from agent_task_loop import (
    AgentTaskLoop,
    approval_completion,
    approval_task_context,
    prepare_approval_task_continuation,
)


def _ok_completion(context):
    return approval_completion(
        context,
        raw_result={"ok": True},
        outcome={"status": "ok", "summary": "approved"},
    )


def test_approval_seed_and_resume_keep_complete_history_without_preview_caps() -> None:
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "text": f"entry-{index}-" + ("x" * 2500)}
        for index in range(24)
    ]
    assert len(history) > 20
    assert sum(len(item["text"]) for item in history) > 12_000

    loop = AgentTaskLoop("continue after approval", session_id="approval-history", history=history)
    seed = loop.approval_seed(
        requested_tool="vrcforge_write_asset",
        requested_arguments={"path": "fixture"},
    )
    assert seed["history"] == history

    context = approval_task_context(
        seed,
        tool="vrcforge_write_asset",
        arguments={"path": "fixture"},
    )
    assert context is not None
    assert context["history"] == history

    completion = _ok_completion(context)
    prepared = prepare_approval_task_continuation(
        {"id": "approval-history-1", "taskContext": context},
        {"status": "applied", "taskCompletion": completion},
    )
    assert prepared is not None
    assert prepared["params"]["history"] == history
    assert prepared["taskContinuation"]["context"]["history"] == history

    resumed = AgentTaskLoop.from_approval_context(context, completion)
    assert resumed.history == history
    assert resumed.approval_seed(
        requested_tool="vrcforge_write_asset",
        requested_arguments={"path": "fixture"},
    )["history"] == history

