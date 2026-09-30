from copy import deepcopy

from runtime_planner_service import RuntimePlannerService
from tests.test_native_tool_ordering import Catalog, build


def test_instructions_promote_early_batch_loading_and_turn_scoped_result_refs():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    request, _ = build(planner, "execution", False, {}, ["core"])
    instructions = request["instructions"]

    assert "one or two load calls" in instructions
    assert "invalidates the provider cache prefix for the entire conversation" in instructions
    assert "Avoid loading new tools in the middle or end of a task" in instructions
    assert "Use available results before repeating work" in instructions
    assert "resultRef values are valid only within the turn that produced the result" in instructions
    assert "in later turns rely on the conversation history" in instructions


def test_native_batch_guidance_keeps_core_and_control_prefix_stable_across_loads():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    baseline, _ = build(planner, "execution", False, {}, ["core"])
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
        request, _ = build(planner, "execution", False, selections, blocks)
        # The core+control prefix must survive every load, append and unload.
        assert request["tools"][:len(prefix)] == prefix
        # Block tools may only append after the stable prefix; nothing else may drift.
        tail_names = [t["function"]["name"] for t in request["tools"][len(prefix):]]
        assert all(name in {"b_read", "b_write", "a_extra", "core_named_deferred"} for name in tail_names)
        assert selections == frozen, "no activation-order state mutations"
