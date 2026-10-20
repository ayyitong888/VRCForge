"""Real CU handlers with isolated files and no connected desktop bridge."""
import pytest

from agent_gateway import AgentGateway, AgentGatewayError


def owned_turn(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.developer_options_enabled = True
    config.computer_use_enabled = True
    gateway.save_config(config)
    scope = {"sessionId": "owned-session", "clientTurnId": "owned-client", "projectRoot": str(tmp_path / "ProjectA")}
    grant = gateway.desktop.issue_computer_use_turn_grant(scope)
    return gateway, {**scope, "_computerUseRequested": True, "_computerUseGrantId": grant["grantId"]}


@pytest.mark.parametrize("field", ["sessionId", "session_id", "clientTurnId", "client_turn_id", "projectRoot", "project_root", "projectPath"])
def test_cu_action_rejects_explicit_scope_conflict_before_dispatch(tmp_path, field):
    gateway, turn = owned_turn(tmp_path)
    with gateway.desktop.runtime_turn_context(turn):
        with pytest.raises(AgentGatewayError) as blocked:
            gateway.desktop.request_turn_authorized_desktop_action_and_wait({
                "action": "computer_use", "params": {"operation": "list_apps"},
                field: "different-scope", "waitForCompletion": False,
            })
        assert blocked.value.status_code == 403
    assert not gateway.desktop.desktop_action_log_path.exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_cu_action_keeps_owned_scope_and_restores_context(tmp_path, explicit):
    gateway, turn = owned_turn(tmp_path)
    action = {"action": "computer_use", "params": {"operation": "list_apps"}, "waitForCompletion": False}
    if explicit:
        action.update(session_id=turn["sessionId"], client_turn_id=turn["clientTurnId"],
                      projectPath=turn["projectRoot"].replace("\\", "/"))
    with gateway.desktop.runtime_turn_context(turn):
        result = gateway.desktop.request_turn_authorized_desktop_action_and_wait(action)
    # No bridge is registered: verify dispatch metadata without any OS action.
    assert result["status"] == "unavailable"
    for field in ("sessionId", "clientTurnId", "projectRoot"):
        assert result["event"][field] == turn[field]
    assert not gateway.desktop.computer_use_turn_active()


def test_cu_general_turn_keeps_empty_project_scope(tmp_path):
    gateway, turn = owned_turn(tmp_path)
    scope = {"sessionId": turn["sessionId"], "clientTurnId": "general-turn", "projectRoot": ""}
    grant = gateway.desktop.issue_computer_use_turn_grant(scope)
    with gateway.desktop.runtime_turn_context({**scope, "_computerUseRequested": True,
                                              "_computerUseGrantId": grant["grantId"]}):
        result = gateway.desktop.request_turn_authorized_desktop_action_and_wait({
            "action": "computer_use", "params": {"operation": "list_apps"},
            "projectRoot": "", "waitForCompletion": False,
        })
    assert result["event"]["projectRoot"] == ""
    assert result["event"]["sessionId"] == scope["sessionId"]


def test_cu_conflicting_alias_cannot_hide_behind_matching_canonical(tmp_path):
    gateway, turn = owned_turn(tmp_path)
    with gateway.desktop.runtime_turn_context(turn):
        with pytest.raises(AgentGatewayError) as blocked:
            gateway.desktop.request_turn_authorized_desktop_action_and_wait({
                "action": "computer_use", "params": {"operation": "list_apps"},
                "sessionId": turn["sessionId"], "session_id": "other-session",
                "waitForCompletion": False,
            })
        assert blocked.value.status_code == 403
    assert not gateway.desktop.desktop_action_log_path.exists()
