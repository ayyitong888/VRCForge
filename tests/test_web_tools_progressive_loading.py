"""Exercise the registered catalog, session loader and actual native projection."""

import socket
import subprocess
from copy import deepcopy

import pytest

import dashboard_server as host
from agent_runtime_native_turn import NativeRuntimeTurn
from runtime_planner_service import PlannerModelResult, RuntimePlannerService, planner_tool_usage_description


class CapturingNativeModel:
    """Capture the final provider boundary without a provider or transport."""

    def __init__(self):
        self.requests = []

    def plan_native(self, request):
        self.requests.append(deepcopy(request))
        return PlannerModelResult(
            "", assistant_message={"role": "assistant", "content": "Inspection recorded."},
            finish_reason="stop",
        )


def native_request_tools(planner, model, state, session_id, owner, *, exposure_layer, project_context_active):
    count = len(model.requests)
    planner._llm_plan_agent_turn(
        "Inspect the available read tools.",
        observe={"internalToolSelections": state.internal_tool_selections(session_id)},
        history=[], exposure_layer=exposure_layer,
        project_context_active=project_context_active,
        project_path="D:/Fixture" if project_context_active else None,
        internal_tool_blocks=state.internal_tool_blocks(session_id),
        global_instructions="", project_instructions="", native_turn=owner,
        propagate_provider_errors=True,
    )
    assert len(model.requests) == count + 1
    return {item["function"]["name"]: item for item in model.requests[-1]["tools"]}


def call_tool_receipt(name, arguments):
    import json

    return {"tool_calls": [{"id": "call-1", "function": {
        "name": "call_tool",
        "arguments": json.dumps({"tool": name, "arguments": arguments}),
    }}]}


@pytest.mark.parametrize("project_context_active", [False, True], ids=["general", "unity"])
@pytest.mark.parametrize("exposure_layer", ["planning", "execution"])
def test_web_tools_are_discovered_and_loaded_through_runtime_catalog(
    monkeypatch, project_context_active, exposure_layer,
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Catalog/loading regression must not use network or subprocesses")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    state = host.AGENT_GATEWAY.runtime_sessions
    session_id = f"web-loading-regression-{project_context_active}-{exposure_layer}"
    state.discard_session(session_id)
    catalog = host._RuntimePlannerCatalog()
    model = CapturingNativeModel()
    planner = RuntimePlannerService(catalog=catalog, desktop=None, model=model)
    owner = NativeRuntimeTurn(state, planner, session_id, "turn", "offline-test", "Read a URL.", [], None, None, None)
    web_names = {"web_fetch", "web_search"}
    params = {
        "sessionId": session_id,
        "exposureLayer": exposure_layer,
        "projectContextActive": project_context_active,
    }

    def request_tools():
        return native_request_tools(
            planner, model, state, session_id, owner,
            exposure_layer=exposure_layer, project_context_active=project_context_active,
        )

    def selected_native_tools():
        _, tools = planner._build_native_plan_request(
            [], observe={"internalToolSelections": state.internal_tool_selections(session_id)},
            exposure_layer=exposure_layer, project_context_active=project_context_active,
            project_path=None, internal_tool_blocks=state.internal_tool_blocks(session_id),
            global_instructions="", project_instructions="",
        )
        return tools

    try:
        registered = {
            tool.name: tool for tool in catalog.read(
                exposure_layer, project_context_active=project_context_active,
            ).visible_tools
        }
        assert web_names <= registered.keys()
        before = request_tools()
        assert not web_names.intersection(before), "Web definitions must not remain resident in core"
        assert "load_internal_tool_block" in before

        roots = host.load_internal_tool_block({**params, "tools": []})
        assert roots["ok"] is True
        research = next(row for row in roots["blocks"] if row["id"] == "research")
        branch = host.load_internal_tool_block({**params, **research["expandArguments"]})
        leaf = next(row for row in branch["blocks"] if row["id"] == "research/web_research")
        assert set(leaf["toolNames"]) == web_names
        assert state.internal_tool_blocks(session_id) == frozenset({"core"})
        assert request_tools() == before

        selected = host.load_internal_tool_block({
            **params, **leaf["expandArguments"], "tools": ["web_fetch"],
        })
        assert selected["ok"] is True and selected["status"] == "loaded"
        subset = request_tools()
        assert list(subset.items()) == list(before.items())

        loaded_observation = planner.native_result_observation({
            "tool": "vrcforge_load_internal_tool_block", "status": "completed",
            "result": selected, "outcome": {"status": "succeeded"},
        }, project_context_active=project_context_active)
        definitions = loaded_observation["loadedToolDefinitions"]
        assert [item["name"] for item in definitions] == ["web_fetch"]
        assert definitions[0]["description"] == planner_tool_usage_description(
            "web_fetch", registered["web_fetch"].description, write=False)
        assert definitions[0]["parameters"] == dict(registered["web_fetch"].input_schema)

        native_tools = selected_native_tools()
        _, rejection = planner._native_action_payload(call_tool_receipt("web_fetch", {"url": "https://example.invalid"}), native_tools)
        assert rejection is None
        _, rejection = planner._native_action_payload(call_tool_receipt("web_search", {"query": "offline"}), native_tools)
        assert rejection and rejection["issues"][0]["code"] == "tool_not_visible"

        whole = host.load_internal_tool_block({**params, **leaf["expandArguments"], "tools": leaf["toolNames"]})
        assert whole["ok"] is True and whole["status"] == "loaded"
        after = request_tools()
        assert list(after.items()) == list(before.items())

        whole_observation = planner.native_result_observation({
            "tool": "vrcforge_load_internal_tool_block", "status": "completed",
            "result": whole, "outcome": {"status": "succeeded"},
        }, project_context_active=project_context_active)
        whole_definitions = whole_observation["loadedToolDefinitions"]
        assert [item["name"] for item in whole_definitions] == ["web_fetch", "web_search"]
        for item in whole_definitions:
            assert item["description"] == planner_tool_usage_description(
                item["name"], registered[item["name"]].description, write=False)
        assert {item["name"]: item["parameters"] for item in whole_definitions} == {
            name: dict(registered[name].input_schema) for name in ("web_fetch", "web_search")
        }

        host.unload_internal_tool_block({**params, **leaf["expandArguments"]})
        assert request_tools() == before
        native_tools = selected_native_tools()
        _, rejection = planner._native_action_payload(call_tool_receipt("web_fetch", {"url": "https://example.invalid"}), native_tools)
        assert rejection and rejection["issues"][0]["code"] == "tool_not_visible"
    finally:
        state.discard_session(session_id)


def test_native_send_appends_across_real_leaves_and_later_whole_leaf_load(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Catalog/loading regression must not use network or subprocesses")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    state = host.AGENT_GATEWAY.runtime_sessions
    session_id = "native-multiple-leaf-order-regression"
    state.discard_session(session_id)
    model = CapturingNativeModel()
    planner = RuntimePlannerService(catalog=host._RuntimePlannerCatalog(), desktop=None, model=model)
    owner = NativeRuntimeTurn(state, planner, session_id, "turn", "offline-test", "Inspect avatar.", [], None, None, None)
    params = {"sessionId": session_id, "exposureLayer": "execution", "projectContextActive": True}

    def request_tools():
        return native_request_tools(planner, model, state, session_id, owner,
                                    exposure_layer="execution", project_context_active=True)

    try:
        before = request_tools()
        fixed_order = list(before.items())
        # Load two different real leaves, then expand the first leaf. The native
        # transport definitions remain fixed; load receipts carry tool schemas.
        stages = [
            ("research/web_research", ["web_fetch"]),
            ("behavior/parameters_menus_layers", ["unity_scan_parameters", "unity_scan_wardrobe"]),
            ("research/web_research", ["web_fetch", "web_search"]),
        ]
        for block, selected in stages:
            arguments = {**params, "block": block}
            if selected is not None:
                arguments["tools"] = selected
            result = host.load_internal_tool_block(arguments)
            assert result["ok"] is True and result["status"] == "loaded"
            current = request_tools()
            assert list(current.items()) == fixed_order
    finally:
        state.discard_session(session_id)
