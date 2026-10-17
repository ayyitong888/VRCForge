"""Replay the overlong discovery query without losing read-tool boundaries."""
import pytest

import dashboard_server
from runtime_planner_service import (
    PlannerCatalogSnapshot, PlannerTool, RuntimePlannerService,
    planner_tool_input_schema, validate_planner_tool_arguments,
)
from tests.test_native_runtime_gateway import call
from tool_discovery_search import search_tool_leaves


QUERY = (
    "read only inspect current Unity execution identity loaded avatar scan materials "
    "exact material name semantic properties dissolve readiness without texture dependencies"
)


def _tools():
    return (
        PlannerTool("unity_scan_materials", "Read avatar materials and their semantic properties.",
                    "read", runtime_name="vrcforge_scan_materials",
                    block="appearance/materials_shaders"),
        PlannerTool("unity_avatar_upload_readiness", "Inspect avatar upload readiness.",
                    "read", runtime_name="vrcforge_avatar_upload_readiness",
                    block="diagnostics_build/build_runtime"),
    )


def test_actual_overlong_query_searches_leading_whole_keywords_and_marks_truncation():
    assert len(QUERY) == 167
    result = search_tool_leaves(QUERY, _tools(), {})
    assert result["ok"] and result["status"] == "found"
    assert result["query"] == QUERY.rsplit(" ", 1)[0]
    assert result["queryTruncated"] is True
    assert result["queryLimit"] == 160
    assert result["originalQueryChars"] == 167
    assert result["effectiveQueryChars"] == len(result["query"]) <= 160
    assert result["matches"][0]["toolNames"] == ["unity_scan_materials"]


def test_native_admission_accepts_overlong_query_before_search_normalization():
    schema = planner_tool_input_schema("vrcforge_load_internal_tool_block")
    assert "160" in schema["properties"]["query"]["description"]
    args = {"query": QUERY, "tools": []}
    assert validate_planner_tool_arguments(schema, args)["ok"]
    loader = PlannerTool("load_internal_tool_block", "Load a tool leaf.", "read",
                         runtime_name="vrcforge_load_internal_tool_block", input_schema=schema)
    action, error = RuntimePlannerService._native_action_payload(
        call("actual-overlong-search", "load_internal_tool_block", args), (loader,)
    )
    assert error is None
    assert action["skill_params"] == args
    assert not validate_planner_tool_arguments(schema, {"query": QUERY, "tools": "invalid"})["ok"]


def test_host_overlong_search_remains_read_only_and_returns_original_result_metadata(monkeypatch):
    snapshot = PlannerCatalogSnapshot(visible_tools=_tools(), routable_tools=_tools())

    class Catalog:
        def read(self, *args, **kwargs):
            return snapshot

    monkeypatch.setattr(dashboard_server, "_RuntimePlannerCatalog", Catalog)
    session = "overlong-discovery-read-only"
    state = dashboard_server.AGENT_GATEWAY.runtime_sessions
    before = state.internal_tool_selections(session)
    result = dashboard_server.load_internal_tool_block({
        "sessionId": session, "query": QUERY, "tools": [],
        "projectContextActive": True, "exposureLayer": "planning",
    })
    assert result["ok"] and result["queryTruncated"]
    assert result["matches"][0]["loadCall"] == {
        "block": "appearance/materials_shaders", "tools": ["unity_scan_materials"]
    }
    assert state.internal_tool_selections(session) == before


def test_oversized_first_keyword_falls_back_without_splitting_or_loading():
    result = search_tool_leaves("x" * 161 + " scan materials", _tools(), {})
    assert result["ok"] and result["status"] == "not_found"
    assert result["query"] == ""
    assert result["queryTruncated"]
    assert result["matches"] == [] and result["fallbackCall"] == {"tools": []}


def test_exact_limit_does_not_truncate_valid_keywords():
    query = "scan materials " + "x" * 145
    assert len(query) == 160
    result = search_tool_leaves(query, _tools(), {})
    assert result["query"] == query
    assert not result.get("queryTruncated", False)


@pytest.mark.parametrize("args", [
    {"query": QUERY, "tools": [], "block": "appearance"},
    {"query": QUERY, "tools": ["unity_scan_materials"]},
    {"query": QUERY, "tools": [], "allTools": True},
    {"query": "   ", "tools": []},
])
def test_truncation_does_not_admit_mixed_navigation_or_empty_queries(args):
    result = dashboard_server.load_internal_tool_block(args)
    assert result["ok"] is False
    assert result["mutationStarted"] is False
