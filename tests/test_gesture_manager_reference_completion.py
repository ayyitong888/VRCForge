"""Real registration/identity path with local fixtures; no Unity or provider."""
import copy
import json

import pytest

import dashboard_server as ds
from agent_gateway import AgentGateway, AgentGatewayError
from execution_target import canonical_namespace
from tests.test_execution_target_reference_equivalence import _live_target


TOOL = "vrcforge_gesture_manager_enter_play_mode"


def _registered_fixture(tmp_path, monkeypatch, tool=TOOL, scope="avatar"):
    registered = ds.AGENT_GATEWAY._write_handlers[tool]
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.allow_write_requests = True
    config.execution_mode = "approval"
    gateway.save_config(config)
    gateway._write_handlers[tool] = registered
    monkeypatch.setattr(gateway, "execution_target_session_context", lambda: "fixture-session")
    monkeypatch.setattr(ds, "AGENT_GATEWAY", gateway)
    target = _live_target(tmp_path / "Project", scope=scope)
    ref = gateway.bind_execution_target(target, project_root=target["project"]["root"])["executionTarget"]
    return gateway, target, ref


@pytest.mark.parametrize("tool,scope", [(TOOL, "avatar"), ("vrcforge_set_play_mode", "project")])
def test_registered_play_request_freezes_reference_before_domain_reload(tmp_path, monkeypatch, tool, scope):
    gateway, target, ref = _registered_fixture(tmp_path, monkeypatch, tool, scope)
    arguments = {"projectPath": target["project"]["root"], "executionTarget": ref}
    arguments.update({"avatarPath": "Root/Avatar"} if tool == TOOL else {"isPlaying": True})
    requested = gateway.approval_transactions.create_apply_request({"target_tool": tool, "arguments": arguments})
    prepared = gateway._approvals[requested["approval"]["id"]]["arguments"]
    # Simulate only a Core reload, keeping the project, PID and process lifetime.
    descriptor = tmp_path / "Project/Library/VRCForge/mcp-core.json"
    contents = json.loads(descriptor.read_text())
    contents["instanceId"] = "fixture-reloaded-core"
    descriptor.write_text(json.dumps(contents), encoding="utf-8")
    fresh = copy.deepcopy(target)
    fresh["scope"] = "project"
    fresh.pop("scene", None)
    fresh.pop("avatar", None)
    fresh["editor"]["coreInstanceId"] = contents["instanceId"]
    fresh["namespace"] = canonical_namespace(fresh)
    monkeypatch.setattr(ds, "list_execution_targets_sync", lambda _args: {"ok": True, "targets": [fresh]})
    monkeypatch.setattr(ds, "gesture_manager_status_sync", lambda _args: {
        "ok": True, "isPlayMode": True, "managers": [{"avatarPath": "Root/Avatar", "moduleConnected": True, "managerPath": "GM"}],
    })
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda _args: object())
    monkeypatch.setattr(ds, "capture_scene_view_status_direct", lambda *_args, **_kwargs: {"isPlayMode": True})
    result = ds.AGENT_GATEWAY._write_handlers[tool].verification_finalize_handler(prepared, {}, {"ok": True})
    assert prepared["executionTarget"] == target
    assert arguments["executionTarget"] == ref
    assert result["identityVerification"]["verified"] is True
    assert result["identityVerification"]["editor"]["coreInstanceId"] == "fixture-reloaded-core"


def test_registered_gm_preparer_rejects_reference_for_another_project(tmp_path, monkeypatch):
    gateway, target, ref = _registered_fixture(tmp_path, monkeypatch)
    other = tmp_path / "OtherProject"
    other.mkdir()
    with pytest.raises(AgentGatewayError):
        gateway.approval_transactions.create_apply_request({"target_tool": TOOL, "arguments": {
            "projectPath": str(other), "avatarPath": "Root/Avatar", "executionTarget": ref,
        }})
