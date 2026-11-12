from concurrent.futures import ThreadPoolExecutor
import threading
import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agent_gateway import AgentGateway, AgentGatewayError
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from agent_runtime_turn_identity import continuation_client_turn_id
from dashboard_api_models import AgentRuntimeMessageRequest


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


@pytest.mark.parametrize("identity", [{"session_id": "s" * 181}, {"clientTurnId": "c" * 241}])
def test_oversized_turn_identity_is_rejected_before_admission(tmp_path, identity):
    with pytest.raises(ValidationError):
        AgentRuntimeMessageRequest(message="fixture", **identity)
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    with pytest.raises(AgentGatewayError, match="exceeds its length limit") as error:
        gateway.runtime_message({"message": "fixture", **identity})
    assert error.value.status_code == 400
    assert gateway.runtime_runs.list_runs()["runs"] == []


def test_runtime_stop_cancels_only_desktop_actions_of_the_exact_generation(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    state = gateway.runtime_sessions
    assert state.begin_turn(session_id="requested", turn_id="active", client_turn_id="reused")
    assert state.begin_turn(session_id="unrelated", turn_id="other", client_turn_id="reused")
    actions = [{"actionId": action, "sessionId": session, "turnId": turn, "clientTurnId": "reused"}
               for action, session, turn in [("right", "requested", "active"), ("old", "requested", "prior"),
                                             ("other", "unrelated", "other")]]
    desktop = type(gateway.desktop)
    with (patch.object(desktop, "list_active_desktop_actions", return_value={"actions": actions}),
          patch.object(desktop, "request_desktop_action_cancel", return_value={"ok": True}) as cancel):
        result = gateway.request_runtime_cancel({"sessionId": "requested", "turnId": "active", "clientTurnId": "reused"})
    assert result["cancelledDesktopActionIds"] == ["right"]
    assert [call.args[0] for call in cancel.call_args_list] == ["right"]
    assert not state.cancel_requested(session_id="unrelated", turn_id="other", client_turn_id="reused")


def test_unscoped_stop_cannot_attach_to_a_future_session():
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
    assert state.mark_cancel_requested(client_turn_id="reused") == ("", "", "reused")
    assert not state.queue_turn(session_id="unrelated", turn_id="future", client_turn_id="reused").is_set()


def test_interrupted_visual_feedback_recovers_under_its_dispatched_identity(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    seed = {"sessionId": "fixture", "clientTurnId": "parent", "_questionKind": "visual_checkpoint", "_questionFeedbackRevision": 2}
    expected = gateway._question_continuation_client_turn_id("question", seed)
    event = gateway.record_interrupted_runtime_task(task_seed=seed, continuation_source="question_answered",
                                                    owned_id="question", summary="fixture")
    assert event["clientTurnId"] == expected == "parent:question:question:feedback:2"
    assert gateway.get_runtime_session("fixture", client_turn_id=expected)["status"] == "completed"


def test_long_continuation_id_keeps_unique_suffix():
    ids = [continuation_client_turn_id("p" * 240, "subagent", "c" * 99 + revision) for revision in "12"]
    assert all(len(value) == 240 for value in ids)
    assert ids[0].endswith(":subagent:" + "c" * 99 + "1")
    assert ids[1].endswith(":subagent:" + "c" * 99 + "2")


def test_same_session_native_owner_cannot_settle_another_running_call():
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
    assert state.begin_turn(session_id="s", turn_id="a", client_turn_id="ca")
    state.begin_native_turn("s", binding="b", turn_id="a", message="first")
    state.append_native_assistant("s", binding="b", message={
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "call-a", "type": "function", "function": {"name": "read", "arguments": "{}"},
        }],
    })
    state.mark_native_dispatch("s", binding="b", call_id="call-a")
    assert not state.begin_turn(session_id="s", turn_id="b", client_turn_id="cb")
    state.settle_native_call("s", binding="b", call_id="call-a", content="actual result")
    assert state.native_conversation("s", binding="b")["messages"][-1]["content"] == "actual result"


@pytest.mark.parametrize("prior", [{}, {"sessionId": "outer", "turnId": "outer-turn", "clientTurnId": "outer-cid"}])
def test_failed_turn_restores_stream_identity_on_reused_worker(tmp_path, monkeypatch, prior):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    sessions = gateway.runtime_sessions

    def body(params, **kwargs):
        sessions.set_stream_context({"sessionId": "failed", "turnId": "failed-turn", "clientTurnId": "failed-cid"})
        raise OSError("original ledger append failure")

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", body)

    def invoke():
        sessions.set_stream_context(prior)
        with pytest.raises(OSError, match="original ledger"):
            gateway._runtime_message_impl({"message": "fixture"})

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(invoke).result()
        assert worker.submit(sessions.stream_context).result() == prior


@pytest.mark.parametrize("session_stop", [False, True])
def test_waiting_turn_stop_never_starts_after_owner_finishes(tmp_path, session_stop):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    state = gateway.runtime_sessions
    assert state.begin_turn(session_id="s", turn_id="a", client_turn_id="ca")
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(gateway._admit_runtime_turn, session_id="s", turn_id="b", client_turn_id="cb")
        wait_until(lambda: ("s", "cb") in state._waiting_turns)
        assert state.final_response(session_id="s", client_turn_id="cb")["status"] == "running"
        with gateway._runtime_continuation_condition:
            assert state.mark_cancel_requested(session_id="s", **({} if session_stop else {"turn_id": "b", "client_turn_id": "cb"}))
            state.finish_turn(session_id="s", turn_id="a", client_turn_id="ca")
            state.record_final_response(session_id="s", client_turn_id="ca", status="completed", response={})
            gateway._runtime_continuation_condition.notify_all()
        with pytest.raises(Exception, match="stopped before execution"):
            future.result(timeout=3)
        assert not state.owns_turn(session_id="s", turn_id="b", client_turn_id="cb")


@pytest.mark.parametrize("session_stop", [False, True])
def test_stop_before_admission_is_consumed_without_starting_provider(tmp_path, session_stop):
    from tests.test_native_runtime_gateway import setup_gateway
    gateway, model, _ = setup_gateway(tmp_path, [])
    state = gateway.runtime_sessions
    state.mark_cancel_requested(session_id="s", **({} if session_stop else {"client_turn_id": "ca"}))
    with pytest.raises(Exception, match="stopped before execution"):
        gateway.runtime_message({"message": "do not run", "sessionId": "s", "clientTurnId": "ca"})
    assert model.requests == []
    assert not state.session_busy("s")
    assert not state.cancel_requested(session_id="s", client_turn_id="ca")


def test_waiting_turn_is_fifo_and_other_session_remains_concurrent(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    state = gateway.runtime_sessions
    assert state.begin_turn(session_id="s", turn_id="a", client_turn_id="ca")
    with ThreadPoolExecutor(max_workers=2) as workers:
        second = workers.submit(gateway._admit_runtime_turn, session_id="s", turn_id="b", client_turn_id="cb")
        wait_until(lambda: ("s", "cb") in state._waiting_turns)
        third = workers.submit(gateway._admit_runtime_turn, session_id="s", turn_id="c", client_turn_id="cc")
        wait_until(lambda: ("s", "cc") in state._waiting_turns)
        gateway._admit_runtime_turn(session_id="other", turn_id="z", client_turn_id="cz")
        assert state.owns_turn(session_id="other", turn_id="z", client_turn_id="cz")
        for turn, cid, future in [("a", "ca", second), ("b", "cb", third)]:
            with gateway._runtime_continuation_condition:
                state.finish_turn(session_id="s", turn_id=turn, client_turn_id=cid)
                state.record_final_response(session_id="s", client_turn_id=cid, status="completed", response={})
                gateway._runtime_continuation_condition.notify_all()
            future.result(timeout=3)
        assert state.owns_turn(session_id="s", turn_id="c", client_turn_id="cc")


@pytest.mark.parametrize("stop_pending", [False, True])
def test_fast_child_keeps_delivery_pending_until_parent_releases_owner(tmp_path, stop_pending):
    from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    state = gateway.runtime_sessions
    state.begin_turn(session_id="s", turn_id="a", client_turn_id="ca")
    delivered = []
    registry = SubAgentTaskRegistry(
        tmp_path / "children", roles=[SubAgentRole("fast", "Fast", "Fixture")],
        handlers={"fast": lambda *_: {"ok": True, "summaryText": "done"}},
        task_finished=lambda event: delivered.append(not state.session_busy("s")),
        wait_parent_idle=gateway.wait_runtime_session_idle,
    )
    created = registry.create_task(role="fast", task="read", display_name="fast", parent_session_id="s", params={
        "_taskSeed": {"sessionId": "s", "turnId": "a", "clientTurnId": "ca"},
    })
    task_id = created["task"]["id"]
    wait_until(lambda: registry.get_task(task_id)["task"]["status"] == "completed")
    assert registry.get_task(task_id)["task"]["parentContinuationStatus"] == "pending"
    assert not delivered
    if stop_pending:
        registry.cancel_task(task_id)
    with gateway._runtime_continuation_condition:
        state.finish_turn(session_id="s", turn_id="a", client_turn_id="ca")
        state.record_final_response(session_id="s", client_turn_id="ca", status="completed", response={})
        gateway._runtime_continuation_condition.notify_all()
    registry._threads[task_id].join(3)
    assert delivered == ([] if stop_pending else [True])
    assert registry.get_task(task_id)["task"]["parentContinuationStatus"] == ("interrupted" if stop_pending else "delivered")


@pytest.mark.parametrize("stop_pending", [False, True])
def test_shell_delivery_stays_pending_until_parent_owner_finishes(tmp_path, monkeypatch, stop_pending):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    state = gateway.runtime_sessions
    state.begin_turn(session_id="s", turn_id="a", client_turn_id="ca")
    gateway.runtime_runs.stage_shell_continuation(shell_session_id="shell", task_seed={
        "sessionId": "s", "turnId": "a", "clientTurnId": "ca",
    }, terminal_event={"shellSessionId": "shell", "runtimeSessionId": "s", "turnId": "a", "clientTurnId": "ca"})
    delivered = []
    monkeypatch.setattr(gateway, "resume_runtime_task_after_shell", lambda event: delivered.append(not state.session_busy("s")) or {"ok": True})
    worker = threading.Thread(target=gateway._dispatch_runtime_shell_continuation, args=("shell",), daemon=True)
    worker.start()
    worker.join(0.1)
    try:
        assert gateway.runtime_runs.shell_continuation_states()[0]["shellContinuationState"] == "pending"
        assert not delivered
        if stop_pending:
            gateway.request_runtime_cancel({"sessionId": "s", "turnId": "a", "clientTurnId": "ca"})
    finally:
        with gateway._runtime_continuation_condition:
            state.finish_turn(session_id="s", turn_id="a", client_turn_id="ca")
            state.record_final_response(session_id="s", client_turn_id="ca", status="completed", response={})
            gateway._runtime_continuation_condition.notify_all()
        worker.join(3)
    assert delivered == ([] if stop_pending else [True])


def test_two_native_gateway_requests_keep_first_tool_receipt_until_owner_releases(tmp_path):
    from tests.test_native_runtime_gateway import call, finish, setup_gateway

    def final_receipt(call_id):
        def reply(request):
            result = finish(request)
            result["tool_calls"][0]["id"] = call_id
            return result
        return reply

    gateway, model, _ = setup_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a"}), final_receipt("final-a"),
        call("read-b", "vrcforge_read_text_file", {"path": "b"}), final_receipt("final-b"),
    ])
    started, release = threading.Event(), threading.Event()

    def read(params):
        if params["path"] == "a":
            started.set()
            assert release.wait(5)
        return {"text": "actual receipt " + params["path"]}

    gateway.register_tool("vrcforge_read_text_file", "When to use: read fixture. When NOT to use: write.", "plan/preview", read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(gateway.runtime_message, {"message": "read a", "sessionId": "s", "clientTurnId": "ca", "maxAgenticTurns": 4})
        assert started.wait(5)
        second = workers.submit(gateway.runtime_message, {"message": "read b", "sessionId": "s", "clientTurnId": "cb", "maxAgenticTurns": 4})
        try:
            wait_until(lambda: ("s", "cb") in gateway.runtime_sessions._waiting_turns)
            assert len(model.requests) == 1
            snapshot = next(iter(gateway.runtime_sessions._native_conversations.values()))
            assert gateway.runtime_sessions._native_pending(snapshot) == {"read-a"}
        finally:
            release.set()
        assert first.result(timeout=8)["ok"]
        assert second.result(timeout=8)["ok"]
    snapshot = next(iter(gateway.runtime_sessions._native_conversations.values()))
    assert not gateway.runtime_sessions._native_pending(snapshot)
    first_tool = next(item for item in snapshot["messages"] if item.get("tool_call_id") == "read-a")
    assert "actual receipt a" in first_tool["content"]
    assert "execution_unknown" not in first_tool["content"]
