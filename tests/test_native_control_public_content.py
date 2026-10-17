"""Preserve public content on runtime controls without inferring phases."""
import json
import pytest
from runtime_planner_service import PlannerTool, RuntimePlannerService
from tests.test_runtime_planner_service import service

# Public text from the real CC capture parsed_model_receipt seq 105.
PUBLIC_TEXT = "The first attempt failed schema validation (`record_id` vs required `recordId`). I'll resubmit the exact same action with corrected arguments."

@pytest.mark.parametrize("action,explicit,phased,expected", [
    ("correct", "", False, PUBLIC_TEXT),
    ("shell", "", False, PUBLIC_TEXT),
    ("correct", "Explicit update.", False, "Explicit update."),
    ("shell", "Explicit update.", False, "Explicit update."),
    ("correct", "", True, ""),
    ("shell", "", True, ""),
    ("reply", "Final result.", False, "Final result."),
])
def test_control_preserves_public_content_without_duplicate_or_final_overwrite(action, explicit, phased, expected):
    _, tools = service()._build_native_plan_request(
        [], observe={}, exposure_layer="execution", project_context_active=False,
        project_path=None, internal_tool_blocks=None, global_instructions="", project_instructions="")
    tools.append(PlannerTool("fixture_read_record", "Read-only fixture", "plan/preview",
        input_schema={"type":"object","properties":{"recordId":{"type":"string"}},"required":["recordId"]}))
    args = {"action": action}
    if action == "correct":
        args.update(tool="fixture_read_record", arguments={"recordId":"sample-103"},
                    correction_for_action_id="planner_validation_c9c0e092738793eae14b6362")
    elif action == "shell":
        args["shell_command"] = "Get-Location"
    if explicit:
        args["reply"] = explicit
    receipt = {"role":"assistant", "content":PUBLIC_TEXT, "tool_calls":[
        {"id":"captured-call", "type":"function", "function":{
            "name":"vrcforge_runtime_action", "arguments":json.dumps(args)}}]}
    if phased:
        receipt["publicMessages"] = [{"id":"public-1","phase":"commentary","text":PUBLIC_TEXT}]
    payload, error = RuntimePlannerService._native_action_payload(receipt, tools)
    assert error is None
    assert payload.get("reply", "") == expected
