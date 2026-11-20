"""Regression for post-install Core readiness and process-only approvals."""
import pytest

from agent_task_loop import AgentTaskLoop
from agent_tool_result_contract import normalize_agent_tool_result
from core_upgrade_readiness import editor_process_receipt, installed_core_receipt
from operation_context import public_tool_receipt
from tests.external_mcp_approval_support import review_external_proposal
from tests.test_mcp_write_transaction_contract import _gateway


def record(loop, kind, tool, raw, action_id):
    args = {"projectPath": raw["projectPath"]}
    if "installedAt" in raw:
        args["installedAt"] = raw["installedAt"]
    if kind == "write":
        loop.require_action(kind=kind, tool=tool, arguments=args, action_id=action_id)
    outcome = normalize_agent_tool_result(raw, fallback_summary=tool, write=kind == "write")
    return loop.record_action(kind=kind, tool=tool, raw_result=raw, outcome=outcome,
                              arguments=args, action_id=action_id)


def opened():
    return editor_process_receipt({"ok": True, "status": "launched", "projectPath": "Example", "processId": 91})


def ready(**overrides):
    return {"ok": True, "schema": "vrcforge.core_upgrade_status.v1", "projectPath": "Example",
            "installedAt": "2026-11-20T15:00:00Z", "runtimeCoreInfo": {"processId": 91},
            "ready": True, "compileSnapshotFresh": True, "runtimeIdentityMatchesTarget": True,
            "consoleErrorCount": 0, **overrides}


@pytest.mark.parametrize("overrides", [
    {"runtimeCoreInfo": {"processId": 92}}, {"projectPath": "Elsewhere"},
    {"installedAt": "2026-11-19T00:00:00Z"}, {"compileSnapshotFresh": False},
    {"ready": False}, {"ready": False, "consoleErrorCount": 1},
])
def test_connection_requires_the_opened_process_and_current_installation(overrides):
    loop = AgentTaskLoop("Connect")
    installed = installed_core_receipt({"ok": True, "status": "executed", "committed": True,
        "schema": "vrcforge.unity_core_install.v1", "projectPath": "Example",
        "installedAt": ready()["installedAt"], "installedSha256": "copied"})
    record(loop, "write", "vrcforge_install_unity_core", installed, "install")
    record(loop, "skill", "vrcforge_core_upgrade_status", ready(ready=False), "waiting")
    record(loop, "write", "vrcforge_open_unity_project", opened(), "open")
    outstanding = loop.pending_obligations()
    assert len(outstanding) == 1 and outstanding[0]["target"]["processId"] == 91
    record(loop, "skill", "vrcforge_core_upgrade_status", ready(**overrides), "wrong")
    assert loop.pending_obligations()
    record(loop, "skill", "vrcforge_core_upgrade_status", ready(), "ready")
    record(loop, "skill", "vrcforge_core_upgrade_status", ready(), "repeated")
    assert not loop.pending_obligations()
    for action in loop.planner_projection()["actions"]:
        if action["kind"] == "write":
            assert action["completionReadActionId"] == "ready"


def test_open_project_external_approval_confirms_os_action_without_asset_recovery(tmp_path):
    gateway = _gateway(tmp_path)
    tool = "vrcforge_contract_process_launch"
    gateway.approval_transactions.register_write_handler(
        tool, "Launch process", "medium", lambda args: opened(), pre_write_checkpoint_required=False)
    gateway.register_external_mcp_unity_tool(tool, "avatar")
    proposal = gateway.call_external_mcp_tool(tool, {})
    executed = gateway.call_external_mcp_tool(tool, {"confirmation": review_external_proposal(gateway, proposal)})
    public = public_tool_receipt(executed, write=True, tool=tool)
    assert executed["ok"] and public["outcome"]["targetEffectVerified"]
    assert public["commitState"] == public["persistenceState"] == "not_applicable"
    assert not public["nextAction"]
    assert not public["outcome"].get("recovery")
    loop = AgentTaskLoop("Open an ordinary Unity project")
    record(loop, "write", "vrcforge_open_unity_project", opened(), "open")
    assert not loop.pending_obligations()


def test_outer_asset_write_cannot_borrow_a_runtime_readback():
    raw = {"ok": True, "committed": False, "result": opened()}
    assert normalize_agent_tool_result(raw, write=True, fallback_summary="save")["targetEffectVerified"] is False
