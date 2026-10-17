import threading

from agent_native_history_store import NativeHistoryStore
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from internal_tool_blocks import canonical_tool_tree
from runtime_planner_service import planner_tool_input_schema, validate_planner_tool_arguments


def test_loader_enum_comes_from_tree_and_root_example_omits_block():
    import dashboard_server as host
    request, _ = host.RUNTIME_PLANNER._build_native_plan_request(
        [{"role": "user", "content": "offline"}], observe={}, exposure_layer="execution",
        project_context_active=True, project_path=None, internal_tool_blocks=(),
        global_instructions="", project_instructions="",
    )
    loader = next(t["function"] for t in request["tools"] if t["function"]["name"] == "load_internal_tool_block")
    schema = loader["parameters"]
    assert set(schema["properties"]["block"]["enum"]) == {"core", *(r["id"] for r in canonical_tool_tree()["roots"])}
    assert 'omit block' in loader['description']
    assert '{"tools":[]}' in loader['description']
    assert validate_planner_tool_arguments(schema, {"tools": []})["ok"]
    assert not validate_planner_tool_arguments(schema, {"block": "unity", "tools": []})["ok"]


def test_block_enum_stays_fixed_across_turns_and_native_store_reload(tmp_path):
    store = NativeHistoryStore(tmp_path / "native")
    def state():
        return AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock(), native_history_store=store))
    owner = state()
    owner.begin_native_turn("session", binding="binding", turn_id="one", message="inspect")
    assert owner.freeze_native_tool_block_ids("session", binding="binding", block_ids=["core", "first/leaf"]) == ["core", "first/leaf"]
    owner.begin_native_turn("session", binding="binding", turn_id="two", message="inspect again")
    assert owner.freeze_native_tool_block_ids("session", binding="binding", block_ids=["core", "new/leaf"]) == ["core", "first/leaf"]
    reloaded = state()
    assert reloaded.freeze_native_tool_block_ids("session", binding="binding", block_ids=["core", "changed/leaf"]) == ["core", "first/leaf"]
    schema = planner_tool_input_schema("vrcforge_load_internal_tool_block", tool_block_ids=["core", "first/leaf"])
    assert not validate_planner_tool_arguments(schema, {"tools": [], "block": "new/leaf"})["ok"]
    reloaded.begin_native_turn("fresh", binding="binding", turn_id="one", message="new session")
    assert reloaded.freeze_native_tool_block_ids("fresh", binding="binding", block_ids=["core", "new/leaf"]) == ["core", "new/leaf"]
