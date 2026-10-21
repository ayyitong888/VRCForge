from __future__ import annotations

import json
import threading

from agent_runtime_native_turn import NativeRuntimeTurn
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService
from tests.test_runtime_planner_service import FakeCatalog


BLOCK = "avatar/fx"


def _catalog() -> tuple[PlannerTool, ...]:
    return (
        PlannerTool(
            "read_fx_state",
            "Read the FX state.",
            "read",
            block=BLOCK,
            runtime_name="vrcforge_read_fx_state",
            input_schema={"type": "object", "properties": {"avatarPath": {"type": "string"}}},
        ),
        PlannerTool(
            "read_fx_parameters",
            "Read the FX parameters.",
            "read",
            block=BLOCK,
            runtime_name="vrcforge_read_fx_parameters",
            input_schema={"type": "object", "properties": {"avatarPath": {"type": "string"}}},
        ),
    )


def _planner(tools: tuple[PlannerTool, ...]) -> RuntimePlannerService:
    snapshot = PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools)
    return RuntimePlannerService(catalog=FakeCatalog(planning=snapshot, execution=snapshot), desktop=None)


def _load_step(status: str = "loaded") -> dict:
    return {
        "tool": "vrcforge_load_internal_tool_block",
        "status": "executed",
        "outcome": {"status": "ok"},
        "result": {
            "ok": True,
            "status": status,
            "block": BLOCK,
            "selectedTools": ["read_fx_state"],
            "exposureLayer": "execution",
        },
    }


def _load_step_with_tools(status: str, tools: list[str]) -> dict:
    step = _load_step(status)
    step["result"]["selectedTools"] = list(tools)
    return step


def _assistant_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": "load_internal_tool_block", "arguments": "{}"},
        }],
    }


def test_reused_load_does_not_repeat_definition_retained_in_native_tool_history() -> None:
    tools = _catalog()
    planner = _planner(tools)
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock()))
    turn = NativeRuntimeTurn(state, planner, "retained", "turn", "binding", "Inspect FX.", [], None, None, None)
    state.load_internal_tool_block_selected("retained", BLOCK, ["read_fx_state"])

    first_observation = planner.native_result_observation(_load_step())
    state.append_native_assistant("retained", binding="binding", message=_assistant_call("first"))
    state.settle_native_call(
        "retained", binding="binding", call_id="first",
        content=json.dumps({"observations": [first_observation]}),
    )
    assert [item["name"] for item in first_observation["loadedToolDefinitions"]] == ["read_fx_state"]

    turn.admit(_assistant_call("second"), tool_names={"load_internal_tool_block"})
    turn.next_receipt()
    turn._loop_state.append(_load_step("reused"))
    turn.settle()

    settled = state.native_conversation("retained", binding="binding")
    payload = json.loads(settled["messages"][-1]["content"])
    observation = payload["observations"][0]
    assert observation["reuseLoadedTools"]["definitionSource"] == "existing_session_history"
    assert "loadedToolDefinitions" not in observation


def test_loaded_leaf_union_projects_only_new_definitions_after_prior_leaf_load() -> None:
    tools = _catalog()
    planner = _planner(tools)
    first = planner.native_result_observation(
        _load_step_with_tools("loaded", ["read_fx_state"])
    )
    history = [{
        "role": "tool",
        "content": json.dumps({"observations": [first]}),
    }]
    retained = NativeRuntimeTurn._retained_tool_definitions(history)

    # Session selection is the monotonic A+B union.  The loader response must
    # still project only B because A's exact definition is already retained.
    second = planner.native_result_observation(
        _load_step_with_tools("loaded", ["read_fx_state", "read_fx_parameters"]),
        retained_tool_definitions=retained,
    )
    assert [item["name"] for item in second["loadedToolDefinitions"]] == ["read_fx_parameters"]


def test_same_name_definition_change_is_reprojected() -> None:
    tools = _catalog()
    planner = _planner(tools)
    first = planner.native_result_observation(
        _load_step_with_tools("loaded", ["read_fx_state"])
    )
    history = [{
        "role": "tool",
        "content": json.dumps({"observations": [first]}),
    }]
    retained = NativeRuntimeTurn._retained_tool_definitions(history)
    changed = PlannerTool(
        "read_fx_state",
        "Read the changed FX state.",
        "read",
        block=BLOCK,
        runtime_name="vrcforge_read_fx_state",
        input_schema={"type": "object", "properties": {"avatarPath": {"type": "string"}, "fresh": {"type": "boolean"}}},
    )
    planner = _planner((changed, tools[1]))
    second = planner.native_result_observation(
        _load_step_with_tools("loaded", ["read_fx_state"]),
        retained_tool_definitions=retained,
    )
    assert [item["name"] for item in second["loadedToolDefinitions"]] == ["read_fx_state"]


def test_retained_projection_does_not_reexpose_tool_after_activation_permission_switch() -> None:
    hidden = PlannerTool(
        "desktop_capture",
        "Capture the desktop.",
        "read",
        block=BLOCK,
        runtime_name="vrcforge_desktop_capture",
        requires_user_activation=True,
    )
    snapshot = PlannerCatalogSnapshot(
        visible_tools=(hidden,),
        routable_tools=(hidden,),
        computer_use_model_invocable=False,
    )
    planner = RuntimePlannerService(catalog=FakeCatalog(planning=snapshot, execution=snapshot), desktop=None)
    observation = planner.native_result_observation(
        _load_step_with_tools("loaded", ["desktop_capture"]),
        retained_tool_definitions=NativeRuntimeTurn._retained_tool_definitions([]),
    )
    assert "loadedToolDefinitions" not in observation


def test_reload_after_history_definition_loss_rehydrates_and_call_tool_still_executes(tmp_path) -> None:
    tools = _catalog()
    planner = _planner(tools)
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock()))
    turn = NativeRuntimeTurn(state, planner, "reloaded", "turn", "binding", "Continue FX.", [], None, None, None)
    state.load_internal_tool_block_selected("reloaded", BLOCK, ["read_fx_state"])

    # The session selection survives, while this compacted/restarted history has
    # no prior loader observation from which to retain a definition name.
    assert NativeRuntimeTurn._retained_tool_definitions(turn.snapshot()["messages"]) == {}
    observation = planner.native_result_observation(_load_step("reused"), retained_tool_definitions={})
    assert [item["name"] for item in observation["loadedToolDefinitions"]] == ["read_fx_state"]
    assert state.internal_tool_selections("reloaded") == {BLOCK: ["read_fx_state"]}

    from agent_gateway import AgentGateway

    invoked: list[dict] = []
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.register_tool(
        "read_fx_state", "Read fixture FX state.", "read/debug",
        lambda params: invoked.append(params) or {"ok": True, "state": "fixture"},
    )
    result = gateway.call_tool("read_fx_state", {"avatarPath": "Avatar"})
    assert result["ok"] is True
    assert invoked == [{"avatarPath": "Avatar"}]
