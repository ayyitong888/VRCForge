"""Cross-block loader rejections must report the authoritative owner block."""
import dashboard_server


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
