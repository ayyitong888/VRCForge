"""Replay an invoked plugin menu through the real approval/recovery boundaries."""
from copy import copy, deepcopy

import pytest

import dashboard_server
from agent_gateway import AgentGateway
from agent_task_loop import AgentTaskLoop


MENU = "vrcforge_execute_unity_editor_menu"


def _invoked_menu():
    # Portable shape of the real menu receipt that previously created a
    # success-without-verified failure and blocked the next unrelated write.
    return {
        "menuItemPath": "GameObject/Plugin/Setup",
        "selectionGlobalObjectIds": ["GlobalObjectId_V1-2-avatar-1-0"],
        "triggered": True,
        "state": "invoked",
        "resultVerification": "pending",
        "mutationMayHaveStarted": True,
        "checkpointRecoveryRequired": False,
        "selectionRestored": False,
    }


@pytest.fixture
def dispatch_case(tmp_path, monkeypatch):
    project = tmp_path / "Project"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (project / folder).mkdir(parents=True)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "auto"
    gateway.save_config(config)
    gateway._write_handlers[MENU] = copy(dashboard_server.AGENT_GATEWAY._write_handlers[MENU])
    service = gateway.approval_transactions
    receipts = {MENU: _invoked_menu()}
    calls = []
    checkpoints = []

    def checkpoint(_service, approval, _arguments):
        value = {"ok": True, "status": "ready", "id": "ckpt_" + approval["id"],
                 "projectRoot": str(project)}
        checkpoints.append(value)
        return value

    def invoke(_service, handler, *_args, **_kwargs):
        calls.append(handler.name)
        return deepcopy(receipts[handler.name])

    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", checkpoint)
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", invoke)
    monkeypatch.setattr(type(service), "_call_write_handler", invoke)
    arguments = {"projectPath": str(project), "menuItemPath": "GameObject/Plugin/Setup",
                 "selectionGlobalObjectIds": ["GlobalObjectId_V1-2-avatar-1-0"]}
    return gateway, service, arguments, receipts, calls, checkpoints


def _execute(case, lane, name=MENU, arguments=None):
    gateway, service, menu_arguments, *_ = case
    args = menu_arguments if arguments is None else arguments
    if lane == "external":
        prepared = service.prepare_external_mcp_write(name, args)
        return service.execute_prepared_external_mcp_write(prepared)
    config = gateway.ensure_config()
    config.execution_mode = "approval"
    gateway.save_config(config)
    loop = AgentTaskLoop("Invoke setup then inspect the generated objects.", session_id="menu-replay")
    requested = service.create_apply_request(
        {"target_tool": name, "arguments": args},
        task_context=loop.approval_seed(requested_kind="write", requested_tool=name, requested_arguments=args),
    )
    approval_id = requested["approval"]["id"]
    service.approve(approval_id)
    return service.apply_approved({"approval_id": approval_id})


@pytest.mark.parametrize("lane", ["external", "internal"])
def test_invoked_menu_is_pending_business_readback_and_does_not_block_next_write(dispatch_case, lane):
    gateway, service, args, receipts, calls, checkpoints = dispatch_case
    result = _execute(dispatch_case, lane)
    assert result["ok"] is True, result
    assert result["status"] == "invoked"
    assert result["result"] == _invoked_menu()
    assert result["outcome"]["status"] == "pending"
    assert result["outcome"]["commitState"] == "unknown"
    assert result["outcome"]["readbackState"] == "pending"
    assert result["outcome"]["persistenceState"] == "pending"
    assert result["outcome"].get("verified") is not True
    assert result["checkpoint"]["id"] == checkpoints[0]["id"]
    assert service._ports.checkpoint.active_apply_recoveries() == []
    assert calls == [MENU]
    if lane == "external":
        public = gateway._external_mcp_write_result(MENU, result)
        assert public["ok"] is True, public
        assert public["status"] == "invoked"
        assert public["outcome"]["status"] == "pending"
        assert public["commitState"] == "unknown"
        assert public["readbackState"] == "pending"
        assert public["persistenceState"] == "pending"
    else:
        assert result["taskCompletion"]["status"] == "running"
        assert result["taskCompletion"]["outcome"]["status"] == "pending"
        resumed = AgentTaskLoop.from_approval_context(result["approval"]["taskContext"], result["taskCompletion"])
        inspected = resumed.record_action(
            kind="read", tool="vrcforge_get_gameobject", arguments={"gameObjectPath": "Avatar/Setup"},
            raw_result={"ok": True, "componentTypes": ["Plugin.Setup"]},
            outcome={"status": "ok", "summary": "The generated setup component exists.",
                     "verification": {"state": "not_required", "checks": []}},
        )
        assert inspected["status"] == "completed"
        assert resumed.planner_projection()["actions"][0]["status"] != "needs_user_action"

    # A real subsequent transaction must run, not merely acquire a stub lock.
    following = "vrcforge_fixture_following_save"
    receipts[following] = {"ok": True, "verified": True, "commitState": "committed",
                           "mutationStarted": True, "mutationApplied": True, "readback": {"saved": True}}
    service.register_write_handler(
        following, "Save the exact fixture asset.", "low", lambda _: receipts[following],
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _: [("vrc_save_asset", {"assetPath": "Assets/Fixture.asset"})],
    )
    next_result = _execute(dispatch_case, lane, following, {"projectPath": args["projectPath"]})
    assert next_result["ok"] is True, next_result
    assert calls == [MENU, following]
    assert len(checkpoints) == 2


@pytest.mark.parametrize("lane", ["external", "internal"])
@pytest.mark.parametrize("exception", [False, True])
def test_real_menu_failure_is_not_reclassified_as_invoked(dispatch_case, lane, exception):
    _, service, _, receipts, _, _ = dispatch_case
    receipt = _invoked_menu()
    receipt.update(ok=False, error="Plugin invocation failed.",
                   triggered=None if exception else False,
                   state="invocation_exception" if exception else "not_invoked",
                   mutationMayHaveStarted=exception,
                   checkpointRecoveryRequired=exception)
    receipts[MENU] = receipt
    result = _execute(dispatch_case, lane)
    assert result["ok"] is False, result
    assert result["status"] != "invoked"
    assert result["outcome"]["status"] == "failed"
    assert "Plugin invocation failed." in str(result)
    if exception:
        assert service._ports.checkpoint.active_apply_recoveries()


def test_dispatch_shape_cannot_bypass_an_asset_tools_required_fresh_readback(dispatch_case):
    _, service, args, receipts, _, _ = dispatch_case
    name = "vrcforge_fixture_readback_required_save"
    receipts[name] = _invoked_menu()
    service.register_write_handler(
        name, "Save and read back the exact fixture asset.", "low", lambda _: receipts[name],
        fresh_readback_required=True, requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _: [("vrc_save_asset", {"assetPath": "Assets/Fixture.asset"})],
    )
    result = _execute(dispatch_case, "external", name, {"projectPath": args["projectPath"]})
    assert result["ok"] is False, result
    assert result["writeFailure"]["failureLayer"] == "fresh_readback", result
    assert "readback" in result["error"].lower()
    assert service._ports.checkpoint.active_apply_recoveries()
