from copy import deepcopy
import json
import threading

from agent_native_history_store import NativeHistoryStore
from agent_runtime_native_turn import NativeRuntimeTurn
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from runtime_planner_service import PlannerTool, PlannerCatalogSnapshot, RuntimePlannerService
from tests.test_runtime_planner_service import FakeCatalog


def tool(description="When to use: bind. When NOT to use: approve. Negative example: binding never authorizes writes."):
    return PlannerTool("unity_bind_execution_target", description, "read",
                       runtime_name="vrcforge_bind_execution_target", block="diagnostics_build/compile_logs")


def planner(tools):
    catalog = PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools)
    return RuntimePlannerService(catalog=FakeCatalog(planning=catalog, execution=catalog), desktop=None)


def request(owner, *, active=True, definition=None):
    return owner._build_native_plan_request([], observe={"internalToolSelections": {}},
        exposure_layer="execution", project_context_active=active, project_path=None,
        internal_tool_blocks=["core"], global_instructions="", project_instructions="",
        binding_tool_definition=definition)


def test_unity_binding_resident_without_loading_leaf_and_global_context_excludes_it():
    owner = planner((tool(),))
    wire, selected = request(owner)
    assert "unity_bind_execution_target" in {x.name for x in selected}
    assert '"name":"unity_bind_execution_target"' in wire["instructions"]
    assert {x["function"]["name"] for x in wire["tools"]} == {
        "load_internal_tool_block", "unload_internal_tool_block", "call_tool", "vrcforge_runtime_action"}
    global_wire, global_selected = request(owner, active=False)
    assert "unity_bind_execution_target" not in {x.name for x in global_selected}
    assert '"name":"unity_bind_execution_target"' not in global_wire["instructions"]


def test_definition_frozen_across_turns_storage_and_compaction_without_authority(tmp_path):
    store = NativeHistoryStore(tmp_path / "native")
    def state():
        return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), native_history_store=store))
    first_state = state()
    first = NativeRuntimeTurn(first_state, None, "session", "one", "binding", "edit", [], None, None, None)
    owner = planner((tool(),))
    definition = owner._binding_tool_definition(True)
    frozen = first.freeze_binding_tool_definition(definition)
    saved = deepcopy(frozen)
    definition["description"] = "caller mutation"
    later = NativeRuntimeTurn(first_state, None, "session", "two", "binding", "continue", [], None, None, None)
    assert later.freeze_binding_tool_definition(definition) == saved
    later.replace_compacted_history(later.snapshot(), "previous task")
    restored = state()
    assert restored.native_conversation("session", binding="binding")["bindingToolDefinition"] == saved
    assert restored.freeze_native_binding_tool_definition("session", binding="binding", definition=definition) == saved
    changed_catalog = planner((tool("changed definition"),))
    old_wire, _ = request(owner, definition=saved)
    new_wire, _ = request(changed_catalog, definition=saved)
    assert old_wire["instructions"] == new_wire["instructions"]
    revoked, allowed = request(planner(()), definition=saved)
    assert '"name":"unity_bind_execution_target"' in revoked["instructions"]
    assert "unity_bind_execution_target" not in {x.name for x in allowed}


def test_real_native_dispatch_can_bind_before_any_discovery(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, call, finish, run
    gateway, model, _ = setup_gateway(tmp_path, [call("bind", "unity_bind_execution_target", {}), finish])
    invoked = []
    gateway.register_tool("vrcforge_bind_execution_target", "When to use: bind. When NOT to use: approve.",
                          "read/debug", lambda args: invoked.append(args) or {"ok": True})
    catalog = PlannerCatalogSnapshot(visible_tools=(tool(),), routable_tools=(tool(),))
    gateway._runtime_planner._catalog = FakeCatalog(planning=catalog, execution=catalog)
    result = run(gateway, tmp_path)
    assert result["plan"]["nextStep"] == "done"
    assert len(invoked) == 1
    assert len(model.requests) == 2
    assert all("load_internal_tool_block" not in [c["function"]["name"] for m in r["messages"]
                                                for c in m.get("tool_calls", [])] for r in model.requests)
    assert gateway.runtime_sessions.internal_tool_blocks("native-session") == frozenset({"core"})
