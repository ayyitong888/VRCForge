"""Real Gateway regressions for repeated read cycles across provider routes."""

from contextlib import nullcontext
import json
import threading

import pytest

from agent_gateway import AgentGateway
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


def _assert_paused_question(gateway, result):
    plan = result["plan"]
    assert plan["nextStep"] == "paused", result
    assert plan["reason"] == "tool_loop_repeated", result
    question_id = plan["loopPause"]["questionId"]
    questions = gateway.questions.list(session_id="observation-cycle-session", include_answered=True)["questions"]
    question = next(item for item in questions if item["questionId"] == question_id)
    assert question["status"] == "pending"
    assert question["sourceTool"] == "runtime_tool_loop"
    assert [option["id"] for option in question["options"]] == ["continue", "stop"]


def _assert_reminder(native, model, request_index):
    reminder = "最近几次调用结果没有变化，可能在原地打转。请检查写入是否生效，或换一种方法。"
    request = model.requests[request_index]
    if native:
        hidden = [message for message in request["messages"]
                  if message.get("role") == "user" and message.get("hidden") is True]
        assert [message["content"] for message in hidden].count(reminder) == 1
    else:
        assert request.count(reminder) == 1


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_ab_cycle_reminds_on_fifth_read_and_pauses_on_sixth(tmp_path, native):
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
    _assert_paused_question(gateway, result)
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
def test_abc_cycle_reminds_on_seventh_read_and_pauses_on_eighth(tmp_path, native):
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
    _assert_paused_question(gateway, result)


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
def test_loop_pause_stop_does_not_dispatch_another_model_request(tmp_path, native):
    paths = ("a.txt", "b.txt") * 3
    gateway, model, calls = _gateway_case(tmp_path, native=native, paths=paths, changing_results=False)

    try:
        paused = _run(gateway, tmp_path)
        question_id = paused["plan"]["loopPause"]["questionId"]
        answered = gateway.questions.answer(question_id, {
            "sessionId": "observation-cycle-session",
            "selectedOptionId": "stop",
        })
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert calls == list(paths)
    assert len(model.requests) == 6
    assert answered["question"]["selectedOptionId"] == "stop"
    assert answered["question"]["runtimeContinuationStatus"] == "settled_without_dispatch"


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_loop_pause_continue_dispatches_one_turn_without_replaying_reads(tmp_path, native):
    paths = ("a.txt", "b.txt") * 3
    gateway, model, calls = _gateway_case(tmp_path, native=native, paths=paths, changing_results=False)
    completed = []
    completed_event = threading.Event()
    gateway._runtime_turn_completed = lambda payload: (completed.append(payload), completed_event.set())

    try:
        paused = _run(gateway, tmp_path)
        question_id = paused["plan"]["loopPause"]["questionId"]
        answered = gateway.questions.answer(question_id, {
            "sessionId": "observation-cycle-session",
            "selectedOptionId": "continue",
        })
        assert answered["question"]["runtimeContinuationStatus"] in {"queued", "claimed", "delivered"}
        assert completed_event.wait(5), gateway.questions.list(
            session_id="observation-cycle-session", include_answered=True,
        )
        assert gateway.shutdown_runtime_continuations(5)["ok"]
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert calls == list(paths)
    assert len(model.requests) == 7
    assert completed and completed[0]["continuationSource"] == "question_answered"
    assert completed[0]["plan"]["nextStep"] == "done"
