from __future__ import annotations

import dashboard_server
from runtime_planner_service import PlannerTool, RuntimePlannerService
from tool_discovery_search import search_tool_leaves


def test_actual_broad_queries_rank_their_subject_over_shared_inspect_and_avatar_terms():
    catalog = dashboard_server._RuntimePlannerCatalog().read(
        "execution", project_context_active=True
    )
    cases = (
        (
            "inspect current Unity avatar execution identity",
            "diagnostics_build/compile_logs",
            "unity_bind_execution_target",
        ),
        (
            "read only current execution identity loaded avatar scan materials "
            "semantic properties shader family dissolve readiness",
            "appearance/materials_shaders",
            "unity_scan_materials",
        ),
        (
            "inspect AnimationClip binding curves for this avatar",
            "behavior/animator_clips_bindings",
            "unity_scan_animation_bindings",
        ),
        (
            "inspect Modular Avatar component on this avatar",
            "behavior/interaction_generated_systems",
            "unity_inspect_modular_avatar_component",
        ),
    )

    for query, expected_block, expected_tool in cases:
        result = search_tool_leaves(query, catalog.visible_tools, {})

        assert result["status"] == "found", query
        assert result["matches"][0]["leafPath"] == expected_block, query
        assert expected_tool in result["matches"][0]["toolNames"], query


def test_explicit_modular_avatar_topic_does_not_escape_to_other_avatar_tools():
    catalog = dashboard_server._RuntimePlannerCatalog().read(
        "execution", project_context_active=True
    )

    result = search_tool_leaves(
        "inspect Modular Avatar component on this avatar",
        catalog.visible_tools,
        {},
    )

    assert result["matches"][0]["leafPath"] == "behavior/interaction_generated_systems"
    assert "unity_inspect_modular_avatar_component" in result["matches"][0]["toolNames"]
    assert all(
        match["leafPath"] != "appearance/materials_shaders"
        for match in result["matches"][:1]
    )


def test_search_reuses_only_tools_in_the_current_runtime_available_set():
    catalog = dashboard_server._RuntimePlannerCatalog().read(
        "execution", project_context_active=True
    )
    _blocks, available = RuntimePlannerService._select_plan_tools(
        catalog,
        ["appearance/materials_shaders"],
        {"internalToolSelections": {"appearance/materials_shaders": ["unity_scan_materials"]}},
        project_context_active=True,
    )
    available_names = {tool.name for tool in available}

    result = search_tool_leaves(
        "inspect current Unity avatar execution identity",
        catalog.visible_tools,
        {"appearance/materials_shaders": ["unity_scan_materials"]},
        available_tool_names=available_names,
    )

    match = result["matches"][0]
    assert match["leafPath"] == "diagnostics_build/compile_logs"
    assert "unity_bind_execution_target" in match["alreadyLoadedTools"]
    assert "unity_bind_execution_target" not in match["loadCall"]["tools"]
    assert "unity_list_execution_targets" in match["loadCall"]["tools"]


def test_dashboard_search_uses_host_runtime_availability_not_model_claims():
    session_id = "discovery-host-availability"
    runtime_sessions = dashboard_server.AGENT_GATEWAY.runtime_sessions
    runtime_sessions.discard_session(session_id)
    runtime_sessions.load_internal_tool_block_selected(
        session_id,
        "appearance/materials_shaders",
        ["unity_scan_materials"],
    )

    try:
        params = {
            "sessionId": session_id,
            "query": "inspect current Unity avatar execution identity",
            "tools": [],
            "exposureLayer": "planning",
            "projectContextActive": True,
        }
        active = dashboard_server.load_internal_tool_block(params)
        active_match = active["matches"][0]
        assert "unity_bind_execution_target" in active_match["alreadyLoadedTools"]
        assert "unity_bind_execution_target" not in active_match["loadCall"]["tools"]

        inactive = dashboard_server.load_internal_tool_block({
            **params,
            "projectContextActive": False,
            "availableToolNames": ["list_user_unity_tools"],
        })
        inactive_match = inactive["matches"][0]
        assert "unity_bind_execution_target" not in inactive_match["alreadyLoadedTools"]
        assert "list_user_unity_tools" not in inactive_match["alreadyLoadedTools"]
        assert "list_user_unity_tools" in inactive_match["loadCall"]["tools"]
    finally:
        runtime_sessions.discard_session(session_id)


def test_multiple_description_and_schema_subject_hits_beat_a_generic_tool_name():
    block = "appearance/materials_shaders"
    tools = (
        PlannerTool(
            "avatar_inspector",
            "Inspect avatar state.",
            "read",
            write=False,
            runtime_name="vrcforge_avatar_inspector",
            block=block,
            input_schema={"type": "object", "properties": {}},
        ),
        PlannerTool(
            "read_target_details",
            "Read shader family and dissolve material properties.",
            "read",
            write=False,
            runtime_name="vrcforge_read_target_details",
            block=block,
            input_schema={
                "type": "object",
                "properties": {"shaderFamily": {"type": "string"}, "dissolve": {"type": "number"}},
            },
        ),
    )

    result = search_tool_leaves("inspect avatar shader family dissolve", tools, {})

    assert result["matches"][0]["toolNames"] == ["read_target_details"]
