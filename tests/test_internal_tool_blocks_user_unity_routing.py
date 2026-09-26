from __future__ import annotations

import dashboard_server

from internal_tool_blocks import build_internal_tool_block_tree, canonical_tool_block_description


def _directory_with_user_unity_tools() -> dict[str, object]:
    return build_internal_tool_block_tree(
        loaded_blocks={"core"},
        leaves=[
            {"name": "vrcforge_list_user_unity_tools", "block": "unity/diagnostics", "mode": "read"},
            {"name": "vrcforge_invoke_user_unity_tool", "block": "unity/diagnostics", "mode": "read"},
        ],
    )


def test_directory_api_routes_user_unity_tools_to_diagnostics_compile_leaf() -> None:
    directory = _directory_with_user_unity_tools()
    root = next(item for item in directory["blocks"] if item["name"] == "diagnostics_build")
    leaf = next(item for item in root["children"] if item["name"] == "diagnostics_build/compile_logs")

    assert "user-defined Unity" in root["description"]
    assert "user-defined Unity" in leaf["description"]
    assert leaf["toolNames"] == [
        "vrcforge_invoke_user_unity_tool",
        "vrcforge_list_user_unity_tools",
    ]


def test_compile_logs_execution_description_keeps_approval_boundary() -> None:
    description = canonical_tool_block_description("diagnostics_build/compile_logs")

    assert "Planning: read-only diagnostics" in description
    assert "approved user-defined Unity tool invocation" in description
    assert "Execution: read-only diagnostics." not in description


def test_planning_keeps_user_unity_invocation_write_gated() -> None:
    catalog = dashboard_server._RuntimePlannerCatalog().read("planning")
    visible = {tool.runtime_name: tool for tool in catalog.visible_tools}
    routable = {tool.runtime_name: tool for tool in catalog.routable_tools}

    assert visible["vrcforge_list_user_unity_tools"].write is False
    assert visible["vrcforge_list_user_unity_tools"].block == "diagnostics_build/compile_logs"
    assert "vrcforge_invoke_user_unity_tool" not in visible
    assert routable["vrcforge_invoke_user_unity_tool"].write is True
    assert routable["vrcforge_invoke_user_unity_tool"].block == "diagnostics_build/compile_logs"
