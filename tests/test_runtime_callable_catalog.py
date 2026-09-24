import pytest

import dashboard_server as host
from agent_gateway import RUNTIME_BLOCKED_SKILLS
from runtime_planner_service import RuntimePlannerService, validate_planner_tool_arguments
from tests.test_runtime_planner_service import native_call


@pytest.mark.parametrize("layer", ["planning", "execution"])
def test_runtime_catalog_and_directory_exclude_non_callable_control_endpoints(layer):
    blocked = RUNTIME_BLOCKED_SKILLS - {"vrcforge_execute_shell"}
    catalog = host._RuntimePlannerCatalog().read(layer, project_context_active=True)
    assert not blocked.intersection(tool.runtime_name for tool in catalog.visible_tools)
    assert not blocked.intersection(tool.runtime_name for tool in catalog.routable_tools)
    leaves = host._internal_tool_block_leaves(layer, project_context_active=True)
    assert "unity_agent_message" not in {row["name"] for row in leaves}
    payload, rejection = RuntimePlannerService._native_action_payload(
        native_call("unity_agent_message"), list(catalog.visible_tools), catalog=catalog,
    )
    assert payload is None and rejection["issues"][0]["code"] == "tool_not_visible"
    # The original App/API entry still exists; only inner-loop discovery changes.
    assert "vrcforge_agent_message" in host.AGENT_GATEWAY._tools


def test_runtime_catalog_retains_the_separately_owned_shell_route():
    catalog = host._RuntimePlannerCatalog().read("execution", project_context_active=True)
    assert {tool.name for tool in catalog.routable_tools if tool.runtime_name == "vrcforge_execute_shell"} == {"shell", "unity_shell"}


def test_managed_write_schema_rejects_preview_without_changing_external_contract():
    catalog = host._RuntimePlannerCatalog().read("execution", project_context_active=True)
    tool = next(item for item in catalog.visible_tools if item.name == "unity_create_gameobject")
    assert tool.input_schema["properties"]["preview"]["const"] is False
    target = {"schema": "vrcforge.execution_target.v1", "namespace": "vrcforge://fixture", "scope": "scene", "project": {}, "editor": {}}
    invalid = validate_planner_tool_arguments(tool.input_schema, {"name": "PreviewOnly", "preview": True, "executionTarget": target})
    assert invalid and any(issue["code"] == "const" for issue in invalid["issues"])
    for arguments in ({"name": "Apply"}, {"name": "Apply", "preview": False}):
        arguments["executionTarget"] = target
        assert validate_planner_tool_arguments(tool.input_schema, arguments)["ok"] is True
    # The external MCP path has its own exact preview plan and remains usable.
    descriptor = host.AGENT_GATEWAY.shared_agent_tool_descriptor(
        "vrcforge_create_gameobject", write=True, exposure_layer="execution",
        block=host.AGENT_GATEWAY.external_mcp_tool_block_for_name("vrcforge_create_gameobject", write=True),
    )
    assert "const" not in descriptor["inputSchema"]["properties"]["preview"]
