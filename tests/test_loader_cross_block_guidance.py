"""Cross-block loader rejections must report the authoritative owner block."""
import dashboard_server
from concurrent.futures import ThreadPoolExecutor


HIERARCHY = "avatar_structure/hierarchy_components"
MESH = "avatar_structure/mesh_shape_data"
ITEMS = "unity_scan_avatar_items"
SHAPES = "unity_scan_blendshapes"


def _execution_leaves():
    return dashboard_server._internal_tool_block_leaves(
        "execution", project_context_active=True
    )


def _load(session_id, block, tools):
    return dashboard_server.load_internal_tool_block({
        "sessionId": session_id,
        "block": block,
        "tools": tools,
        "exposureLayer": "execution",
        "projectContextActive": True,
    })


def test_cross_block_request_reports_authoritative_owner_block_and_retry_hint():
    requested_block = "project_environment/files"
    victim = next(
        item for item in _execution_leaves()
        if item.get("name")
        and item.get("block") not in (requested_block, "core")
        and item.get("mode") == "read"
    )
    victim_name = victim["name"]
    owner_block = victim["block"]

    result = _load("loader-cross-block-guidance-regression", requested_block, [victim_name])

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["errorCode"] == "internal_tool_selection_invalid"
    assert result["mutationStarted"] is False
    assert result["expectedBlock"] == owner_block
    assert result["nextActions"] == [
        f"Retry load_internal_tool_block with block={owner_block} and the exact requested tools."
    ]


def test_unknown_tool_reports_empty_expected_block_and_no_next_actions():
    result = _load(
        "loader-unknown-tool-guidance-regression",
        "project_environment/files",
        ["definitely_not_a_real_vrcforge_tool_name"],
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["errorCode"] == "internal_tool_selection_invalid"
    assert result["mutationStarted"] is False
    assert result["expectedBlock"] == ""
    assert "nextActions" not in result


def test_mixed_avatar_load_returns_independently_executable_leaf_calls():
    selection = [ITEMS, SHAPES, "unknown_tool"]
    for block in (HIERARCHY, MESH):
        result = _load("", block, selection)
        assert result["ok"] is False
        assert result["expectedBlock"] == ""
        assert result["unavailableTools"] == ["unknown_tool"]
        assert result["loadCalls"] == [
            {"name": "load_internal_tool_block", "arguments": {"block": HIERARCHY, "tools": [ITEMS]}},
            {"name": "load_internal_tool_block", "arguments": {"block": MESH, "tools": [SHAPES]}},
        ]
        assert "exact requested tools" not in str(result.get("nextActions"))
        for call in result["loadCalls"]:
            assert _load("", **call["arguments"])["selectedTools"] == call["arguments"]["tools"]
        observation = dashboard_server.RUNTIME_PLANNER.native_result_observation({
            "tool": "vrcforge_load_internal_tool_block", "status": "failed", "result": result,
        }, project_context_active=True)
        assert observation["loadCalls"] == result["loadCalls"]


def test_planning_does_not_suggest_loading_unavailable_write():
    result = dashboard_server.load_internal_tool_block({
        "block": HIERARCHY, "tools": [ITEMS, SHAPES, "unity_apply_blendshapes"],
        "exposureLayer": "planning", "projectContextActive": True,
    })
    assert result["ok"] is False
    assert result["unavailableTools"] == ["unity_apply_blendshapes"]
    assert [call["arguments"]["tools"] for call in result["loadCalls"]] == [[ITEMS], [SHAPES]]


def test_overlapping_loads_report_only_their_own_selection(monkeypatch):
    sessions = dashboard_server.AGENT_GATEWAY.runtime_sessions
    monkeypatch.setattr(type(sessions), "_load_native", lambda *_: None)
    monkeypatch.setattr(type(sessions), "_save_native_selection", lambda *_: None)
    session = "parallel-cross-block-regression"
    preview = "unity_preview_blendshape_apply"
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_load, session, leaf, names) for leaf, names in (
            (MESH, [SHAPES]), (MESH, [SHAPES, preview]), (HIERARCHY, [ITEMS]),
        )]
    assert [f.result()["selectedTools"] for f in futures] == [[SHAPES], [SHAPES, preview], [ITEMS]]
    assert sessions.internal_tool_selections(session) == {
        MESH: sorted([SHAPES, preview]), HIERARCHY: [ITEMS],
    }
