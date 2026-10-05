"""Fixed native transport must not become an authorization bypass."""
from copy import deepcopy
import json

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService
def call(call_id, name, arguments):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class Catalog:
    def read(self, layer, *, project_context_active=True):
        tools = [
            PlannerTool("load_internal_tool_block", "Load", "read", runtime_name="vrcforge_load_internal_tool_block"),
            PlannerTool("unload_internal_tool_block", "Unload", "read", runtime_name="vrcforge_unload_internal_tool_block"),
            PlannerTool("read_one", "When to use: read. When NOT to use: write.", "read", block="fixture/read",
                        input_schema={"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}),
        ]
        if layer == "execution":
            tools.append(PlannerTool("write_one", "Write", "write", write=True, block="fixture/read"))
        return PlannerCatalogSnapshot(visible_tools=tuple(tools), routable_tools=tuple(tools))


def build(planner, *, layer="planning", blocks=("core",), plan=False, project=True):
    return planner._build_native_plan_request([], observe={"planMode": plan}, exposure_layer=layer,
        project_context_active=project, project_path="fixture", internal_tool_blocks=blocks,
        global_instructions="", project_instructions="")


def dispatch(name, arguments):
    return call("one", "call_tool", {"tool": name, "arguments": arguments})


def test_entire_tools_array_is_fixed_across_load_unload_mode_and_project():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    cases = [build(planner), build(planner, blocks=("core", "fixture/read")),
             build(planner, layer="execution", blocks=("core", "fixture/read")),
             build(planner, plan=True), build(planner, project=False)]
    serialized = [json.dumps(req["tools"], ensure_ascii=False) for req, _ in cases]
    assert len(set(serialized)) == 1
    assert {t["function"]["name"] for t in cases[0][0]["tools"]} == {
        "load_internal_tool_block", "unload_internal_tool_block", "call_tool", "vrcforge_runtime_action"}


def test_successful_load_returns_full_schema_in_native_observation():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    step = {"tool": "vrcforge_load_internal_tool_block", "status": "executed", "outcome": {"status": "ok"},
            "result": {"ok": True, "status": "loaded", "block": "fixture/read", "selectedTools": ["read_one"],
                       "exposureLayer": "planning"}}
    observed = planner.native_result_observation(step)
    definitions = observed["loadedToolDefinitions"]
    assert [d["name"] for d in definitions] == ["read_one"]
    assert definitions[0]["parameters"] == dict(Catalog().read("planning").visible_tools[-1].input_schema)
    assert "When NOT to use" in definitions[0]["description"]
    denied = deepcopy(step)
    denied["result"]["ok"] = False
    assert "loadedToolDefinitions" not in planner.native_result_observation(denied)


def test_dispatch_uses_current_loaded_scope_and_rejects_direct_dynamic_call():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    _, unloaded = build(planner)
    _, loaded = build(planner, blocks=("core", "fixture/read"))
    payload, rejection = planner._native_action_payload(dispatch("read_one", {"path": "x"}), loaded)
    assert rejection is None
    assert payload["skill_tool"] == "read_one" and payload["skill_params"] == {"path": "x"}
    assert planner._native_action_payload(dispatch("read_one", {"path": "x"}), unloaded)[1]
    assert planner._native_action_payload(call("raw", "read_one", {"path": "x"}), loaded)[1]


@pytest.mark.parametrize("name,args", [("call_tool", {}), ("vrcforge_runtime_action", {}), ("missing", {}),
                                      ("read_one", {"path": 7})])
def test_dispatch_rejects_recursive_unknown_and_invalid_targets(name, args):
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    _, loaded = build(planner, blocks=("core", "fixture/read"))
    assert planner._native_action_payload(dispatch(name, args), loaded)[1]


def test_dispatch_preserves_write_classification_and_planning_denial():
    planner = RuntimePlannerService(catalog=Catalog(), desktop=None)
    _, read_only = build(planner, blocks=("core", "fixture/read"), plan=True)
    _, execution = build(planner, blocks=("core", "fixture/read"), layer="execution")
    assert planner._native_action_payload(dispatch("write_one", {}), read_only)[1]
    payload, rejection = planner._native_action_payload(dispatch("write_one", {}), execution)
    assert rejection is None and payload["action"] == "write" and payload["write_tool"] == "write_one"


def test_new_load_in_same_receipt_does_not_authorize_queued_dispatch(tmp_path):
    from tests.test_native_runtime_gateway import setup_gateway, run
    receipt = call("load", "load_internal_tool_block", {"block": "fixture/read", "tools": ["read_one"]})
    receipt["tool_calls"] += dispatch("read_one", {"path": "x"})["tool_calls"]
    gateway, model, _ = setup_gateway(tmp_path, [receipt, {"role": "assistant", "content": "Stopped"}])
    gateway.runtime_planner._catalog = Catalog()
    invoked = []
    def load(args):
        gateway.runtime_sessions.load_internal_tool_block_selected(args["sessionId"], args["block"], args["tools"])
        return {"ok": True, "status": "loaded", "block": args["block"], "selectedTools": args["tools"], "exposureLayer": "planning"}
    gateway.register_tool("vrcforge_load_internal_tool_block", "When to use: load. When NOT to use: write.", "read/debug", load)
    gateway.register_tool("read_one", "When to use: read. When NOT to use: write.", "read/debug", lambda args: invoked.append(args))
    run(gateway, tmp_path, maxAgenticTurns=2)
    assert not invoked
    assert model.requests[0]["tools"] == model.requests[1]["tools"]
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in model.requests[1]["messages"] if m["role"] == "tool"}
    assert results["load"]["observations"][0]["loadedToolDefinitions"][0]["name"] == "read_one"
    assert "not advertised" in results["one"]["observations"][0]["admissionError"]


@pytest.mark.parametrize("action", ["shell", "enter_execution"])
def test_fixed_control_schema_cannot_leave_explicit_plan(tmp_path, action):
    from tests.test_native_runtime_gateway import setup_gateway, run
    gateway, model, _ = setup_gateway(tmp_path, [call("control", "vrcforge_runtime_action", {
        "action": action, "shell_command": "echo forbidden"}), {"role": "assistant", "content": "Stopped"}])
    gateway.shell.execute = lambda *a, **kw: pytest.fail("Plan must not execute shell")
    result = run(gateway, tmp_path, planMode=True, maxAgenticTurns=2)
    assert result["exposureLayer"] == "planning"
    assert model.requests[0]["tools"] == model.requests[1]["tools"]
    assert any("plan_mode_read_only" in m.get("content", "") for m in model.requests[1]["messages"] if m["role"] == "tool")
