from copy import deepcopy

import dashboard_server
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService
from tool_discovery_search import search_tool_leaves
from tests.test_runtime_planner_service import FakeCatalog


BLOCK = "behavior/animator_clips_bindings"


def _tool(name, runtime_name, *, block=BLOCK, write=False, description=""):
    return PlannerTool(
        name,
        description or name,
        "write" if write else "read",
        write=write,
        runtime_name=runtime_name,
        block=block,
    )


def _fx_tools():
    return (
        _tool("scan_fx_animator", "vrcforge_unity_scan_fx_animator", description="Inspect FX Animator state."),
        _tool("preview_fx_animator", "vrcforge_preview_manage_fx_animator", description="Preview FX Animator changes."),
        _tool("manage_fx_animator", "vrcforge_manage_fx_animator", write=True,
              description="Manage FX Animator bindings."),
        _tool("scan_curves", "vrcforge_unity_scan_curves", description="Inspect animation curves."),
    )


def test_search_returns_full_leaf_and_target_family_without_unrelated_matches():
    result = search_tool_leaves("FX animator", _fx_tools(), {})

    assert result["status"] == "found"
    match = result["matches"][0]
    assert match["leafPath"] == BLOCK
    assert match["toolNames"] == ["manage_fx_animator", "preview_fx_animator", "scan_fx_animator"]
    assert match["requiredTools"] == ["manage_fx_animator", "preview_fx_animator"]
    assert match["loadCall"] == {"block": BLOCK, "tools": [
        "manage_fx_animator", "preview_fx_animator", "scan_fx_animator"
    ]}
    assert "scan_curves" not in match["toolNames"]
    assert match["sameLeafToolNames"] == [
        "manage_fx_animator", "preview_fx_animator", "scan_curves", "scan_fx_animator"
    ]


def test_search_required_bundle_uses_same_leaf_scope_family():
    tools = (
        _tool("scan_target", "vrcforge_unity_scan_target"),
        _tool("preview_target", "vrcforge_preview_apply_target"),
        _tool("apply_target", "vrcforge_apply_target", write=True),
        _tool("scan_other", "vrcforge_unity_scan_other"),
    )

    result = search_tool_leaves("target", tools, {})
    match = result["matches"][0]

    assert match["toolNames"] == ["apply_target", "preview_target", "scan_target"]
    assert match["requiredTools"] == ["apply_target", "preview_target"]
    assert match["loadCall"] == {"block": BLOCK, "tools": [
        "apply_target", "preview_target", "scan_target"
    ]}
    assert match["sameLeafToolNames"] == [
        "apply_target", "preview_target", "scan_other", "scan_target"
    ]


def test_search_no_match_returns_root_browse_fallback():
    result = search_tool_leaves("does-not-exist", _fx_tools(), {})

    assert result["status"] == "not_found"
    assert result["matches"] == []
    assert result["fallbackCall"] == {"tools": []}


def test_search_does_not_mutate_selection_and_reports_loaded_bundle_reuse():
    tools = _fx_tools()
    selections = {BLOCK: ["scan_fx_animator", "preview_fx_animator", "manage_fx_animator"]}
    before = deepcopy(selections)

    result = search_tool_leaves("FX animator", tools, selections)

    assert selections == before
    match = result["matches"][0]
    assert match["reuse"] is True
    assert match["alreadyLoadedTools"] == match["toolNames"]
    assert match["loadCall"] is None


def test_exact_tool_and_full_leaf_queries_keep_direct_match_scope():
    tools = _fx_tools()

    exact = search_tool_leaves("scan_fx_animator", tools, {})["matches"][0]
    assert exact["leafPath"] == BLOCK
    assert exact["toolNames"] == ["scan_fx_animator"]
    assert exact["requiredTools"] == []

    leaf = search_tool_leaves(BLOCK, tools, {})["matches"][0]
    assert leaf["leafPath"] == BLOCK
    assert leaf["toolNames"] == [
        "manage_fx_animator", "preview_fx_animator", "scan_curves", "scan_fx_animator"
    ]


def test_project_query_does_not_make_unrelated_gameobject_writes_required():
    project_block = "project_environment/files"
    tools = (
        _tool("read_project", "vrcforge_read_project", block=project_block),
        _tool("create_gameobject", "vrcforge_create_gameobject", block=project_block, write=True),
        _tool("delete_gameobject", "vrcforge_delete_gameobject", block=project_block, write=True),
        _tool("rename_gameobject", "vrcforge_rename_gameobject", block=project_block, write=True),
    )

    match = search_tool_leaves("project", tools, {})["matches"][0]
    assert match["toolNames"] == ["read_project"]
    assert match["requiredTools"] == []
    assert match["loadCall"] == {"block": project_block, "tools": ["read_project"]}


def test_planning_query_filters_write_tools_before_search(monkeypatch):
    tools = _fx_tools()
    catalog = PlannerCatalogSnapshot(
        visible_tools=tuple(tool for tool in tools if not tool.write),
        routable_tools=tools,
    )

    class Catalog:
        def read(self, exposure_layer, *, project_context_active=True):
            return catalog

    monkeypatch.setattr(dashboard_server, "_RuntimePlannerCatalog", Catalog)
    result = dashboard_server.load_internal_tool_block({
        "sessionId": "discovery-planning-filter",
        "query": "FX animator",
        "tools": [],
        "exposureLayer": "planning",
        "projectContextActive": True,
    })

    match = result["matches"][0]
    assert "manage_fx_animator" not in match["toolNames"]
    assert "manage_fx_animator" not in match["requiredTools"]
    assert all(name != "manage_fx_animator" for name in match["sameLeafToolNames"])


def test_native_reused_projection_only_defines_missing_tools():
    tools = _fx_tools()
    planner = RuntimePlannerService(
        catalog=FakeCatalog(
            planning=PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools)
        ),
        desktop=None,
    )
    step = {
        "tool": "vrcforge_load_internal_tool_block",
        "status": "executed",
        "outcome": {"status": "ok"},
        "result": {
            "ok": True,
            "status": "reused",
            "block": BLOCK,
            "selectedTools": ["preview_fx_animator", "manage_fx_animator"],
            "exposureLayer": "planning",
        },
    }

    complete = planner.native_result_observation(
        step, retained_tool_names={"preview_fx_animator", "manage_fx_animator"}
    )
    assert "loadedToolDefinitions" not in complete

    partial = planner.native_result_observation(
        step, retained_tool_names={"preview_fx_animator"}
    )
    assert [item["name"] for item in partial["loadedToolDefinitions"]] == ["manage_fx_animator"]


def test_repeated_loader_call_reuses_session_without_second_state_mutation(monkeypatch):
    tools = (_tool("scan_fx_animator", "vrcforge_unity_scan_fx_animator"),)
    catalog = PlannerCatalogSnapshot(visible_tools=tools, routable_tools=tools)

    class Catalog:
        def read(self, exposure_layer, *, project_context_active=True):
            return catalog

    monkeypatch.setattr(dashboard_server, "_RuntimePlannerCatalog", Catalog)
    state = dashboard_server.AGENT_GATEWAY.runtime_sessions
    original = type(state).load_internal_tool_block_selected
    mutations = []

    def tracked(owner, session_id, block, selected):
        mutations.append((session_id, block, list(selected)))
        return original(owner, session_id, block, selected)

    monkeypatch.setattr(type(state), "load_internal_tool_block_selected", tracked)
    params = {
        "sessionId": "discovery-reuse-session",
        "block": BLOCK,
        "tools": ["scan_fx_animator"],
        "exposureLayer": "planning",
        "projectContextActive": True,
    }

    first = dashboard_server.load_internal_tool_block(dict(params))
    second = dashboard_server.load_internal_tool_block(dict(params))

    assert first["status"] == "loaded"
    assert second["status"] == "reused"
    assert len(mutations) == 1
    assert second["selectedTools"] == ["scan_fx_animator"]
