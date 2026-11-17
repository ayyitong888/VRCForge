import asyncio
import json
import threading

import pytest

from agent_task_loop import AgentTaskLoop, approval_task_context
from dashboard_api_models import AgentRuntimeMessageRequest, AgentRuntimeQueueRequest
from dashboard_server import agent_runtime_request_payload
from tests.test_native_permission_modes import MODES, _set_mode, _write_gateway_for_mode
from tests.test_native_runtime_gateway import call, run, setup_gateway, finish


def runtime_state(request):
    prefix = "Current runtime state (data): "
    return next(json.loads(item["content"][len(prefix):]) for item in reversed(request["messages"])
                if item.get("role") == "system" and item["content"].startswith(prefix))


def test_plan_request_defaults_to_execution_and_accepts_explicit_plan():
    assert AgentRuntimeMessageRequest(message="hi").plan_mode is False
    assert AgentRuntimeMessageRequest(message="hi", planMode=True).plan_mode is True
    assert agent_runtime_request_payload(AgentRuntimeMessageRequest(message="hi", planMode=True))["planMode"] is True


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("action", ["enter_execution", "shell"])
def test_explicit_plan_hides_writes_and_model_cannot_enter_execution(tmp_path, mode, action):
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase", "vrcforge_runtime_action", {
            "action": action, "shell_command": "Set-Content bypass.txt bad",
        }),
        {"role": "assistant", "content": "Here is the plan."},
    ])
    _set_mode(gateway, mode)
    result = run(gateway, tmp_path, planMode=True)
    assert result["exposureLayer"] == "planning"
    assert not invoked
    assert not (tmp_path / "bypass.txt").exists()
    assert len(model.requests) == 2
    assert any("plan_mode_read_only" in message.get("content", "")
               for message in model.requests[1]["messages"] if message["role"] == "tool")
    for request in model.requests:
        assert request["tools"] == model.requests[0]["tools"]
        tools = {item["function"]["name"]: item["function"] for item in request["tools"]}
        assert "fixture_write" not in tools
        assert "fixture_write" not in runtime_state(request)["availableToolNames"]
        assert runtime_state(request)["allowedRuntimeActions"] == ["reply", "correct"]
        assert runtime_state(request)["planMode"] is True


def test_plan_lock_survives_continuation_context():
    loop = AgentTaskLoop.from_approval_context(
        {"objective": "plan", "planMode": True, "exposureLayer": "planning"},
        {"status": "completed"},
    )
    assert loop.plan_mode is True
    seed = loop.approval_seed(requested_tool="vrcforge_ask_user", requested_kind="skill")
    context = approval_task_context(seed, tool="vrcforge_ask_user", arguments={})
    assert context["planMode"] is True


@pytest.mark.parametrize("proposal", [
    {"enterExecution": True},
    {"writeNeeded": True, "writeTool": "fixture_write", "writeParams": {}},
    {"skillNeeded": True, "skillTool": "fixture_write", "skillParams": {}},
    {"shellNeeded": True, "shellCommand": "Set-Content bypass.txt bad"},
    {"skillNeeded": True, "skillTool": "vrcforge_followup_task", "skillParams": {}},
    {"skillNeeded": True, "skillTool": "user.package", "skillParams": {}},
])
def test_gateway_plan_guard_blocks_proposals_even_if_planner_ignores_mode(tmp_path, proposal):
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [])
    _set_mode(gateway, "roslyn_full_auto")
    gateway.runtime_planner.plan_agent_turn = lambda *args, **kwargs: proposal
    result = run(gateway, tmp_path, planMode=True)
    assert result["plan"]["nextStep"] == "plan_mode_blocked"
    assert result["exposureLayer"] == "planning"
    assert not invoked
    assert not (tmp_path / "bypass.txt").exists()


def test_plan_allows_read_tools(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read", "vrcforge_read_text_file", {"path": "fixture.txt"}), finish,
    ])
    result = run(gateway, tmp_path, planMode=True)
    assert invoked
    assert result["exposureLayer"] == "planning"


def test_plan_can_finish_after_rejected_control_correction(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read", "vrcforge_read_text_file", {"path": "fixture.txt"}),
        call("invalid-correction", "vrcforge_runtime_action", {"action": "correct"}),
        finish,
    ])
    result = run(gateway, tmp_path, planMode=True)
    assert len(invoked) == 1
    assert result["plan"]["nextStep"] == "done"
    assert result["exposureLayer"] == "planning"


def test_plan_question_continuation_keeps_readonly_mode(tmp_path):
    from tests.test_native_permission_modes import _question_gateway

    gateway, model = _question_gateway(tmp_path, [
        call("question", "vrcforge_ask_user", {"question": "Which option?"}),
        {"role": "assistant", "content": "Here is the revised plan."},
    ])
    completed, done = [], threading.Event()
    gateway._runtime_turn_completed = lambda payload: (completed.append(payload), done.set())
    first = run(gateway, tmp_path, planMode=True)
    assert first["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(session_id="native-session", project_root=str(tmp_path))["questions"][0]
    gateway.questions.answer(question["questionId"], {"sessionId": "native-session", "projectRoot": str(tmp_path), "answer": "first"})
    assert done.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert completed[0]["planMode"] is True
    assert completed[0]["exposureLayer"] == "planning"


def test_plan_queue_envelope_survives_reload(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    gateway.enqueue_runtime_followup({"sessionId": "queue", "clientTurnId": "queued-plan", "message": "Plan", "planMode": True})
    reloaded, _, _ = setup_gateway(tmp_path, [])
    assert reloaded.claim_runtime_followups(session_id="queue", owner_id="test", limit=1)[0]["planMode"] is True


@pytest.mark.parametrize("saved_mode,replay_mode", [(True, None), (True, False), (False, True)])
def test_queued_replay_uses_persisted_plan_mode(tmp_path, monkeypatch, saved_mode, replay_mode):
    from unittest.mock import Mock
    import dashboard_server as host

    gateway, _, _ = setup_gateway(tmp_path, [])
    queued = gateway.enqueue_runtime_followup({
        "sessionId": "queue", "clientTurnId": "queued-plan", "message": "Inspect", "planMode": saved_mode,
    })
    reloaded, _, _ = setup_gateway(tmp_path, [])
    reloaded.runtime_message = Mock(return_value={
        "ok": True, "sessionId": "queue", "plan": {"nextStep": "done"}, "steps": [],
    })
    monkeypatch.setattr(host, "AGENT_GATEWAY", reloaded)
    request = {"session_id": "queue", "clientTurnId": "queued-plan", "message": "Inspect",
               "followupQueueId": queued["queueId"], "followupLaneId": "queue"}
    if replay_mode is not None:
        request["planMode"] = replay_mode
    asyncio.run(host.app_agent_runtime_message(AgentRuntimeMessageRequest(**request)))
    assert reloaded.runtime_message.call_args.args[0]["planMode"] is saved_mode



def test_same_chat_execute_after_explicit_plan_exit_runs_authorized_request(tmp_path):
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        {"role": "assistant", "content": "Here is the requested plan."},
        call("write", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "done"}),
    ])
    _set_mode(gateway, "approval")
    planned = run(gateway, tmp_path, message="How would you do this?", planMode=True)
    assert planned["planMode"] is True
    assert not invoked
    executed = run(
        gateway, tmp_path, message="First research the approach, then make the change.",
        clientTurnId="explicit-plan-exit", planMode=False,
    )
    assert executed["planMode"] is False
    assert "fixture_write" in runtime_state(model.requests[-1])["availableToolNames"]
    assert executed["write"]["status"] == "approval_pending"
    assert not invoked


@pytest.mark.parametrize("active_mode", [False, True])
def test_mode_change_while_restored_turn_is_running_queues_its_own_mode(tmp_path, monkeypatch, active_mode):
    import dashboard_server as host

    entered, release = threading.Event(), threading.Event()
    result = []

    def provider(_request):
        entered.set()
        release.wait(5)
        return {"role": "assistant", "content": "Current turn finished."}

    gateway, _model, _invoked = setup_gateway(tmp_path, [provider])
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    def run_restored_turn():
        params = {
            "message": "Continue the original instructions.",
            "sessionId": "native-session",
            "clientTurnId": "native-turn",
            "projectRoot": str(tmp_path),
            "planMode": not active_mode,
        }
        context = {
            "objective": params["message"], "sessionId": "native-session", "clientTurnId": "native-turn",
            "projectRoot": str(tmp_path), "planMode": active_mode,
            "exposureLayer": "planning" if active_mode else "execution",
        }
        with gateway.runtime_planner.bind_turn(params):
            result.append(gateway._runtime_message_impl(
                params, task_continuation={"context": context, "completion": {"status": "completed"}},
            ))

    worker = threading.Thread(target=run_restored_turn)
    worker.start()
    try:
        assert entered.wait(5)
        reply = asyncio.run(host.app_agent_runtime_queue(AgentRuntimeQueueRequest(
            sessionId="native-session", targetClientTurnId="native-turn", clientTurnId="new-mode",
            message="Work in the mode selected for this message.", planMode=not active_mode,
        )))
        assert reply["queueId"]
        assert reply.get("mode") != "steer"
        queued = gateway.list_runtime_followups(session_id="native-session")
        assert [(entry["clientTurnId"], entry["planMode"]) for entry in queued] == [("new-mode", not active_mode)]
    finally:
        release.set()
        worker.join(6)
    assert result[0]["planMode"] is active_mode
