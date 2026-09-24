from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from agent_gateway import AgentGateway
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata, RuntimePlannerService
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop


def call(call_id, name, arguments):
    return {"role": "assistant", "content": None, "reasoning_content": "private-fixture-replay",
            "tool_calls": [{"id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}]}


class NativeTurn:
    def bind(self, request):
        return nullcontext(PlannerTurnMetadata(native_binding="fixture-provider-model-route"))


class NativeModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def plan(self, prompt):
        raise AssertionError("Native bound turn must not use the legacy JSON planner")

    def plan_native(self, request):
        self.requests.append(deepcopy(request))
        receipt = next(self.replies)
        if callable(receipt):
            receipt = receipt(request)
        return PlannerModelResult("", assistant_message=receipt,
                                  finish_reason="tool_calls" if receipt.get("tool_calls") else "stop")


def finish(request):
    evidence = []
    for message in request["messages"]:
        if message["role"] == "tool":
            for item in json.loads(message["content"]).get("observations", []):
                if item.get("actionId") and item.get("outcome", {}).get("status") == "ok":
                    evidence.append(item["actionId"])
    return call("final", "vrcforge_runtime_action", {
        "action": "reply", "reply": "Checked", "completion_claim": {
            "satisfied": True, "evidence_action_ids": list(dict.fromkeys(evidence))}})


def setup_gateway(tmp_path, replies):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    invoked = []
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", lambda params: invoked.append(params) or {"text": "fixture-body"})
    model = NativeModel(replies)
    planner = RuntimePlannerService(catalog=_TestRuntimePlannerCatalog(gateway),
                                    desktop=_TestRuntimePlannerDesktop(gateway), model=model, turn=NativeTurn())
    gateway.bind_runtime_planner(planner)
    return gateway, model, invoked


def run(gateway, tmp_path, **params):
    return gateway.runtime_message({"message": "Read this file", "sessionId": "native-session",
                                    "clientTurnId": "native-turn", "projectRoot": str(tmp_path),
                                    "maxAgenticTurns": 5, **params})


def test_native_gateway_pairs_actual_tool_result_and_keeps_receipt_private(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    messages = model.requests[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["reasoning_content"] == "private-fixture-replay"
    assert messages[2]["tool_call_id"] == "read-1"
    assert "fixture-body" in messages[2]["content"]
    assert result["plan"]["nextStep"] == "done"
    assert "private-fixture-replay" not in json.dumps(result)
    assert "private-fixture-replay" not in json.dumps(gateway._runtime_session_state.get_session("native-session"))


@pytest.mark.parametrize("kind", ["hidden", "invalid", "parallel"])
def test_native_gateway_rejects_every_call_without_dispatch(tmp_path, kind):
    receipt = call("bad-1", "not_advertised" if kind == "hidden" else "vrcforge_read_text_file", {})
    if kind == "parallel":
        receipt["tool_calls"] += call("bad-2", "vrcforge_read_text_file", {"path": "b.txt"})["tool_calls"]
    gateway, model, invoked = setup_gateway(tmp_path, [receipt, {"role": "assistant", "content": "Cannot proceed"}])
    run(gateway, tmp_path)
    assert not invoked
    results = [m for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in results] == [c["id"] for c in receipt["tool_calls"]]
    assert all("failed" in m["content"] or "invalid" in m["content"] for m in results)


def test_native_unadvertised_tool_without_completed_correction_remains_failed(tmp_path):
    rejected = call("wrong-tool", "unity_agent_message", {"message": "not advertised here"})
    gateway, model, invoked = setup_gateway(tmp_path, [
        rejected,
        {"role": "assistant", "content": "No valid correction is available."},
    ])

    result = run(gateway, tmp_path, maxAgenticTurns=3)

    assert not invoked
    assert result["plan"]["nextStep"] == "planner_failed"


def test_native_gateway_settles_execution_phase_before_replanning(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        {"role": "assistant", "content": "Ready"}])
    run(gateway, tmp_path)
    assert not invoked
    assert model.requests[1]["messages"][-1]["tool_call_id"] == "phase"
    assert "entered_execution" in model.requests[1]["messages"][-1]["content"]


def test_native_control_validation_recovers_on_valid_control_then_tool(tmp_path):
    invalid = call("bad-control", "vrcforge_runtime_action", {
        "action": "enter_execution", "unexpected": True,
    })
    valid = call("good-control", "vrcforge_runtime_action", {"action": "enter_execution"})
    gateway, model, invoked = setup_gateway(tmp_path, [invalid, valid,
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    assert len(model.requests) == 4
    assert result["plan"]["nextStep"] == "done", result


def test_native_control_validation_cap_still_stops_repeated_invalid_control(tmp_path):
    invalid = call("bad-control", "vrcforge_runtime_action", {
        "action": "enter_execution", "unexpected": True,
    })
    invalid_again = call("bad-control-2", "vrcforge_runtime_action", {
        "action": "enter_execution", "unexpected": True,
    })
    gateway, model, invoked = setup_gateway(tmp_path, [invalid, invalid_again])
    result = run(gateway, tmp_path)
    assert not invoked
    assert len(model.requests) == 2
    assert result["plan"]["nextStep"] == "planner_failed"


def test_native_control_validation_recovers_on_plain_reply(tmp_path):
    invalid = call("bad-control", "vrcforge_runtime_action", {
        "action": "enter_execution", "unexpected": True,
    })
    gateway, model, invoked = setup_gateway(tmp_path, [invalid, {
        "role": "assistant", "content": "The request is ready for you."
    }])
    result = run(gateway, tmp_path)
    assert not invoked
    assert len(model.requests) == 2
    assert result["plan"]["nextStep"] != "planner_failed", result


def test_native_control_recovery_does_not_clear_real_tool_failure(tmp_path):
    invalid = call("bad-control", "vrcforge_runtime_action", {
        "action": "enter_execution", "unexpected": True,
    })
    gateway, model, invoked = setup_gateway(tmp_path, [
        invalid,
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}),
        {"role": "assistant", "content": "The read is complete."},
    ])
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", lambda args: invoked.append(args) or {"ok": False, "error": "fixture-read-failed"})
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    assert result["plan"]["nextStep"] != "done"
    assert result["plan"]["completionGate"]["status"] == "needs_user_action"


@pytest.mark.parametrize("control", ["cancel", "steer"])
def test_native_gateway_user_control_discards_proposal_before_execution(tmp_path, control):
    def proposal(request):
        if control == "cancel":
            gateway._runtime_session_state.mark_cancel_requested(session_id="native-session", client_turn_id="native-turn")
        else:
            receipt = gateway._runtime_session_state.submit_steer(
                session_id="native-session", target_client_turn_id="native-turn", input_id="steer-1", message="Stop reading; just reply")
            assert receipt["accepted"]
        return call("discarded", "vrcforge_read_text_file", {"path": "a.txt"})
    gateway, model, invoked = setup_gateway(tmp_path, [proposal, {"role": "assistant", "content": "Stopped"}])
    result = run(gateway, tmp_path)
    assert not invoked
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    tool_result = next(m for m in snapshot["messages"] if m["role"] == "tool")
    assert tool_result["tool_call_id"] == "discarded"
    if control == "cancel":
        assert result["plan"]["nextStep"] == "cancelled"
        assert "cancelled" in tool_result["content"]
    else:
        assert model.requests[1]["messages"][-1] == {"role": "user", "content": "Stop reading; just reply"}


def test_native_gateway_failure_reaches_model_without_success_claim(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [call("failed", "vrcforge_read_text_file", {"path": "a.txt"}),
                                                       {"role": "assistant", "content": "Read failed"}])
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.", "plan/preview",
                          lambda args: {"ok": False, "error": "fixture-read-failed"})
    result = run(gateway, tmp_path)
    assert "fixture-read-failed" in model.requests[1]["messages"][-1]["content"]
    assert result["plan"]["nextStep"] != "done"


def test_native_read_observation_notfound_create_verified_then_targeted_read_finishes(tmp_path):
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode

    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [])
    _set_mode(gateway, "roslyn_full_auto")
    created = False

    def write_fixture(args):
        nonlocal created
        created = True
        invoked.append(dict(args))
        return {"ok": True, "status": "written", "persistedReadback": True, "sceneSaved": True, "consoleVerified": True}

    gateway._tools["fixture_write"].handler = write_fixture
    write_handler = gateway.approval_transactions._ports.state.write_handlers["fixture_write"]
    write_handler.handler = write_fixture
    write_handler.verification_profile = "persisted_scene_write_console"
    write_handler.verification_prepare_handler = lambda _arguments: {}
    write_handler.verification_finalize_handler = lambda _arguments, _baseline, result: result

    gateway.register_tool(
        "vrcforge_get_gameobject", "When to use: read. When NOT to use: write.",
        "plan/preview", lambda args: invoked.append(("read", args)) or (
            {"ok": True, "gameObjectPath": "HarnessAutoCheck"}
            if created and args.get("executionTarget")
            else {"ok": False, "error": "gameobject_not_found"}
        ),
    )
    model.replies = iter([
        call("wrong-tool", "unity_agent_message", {"message": "not advertised here"}),
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("read-before", "vrcforge_get_gameobject", {"gameObjectPath": "HarnessAutoCheck"}),
        call("create", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "create"}),
        call("read-target", "vrcforge_get_gameobject", {
            "gameObjectPath": "HarnessAutoCheck",
            "executionTarget": {"project": {"root": str(tmp_path), "projectId": "p1"}, "editor": {"coreInstanceId": "core-1"}},
        }),
        finish,
    ])
    result = run(gateway, tmp_path, maxAgenticTurns=8)
    assert result["plan"]["nextStep"] == "done", result
    assert result["plan"]["task"]["status"] == "completed"
    actions = result["plan"]["task"]["actions"]
    reads = [item for item in actions if item.get("tool") == "vrcforge_get_gameobject"]
    assert [item.get("status") for item in reads] == ["failed", "completed"]
    assert [item.get("outcome", {}).get("status") for item in reads] == ["failed", "ok"]
    write_action = next(item for item in actions if item.get("tool") == "fixture_write")
    assert write_action["kind"] == "write"
    write_step = next(item for item in result["plan"]["steps"] if item.get("tool") == "fixture_write")
    assert write_step["result"]["persistedReadback"] is True
    assert write_step["result"]["sceneSaved"] is True
    assert write_step["result"]["consoleVerified"] is True
    assert any(item.get("value") == "create" for item in invoked if isinstance(item, dict))


def test_native_gateway_completion_rejection_is_returned_to_same_call(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("unbound", "vrcforge_runtime_action", {"action": "reply", "reply": "Incorrect success",
              "completion_claim": {"satisfied": True, "evidence_action_ids": []}}), finish])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    assert model.requests[2]["messages"][-1]["tool_call_id"] == "unbound"
    assert "requiredEvidenceActionIds" in model.requests[2]["messages"][-1]["content"]
    assert result["plan"]["nextStep"] == "done"


def test_native_observation_reuses_sensitive_output_boundary(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    step = {"tool": "fixture", "status": "executed", "actionId": "action-a",
            "outcome": {"status": "ok", "observed": {"apiKey": "SECRET_SENTINEL"}}}
    assert "SECRET_SENTINEL" not in json.dumps(gateway.runtime_planner.native_result_observation(step))


def test_native_tool_time_steer_reaches_immediate_next_request(tmp_path):
    gateway, model, _ = setup_gateway(tmp_path, [call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish])
    def read(args):
        assert gateway._runtime_session_state.submit_steer(session_id="native-session", target_client_turn_id="native-turn",
                                                          input_id="during-tool", message="Change direction now")["accepted"]
        return {"text": "fixture-body"}
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.", "plan/preview", read)
    run(gateway, tmp_path)
    assert model.requests[1]["messages"][-1] == {"role": "user", "content": "Change direction now"}


def test_native_dispatch_exception_does_not_leave_orphan_call(tmp_path, monkeypatch):
    gateway, model, _ = setup_gateway(tmp_path, [call("interrupted", "vrcforge_read_text_file", {"path": "a.txt"})])
    def crash(*args, **kwargs):
        raise RuntimeError("fixture-dispatch-interruption")
    monkeypatch.setattr(type(gateway._runtime_skill_executor), "execute", crash)
    with pytest.raises(RuntimeError, match="fixture-dispatch-interruption"):
        run(gateway, tmp_path)
    snapshot = next(iter(gateway._runtime_session_state._native_conversations.values()))
    assert not gateway._runtime_session_state._native_pending(snapshot)
    assert snapshot["messages"][-1]["tool_call_id"] == "interrupted"
    assert "interrupted" in snapshot["messages"][-1]["content"]


def test_native_scope_denial_does_not_block_a_new_user_turn(tmp_path):
    gateway, model, _ = setup_gateway(tmp_path, [call("denied", "vrcforge_read_text_file", {"path": "a.txt"}),
                                               {"role": "assistant", "content": "New request accepted"}])
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.", "plan/preview",
                          lambda args: {"ok": False, "status": "needs_user_action", "error": "Scope denied"})
    first = run(gateway, tmp_path)
    assert first["plan"]["nextStep"] == "needs_user_action"
    second = run(gateway, tmp_path, clientTurnId="second-turn", message="Just reply")
    assert len(model.requests) == 2
    assert second["plan"]["reply"] == "New request accepted"


@pytest.mark.parametrize("later_tool", ["vrcforge_ask_user", "fixture_write"])
def test_stale_question_stop_cannot_cancel_a_later_pending_call(tmp_path, later_tool):
    from agent_runtime_native_turn import NativeRuntimeTurn
    gateway, _, _ = setup_gateway(tmp_path, [])
    state = gateway.runtime_sessions
    state.begin_native_turn("s", binding="b", turn_id="t", message="question")
    state.append_native_assistant("s", binding="b", message=call("old-question", "vrcforge_ask_user", {}))
    seed = {"sessionId": "s", "_nativeConversation": state.native_conversation("s", binding="b")}
    state.settle_native_call("s", binding="b", call_id="old-question", content="answered")
    state.append_native_assistant("s", binding="b", message=call("later", later_tool, {}))
    assert NativeRuntimeTurn.cancel_question(state, seed) == []
    assert state._native_pending(state.native_conversation("s", binding="b")) == {"later"}
