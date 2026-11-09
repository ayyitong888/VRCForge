import asyncio
import threading
from unittest.mock import AsyncMock, Mock
import pytest
import dashboard_server as host
from agent_gateway import AgentGateway, AgentGatewayError


def test_rejected_followup_keeps_input_and_can_be_reclaimed(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    queued = gateway.enqueue_runtime_followup({"sessionId": "lane", "clientTurnId": "input", "message": "do the requested task"})
    error = AgentGatewayError("admission rejected", status_code=409)
    error.runtime_admitted = False
    gateway.runtime_message = Mock(side_effect=error)
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    request = host.AgentRuntimeMessageRequest(message="do the requested task", session_id="s", clientTurnId="input", followupQueueId=queued["queueId"], followupLaneId="lane")
    with pytest.raises(host.HTTPException) as failure:
        asyncio.run(host.app_agent_runtime_message(request))
    item = gateway.list_runtime_followups(session_id="lane")[0]
    assert item["status"] == "paused"
    assert item["message"] == "do the requested task"
    assert failure.value.detail["code"] == "followup_admission_rejected"
    assert gateway.claim_runtime_followups(session_id="lane", owner_id="manual-retry", queue_id=queued["queueId"])[0]["clientTurnId"] == "input"


@pytest.mark.parametrize("admitted,recovery_status,expected", [(None, "missing", "claimed"), (False, "missing", "paused"), (True, "missing", "claimed"), (True, "failed", "acked"), (True, "completed", "acked")])
def test_followup_exception_uses_admission_and_exact_terminal_facts(tmp_path, monkeypatch, admitted, recovery_status, expected):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    queued = gateway.enqueue_runtime_followup({"sessionId": "lane", "clientTurnId": "input", "message": "task"})
    error = RuntimeError("provider or response failed")
    error.runtime_admitted = admitted
    gateway.runtime_message = Mock(side_effect=error)
    gateway.get_runtime_session = Mock(return_value={"status": recovery_status})
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    request = host.AgentRuntimeMessageRequest(message="task", session_id="s", clientTurnId="input", followupQueueId=queued["queueId"], followupLaneId="lane")
    with pytest.raises((RuntimeError, host.HTTPException)):
        asyncio.run(host.app_agent_runtime_message(request))
    assert gateway.list_runtime_followups(session_id="lane")[0]["status"] == expected


def test_runtime_message_marks_provider_setup_rejection_as_not_admitted(tmp_path, monkeypatch):
    from contextlib import contextmanager
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    @contextmanager
    def reject(_params):
        raise AgentGatewayError("provider not configured", status_code=409)
        yield
    from types import SimpleNamespace
    gateway._runtime_planner = SimpleNamespace(bind_turn=reject)
    with pytest.raises(AgentGatewayError) as failure:
        gateway.runtime_message({"message": "task", "_runtimeAdmitted": True})
    assert failure.value.runtime_admitted is False


def test_paused_release_retains_claim_on_storage_failure_and_can_cancel(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    queued = gateway.enqueue_runtime_followup({"sessionId": "lane", "clientTurnId": "input", "message": "task"})
    item = gateway.claim_runtime_followups(session_id="lane", owner_id="owner")[0]
    queue = gateway._runtime_followup_queue
    persist = queue._persist
    monkeypatch.setattr(queue, "_persist", Mock(side_effect=OSError("disk unavailable")))
    with pytest.raises(OSError):
        gateway.release_runtime_followup(queued["queueId"], "lane", item["claimToken"])
    assert queue.list()[0]["status"] == "claimed"
    monkeypatch.setattr(queue, "_persist", persist)
    assert gateway.release_runtime_followup(queued["queueId"], "lane", item["claimToken"])
    assert gateway.cancel_runtime_followup(queued["queueId"], "lane")


def test_successful_runtime_is_consumed_even_if_response_projection_fails(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    queued = gateway.enqueue_runtime_followup({"sessionId": "lane", "clientTurnId": "input", "message": "task"})
    gateway.runtime_message = Mock(return_value={"ok": True})
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(host, "attach_agent_harness_journey_receipt", Mock(side_effect=RuntimeError("response projection failed")))
    request = host.AgentRuntimeMessageRequest(message="task", session_id="s", clientTurnId="input", followupQueueId=queued["queueId"], followupLaneId="lane")
    with pytest.raises(RuntimeError):
        asyncio.run(host.app_agent_runtime_message(request))
    assert gateway.list_runtime_followups(session_id="lane")[0]["status"] == "acked"


def test_queued_user_turn_waits_for_child_delivery_before_native_history_advances(tmp_path, monkeypatch):
    from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry
    from tests.test_native_runtime_gateway import call, finish, setup_gateway

    gateway, model, _ = setup_gateway(tmp_path, [
        call("read-with-child", "vrcforge_delegate_subagent", {"task": "Read the fixture."}),
        finish,
        {"role": "assistant", "content": "Queued input handled after the report."},
    ])
    worker_release = threading.Event()
    dispatching = threading.Event()
    parent_resume = threading.Event()
    child_results = []

    def worker(_params, _cancel):
        assert worker_release.wait(5)
        return {"ok": True, "summaryText": "Verified fixture report."}

    def deliver(event):
        dispatching.set()
        assert parent_resume.wait(5)
        child_results.append(gateway.resume_runtime_task_after_sub_agent(event))

    registry = SubAgentTaskRegistry(
        tmp_path / "tasks", roles=[SubAgentRole("fixture", "Fixture", "Read the fixture.")],
        handlers={"fixture": worker}, task_finished=deliver,
        wait_parent_idle=gateway.wait_runtime_session_idle,
    )
    gateway.bind_runtime_subagent_control(
        active_parent_task_ids=registry.active_parent_task_ids, cancel_task=registry.cancel_task,
        parent_delivery_pending=registry.parent_delivery_pending,
    )

    def delegate(params):
        created = registry.create_task(
            role="fixture", task="Read", display_name="Reader", parent_session_id="parent",
            params={"_taskSeed": params["_taskSeed"]},
        )["task"]
        return {"ok": True, "status": "running", "subAgentTaskId": created["id"]}

    gateway.register_tool("vrcforge_delegate_subagent", "When to use: delegate a read. When NOT to use: write.",
                          "plan/preview", delegate)
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(host, "TELEGRAM_SERVICE", Mock())
    monkeypatch.setattr(host, "EVENT_BUS", Mock(broadcast=AsyncMock()))
    parent = {"sessionId": "parent", "clientTurnId": "initial", "projectRoot": str(tmp_path)}
    assert gateway.runtime_message({**parent, "message": "Read using the specialist."})["plan"]["reason"] == "sub_agent_pending"
    task_id = registry.list_tasks()["tasks"][0]["id"]
    queued = gateway.enqueue_runtime_followup({"sessionId": "lane", "clientTurnId": "queued", "message": "Next input"})
    request = host.AgentRuntimeMessageRequest(
        message="Next input", session_id="parent", clientTurnId="queued", project_root=str(tmp_path),
        followupQueueId=queued["queueId"], followupLaneId="lane",
    )

    def assert_deferred():
        with pytest.raises(host.HTTPException) as failure:
            asyncio.run(host.app_agent_runtime_message(request))
        assert failure.value.status_code == 409
        assert failure.value.detail["code"] == "followup_queue_not_ready"
        assert gateway.list_runtime_followups(session_id="lane")[0]["status"] == "pending"
        assert len(model.requests) == 1

    try:
        assert_deferred()
        worker_release.set()
        assert dispatching.wait(5)
        assert_deferred()
    finally:
        worker_release.set()
        parent_resume.set()
        registry._threads[task_id].join(5)
    assert len(child_results) == 1 and child_results[0]["plan"]["nextStep"] == "done"
    assert registry.get_task(task_id)["task"]["parentContinuationStatus"] == "delivered"
    sent = asyncio.run(host.app_agent_runtime_message(request))
    assert sent["plan"]["reply"] == "Queued input handled after the report."
    assert gateway.list_runtime_followups(session_id="lane")[0]["status"] == "acked"
    assert len(model.requests) == 3
