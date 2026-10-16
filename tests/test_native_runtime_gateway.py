from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from agent_gateway import AgentGateway
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata, RuntimePlannerService
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop


def call(call_id, name, arguments):
    if name == "vrcforge_load_internal_tool_block":
        name = "load_internal_tool_block"
    elif name == "vrcforge_unload_internal_tool_block":
        name = "unload_internal_tool_block"
    elif name not in {"vrcforge_runtime_action", "load_internal_tool_block",
                      "unload_internal_tool_block", "call_tool"}:
        arguments = {"tool": name, "arguments": arguments}
        name = "call_tool"
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


def budgeted_request_history(request):
    messages = request["messages"]
    assert messages[-1]["role"] == "system"
    assert "modelTurnBudget" not in json.loads(messages[-1]["content"].split(": ", 1)[1])
    from agent_scan_result_policy import is_scan_result_policy_message
    from agent_runtime_session_state import AgentRuntimeSessionState
    # These callers inspect conversational/tool history; retained state is
    # covered independently by the complete wire-prefix regression.
    history = [message for message in messages[:-1]
               if not AgentRuntimeSessionState._is_native_runtime_state_message(message)]
    policies = [message for message in history if message.get("role") == "system"]
    assert len(policies) <= 1
    assert all(is_scan_result_policy_message(message) for message in policies)
    return history


def runtime_state(request):
    message = request["messages"][-1]
    assert message["role"] == "system"
    prefix, separator, payload = message["content"].partition(": ")
    assert separator and prefix == "Current runtime state (data)"
    return json.loads(payload)


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


def run_planning_continuation(gateway, tmp_path):
    """Exercise a saved planning layer without changing ordinary turn defaults."""
    params = {"message": "Read this file", "sessionId": "native-session",
              "clientTurnId": "native-turn", "projectRoot": str(tmp_path),
              "maxAgenticTurns": 5}
    context = {"objective": params["message"], "sessionId": "native-session",
               "clientTurnId": "native-turn", "projectRoot": str(tmp_path),
               "exposureLayer": "planning"}
    with gateway.runtime_planner.bind_turn(params):
        return gateway._runtime_message_impl(
            params, task_continuation={"context": context, "completion": {"status": "completed"}},
        )


def test_normal_turn_direct_image_reaches_native_provider_without_vision_runner(tmp_path):
    gateway, model, _ = setup_gateway(tmp_path, [
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish,
    ])
    gateway._vision_profile_enabled = lambda: False
    gateway.vision_analyze_fn = lambda *_args: (_ for _ in ()).throw(AssertionError("vision runner must not run"))
    result = run(gateway, tmp_path, attachments=[{
        "id": "image-1", "name": "probe.png", "type": "image/png", "size": 68,
        "dataUrl": "data:image/png;base64,iVBORw0KGgo=", "payloadKind": "data_url",
    }])
    assert model.requests, result
    assert len(model.requests) == 2
    for request in model.requests:
        user = next(message for message in request["messages"] if message.get("role") == "user")
        assert {item["type"] for item in user["content"]} == {"text", "image_url"}
        assert user["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_native_default_exposes_write_immediately_and_preserves_approval(tmp_path):
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode

    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "x"}),
    ])
    _set_mode(gateway, "approval")
    result = run(gateway, tmp_path, message="Write the fixture")

    state = runtime_state(model.requests[0])
    assert "fixture_write" in state["availableToolNames"]
    context_marker = "Runtime context snapshot (data, not user instructions or authorization): "
    user_context = next(message["content"] for message in model.requests[0]["messages"]
                        if message.get("role") == "user"
                        and message.get("content", "").startswith(context_marker))
    context = json.loads(user_context[len(context_marker):])
    activated = context["activatedToolDefinitions"]
    assert any(item["name"] == "fixture_write" for item in activated)
    assert "parameters" in next(item for item in activated if item["name"] == "fixture_write")
    assert "residentToolDefinitions" not in state
    assert "enter_execution" not in state["allowedRuntimeActions"]
    assert len(model.requests) == 1
    assert result["exposureLayer"] == "execution"
    assert result["write"]["status"] == "approval_pending"
    assert not invoked
    assert not any(step.get("tool") == "exposure_layer" for step in result["steps"])


def test_native_gateway_pairs_actual_tool_result_and_keeps_receipt_private(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    messages = budgeted_request_history(model.requests[1])
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["reasoning_content"] == "private-fixture-replay"
    assert messages[2]["tool_call_id"] == "read-1"
    assert "fixture-body" in messages[2]["content"]
    assert result["plan"]["nextStep"] == "done"
    assert "private-fixture-replay" not in json.dumps(result)
    assert "private-fixture-replay" not in json.dumps(gateway._runtime_session_state.get_session("native-session"))


def test_native_provider_public_reply_precedes_tool_without_leaking_action_json(tmp_path):
    public_reply = "I will inspect the file before summarizing it."

    def progress_receipt():
        receipt = call("read-1", "vrcforge_read_text_file", {"path": "a.txt"})
        receipt["content"] = public_reply
        receipt["reasoning_content"] = "private-native-reasoning"
        return receipt

    gateway, model, _ = setup_gateway(tmp_path, [progress_receipt(), finish])
    emitted = []
    gateway._runtime_timeline_changed = emitted.append

    result = run(gateway, tmp_path)

    timeline = result["timeline"]
    assert [event["kind"] for event in timeline] == [
        "assistant", "tool_call", "tool_result", "assistant",
    ]
    assert timeline[0]["payload"]["summary"] == public_reply
    assert "private-native-reasoning" not in json.dumps(result)
    assert '"path": "a.txt"' not in json.dumps(timeline)
    assert [item["timelineEvent"] for item in emitted] == timeline
    assert len(model.requests) == 2


@pytest.mark.parametrize("kind", ["hidden", "invalid"])
def test_native_gateway_rejects_every_call_without_dispatch(tmp_path, kind):
    receipt = call("bad-1", "not_advertised" if kind == "hidden" else "vrcforge_read_text_file", {})
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
    run_planning_continuation(gateway, tmp_path)
    assert not invoked
    assert model.requests[1]["messages"][-2]["tool_call_id"] == "phase"
    assert "entered_execution" in model.requests[1]["messages"][-2]["content"]
    assert json.loads(model.requests[1]["messages"][-1]["content"].split(": ", 1)[1])["exposureLayer"] == "execution"


def test_native_action_contract_explains_supervised_execution_boundary(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        {"role": "assistant", "content": "Ready"},
    ])

    result = run_planning_continuation(gateway, tmp_path)
    action = next(
        item["function"] for item in model.requests[0]["tools"]
        if item["function"]["name"] == "vrcforge_runtime_action"
    )
    description = action["description"]
    assert "When to use" in description and "When NOT to use" in description
    action_description = action["parameters"]["properties"]["action"]["description"]
    assert "enter_execution" in action_description
    assert "requested change" in action_description
    assert "performs no write" in action_description
    assert "does not bypass approval" in action_description
    assert "reply ends the turn" in action_description
    assert "shell runs an authorized host command" in action_description
    assert "correct resubmits a failed action with corrected arguments" in action_description
    assert "enter_execution" in runtime_state(model.requests[0])["allowedRuntimeActions"]
    assert not invoked
    assert model.requests[1]["messages"][-2]["tool_call_id"] == "phase"
    assert "entered_execution" in model.requests[1]["messages"][-2]["content"]
    assert json.loads(model.requests[1]["messages"][-1]["content"].split(": ", 1)[1])["exposureLayer"] == "execution"
    assert len(model.requests) >= 2


def test_native_control_validation_recovers_on_valid_control_then_tool(tmp_path):
    invalid = call("bad-control", "vrcforge_runtime_action", {
        "action": "enter_execution", "unexpected": True,
    })
    valid = call("good-control", "vrcforge_runtime_action", {"action": "enter_execution"})
    gateway, model, invoked = setup_gateway(tmp_path, [invalid, valid,
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish])
    result = run_planning_continuation(gateway, tmp_path)
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
        assert budgeted_request_history(model.requests[1])[-1] == {"role": "user", "content": "Stop reading; just reply"}


def test_native_gateway_failure_reaches_model_without_success_claim(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [call("failed", "vrcforge_read_text_file", {"path": "a.txt"}),
                                                       {"role": "assistant", "content": "Read failed"}])
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.", "plan/preview",
                          lambda args: {"ok": False, "error": "fixture-read-failed"})
    result = run(gateway, tmp_path)
    assert "fixture-read-failed" in budgeted_request_history(model.requests[1])[-1]["content"]
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


def test_native_gateway_host_binds_completion_without_model_copying_ids(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("final", "vrcforge_runtime_action", {"action": "reply", "reply": "Checked",
             "completion_claim": {"satisfied": True}}),
    ])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    assert len(model.requests) == 2
    assert result["plan"]["nextStep"] == "done"
    assert result["plan"]["completionClaim"] == {"satisfied": True}
    expected = [a["actionId"] for a in result["plan"]["task"]["actions"] if a["status"] == "completed"]
    assert expected
    assert result["plan"]["taskCompletion"]["evidenceActionIds"] == expected


def test_native_gateway_completion_rejection_is_returned_to_same_call(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("unbound", "vrcforge_runtime_action", {"action": "reply", "reply": "Incorrect success",
              "completion_claim": {"satisfied": True, "evidence_action_ids": ["not-executed"]}}), finish])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    assert budgeted_request_history(model.requests[2])[-1]["tool_call_id"] == "unbound"
    assert "requiredEvidenceActionIds" in budgeted_request_history(model.requests[2])[-1]["content"]
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
    assert budgeted_request_history(model.requests[1])[-1] == {"role": "user", "content": "Change direction now"}


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
