from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy

import pytest

from tests.test_native_runtime_gateway import call, finish, setup_gateway
from agent_task_loop import prepare_sub_agent_task_continuation


def _start_background_shell(gateway, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use the real gateway continuation path with only the process boundary faked."""

    calls: list[dict[str, object]] = []

    def fake_execute(params, agent_name="desktop-agent", **kwargs):
        calls.append({"params": dict(params), "kwargs": dict(kwargs)})
        return {
            "ok": True,
            "status": "running",
            "sessionId": "shell-async-1",
            "session": {"sessionId": "shell-async-1", "status": "running"},
        }

    monkeypatch.setattr(gateway.shell, "execute", fake_execute)
    first = gateway.runtime_message(
        {
            "message": "Run the background inspection.",
            "sessionId": "native-shell-session",
            "clientTurnId": "native-shell-turn",
            "projectRoot": str(tmp_path),
            "maxAgenticTurns": 5,
        }
    )
    assert first.get("shell", {}).get("status") == "running", first.get("plan")
    assert len(calls) == 1
    task_seed = calls[0]["kwargs"].get("task_context")
    assert isinstance(task_seed, dict)
    assert isinstance(task_seed.get("_nativeConversation"), dict)
    return calls, task_seed


def _finish_shell(gateway, task_seed: dict[str, object], *, cancelled=False, exit_code=0):
    gateway.shell._ports.session_finished(
        {
            "shellSessionId": "shell-async-1",
            "runtimeSessionId": "native-shell-session",
            "turnId": str(task_seed.get("turnId") or ""),
            "clientTurnId": "native-shell-turn",
            "status": "finished",
            "exitCode": exit_code,
            "timedOut": False,
            "cancelled": cancelled,
            "terminationFailed": False,
            "taskSeed": task_seed,
            "result": {"stdout": "background result", "stderr": "", "exitCode": exit_code},
        }
    )


def _ledger_json(gateway) -> str:
    return json.dumps(gateway.runtime_runs.list_runs(limit=100), ensure_ascii=False)


def test_subagent_terminal_projection_preserves_private_native_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _model, _ = setup_gateway(
        tmp_path,
        [
            call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
            call("shell-call", "vrcforge_runtime_action", {
                "action": "shell",
                "shell_command": "Get-ChildItem",
            }),
        ],
    )
    _calls, shell_seed = _start_background_shell(
        gateway,
        tmp_path,
        monkeypatch,
    )
    seed = deepcopy(shell_seed)
    seed.update(
        {
            "requestedTool": "vrcforge_delegate_subagent",
            "requestedKind": "skill",
            "requestedArguments": {"objective": "Inspect the background result."},
            "requestedActionId": "subagent-action-1",
        }
    )
    prepared = prepare_sub_agent_task_continuation(
        seed,
        {
            "parentSessionId": seed["sessionId"],
            "subAgentTaskId": "subagent-1",
            "status": "completed",
            "summary": "Sub-agent completed.",
            "result": {"ok": True},
        },
    )
    assert prepared is not None
    context = prepared["taskContinuation"]["context"]
    assert context["_nativeConversation"] == seed["_nativeConversation"]
    assert "private-fixture-replay" not in json.dumps(prepared["params"])


def test_subagent_parent_resume_settles_original_native_call_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, model, _ = setup_gateway(
        tmp_path,
        [
            call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
            call("shell-call", "vrcforge_runtime_action", {
                "action": "shell",
                "shell_command": "Get-ChildItem",
            }),
            finish,
        ],
    )
    calls, shell_seed = _start_background_shell(gateway, tmp_path, monkeypatch)
    seed = deepcopy(shell_seed)
    seed.update(
        {
            "requestedTool": "vrcforge_delegate_subagent",
            "requestedKind": "skill",
            "requestedArguments": {"objective": "Inspect the background result."},
            "requestedActionId": "subagent-action-1",
        }
    )
    resumed = gateway.resume_runtime_task_after_sub_agent(
        {
            "subAgentTaskId": "subagent-1",
            "parentSessionId": seed["sessionId"],
            "status": "completed",
            "summary": "Sub-agent completed.",
            "result": {"ok": True},
            "taskSeed": seed,
        }
    )
    assert resumed is not None
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert len(calls) == 1
    assert len(model.requests) == 3
    shell_results = [
        item for item in snapshot["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "shell-call"
    ]
    assert len(shell_results) == 1
    assert "private-fixture-replay" not in json.dumps(resumed)


def test_native_shell_completion_resumes_same_call_once_and_keeps_snapshot_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, model, _ = setup_gateway(
        tmp_path,
        [
            call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
            call("shell-call", "vrcforge_runtime_action", {
                "action": "shell",
                "shell_command": "Get-ChildItem",
            }),
            finish,
        ],
    )
    calls, task_seed = _start_background_shell(gateway, tmp_path, monkeypatch)

    _finish_shell(gateway, task_seed)
    _finish_shell(gateway, task_seed)

    # A terminal callback must settle the original native tool call and replay
    # the same turn exactly once; an interrupted continuation means the private
    # native receipt was lost before resumption.
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert len(calls) == 1
    assert 2 <= len(model.requests) <= 3
    assert "background result" in json.dumps(snapshot)
    public = _ledger_json(gateway)
    assert "private-fixture-replay" not in public
    assert "_nativeConversation" not in public
    assert "runtime_shell_continuation_interrupted" not in public


def test_native_shell_stop_path_settles_call_without_resampling_and_allows_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, model, _ = setup_gateway(
        tmp_path,
        [
            call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
            call("shell-call", "vrcforge_runtime_action", {
                "action": "shell",
                "shell_command": "Get-ChildItem",
            }),
            {"role": "assistant", "content": "The next turn can continue."},
        ],
    )
    calls, task_seed = _start_background_shell(gateway, tmp_path, monkeypatch)
    cancelled_owners: list[str] = []
    monkeypatch.setattr(
        gateway.shell,
        "cancel_owner",
        lambda owner_id: (cancelled_owners.append(owner_id) or ["shell-async-1"]),
    )

    cancelled = gateway.request_runtime_cancel(
        {"sessionId": "native-shell-session", "clientTurnId": "native-shell-turn", "reason": "user_stop"}
    )
    assert cancelled["status"] == "cancel_requested"
    assert cancelled["cancelledShellSessionIds"] == ["shell-async-1"]
    assert cancelled_owners
    requests_before_completion = len(model.requests)
    _finish_shell(gateway, task_seed, cancelled=True)
    _finish_shell(gateway, task_seed, cancelled=True)

    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert len(calls) == 1
    assert len(model.requests) == requests_before_completion
    shell_results = [
        item for item in snapshot["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "shell-call"
    ]
    assert len(shell_results) == 1
    assert "failed" in shell_results[0]["content"]

    next_turn = gateway.runtime_message(
        {
            "message": "Continue after the stop.",
            "sessionId": "native-shell-session",
            "clientTurnId": "native-shell-next",
            "projectRoot": str(tmp_path),
            "maxAgenticTurns": 5,
        }
    )
    assert next_turn["plan"]["reply"] == "The next turn can continue."
    assert len(model.requests) == requests_before_completion + 1


@pytest.mark.parametrize(
    ("cancelled", "exit_code", "expected_fragment"),
    [(True, 0, "failed"), (False, 1, "failed")],
)
def test_native_shell_cancel_and_failure_resume_with_matched_result_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
    exit_code: int,
    expected_fragment: str,
) -> None:
    gateway, model, _ = setup_gateway(
        tmp_path,
        [
            call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
            call("shell-call", "vrcforge_runtime_action", {
                "action": "shell",
                "shell_command": "Get-ChildItem",
            }),
            finish,
        ],
    )
    calls, task_seed = _start_background_shell(gateway, tmp_path, monkeypatch)
    _finish_shell(gateway, task_seed, cancelled=cancelled, exit_code=exit_code)
    _finish_shell(gateway, task_seed, cancelled=cancelled, exit_code=exit_code)

    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert len(calls) == 1
    assert 2 <= len(model.requests) <= 3
    tool_results = [
        item for item in snapshot["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "shell-call"
    ]
    assert len(tool_results) == 1
    assert expected_fragment in tool_results[0]["content"]
    if cancelled:
        assert '"status": "cancelled"' in _ledger_json(gateway)
    assert "_nativeConversation" not in _ledger_json(gateway)
