"""The original planner rejection must lead the internal and external receipt."""
import json

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from approved_unity_execution import freeze_approved_unity_execution_plan
from external_tool_result_contract import build_external_tool_error
from runtime_planner_service import RuntimePlannerService
from unity_editor_extension_tools import editor_menu_request


@pytest.mark.parametrize("external_lane", [False, True])
@pytest.mark.parametrize("arguments,reason", [
    ({"menuPath": "GameObject/Modular Avatar/Setup Outfit"}, "menuItemPath must be one exact discovered menu path."),
    ({"menuItemPath": "GameObject/Modular Avatar/Setup Outfit"}, "selectionGlobalObjectIds must be an explicit array"),
])
def test_original_zcode_menu_argument_failure_leads_receipt(tmp_path, arguments, reason, external_lane):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.ensure_config().allow_write_requests = True
    gateway.approval_transactions.register_write_handler(
        "vrcforge_execute_unity_editor_menu", "Menu fixture", "high",
        lambda _: pytest.fail("invalid arguments must not invoke Unity"),
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_execute_unity_editor_menu", editor_menu_request(args))],
    )
    arguments = {"projectPath": str(tmp_path), **arguments}
    with pytest.raises(AgentGatewayError) as caught:
        if external_lane:
            gateway.approval_transactions.prepare_external_mcp_write("vrcforge_execute_unity_editor_menu", arguments)
        else:
            gateway.approval_transactions.create_apply_request({
                "target_tool": "vrcforge_execute_unity_editor_menu", "arguments": arguments})
    assert reason in str(caught.value)[:100]
    assert isinstance(caught.value.__cause__, ValueError)
    external = build_external_tool_error(exception=caught.value, operation_kind="write",
        failure_layer="write_preparation", failure_phase="approved_plan_freeze",
        tool="vrcforge_execute_unity_editor_menu", mutation_started=False, committed=False)
    assert reason in external["error"][:100]
    assert any(reason in row.get("message", "") for row in external["causeChain"])
    assert external["mutationStarted"] is False and external["committed"] is False
    internal = gateway.approval_transactions._execute_write_request(
        "vrcforge_execute_unity_editor_menu", arguments, "fixture-native")
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    visible = planner.native_result_observation({"kind": "write", **internal})
    assert reason in json.dumps(visible)
    assert gateway._approvals == {}


def test_valid_menu_arguments_still_freeze_exact_plan(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.approval_transactions.register_write_handler(
        "vrcforge_execute_unity_editor_menu", "Menu fixture", "high",
        lambda _: pytest.fail("preparation must not invoke Unity"),
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_execute_unity_editor_menu", editor_menu_request(args))],
    )
    request = {"menuItemPath": "GameObject/Modular Avatar/Setup Outfit", "selectionGlobalObjectIds": []}
    result = gateway.approval_transactions.create_apply_request({
        "target_tool": "vrcforge_execute_unity_editor_menu",
        "arguments": {"projectPath": str(tmp_path), **request}})
    assert result["approval"]["approvedUnityExecutionPlan"] == freeze_approved_unity_execution_plan(
        [("vrc_execute_unity_editor_menu", request)])


@pytest.mark.parametrize("external_lane", [False, True])
@pytest.mark.parametrize("phase", ["request_preparer", "manual_approval_resolver", "risk_level_resolver"])
def test_write_preparation_failure_identifies_the_original_reason(tmp_path, external_lane, phase):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    def reject(*_args):
        raise ValueError("requested field must identify an existing target; inspect its exact identity before retrying")
    gateway.approval_transactions.register_write_handler(
        "fixture_write", "Fixture write", "medium", lambda _: pytest.fail("no write should start"),
        **{phase: reject})
    with pytest.raises(AgentGatewayError) as caught:
        if external_lane:
            gateway.approval_transactions.prepare_external_mcp_write("fixture_write", {"projectPath": str(tmp_path)})
        else:
            gateway.approval_transactions.create_apply_request({"target_tool": "fixture_write", "arguments": {"projectPath": str(tmp_path)}})
    assert str(caught.value).startswith("ValueError: requested field must identify an existing target;")
    assert isinstance(caught.value.__cause__, ValueError)
    assert gateway._approvals == {}
