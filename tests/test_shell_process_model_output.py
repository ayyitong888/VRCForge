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
from runtime_planner_service import (
    PlannerCatalogSnapshot,
    PlannerTool,
    RuntimePlannerService,
    sanitize_planner_observation_text,
)


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
def test_harness_finishes_yielded_foreground_shell_after_independent_work(
    tmp_path, exit_code,
):
    from tests.test_native_permission_modes import _set_mode
    from tests.test_native_runtime_gateway import call, finish, setup_gateway

    gateway, model, reads = setup_gateway(tmp_path, [
        call("shell", "vrcforge_runtime_action", {"action": "shell",
                                                  "shell_command": f"Start-Sleep -Milliseconds 400; exit {exit_code}",
                                                  "shell_params": {"yieldMs": 10}}),
        call("read", "vrcforge_read_text_file", {"path": "fixture.txt"}),
        {"role": "assistant", "content": "The work is complete."}, finish,
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
        assert len(model.requests) == 4 and len(reads) == 1
        launched = next(message["content"] for message in model.requests[1]["messages"]
                        if message.get("tool_call_id") == "shell")
        assert "vrcforge_shell_process" in launched and re.search(r"shell-[a-f0-9]{32}", launched)
        assert "no final result has been observed" in launched
        launch_receipt = next(event["payload"] for event in response["timeline"]
                            if event["kind"] == "tool_result" and event["payload"].get("tool") == "shell")
        assert launch_receipt["status"] == "pending"
        actions = response["task"]["actions"]
        assert sum(action["kind"] == "shell" for action in actions) == 1
        launch = next(action for action in actions if action["kind"] == "shell")
        assert launch["status"] == ("failed" if exit_code else "completed")
        assert launch["completionReadActionId"] == next(a["actionId"] for a in actions if a["tool"] == tool)
        result = next(step["result"] for step in response["steps"] if step["tool"] == tool)
        assert result["session"]["sessionId"] == re.search(r"shell-[a-f0-9]{32}", launched)[0]
        assert result["session"]["exitCode"] == exit_code
        assert not gateway.runtime_runs.shell_continuation_states()
    finally:
        assert gateway.shell.shutdown().pending_count == 0


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_poll_correction_requests_existing_tool_block_before_invocation(native):
    from tests.test_native_runtime_gateway import call
    from tests.test_runtime_planner_service import FakeModel, PlannerModelResult

    loader = PlannerTool("load_internal_tool_block", "", "read", runtime_name="vrcforge_load_internal_tool_block")
    process = PlannerTool("shell_process", "", "write", runtime_name="vrcforge_shell_process", write=True,
                          block="project_environment/shell")
    catalog = PlannerCatalogSnapshot(visible_tools=(loader, process))
    payload = {"action": "shell", "shell_params": {"action": "poll", "sessionId": "shell-owned"}}
    planner = RuntimePlannerService(
        catalog=SimpleNamespace(read=lambda *args, **kwargs: catalog),
        desktop=None, model=FakeModel(PlannerModelResult(json.dumps(payload))),
    )
    if native:
        control = PlannerTool("vrcforge_runtime_action", "", "control")
        receipt = call("wrong-poll", control.name, payload)
        admission, rejected = planner._native_action_payload(receipt, [control, loader], catalog=catalog)
        assert admission is None
        guidance = rejected["summary"]
    else:
        plan = planner._llm_plan_agent_turn("Check status.", {}, [], exposure_layer="execution",
                                            internal_tool_blocks={"core"})
        assert plan["continueLoop"] and not plan["shellNeeded"] and not plan["writeNeeded"]
        guidance = plan["argumentValidation"]["summary"]
    recipe = json.loads(guidance.split("Next call: ", 1)[1])
    load_args = {"block": "project_environment/shell", "tools": ["shell_process"]}
    if native:
        assert recipe == {"name": "load_internal_tool_block", "arguments": load_args}
    else:
        assert recipe == {"action": "skill", "skill_tool": "load_internal_tool_block", "skill_params": load_args}
