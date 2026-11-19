"""Real Gateway regressions for repeated read cycles across provider routes."""

from contextlib import nullcontext
import json
from threading import Event

import pytest

from agent_gateway import AgentGateway
from agent_general_no_progress import LOOP_REMINDER
from agent_runtime_session_state import NATIVE_RUNTIME_STATE_PREFIX
from responses_native_protocol import build_responses_input
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata, RuntimePlannerService
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop


def _native_call(call_id: str, path: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "call_tool",
                "arguments": json.dumps({
                    "tool": "vrcforge_read_text_file",
                    "arguments": {"path": path},
                }),
            },
        }],
    }


def _native_reply(request: dict) -> dict:
    return {
        "role": "assistant",
        "content": "The reads provide enough evidence.",
        "tool_calls": [{
            "id": "finish",
            "type": "function",
            "function": {
                "name": "vrcforge_runtime_action",
                "arguments": json.dumps({
                    "action": "reply",
                    "reply": "Finished after the changed observations.",
                }),
            },
        }],
    }


class _Turn:
    def __init__(self, native: bool):
        self.native = native

    def bind(self, _request):
        binding = "fixture-native-route" if self.native else ""
        return nullcontext(PlannerTurnMetadata(native_binding=binding))


class _Model:
    def __init__(self, *, native: bool, paths: tuple[str, ...], changing_results: bool):
        self.native = native
        self.paths = paths
        self.changing_results = changing_results
        self.requests = []

    def plan_native(self, request):
        self.requests.append(request)
        index = len(self.requests)
        if index <= len(self.paths):
            path = self.paths[index - 1]
            receipt = _native_call(f"read-{index}", path)
        else:
            receipt = _native_reply(request)
        return PlannerModelResult(
            "", assistant_message=receipt,
            finish_reason="tool_calls" if receipt.get("tool_calls") else "stop",
        )

    def plan(self, _prompt):
        self.requests.append(_prompt)
        index = len(self.requests)
        if index <= len(self.paths):
            path = self.paths[index - 1]
            value = {
                "action": "skill",
                "skill_tool": "vrcforge_read_text_file",
                "skill_params": {"path": path},
                "continue_loop": True,
            }
        else:
            value = {"action": "reply", "reply": "Finished after the changed observations."}
        return PlannerModelResult(text=json.dumps(value), planner_label="fixture")


def _gateway_case(tmp_path, *, native: bool, paths: tuple[str, ...], changing_results: bool):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    calls = []

    def read_file(params):
        path = str(params["path"])
        index = len(calls) + 1
        calls.append(path)
        value = f"stable contents for {path}"
        if changing_results:
            value += f" (observation {index})"
        return {"ok": True, "text": value}

    gateway.register_tool(
        "vrcforge_read_text_file",
        "When to use: read a text fixture. When NOT to use: write files.",
        "plan/preview",
        read_file,
    )
    model = _Model(native=native, paths=paths, changing_results=changing_results)
    planner = RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=model,
        turn=_Turn(native),
    )
    gateway.bind_runtime_planner(planner)
    return gateway, model, calls


def _run(gateway, tmp_path):
    return gateway.runtime_message({
        "message": "Inspect the two supplied files.",
        "sessionId": "observation-cycle-session",
        "clientTurnId": "observation-cycle-turn",
        "projectRoot": str(tmp_path),
        "projectType": "general",
        "_projectContextActive": False,
        "maxAgenticTurns": 12,
    })


def _assert_stalled_without_question(gateway, result, *, session_id="observation-cycle-session"):
    plan = result["plan"]
    assert plan["nextStep"] == "loop_suppressed", result
    assert plan["reason"] == "tool_loop_repeated", result
    assert "loopPause" not in plan
    assert result["turnStatus"]["status"] == "failed"
    assert result["turnStatus"]["error"]["code"] == "tool_loop_repeated"
    questions = gateway.questions.list(session_id=session_id, include_answered=True)["questions"]
    assert not questions


def _assert_reminder(native, model, request_index):
    request = model.requests[request_index]
    if native:
        messages = request["messages"]
        reminders = [message for message in messages if LOOP_REMINDER in str(message.get("content") or "")]
        assert len(reminders) == 1
        assert reminders[0]["role"] == "system"
        assert json.loads(reminders[0]["content"].removeprefix(NATIVE_RUNTIME_STATE_PREFIX))["toolLoopReminder"] == LOOP_REMINDER
        for role in ("developer", "system"):
            wire = build_responses_input(messages, system_role=role)
            projected = [item for item in wire if LOOP_REMINDER in str(item.get("content") or "")]
            assert len(projected) == 1 and projected[0]["role"] == role
    else:
        state_line = next(line for line in request.splitlines() if line.startswith("Current operation state (data): "))
        assert json.loads(state_line.removeprefix("Current operation state (data): "))["toolLoopReminder"] == LOOP_REMINDER
        assert request.count(LOOP_REMINDER) == 1
        assert f"用户: {LOOP_REMINDER}" not in request


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_ab_cycle_reminds_on_fifth_read_and_stops_on_sixth(tmp_path, native):
    paths = ("a.txt", "b.txt") * 3
    gateway, model, calls = _gateway_case(tmp_path, native=native, paths=paths, changing_results=False)

    try:
        result = _run(gateway, tmp_path)
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert calls == list(paths), result
    read_steps = [step for step in result["steps"] if step.get("tool") == "vrcforge_read_text_file"]
    assert len(read_steps) == 6
    assert [step["result"]["text"] for step in read_steps] == [
        f"stable contents for {path}" for path in calls
    ]
    assert len(model.requests) == 6
    _assert_reminder(native, model, 5)
    _assert_stalled_without_question(gateway, result)
    if native:
        for call_index in range(1, 6):
            call_id = f"read-{call_index}"
            history = model.requests[call_index]["messages"]
            assert any(
                message.get("role") == "assistant"
                and any(item.get("id") == call_id for item in message.get("tool_calls", []))
                for message in history
            )
            paired_result = next(
                message for message in history
                if message.get("role") == "tool" and message.get("tool_call_id") == call_id
            )
            assert f"stable contents for {calls[call_index - 1]}" in paired_result["content"]


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_abc_cycle_reminds_on_seventh_read_and_stops_on_eighth(tmp_path, native):
    paths = ("a.txt", "b.txt", "c.txt") * 2 + ("a.txt", "b.txt")
    gateway, model, calls = _gateway_case(tmp_path, native=native, paths=paths, changing_results=False)

    try:
        result = _run(gateway, tmp_path)
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert calls == list(paths), result
    read_steps = [step for step in result["steps"] if step.get("tool") == "vrcforge_read_text_file"]
    assert len(read_steps) == 8
    assert [step["result"]["text"] for step in read_steps] == [
        f"stable contents for {path}" for path in paths
    ]
    assert len(model.requests) == 8
    _assert_reminder(native, model, 7)
    _assert_stalled_without_question(gateway, result)


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_alternating_reads_with_new_results_are_allowed_to_finish(tmp_path, native):
    paths = ("a.txt", "b.txt") * 3
    gateway, model, calls = _gateway_case(tmp_path, native=native, paths=paths, changing_results=True)

    try:
        result = _run(gateway, tmp_path)
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert calls == list(paths), result
    assert result["plan"]["nextStep"] == "done", result
    assert result["plan"].get("plannerFailure", {}).get("code") != "planner_no_progress"
    read_steps = [step for step in result["steps"] if step.get("tool") == "vrcforge_read_text_file"]
    assert [step["result"]["text"] for step in read_steps] == [
        f"stable contents for {path} (observation {index})"
        for index, path in enumerate(calls, start=1)
    ]
    assert model.requests


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_llm_child_reports_stalled_work_to_its_parent_without_asking_the_user(tmp_path, native):
    session_id = "loop-child-session"
    paths = ("a.txt", "b.txt") * 3
    gateway, model, calls = _gateway_case(tmp_path, native=native, paths=paths, changing_results=False)
    try:
        child = gateway.run_sub_agent_llm(
            task="Check the supplied files.",
            child_session_id=session_id,
            system_prompt="Use the available read-only evidence.",
            project_path=str(tmp_path),
            max_steps=12,
            cancel_event=Event(),
        )
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert calls == list(paths)
    assert len(model.requests) == 6
    _assert_reminder(native, model, 5)
    _assert_stalled_without_question(gateway, child["rawResult"], session_id=session_id)
    assert child["ok"] is False and child["status"] == "failed"
    assert child["finalReport"] == child["rawResult"]["plan"]["reply"]
