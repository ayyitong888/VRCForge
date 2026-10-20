"""Current planner failures remain terminal; earlier tool failures remain evidence."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tests.test_native_runtime_gateway import call, run, setup_gateway


def _original_failure():
    path = os.environ.get("VRCFORGE_TERMINAL_FAILURE_RECORD")
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "oldShellError": "Command could not be parsed.",
        "plan": {"plannerFailure": {"providerError": {
            "message": "Our servers are currently overloaded. Please try again later.",
            "body": {"code": "server_is_overloaded", "type": "service_unavailable_error",
                     "message": "Our servers are currently overloaded. Please try again later.",
                     "headers": {"x-retry-metadata": "NO_MORE_RETRY"}},
        }}},
    }


@pytest.mark.parametrize("kind,expected_code", [
    ("original_overload", "provider_capacity"),
    ("connection", "provider_connection_failed"),
    ("timeout", "provider_timeout"),
])
def test_native_current_provider_failure_preserves_older_tool_evidence(tmp_path, monkeypatch, kind, expected_code):
    original = _original_failure()
    old_error = original["oldShellError"]
    original_provider = original["plan"]["plannerFailure"]["providerError"]
    if kind == "original_overload":
        failure = RuntimeError(original_provider["message"])
        failure.status_code = 503
        failure.body = deepcopy(original_provider["body"])
    elif kind == "connection":
        failure = ConnectionError("The provider connection ended unexpectedly.")
    else:
        failure = TimeoutError("The provider response timed out.")
    # The original stream had already failed terminally. Do not introduce
    # provider retry attempts into this completion-projection regression.
    failure.stream_started = True

    def fail_after_tools(request):
        tool_results = [item for item in request["messages"] if item.get("role") == "tool"]
        assert any(old_error in item["content"] for item in tool_results)
        assert any("fixture-body" in item["content"] for item in tool_results)
        raise failure

    gateway, model, _ = setup_gateway(tmp_path, [
        call("old-shell", "vrcforge_runtime_action", {"action": "shell", "shell_command": "inspect"}),
        call("later-read", "vrcforge_read_text_file", {"path": str(tmp_path / "fixture.txt")}),
        fail_after_tools,
    ])
    monkeypatch.setattr(gateway.shell, "execute", lambda *_args, **_kwargs: {
        "ok": False, "status": "failed", "result": {"ok": False, "error": old_error},
    })
    response = run(gateway, tmp_path, projectType="general", maxAgenticTurns=6)
    plan = response["plan"]
    assert plan["plannerFailed"] is True
    assert plan["plannerFailure"]["phase"] == "post_tool"
    assert plan["plannerFailure"]["code"] == expected_code
    assert plan["nextStep"] == "planner_failed"
    assert plan["completionGate"] == {"status": "failed", "reason": expected_code}
    assert plan["summary"] != old_error
    assert old_error not in plan["reply"]
    shell = next(step for step in response["steps"] if step.get("kind") == "shell")
    later_read = next(step for step in response["steps"] if step.get("tool") == "vrcforge_read_text_file")
    assert shell["outcome"]["summary"] == old_error
    assert later_read["outcome"]["status"] == "ok"
    assert len(model.requests) == 3
    assert not plan.get("completionClaim", {}).get("satisfied", False)
    terminal = [event for event in gateway.runtime_runs.read_events() if event.get("event") == "runtime_turn_completed"]
    assert terminal[-1]["status"] == "failed"
