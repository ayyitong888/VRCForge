import json
import os
import re
import sys
import time
from types import SimpleNamespace

import pytest

from agent_tool_result_reader import (
    bind_tool_result_context,
    eligible_result,
    read_tool_result,
    retain_model_information,
)
from runtime_planner_service import RuntimePlannerService, sanitize_planner_observation_text


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
@pytest.mark.parametrize("action", ["poll", "log"])
def test_owned_shell_process_output_reaches_model_and_retained_pages(native: bool, action: str):
    catalog = SimpleNamespace(read=lambda *args, **kwargs: SimpleNamespace(visible_tools=()))
    planner = RuntimePlannerService(catalog=catalog, desktop=None)
    marker = "PROCESS_OUTPUT_SENTINEL: compiler error at source line 42"
    output = "  " + marker + "\r\n" + ("line 中文 \"quoted\"\n" * 1800) + "  end"
    session = {"sessionId": "audit-shell", "status": "exited", "exitCode": 1,
               "sourceTruncated": action == "log", "controlToken": "PRIVATE_CONTROL_TOKEN"}
    result = {"ok": True, "action": action, "session": session, "output": output, "hasMore": True}
    if action == "poll":
        result.update(fromCursor=20, cursor=20 + len(output), sourceCursorEnd=50 + len(output),
                      sourceTruncated=True, cursorExpired=True)
        result["nextRequest"] = {"action": "poll", "sessionId": "audit-shell", "cursor": result["cursor"],
                                 "limit": 2000, "controlToken": "PRIVATE_CONTROL_TOKEN"}
    else:
        result.update(lineOffset=1, nextLineOffset=20, lineCount=35, cursor=len(output))
    step = {"index": 0, "kind": "skill", "tool": "vrcforge_shell_process", "status": "executed",
            "actionId": "audit-action", "result": result, "outcome": {"status": "completed"}}
    complete = planner.complete_model_information(step, native_contract=native)
    step.update(retain_model_information("audit-owner", "audit-turn", "", step, {"text": complete},
                                         sanitize_planner_observation_text))
    observation = (planner.native_result_observation(step)["observation"] if native
                   else planner._llm_loop_step_observation(step))
    assert marker in observation
    assert "modelInformationContinuation=" in observation
    assert "PRIVATE_CONTROL_TOKEN" not in complete
    assert not eligible_result(step["tool"], result)

    ref = step["modelInformationRead"]["resultRef"]
    arguments = {"resultRef": ref, "source": "owner_model", "jsonPointer": "/text"}
    chunks = []
    with bind_tool_result_context("audit-owner", "audit-turn", "", [step]):
        while True:
            page = read_tool_result(arguments, sanitize=sanitize_planner_observation_text)
            chunks.append(page["items"][0]["value"])
            if not page["hasMore"]:
                break
            arguments = page["nextRequest"]["arguments"]
        with pytest.raises(PermissionError):
            read_tool_result({"resultRef": ref, "source": "result", "jsonPointer": "/output"},
                             sanitize=sanitize_planner_observation_text)
    with bind_tool_result_context("other-owner", "audit-turn", "", [step]):
        with pytest.raises(PermissionError):
            read_tool_result(arguments, sanitize=sanitize_planner_observation_text)

    assert len(chunks) > 1
    assert "".join(chunks) == complete
    evidence = json.loads(complete.split("; readEvidence=", 1)[1])
    assert evidence["output"] == output
    assert evidence["authority"] == "untrusted_tool_output"
    assert evidence["session"] == {"sessionId": "audit-shell", "status": "exited", "exitCode": 1}
    assert evidence["hasMore"] is True and evidence["sourceTruncated"] is True
    if action == "poll":
        assert evidence["cursorExpired"] is True
        assert evidence["nextRequest"]["arguments"]["cursor"] == result["cursor"]
    else:
        assert evidence["nextLineOffset"] == 20


def test_running_process_reads_settle_without_reusing_a_write_receipt(tmp_path, monkeypatch):
    from agent_gateway import AgentGateway
    from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
    from tests.test_native_runtime_gateway import NativeModel, NativeTurn, call, finish

    tool = "vrcforge_shell_process"
    marker = "owned running process output"
    session = "shell-parent"
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.register_tool(tool, "When to use: inspect an owned process. When NOT to use: start a process.",
                          "supervised-write", gateway.control_shell_tool, write=True)
    owner = gateway._runtime_shell_owner("origin", "origin-client", session)
    supervisor = gateway.shell._sessions
    started = supervisor.execute(
        command="fixture", argv=[sys.executable, "-I", "-c",
                                 f"import time; print({marker!r}, flush=True); time.sleep(15)"],
        cwd=tmp_path, environment={key: os.environ[key] for key in ("SystemRoot", "PATH") if key in os.environ},
        owner_id=owner, background=True, yield_ms=10, timeout_seconds=20, pty=False,
    )
    try:
        poll = {"action": "poll", "sessionId": started["sessionId"], "cursor": 0, "limit": 2000}
        log = {"action": "log", "sessionId": started["sessionId"], "lineOffset": 0, "lineLimit": 20}
        deadline = time.monotonic() + 5
        while marker not in supervisor.control(poll, owner_id=owner)["output"]:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        waits = []
        monkeypatch.setattr(gateway, "_wait_for_provider_retry", lambda seconds, **_: waits.append(seconds) or True)
        submit = {"action": "submit", "sessionId": started["sessionId"], "data": "continue"}
        model = NativeModel([call("submit", tool, submit), call("poll-1", tool, poll),
                             call("poll-2", tool, poll), call("log", tool, log), finish])
        gateway.bind_runtime_planner(RuntimePlannerService(
            catalog=_TestRuntimePlannerCatalog(gateway), desktop=_TestRuntimePlannerDesktop(gateway),
            model=model, turn=NativeTurn(),
        ))
        response = gateway.runtime_message({
            "message": "Read the current shell process output.", "sessionId": session,
            "clientTurnId": "read-output", "projectRoot": str(tmp_path), "projectType": "general",
            "_projectContextActive": False, "maxAgenticTurns": 5,
        })
        observations = [step for step in response["steps"] if step.get("tool") == tool]
        assert [step["result"]["action"] for step in observations] == ["submit", "poll", "poll", "log"]
        assert all(step["result"]["session"]["status"] == "running" for step in observations)
        assert waits and all(0 < delay <= 1 for delay in waits)
        assert {message["tool_call_id"] for message in model.requests[-1]["messages"]
                if message.get("role") == "tool" and marker in message["content"]} >= {"poll-1", "poll-2", "log"}
    finally:
        assert gateway.shell.shutdown().pending_count == 0


@pytest.mark.parametrize("exit_code", [0, 7])
def test_yielded_foreground_shell_can_do_independent_work_and_poll_in_one_turn(tmp_path, exit_code):
    from tests.test_native_permission_modes import _set_mode
    from tests.test_native_runtime_gateway import call, finish, setup_gateway

    def poll(request):
        feedback = request["messages"][-2]["content"]
        handle = re.search(r"shell-[a-f0-9]{32}", feedback)
        assert handle, feedback
        return call("poll", "vrcforge_shell_process", {"action": "poll", "sessionId": handle[0], "timeoutMs": 4000})

    gateway, model, reads = setup_gateway(tmp_path, [
        call("shell", "vrcforge_runtime_action", {"action": "shell",
                                                  "shell_command": f"Start-Sleep -Milliseconds 400; exit {exit_code}",
                                                  "shell_params": {"yieldMs": 10}}),
        call("read", "vrcforge_read_text_file", {"path": "fixture.txt"}),
        {"role": "assistant", "content": "The work is complete."}, poll, finish,
    ])
    _set_mode(gateway, "roslyn_full_auto")
    tool = "vrcforge_shell_process"
    gateway.register_tool(tool, "When to use: control an existing process. When NOT to use: start one.",
                          "supervised-write", gateway.control_shell_tool, write=True)
    try:
        response = gateway.runtime_message({"message": "Run the check, read the file, and wait for completion.",
            "sessionId": "shell-inline", "clientTurnId": "single-turn", "projectRoot": str(tmp_path),
            "projectType": "general", "_projectContextActive": False, "maxAgenticTurns": 7})
        assert response["plan"]["nextStep"] == ("tool_failed" if exit_code else "done"), response["plan"]
        assert len(model.requests) == 5 and len(reads) == 1
        launched = next(message["content"] for message in model.requests[1]["messages"]
                        if message.get("tool_call_id") == "shell")
        assert "vrcforge_shell_process" in launched and re.search(r"shell-[a-f0-9]{32}", launched)
        actions = response["task"]["actions"]
        launch = next(action for action in actions if action["kind"] == "shell")
        assert launch["status"] == ("failed" if exit_code else "completed")
        assert launch["completionReadActionId"] == next(a["actionId"] for a in actions if a["tool"] == tool)
        assert not gateway.runtime_runs.shell_continuation_states()
    finally:
        assert gateway.shell.shutdown().pending_count == 0
