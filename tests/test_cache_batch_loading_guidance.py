from copy import deepcopy

from runtime_planner_service import RuntimePlannerService
from tests.test_native_tool_ordering import Catalog, build


def test_instructions_promote_early_batch_loading_and_turn_scoped_result_refs():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    request, _ = build(planner, "execution", False, {}, ["core"])
    instructions = request["instructions"]

    assert "Call independent read tools together when useful" in instructions
    assert "one exact owner leaf" in instructions
    assert "Load additional tools on demand" in instructions
    assert "loading and unloading never change the native tools array" in instructions
    assert "Use available results before repeating work" in instructions
    assert "resultRef values are valid only within the turn that produced the result" in instructions
    assert "in later turns rely on the conversation history" in instructions


def test_native_batch_guidance_keeps_core_and_control_prefix_stable_across_loads():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    baseline, _ = build(planner, "execution", False, {}, ["core"])
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
        request, _ = build(planner, "execution", False, selections, blocks)
        assert request["tools"] == fixed_tools
        assert selections == frozen, "no activation-order state mutations"


def test_independent_leaf_loads_share_one_model_response_and_survive_restart(tmp_path):
    from agent_native_history_store import NativeHistoryStore
    from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
    from tests.test_native_runtime_gateway import call, run, setup_gateway
    from tests.test_runtime_planner_service import FakeCatalog
    from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool
    import threading

    tools = (
        PlannerTool("load_internal_tool_block", "Load.", "read",
                    runtime_name="vrcforge_load_internal_tool_block", input_schema={
                        "type": "object", "properties": {"block": {"type": "string"},
                        "tools": {"type": "array", "items": {"type": "string"}}}}),
        PlannerTool("read_a", "Read A.", "read", block="a"),
        PlannerTool("read_b", "Read B.", "read", block="b"),
    )
    receipt = call("load-a", "load_internal_tool_block", {"block": "a", "tools": ["read_a"]})
    receipt["tool_calls"].extend(call("load-b", "load_internal_tool_block", {
        "block": "b", "tools": ["read_b"]})["tool_calls"])
    gateway, model, _ = setup_gateway(tmp_path, [receipt, {"role": "assistant", "content": "Ready"}])
    loaded = []

    def load(args):
        loaded.append(args["block"])
        gateway.runtime_sessions.load_internal_tool_block_selected(args["sessionId"], args["block"], args["tools"])
        return {"ok": True, "status": "loaded"}

    gateway.register_tool("vrcforge_load_internal_tool_block", "When to use: load. When NOT to use: write.", "read/debug", load)
    gateway._runtime_planner._catalog = FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools))
    run(gateway, tmp_path, maxAgenticTurns=2)
    assert loaded == ["a", "b"]
    assert len(model.requests) == 2
    fixed_names = {"load_internal_tool_block", "unload_internal_tool_block", "call_tool", "vrcforge_runtime_action"}
    assert {t["function"]["name"] for t in model.requests[0]["tools"]} == fixed_names
    assert {t["function"]["name"] for t in model.requests[1]["tools"]} == fixed_names
    later_messages = str(model.requests[1]["messages"])
    assert "read_a" in later_messages and "read_b" in later_messages
    restored = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(
        threading.RLock(), NativeHistoryStore(tmp_path / "audit/native-history")))
    assert restored.internal_tool_selections("native-session") == {"a": ["read_a"], "b": ["read_b"]}
