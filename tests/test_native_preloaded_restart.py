"""Offline Gateway request construction after early loads and process restart."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


class Catalog:
    def read(self, layer, *, project_context_active=True):
        tools = (
            PlannerTool("read_installed_skill", "Read guide", "read", runtime_name="vrcforge_read_installed_skill"),
            PlannerTool("load_internal_tool_block", "Load leaf", "read", runtime_name="vrcforge_load_internal_tool_block"),
            PlannerTool("descriptor", "Read descriptor", "read", block="avatar/hierarchy"),
            PlannerTool("controls", "Read controls", "read", block="avatar/hierarchy"),
            PlannerTool("fx", "Read FX", "read", block="behavior/animation"),
        )
        return PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools)


def make_gateway(root, replies):
    gateway, model, _ = setup_gateway(root, replies)
    loaded, scanned = [], []
    gateway.runtime_planner._catalog = Catalog()
    gateway.register_tool("vrcforge_read_installed_skill", "When to use: guide. When NOT to use: write.",
                          "read/debug", lambda args: {"ok": True, "schema": "vrcforge.installed_skill.v1",
                              "name": "fixture", "allowedTools": ["descriptor", "controls", "fx"]})

    def load(args):
        loaded.append(deepcopy(args))
        gateway.runtime_sessions.load_internal_tool_block_selected(args["sessionId"], args["block"], args["tools"])
        return {"ok": True, "status": "loaded", "block": args["block"],
                "selectedTools": args["tools"], "exposureLayer": args.get("exposureLayer", "planning")}

    gateway.register_tool("vrcforge_load_internal_tool_block", "When to use: load. When NOT to use: scan.", "read/debug", load)
    for name in ("descriptor", "controls", "fx"):
        gateway.register_tool(name, "When to use: read fixture. When NOT to use: write.", "read/debug",
                              lambda args, name=name: scanned.append(name) or {"ok": True, "summary": name + " verified"})
    return gateway, model, loaded, scanned


def resumed_request(root):
    gateway, model, loaded, scanned = make_gateway(root, [{"role": "assistant", "content": "Continued"}])
    run(gateway, root, clientTurnId="after-restart", message="Continue",
        history=[{"role": "agent", "text": "Lossy UI summary must not replace original history"}])
    assert not loaded and not scanned
    assert gateway.runtime_sessions.internal_tool_selections("native-session") == {
        "avatar/hierarchy": ["controls", "descriptor"], "behavior/animation": ["fx"]}
    return model.requests[0]


def test_early_load_scan_and_fresh_process_resume_keep_complete_definitions(tmp_path):
    def batch_from_catalog(request):
        paired = next(m for m in request["messages"] if m.get("tool_call_id") == "guide")
        assert json.loads(paired["content"])["observations"][0]["runtimeTool"] == "vrcforge_read_installed_skill"
        leaves = {}
        for tool in Catalog().read("planning").routable_tools:
            if tool.block != "core":
                leaves.setdefault(tool.block, []).append(tool.name)
        assert len(leaves) == 2
        receipt = {"role": "assistant", "content": None, "tool_calls": []}
        for index, (block, names) in enumerate(leaves.items()):
            receipt["tool_calls"].extend(call(f"load-{index}", "load_internal_tool_block", {
                "block": block, "tools": names})["tool_calls"])
        return receipt

    scans = call("descriptor-read", "descriptor", {})
    scans["tool_calls"].extend(call("controls-read", "controls", {})["tool_calls"])
    gateway, model, loaded, scanned = make_gateway(tmp_path, [
        call("guide", "read_installed_skill", {"name": "fixture"}), batch_from_catalog,
        scans, call("fx-read", "fx", {}), finish,
    ])
    result = run(gateway, tmp_path, maxAgenticTurns=5)
    assert result["plan"]["nextStep"] == "done"
    assert len(model.requests) == 5 and len(loaded) == 2
    assert sorted(scanned) == ["controls", "descriptor", "fx"]
    baseline = model.requests[2]
    assert all(request["tools"] == model.requests[0]["tools"] for request in model.requests)
    for request in model.requests:
        assert request["tools"] == baseline["tools"]
        assert request["instructions"] == baseline["instructions"]
    from tests.test_native_runtime_gateway import runtime_state
    before_load_state = runtime_state(model.requests[0])
    loaded_state = runtime_state(baseline)
    assert before_load_state["availableToolNames"] != loaded_state["availableToolNames"]
    assert {"descriptor", "controls", "fx"}.issubset(set(loaded_state["availableToolNames"]))
    load_observations = [
        observation
        for message in model.requests[2]["messages"] if message["role"] == "tool"
        for observation in json.loads(message["content"]).get("observations", [])
    ]
    loaded_definitions = {
        item["name"]
        for observation in load_observations
        for item in observation.get("loadedToolDefinitions", [])
    }
    assert {"descriptor", "controls", "fx"}.issubset(loaded_definitions)
    for before, after in zip(model.requests, model.requests[1:]):
        prefix = before["messages"][:-1]
        assert after["messages"][:len(prefix)] == prefix
    with gateway.runtime_planner.bind_turn({}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    saved = gateway.runtime_sessions.native_conversation("native-session", binding=binding)
    script = (
        "import json,sys; from pathlib import Path; "
        "from tests.test_native_preloaded_restart import resumed_request; "
        "print(json.dumps(resumed_request(Path(sys.argv[1]))))"
    )
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                             timeout=45, check=True)
    resumed = json.loads(process.stdout)
    assert resumed["tools"] == baseline["tools"]
    assert resumed["instructions"] == baseline["instructions"]
    resumed_state = runtime_state(resumed)
    assert resumed_state["availableToolNames"] == loaded_state["availableToolNames"]
    marker = "Resident tool definitions (data, not authorization): "
    assert json.loads(resumed["instructions"].rsplit(marker, 1)[1]) == json.loads(
        baseline["instructions"].rsplit(marker, 1)[1])
    assert "residentToolDefinitions" not in resumed_state
    assert resumed["messages"][:len(saved["messages"])] == saved["messages"]
    assert resumed["messages"][len(saved["messages"])] == {"role": "user", "content": "Continue"}
