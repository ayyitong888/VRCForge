from copy import deepcopy

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


@pytest.mark.parametrize("layer,plan", [("planning", False), ("execution", False), ("execution", True)])
def test_native_keeps_core_and_control_prefix_across_load_append_unload(layer, plan):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    baseline, _ = build(planner, layer, plan, {}, ["core"])
    prefix = baseline["tools"]
    assert [t["function"]["name"] for t in prefix] == ["z_resident", "a_resident", "vrcforge_runtime_action"]
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
        assert request["tools"][:len(prefix)] == prefix
        _, selected = planner._select_plan_tools(Catalog().read(layer), blocks, observe)
        expected_order = [t.name for t in selected if t.block == "core"] + ["vrcforge_runtime_action"] + [t.name for t in selected if t.block != "core"]
        assert [t["function"]["name"] for t in request["tools"]] == expected_order
        # Compare the full old definitions by name: only order may change.
        expected = {t.name: {"type": "function", "function": {
            "name": t.name,
            "description": planner_tool_usage_description(t.name, t.description, write=t.write),
            "parameters": deepcopy(dict(t.input_schema)),
        }} for t in selected}
        expected["vrcforge_runtime_action"] = prefix[-1]
        assert {t["function"]["name"]: t for t in request["tools"]} == expected
        assert selections == frozen, "no activation-order state mutations"
        assert ("b_write" in expected) == (layer == "execution" and not plan and "b" in blocks)


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
