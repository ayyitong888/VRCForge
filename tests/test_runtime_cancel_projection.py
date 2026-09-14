from test_agent_runtime_run_ledger import make_ledger
import pytest


@pytest.mark.parametrize("terminal", ["cancelled", "completed", "failed"])
def test_late_cancel_request_preserves_terminal_and_identity(tmp_path, terminal):
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running", "clientTurnId": "client", "sessionId": "session", "turnId": "turn"})
    ledger.append({"event": "runtime_turn_completed", "status": terminal, "clientTurnId": "client", "sessionId": "session", "turnId": "turn", "stepCount": 10})
    ledger.append({"event": "runtime_turn_cancel_requested", "status": "cancel_requested", "clientTurnId": "client", "sessionId": "", "turnId": ""})
    result = ledger.list_runs(client_turn_id="client")
    run = result["runs"][0]
    assert run["status"] == terminal
    assert run["event"] == "runtime_turn_completed"
    assert (run["sessionId"], run["turnId"]) == ("session", "turn")
    assert run["stepCount"] == 10
    assert run["eventCount"] == 3
    assert run["lastEvent"] == "runtime_turn_cancel_requested"
    assert result["events"][-1]["status"] == "cancel_requested"


def test_active_cancel_request_still_projects_and_keeps_resolved_identity(tmp_path):
    ledger, _ = make_ledger(tmp_path)
    ledger.append({"event": "runtime_turn_started", "status": "running", "clientTurnId": "client", "sessionId": "session", "turnId": "turn"})
    ledger.append({"event": "runtime_turn_cancel_requested", "status": "cancel_requested", "clientTurnId": "client", "sessionId": "", "turnId": ""})
    run = ledger.list_runs()["runs"][0]
    assert run["status"] == "cancel_requested"
    assert (run["sessionId"], run["turnId"]) == ("session", "turn")
