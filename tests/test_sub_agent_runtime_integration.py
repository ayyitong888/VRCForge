"""Fixture-provider acceptance for the isolated read-only child runtime."""
from __future__ import annotations

import json
import threading
import time
from contextlib import nullcontext

from tests.test_native_runtime_gateway import call, setup_gateway
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata


class RecordingTurn:
    def __init__(self):
        self.requests = []

    def bind(self, request):
        self.requests.append(dict(request))
        return nullcontext(PlannerTurnMetadata(native_binding="fixture-provider-model-route"))


def _finish(request):
    evidence = []
    for message in request["messages"]:
        if message.get("role") == "tool":
            for observation in json.loads(message["content"]).get("observations", []):
                if observation.get("actionId") and observation.get("outcome", {}).get("status") == "ok":
                    evidence.append(observation["actionId"])
    return call("final", "vrcforge_runtime_action", {"action": "reply", "reply": "Two checks agree.",
        "completion_claim": {"satisfied": True, "evidence_action_ids": evidence}})


def _child_gateway(tmp_path, replies):
    gateway, model, invoked = setup_gateway(tmp_path, replies)
    gateway.runtime_planner._turn = RecordingTurn()
    return gateway, model, invoked


def _run(gateway, tmp_path, **overrides):
    cancel = overrides.pop("cancel_event", threading.Event())
    request = dict(
        task="Check whether the two fixture reads support a conclusion.",
        child_session_id="child-session-1", model="fixture-child-model",
        system_prompt="Use evidence from both reads.", max_steps=4,
        project_path=str(tmp_path), selected_materials=[], cancel_event=cancel,
    )
    request.update(overrides)
    return gateway.run_sub_agent_llm(**request)


def test_child_uses_real_runtime_loop_for_two_readonly_tools_then_final(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        _finish,
    ])
    result = _run(gateway, tmp_path)
    assert gateway.runtime_planner._turn.requests[0]["model"] == "fixture-child-model"
    assert [item.get("path") for item in invoked] == ["a.txt", "b.txt"]
    assert len(model.requests) == 3
    assert result["ok"] is True
    assert isinstance(result["finalReport"], str) and result["finalReport"]
    assert result["stepCount"] <= 4


def test_plain_text_reply_is_a_successful_terminal_analysis_without_tool_completion_claim(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        {"role": "assistant", "content": "The supplied notes support the conclusion because both state the same date."},
    ])
    result = _run(gateway, tmp_path, task="Compare the supplied notes.")
    assert not invoked
    assert len(model.requests) == 1
    assert result["finalReport"].startswith("The supplied notes support")
    assert result["rawResult"]["plan"]["nextStep"] == "done"
    assert result["rawResult"]["task"]["status"] == "planning"
    assert result["ok"] is True
    assert result["status"] == "completed"


def test_child_history_system_prompt_and_model_are_bound_without_parent_history(tmp_path):
    gateway, model, _ = _child_gateway(tmp_path, [_finish])
    with gateway.runtime_planner.bind_turn({"model": "fixture-child-model", "projectRoot": str(tmp_path)}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    gateway._runtime_session_state.begin_native_turn(
        "parent-session", binding=binding, turn_id="parent-turn",
        message="PARENT PRIVATE HISTORY SENTINEL")
    parent_instructions = gateway.runtime_planner._read_global_instructions()
    result = _run(gateway, tmp_path)
    request = model.requests[0]
    messages = request["messages"] if isinstance(request, dict) else request.messages
    assert "PARENT PRIVATE HISTORY SENTINEL" not in json.dumps(messages)
    instructions = request["instructions"] if isinstance(request, dict) else request.instructions
    assert "Use evidence from both reads." in instructions
    assert gateway.runtime_planner._read_global_instructions() == parent_instructions
    bound = gateway.runtime_planner._turn.requests[1]
    assert bound.get("sessionId") == "child-session-1" or bound.get("session_id") == "child-session-1"
    assert gateway._runtime_session_state.native_conversation(
        "child-session-1", binding=binding) is not None
    assert gateway._runtime_session_state.native_conversation(
        "parent-session", binding=binding)["messages"][-1]["content"] == "PARENT PRIVATE HISTORY SENTINEL"


def test_plan_mode_blocks_proposed_write_before_handler_dispatch(tmp_path):
    from tests.test_native_permission_modes import _write_gateway_for_mode
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        call("write-attempt", "fixture_write", {"value": "must-not-run"}),
        {"role": "assistant", "content": "Write was blocked."},
    ])
    result = _run(gateway, tmp_path)
    assert not invoked
    assert len(model.requests) <= 2
    assert result["ok"] is False


def test_child_cancel_event_stops_runtime_before_later_tool_dispatch(tmp_path):
    cancel = threading.Event()
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        lambda _request: (cancel.set() or call("read-b", "vrcforge_read_text_file", {"path": "b.txt"})),
        {"role": "assistant", "content": "Stopped."},
    ])
    result = _run(gateway, tmp_path, cancel_event=cancel)
    assert [item.get("path") for item in invoked] == ["a.txt"]
    assert result["rawResult"]["plan"]["nextStep"] == "cancelled"
    assert result["ok"] is False and result["status"] == "cancelled"


def test_registry_cancel_watcher_interrupts_provider_waiting_for_child_cancel(tmp_path):
    gateway, _model, invoked = _child_gateway(tmp_path, [])
    provider_entered = threading.Event()
    provider_cancel_seen = threading.Event()
    cancel = threading.Event()

    class WaitingProvider:
        def plan_native(self, request):
            provider_entered.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if gateway._runtime_session_state.cancel_requested(
                    session_id="child-session-1", client_turn_id="child-session-1:initial"):
                    provider_cancel_seen.set()
                    return PlannerModelResult("", assistant_message={
                        "role": "assistant", "content": "Stopped after cancellation."}, finish_reason="stop")
                time.sleep(.01)
            raise AssertionError("cancel watcher did not reach the blocked provider")

    gateway.runtime_planner._model = WaitingProvider()
    result_holder = []
    errors = []

    def run_child():
        try:
            result_holder.append(_run(gateway, tmp_path, cancel_event=cancel))
        except BaseException as exc:  # surfaced in the test thread after bounded join
            errors.append(exc)

    worker = threading.Thread(target=run_child, name="fixture-child-call")
    worker.start()
    try:
        assert provider_entered.wait(2), "fixture provider was not entered"
        cancel.set()
        worker.join(4)
        assert not worker.is_alive(), "child call did not stop after cancellation"
        assert provider_cancel_seen.is_set(), "watcher did not forward cancellation to the provider owner"
        assert not errors, errors
        assert result_holder and result_holder[0]["status"] == "cancelled"
        assert result_holder[0]["ok"] is False
        assert not invoked
        assert not any(thread.name == "sub-agent-cancel" for thread in threading.enumerate())
    finally:
        cancel.set()
        worker.join(4)
    assert not worker.is_alive(), "fixture child worker leaked"


def test_child_max_step_budget_is_enforced_by_runtime(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        call("read-c", "vrcforge_read_text_file", {"path": "c.txt"}),
    ])
    result = _run(gateway, tmp_path, max_steps=1)
    assert len(model.requests) <= 2
    assert len(invoked) <= 1
    assert result.get("stepCount", 0) <= 1
    assert result["ok"] is False
