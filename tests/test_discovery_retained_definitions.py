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


def test_reload_after_history_definition_loss_rehydrates_and_call_tool_still_executes(tmp_path) -> None:
    tools = _catalog()
    planner = _planner(tools)
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(threading.RLock()))
    turn = NativeRuntimeTurn(state, planner, "reloaded", "turn", "binding", "Continue FX.", [], None, None, None)
    state.load_internal_tool_block_selected("reloaded", BLOCK, ["read_fx_state"])

    # The session selection survives, while this compacted/restarted history has
    # no prior loader observation from which to retain a definition name.
    assert NativeRuntimeTurn._retained_tool_names(turn.snapshot()["messages"]) == set()
    observation = planner.native_result_observation(_load_step("reused"), retained_tool_names=set())
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
