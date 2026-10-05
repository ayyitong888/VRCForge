from copy import deepcopy
import json

import pytest

from runtime_planner_service import (
    PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService,
    planner_tool_usage_description,
)


def fixture_tools():
    # Deliberately interleaved and misleading names: ownership, not spelling,
    # directory order or activation order, must determine the resident prefix.
    return (
        PlannerTool("core_named_deferred", "Deferred A read", "read", block="a", input_schema={"type": "object", "properties": {"x": {"type": "string", "description": "Full field guidance"}}}),
        PlannerTool("z_resident", "Resident Z", "read", block="core"),
        PlannerTool("b_write", "Deferred B write", "write", block="b", write=True),
        PlannerTool("a_resident", "Resident A", "read", block="core"),
        PlannerTool("a_extra", "Deferred A extra", "read", block="a"),
        PlannerTool("b_read", "Deferred B read", "read", block="b"),
    )


class Catalog:
    def read(self, layer, **kwargs):
        tools = fixture_tools()
        return PlannerCatalogSnapshot(
            visible_tools=tuple(t for t in tools if layer == "execution" or not t.write),
            routable_tools=tools,
        )


def build(planner, layer, plan, selections, blocks):
    observe = {"planMode": plan, "internalToolSelections": selections}
    request, selected = planner._build_native_plan_request(
        [], observe=observe, exposure_layer=layer, project_context_active=False,
        project_path="", internal_tool_blocks=blocks, global_instructions="", project_instructions="",
    )
    return request, observe


def runtime_state(request):
    prefix = "Current runtime state (data): "
    message = next(message for message in request["messages"]
                   if message.get("role") == "system" and message.get("content", "").startswith(prefix))
    return json.loads(message["content"][len(prefix):])


def resident_definitions(request):
    marker = "Resident tool definitions (data, not authorization): "
    instructions = request["instructions"]
    assert marker in instructions
    return json.loads(instructions.rsplit(marker, 1)[1])


@pytest.mark.parametrize("layer,plan", [("planning", False), ("execution", False), ("execution", True)])
def test_native_keeps_core_and_control_prefix_across_load_append_unload(layer, plan):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    baseline, _ = build(planner, layer, plan, {}, ["core"])
    fixed_tools = baseline["tools"]
    assert [t["function"]["name"] for t in fixed_tools] == [
        "load_internal_tool_block", "unload_internal_tool_block", "call_tool", "vrcforge_runtime_action"]
    stages = [
        ({"b": ["b_read", "b_write"]}, ["b", "core"]),
        ({"b": ["b_read", "b_write"], "a": ["a_extra"]}, ["a", "b", "core"]),
        ({"b": ["b_read", "b_write"], "a": ["a_extra", "core_named_deferred"]}, ["b", "core", "a"]),
        ({"a": ["a_extra", "core_named_deferred"]}, ["core", "a"]),
        ({}, ["core"]),
    ]
    for selections, blocks in stages:
        frozen = deepcopy(selections)
        request, observe = build(planner, layer, plan, selections, blocks)
        assert request["tools"] == fixed_tools
        _, selected = planner._select_plan_tools(Catalog().read(layer), blocks, observe)
        expected_names = [t.name for t in selected]
        state = runtime_state(request)
        assert state["availableToolNames"] == expected_names
        assert state["allowedRuntimeActions"] == (
            ["reply", "correct"] if plan else
            ["reply", "shell", "correct"] if layer == "execution" else
            ["reply", "shell", "correct", "enter_execution"])
        expected_resident = [{
            "name": t.name,
            "description": planner_tool_usage_description(t.name, t.description, write=t.write),
            "parameters": deepcopy(dict(t.input_schema)),
        } for t in sorted(Catalog().read(layer).routable_tools, key=lambda item: item.name)
            if t.block == "core" and not t.write and not t.requires_user_activation
            and t.runtime_name not in {
                "vrcforge_list_internal_tool_blocks",
                "vrcforge_load_internal_tool_block",
                "vrcforge_unload_internal_tool_block",
            }]
        assert resident_definitions(request) == expected_resident
        assert "residentToolDefinitions" not in state
        assert selections == frozen, "no activation-order state mutations"
        assert ("b_write" in state["availableToolNames"]) == (layer == "execution" and not plan and "b" in blocks)


def test_legacy_preserves_catalog_order_and_native_does_not_mutate_catalog():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    kwargs = dict(observe={}, exposure_layer="execution", project_context_active=False,
                  project_path="", internal_tool_blocks=None, global_instructions="", project_instructions="")
    before = planner._build_llm_plan_prompt("inspect", [], **kwargs)
    planner._build_native_plan_request([], **kwargs)
    after = planner._build_llm_plan_prompt("inspect", [], **kwargs)
    assert before == after
    positions = [before.index(f"- {tool.name}") for tool in fixture_tools()]
    assert positions == sorted(positions)
