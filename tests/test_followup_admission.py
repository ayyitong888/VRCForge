import asyncio
from unittest.mock import Mock
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
