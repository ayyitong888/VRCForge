import json

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
from tests.test_runtime_planner_service import FakeCatalog, native_call


def catalog_tools():
    return (
        PlannerTool("list_internal_tool_blocks", "Discover.", "read", runtime_name="vrcforge_list_internal_tool_blocks"),
        PlannerTool("load_internal_tool_block", "Load.", "read", runtime_name="vrcforge_load_internal_tool_block",
                    input_schema={"type": "object", "properties": {"block": {"type": "string"}, "tools": {"type": "array", "items": {"type": "string"}}}, "required": ["block"]}),
        PlannerTool("read_text_file", "Read.", "read", runtime_name="vrcforge_read_text_file", block="files"),
    )


@pytest.mark.parametrize("attempted", ["read_text_file", "vrcforge_read_text_file", "vrc_not_a_registered_alias"])
def test_rejected_native_call_gets_current_recipe_then_loads_and_runs(tmp_path, attempted):
    tools = catalog_tools()
    gateway, model, invoked = setup_gateway(tmp_path, [])
    load_calls = []

    def load(args):
        assert args["block"] == "files" and args["tools"] == ["read_text_file"]
        load_calls.append(args)
        blocks = gateway.runtime_sessions.load_internal_tool_block_selected(args["sessionId"], args["block"], args["tools"])
        return {"ok": True, "status": "loaded", "loadedBlocks": sorted(blocks), "selectedTools": args["tools"]}

    gateway.register_tool("vrcforge_load_internal_tool_block", "When to use: load. When NOT to use: writes.", "read/debug", load)
    gateway.register_tool("vrcforge_list_internal_tool_blocks", "When to use: discover. When NOT to use: writes.", "read/debug",
                          lambda _args: {"ok": True, "blocks": [{"name": "files", "toolNames": ["read_text_file"]}]})
    gateway._runtime_planner._catalog = FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools))

    def recover(request):
        assert not invoked and not load_calls
        paired = request["messages"][-1]
        assert paired["role"] == "tool" and paired["tool_call_id"] == "rejected"
        data = json.loads(paired["content"])["observations"][0]
        recipe = json.loads(data["admissionError"].split("Next call: ", 1)[1])
        advertised = {entry["function"]["name"] for entry in request["tools"]}
        assert recipe["name"] in advertised
        if attempted == "vrc_not_a_registered_alias":
            assert recipe == {"name": "list_internal_tool_blocks", "arguments": {}}
        else:
            assert recipe == {"name": "load_internal_tool_block", "arguments": {"block": "files", "tools": ["read_text_file"]}}
        return call("recovery", recipe["name"], recipe["arguments"])

    responses = [call("rejected", attempted, {"path": "a.txt"}), recover]
    if attempted == "vrc_not_a_registered_alias":
        responses.append(call("load", "load_internal_tool_block", {"block": "files", "tools": ["read_text_file"]}))
    responses.extend([call("read", "read_text_file", {"path": "a.txt"}), finish])
    model.replies = iter(responses)
    result = run(gateway, tmp_path, maxAgenticTurns=6)
    assert result["plan"]["nextStep"] == "done", result["plan"]
    assert len(invoked) == len(load_calls) == 1
    assert not gateway.runtime_sessions._native_pending(next(iter(gateway.runtime_sessions._native_conversations.values())))


def test_recovery_does_not_reveal_planning_hidden_or_user_disabled_tools():
    directory, loader, read = catalog_tools()
    hidden = PlannerTool("hidden_write", "Write.", "write", write=True, block="private")
    activation = PlannerTool("desktop_action", "Act.", "read", requires_user_activation=True, block="desktop")
    catalog = PlannerCatalogSnapshot(visible_tools=(directory, loader, activation), routable_tools=(directory, loader, read, hidden, activation))
    for name in ("hidden_write", "desktop_action", "vrc_read_text_file"):
        payload, rejection = RuntimePlannerService._native_action_payload(native_call(name), [directory, loader], catalog=catalog)
        assert payload is None
        recipe = json.loads(rejection["summary"].split("Next call: ", 1)[1])
        assert recipe == {"name": directory.name, "arguments": {}}


def test_recovery_never_invents_a_missing_loader():
    _, _, read = catalog_tools()
    payload, rejection = RuntimePlannerService._native_action_payload(native_call(read.name), [], catalog=PlannerCatalogSnapshot(visible_tools=(read,)))
    assert payload is None and "Next call:" not in rejection["summary"]
