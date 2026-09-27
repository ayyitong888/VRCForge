"""Exercise the registered catalog, session loader and actual native projection."""

import socket
import subprocess

import pytest

import dashboard_server as host
from runtime_planner_service import RuntimePlannerService


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
    planner = RuntimePlannerService(catalog=catalog, desktop=None)
    web_names = {"web_fetch", "web_search"}
    params = {
        "sessionId": session_id,
        "exposureLayer": exposure_layer,
        "projectContextActive": project_context_active,
    }

    def request_tools():
        request, _ = planner._build_native_plan_request(
            [{"role": "user", "content": "Read the supplied public URL."}],
            observe={"internalToolSelections": state.internal_tool_selections(session_id)},
            exposure_layer=exposure_layer,
            project_context_active=project_context_active,
            project_path="D:/Fixture" if project_context_active else None,
            internal_tool_blocks=state.internal_tool_blocks(session_id),
            global_instructions="", project_instructions="",
        )
        return {item["function"]["name"]: item for item in request["tools"]}

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

        roots = host.load_internal_tool_block(params)
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
        assert set(subset) - set(before) == {"web_fetch"}
        assert "web_search" not in subset
        assert subset["web_fetch"]["function"]["parameters"] == dict(registered["web_fetch"].input_schema)
        assert all(subset[name] == definition for name, definition in before.items())

        whole = host.load_internal_tool_block({**params, **leaf["expandArguments"]})
        assert whole["ok"] is True and whole["status"] == "loaded"
        after = request_tools()
        assert set(after) - set(before) == web_names
        assert after["web_fetch"] == subset["web_fetch"]
        assert after["web_search"]["function"]["parameters"] == dict(registered["web_search"].input_schema)
        assert all(after[name] == definition for name, definition in before.items())

        host.unload_internal_tool_block({**params, **leaf["expandArguments"]})
        assert request_tools() == before
    finally:
        state.discard_session(session_id)
