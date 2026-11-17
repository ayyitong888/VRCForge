import pytest

from agent_gateway import AgentGateway


OWNER = {"session_id": "runtime", "turn_id": "server", "client_turn_id": "client"}
STEER = {"sessionId": "runtime", "targetClientTurnId": "client", "clientTurnId": "input",
         "laneId": "chat", "message": "use the latest file"}


def test_accepted_steer_survives_restart_with_exact_lane_owner(tmp_path):
    def start():
        return AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    gateway = start()
    gateway.runtime_sessions.begin_turn(**OWNER)
    request = {**STEER, "provider": "example-provider", "planMode": True,
               "attachments": [{"id": "a", "name": "notes.txt", "size": 4,
                                "payloadKind": "vault_file", "payloadHash": "a" * 64,
                                "vaultKind": "file", "content": "never persist inline bytes"}]}
    accepted = gateway.submit_runtime_steer(request)
    assert accepted["accepted"] and accepted["mode"] == "steer"
    assert not gateway.list_runtime_followups(session_id="chat")
    assert not gateway.claim_runtime_followups(session_id="chat", owner_id="other")

    restarted = start()
    [recovered] = restarted.list_runtime_followups(session_id="chat", include_terminal=False)
    assert recovered["status"] == "pending"
    assert recovered["runtimeSessionId"] == "runtime"
    assert recovered["targetClientTurnId"] == "client"
    assert recovered["clientTurnId"] == "input"
    assert recovered["message"] == request["message"]
    assert recovered["queueId"] == accepted["queueId"]
    assert recovered["provider"] == "example-provider" and recovered["planMode"] is True
    assert recovered["attachments"][0]["payloadHash"] == "a" * 64
    assert recovered["attachments"][0]["vaultKind"] == "file"
    assert "never persist inline bytes" not in gateway._runtime_followup_queue._ports.path.read_text()
    assert not restarted.list_runtime_followups(session_id="different-lane")


@pytest.mark.parametrize("boundary", ["drain", "stop"])
def test_restart_requires_manual_confirmation_after_drain_or_stop(tmp_path, boundary):
    path = tmp_path / "config.json"
    audit = tmp_path / "audit"
    gateway = AgentGateway(path, audit)
    gateway.runtime_sessions.begin_turn(**OWNER)
    accepted = gateway.submit_runtime_steer(STEER)
    if boundary == "drain":
        [drained] = gateway._drain_runtime_steers(**OWNER)
        assert drained["inputId"] == "input"
    else:
        cancelled = gateway.request_runtime_cancel({**OWNER})
        assert cancelled["dispatchStopped"]
        assert not gateway._drain_runtime_steers(**OWNER)
        assert gateway.submit_runtime_steer({**STEER, "clientTurnId": "after-stop"})["accepted"] is False

    restarted = AgentGateway(path, audit)
    [item] = restarted.list_runtime_followups(session_id="chat", include_terminal=False)
    assert (item["status"], item["queueId"]) == ("paused", accepted["queueId"])
    assert not restarted.claim_runtime_followups(session_id="chat", owner_id="automatic")
    [claimed] = restarted.claim_runtime_followups(session_id="chat", owner_id="explicit", queue_id=item["queueId"])
    assert claimed["clientTurnId"] == "input"


def test_settled_steer_has_one_tombstone_and_no_followup(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    def body(params, **kwargs):
        params.update(_resolvedRuntimeSessionId="runtime", _resolvedRuntimeTurnId="server",
                      _resolvedRuntimeClientTurnId="client")
        gateway.runtime_sessions.begin_turn(**OWNER)
        first = gateway.submit_runtime_steer(STEER)
        duplicate = gateway.submit_runtime_steer(STEER)
        assert first["accepted"] and duplicate["deduped"]
        assert len(gateway._drain_runtime_steers(**OWNER)) == 1
        return {"ok": True, "plan": {"nextStep": "done"}, "consumedSteerInputIds": ["input"]}

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", body)
    result = gateway._runtime_message_impl({})
    assert "deferredSteerFollowups" not in result
    [item] = AgentGateway(tmp_path / "config.json", tmp_path / "audit").list_runtime_followups(session_id="chat")
    assert item["status"] == "acked" and "message" not in item
