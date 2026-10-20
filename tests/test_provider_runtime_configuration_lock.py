"""Offline lifecycle/config mutation guards; no provider, app, or Unity process."""
from dataclasses import replace
from threading import Event, Thread

import pytest

from agent_gateway import AgentGateway
from provider_configuration_service import ProviderConfigurationBusyError
from tests.test_provider_configuration_service import _owner


def setup_owner(tmp_path):
    owner = _owner(tmp_path / "provider.json")
    owner.save_api_config(owner.current_api_config())
    gateway = AgentGateway(tmp_path / "agent.json", tmp_path / "audit")
    owner.runtime_waiting = gateway.runtime_sessions.runtime_waiting_for_configuration
    return owner, gateway


@pytest.mark.parametrize("field,value", [("model", "new-model"), ("api_key", "fixture-new-key"), ("base_url", "https://fixture.invalid"), ("thinking_level", "high"), ("provider", "other"), ("api_type", "responses")])
def test_active_turn_rejects_binding_changes_before_disk_write(tmp_path, field, value):
    owner, _ = setup_owner(tmp_path)
    original = owner.current_api_config()
    disk = owner.config_path.read_bytes()
    with owner.bind_runtime_config():
        with pytest.raises(ProviderConfigurationBusyError):
            owner.save_api_config(replace(original, **{field: value}))
        assert owner.current_api_config() == original
        assert owner.config_path.read_bytes() == disk
    owner.save_api_config(replace(original, **{field: value}))
    assert getattr(owner.current_api_config(), field) == value


@pytest.mark.parametrize("payload", [
    {"write": {"status": "approval_pending"}, "plan": {"nextStep": "approval_required"}},
    {"plan": {"nextStep": "needs_user_action", "completionGate": {"reason": "question_pending"}}},
    {"shell": {"status": "running"}, "plan": {"nextStep": "wait"}},
])
def test_waiting_locks_until_cancel_then_allows_change(tmp_path, payload):
    owner, gateway = setup_owner(tmp_path)
    state = gateway.runtime_sessions
    state.record_final_response(session_id="fixture", client_turn_id="client", status="completed", response={"turnId": "turn", **payload})
    original = owner.current_api_config()
    disk = owner.config_path.read_bytes()
    with pytest.raises(ProviderConfigurationBusyError):
        owner.save_api_config(replace(original, model="new-model"))
    assert owner.config_path.read_bytes() == disk and owner.current_api_config() == original
    state.mark_cancel_requested(session_id="fixture", turn_id="turn", client_turn_id="client")
    owner.save_api_config(replace(original, model="new-model"))
    assert owner.current_api_config().model == "new-model"


@pytest.mark.parametrize("exit_kind", ["success", "exception", "cancelled"])
def test_binding_exit_always_unlocks(tmp_path, exit_kind):
    owner, _ = setup_owner(tmp_path)
    try:
        with owner.bind_runtime_config():
            assert owner.runtime_configuration_locked()
            if exit_kind != "success":
                raise RuntimeError(exit_kind)
    except RuntimeError:
        pass
    assert not owner.runtime_configuration_locked()
    owner.save_api_config(replace(owner.current_api_config(), model="after-exit"))


def test_failed_turn_and_process_restart_do_not_restore_settings_lock(tmp_path):
    owner, gateway = setup_owner(tmp_path)
    state = gateway.runtime_sessions
    state.record_final_response(session_id="fixture", client_turn_id="client", status="completed",
                                response={"write": {"status": "approval_pending"}})
    assert owner.runtime_configuration_locked()
    state.record_final_response(session_id="fixture", client_turn_id="client", status="failed", response={"plan": {"nextStep": "failed"}})
    assert not owner.runtime_configuration_locked()
    state.record_final_response(session_id="fixture", client_turn_id="client", status="completed",
                                response={"write": {"status": "approval_pending"}})
    restarted = _owner(owner.config_path)
    restarted_gateway = AgentGateway(tmp_path / "agent.json", tmp_path / "audit")
    restarted.runtime_waiting = restarted_gateway.runtime_sessions.runtime_waiting_for_configuration
    assert not restarted.runtime_configuration_locked()
    restarted.save_api_config(replace(restarted.current_api_config(), model="after-restart"))


def test_account_mutation_reservation_precedes_side_effect_and_blocks_turn_start(tmp_path):
    owner, _ = setup_owner(tmp_path)
    with owner.bind_runtime_config():
        with pytest.raises(ProviderConfigurationBusyError):
            with owner.configuration_change():
                pytest.fail("account mutation must not start")
    with owner.configuration_change():
        with pytest.raises(ProviderConfigurationBusyError):
            with owner.bind_runtime_config():
                pytest.fail("turn must not start while logout is in flight")
        owner.save_api_config(replace(owner.current_api_config(), model="changed"))
    with owner.bind_runtime_config():
        pass


def test_config_save_and_runtime_binding_share_one_lock(tmp_path):
    owner, _ = setup_owner(tmp_path)
    ready, release = Event(), Event()
    def run():
        with owner.bind_runtime_config():
            ready.set()
            assert release.wait(5)
    thread = Thread(target=run)
    thread.start()
    try:
        assert ready.wait(5)
        with pytest.raises(ProviderConfigurationBusyError):
            owner.save_api_config(replace(owner.current_api_config(), model="racing-save"))
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    owner.save_api_config(replace(owner.current_api_config(), model="idle-save"))


def test_actual_approval_wait_prevents_original_binding_change(tmp_path):
    from tests.test_native_multi_call_admission import _receipt
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
    from tests.test_native_runtime_gateway import call, finish, run
    project = str(tmp_path / "UnityProject")
    gateway, model, writes = _write_gateway_for_mode(tmp_path, [_receipt(
        call("write", "fixture_write", {"projectRoot": project, "value": "approved"}),
        call("after", "vrcforge_read_text_file", {"path": "after.txt"})), finish])
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", lambda _: {"text": "readback"})
    _set_mode(gateway, "approval")
    owner = _owner(tmp_path / "provider.json")
    owner.runtime_waiting = gateway.runtime_sessions.runtime_waiting_for_configuration
    initial = run(gateway, tmp_path, message="Write and read", maxAgenticTurns=2)
    assert owner.runtime_configuration_locked(), initial
    with pytest.raises(ProviderConfigurationBusyError):
        owner.save_api_config(replace(owner.current_api_config(), thinking_level="high"))
    approval_id = initial["approval_id"]
    approval = gateway._approvals[approval_id]
    gateway.approval_transactions.approve(approval_id)
    applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    resumed = gateway.resume_runtime_task_after_approval(approval, applied)
    assert resumed["plan"]["nextStep"] == "done"
    assert len(writes) == 1 and len(model.requests) == 2
    assert not owner.runtime_configuration_locked()
    owner.save_api_config(replace(owner.current_api_config(), thinking_level="high"))


def test_oauth_logout_rejects_before_touching_account(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from chatgpt_integration.routes import build_chatgpt_oauth_router
    from tests.test_chatgpt_oauth_routes import FakeAuth
    owner, _ = setup_owner(tmp_path)
    auth = FakeAuth()
    async def activate(_):
        return {"ok": True}
    app = FastAPI()
    app.include_router(build_chatgpt_oauth_router(authorize=lambda _: None, activate=activate,
        service_factory=lambda: auth, configuration_change=owner.configuration_change))
    with TestClient(app) as client:
        with owner.bind_runtime_config():
            response = client.post("/api/chatgpt/logout", json={"account_id": "fixture"})
            assert response.status_code == 409
            assert auth.calls == []
        assert client.post("/api/chatgpt/logout", json={"account_id": "fixture"}).status_code == 200
        assert auth.calls == [("logout", "fixture")]
