from pathlib import Path
import pytest
from agent_gateway import AgentGateway, AgentGatewayError
from agent_runtime_native_turn import NativeRuntimeTurn


def test_mid_loop_409_records_failed_turn_and_terminal_without_masking(tmp_path, monkeypatch):
    gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
    error=AgentGatewayError("Exact target unavailable",status_code=409)
    def body(params,**kwargs):
        params.update(_resolvedRuntimeSessionId="failure-session",_resolvedRuntimeTurnId="failure-turn",_resolvedRuntimeClientTurnId="failure-client")
        gateway.runtime_sessions.begin_turn(session_id="failure-session",turn_id="failure-turn",client_turn_id="failure-client")
        gateway._runtime_run_ledger.append({"event":"runtime_turn_started","status":"running","sessionId":"failure-session","turnId":"failure-turn","clientTurnId":"failure-client"})
        params["_runtimeTurnStarted"]=True
        params["_runtimeFailureProgress"]={"steps":[{"kind":"skill","tool":"vrcforge_load_tool_block","status":"success"}],"timeline":[],"contextUsage":{"requestCount":5,"inputTokens":123}}
        raise error
    monkeypatch.setattr(gateway,"_runtime_message_impl_body",body)
    with pytest.raises(AgentGatewayError) as caught:
        gateway._runtime_message_impl({"message":"read current model","provider":"fixture","model":"fixture-model"})
    assert caught.value is error
    turn=gateway.get_runtime_session("failure-session")["session"]["turns"][-1]
    assert turn["status"]=="failed" and turn["error"]["statusCode"]==409
    assert turn["steps"][0]["tool"]=="vrcforge_load_tool_block"
    assert turn["contextUsage"]=={"requestCount":5,"inputTokens":123}
    terminal=gateway._runtime_run_ledger.read_events(limit=20)[-1]
    assert terminal["status"]=="failed" and terminal["event"]=="runtime_turn_completed"
    assert terminal["provider"]=="fixture" and terminal["model"]=="fixture-model"
    audit=gateway.approval_transactions.recent_audit_logs(limit=10)
    failed=next(row for row in audit if row.get("event")=="agent_runtime_turn")
    assert failed["status"]=="failed" and failed["error"]["statusCode"]==409
    assert failed["attemptedTools"]==["vrcforge_load_tool_block"]
    assert gateway.runtime_sessions.begin_turn(session_id="failure-session",turn_id="next",client_turn_id="failure-client")


def test_failure_record_storage_error_does_not_replace_original(tmp_path, monkeypatch):
    gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
    original=AgentGatewayError("original 409",status_code=409)
    def body(params,**kwargs):
        params.update(_resolvedRuntimeSessionId="s",_resolvedRuntimeTurnId="t",_resolvedRuntimeClientTurnId="c",_runtimeTurnStarted=True)
        raise original
    monkeypatch.setattr(gateway,"_runtime_message_impl_body",body)
    monkeypatch.setattr(gateway._runtime_run_ledger.__class__,"append",lambda *a,**k: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(AgentGatewayError) as caught:gateway._runtime_message_impl({"message":"read"})
    assert caught.value is original


def test_native_settlement_error_preserves_original_failure_and_terminal(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    original = NameError("name 'asset_segment' is not defined")
    published = []
    monkeypatch.setattr(gateway, "_runtime_turn_completed", published.append)
    native = object.__new__(NativeRuntimeTurn)
    monkeypatch.setattr(NativeRuntimeTurn, "settle", lambda *_a, **_k: (_ for _ in ()).throw(NameError("settlement failed")))

    def body(params, **_kwargs):
        gateway.runtime_sessions.begin_turn(session_id="s", turn_id="t", client_turn_id="c")
        params.update(_resolvedRuntimeSessionId="s", _resolvedRuntimeTurnId="t",
                      _resolvedRuntimeClientTurnId="c", _runtimeTurnStarted=True)
        params["_runtimeFailureProgress"] = {"_nativeTurn": native, "steps": [], "timeline": []}
        raise original

    monkeypatch.setattr(gateway, "_runtime_message_impl_body", body)
    with pytest.raises(NameError) as caught:
        gateway._runtime_message_impl({"message": "list the materials directory"})
    assert caught.value is original
    turn = gateway.get_runtime_session("s")["session"]["turns"][-1]
    assert turn["status"] == "failed"
    assert turn["error"]["message"] == str(original)
    assert turn["error"]["settlementError"]["message"] == "settlement failed"
    terminal = gateway._runtime_run_ledger.read_events(limit=20)[-1]
    assert terminal["event"] == "runtime_turn_completed" and terminal["status"] == "failed"
    assert len(published) == 1 and published[0]["plan"]["nextStep"] == "failed"


def test_admission_error_is_not_a_started_failed_turn(tmp_path, monkeypatch):
    gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
    def body(params,**kwargs):
        raise AgentGatewayError("not admitted",status_code=409)
    monkeypatch.setattr(gateway,"_runtime_message_impl_body",body)
    with pytest.raises(AgentGatewayError):
        gateway._runtime_message_impl({"message":"read","_runtimeTurnStarted":True})
    assert gateway._runtime_run_ledger.read_events(limit=20)==[]


def test_failure_record_uses_existing_redactor_and_omits_unknown_usage(tmp_path, monkeypatch):
    gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
    def body(params,**kwargs):
        params.update(_resolvedRuntimeSessionId="s",_resolvedRuntimeTurnId="t",_resolvedRuntimeClientTurnId="c",_runtimeTurnStarted=True)
        raise AgentGatewayError("Authorization Bearer fake-test-secret",status_code=409)
    monkeypatch.setattr(gateway,"_runtime_message_impl_body",body)
    with pytest.raises(AgentGatewayError):gateway._runtime_message_impl({"message":"read"})
    turn=gateway.get_runtime_session("s")["session"]["turns"][-1]
    assert "fake-test-secret" not in turn["error"]["message"]
    assert "contextUsage" not in turn
    assert "contextUsage" not in gateway._runtime_run_ledger.read_events(limit=20)[-1]
