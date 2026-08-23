from __future__ import annotations

from types import SimpleNamespace

import dashboard_server
from agent_gateway import EXTERNAL_MCP_READ_TOOL_BLOCKS, EXTERNAL_MCP_WRITE_TOOL_BLOCKS


def test_project_lifecycle_tools_are_registered_for_external_mcp_execution() -> None:
    write_handlers = dashboard_server.AGENT_GATEWAY._write_handlers
    assert {
        "vrcforge_create_project",
        "vrcforge_register_project",
        "vrcforge_register_project_catalog",
        "vrcforge_select_project",
        "vrcforge_rollback_project_lifecycle",
        "vrcforge_rollback_project_catalog_registration",
    } <= set(write_handlers)
    assert write_handlers["vrcforge_create_project"].risk_level == "medium"
    assert write_handlers["vrcforge_create_project"].pre_write_checkpoint_required is False
    assert write_handlers["vrcforge_register_project"].pre_write_checkpoint_required is False
    assert write_handlers["vrcforge_register_project_catalog"].risk_level == "medium"
    assert write_handlers["vrcforge_register_project_catalog"].pre_write_checkpoint_required is False
    assert write_handlers["vrcforge_select_project"].risk_level == "low"
    assert write_handlers["vrcforge_select_project"].pre_write_checkpoint_required is False
    assert write_handlers["vrcforge_rollback_project_lifecycle"].risk_level == "high"
    assert write_handlers["vrcforge_rollback_project_catalog_registration"].risk_level == "high"

    status_tool = dashboard_server.AGENT_GATEWAY._tools["vrcforge_project_lifecycle_status"]
    plan_tool = dashboard_server.AGENT_GATEWAY._tools["vrcforge_project_create_plan"]
    catalog_status_tool = dashboard_server.AGENT_GATEWAY._tools[
        "vrcforge_project_catalog_registration_status"
    ]
    external_index_tool = dashboard_server.AGENT_GATEWAY._tools[
        "vrcforge_external_tool_blocks"
    ]
    for tool in (status_tool, plan_tool, catalog_status_tool, external_index_tool):
        description = tool.description.lower()
        assert "when-to-use" in description
        assert "when-not-to-use" in description
        assert "negative example" in description

    assert "vrcforge_project_catalog_registration_status" in EXTERNAL_MCP_READ_TOOL_BLOCKS["project"]
    assert {
        "vrcforge_register_project_catalog",
        "vrcforge_rollback_project_catalog_registration",
    } <= EXTERNAL_MCP_WRITE_TOOL_BLOCKS["project"]
    assert "vrcforge_external_tool_blocks" in EXTERNAL_MCP_READ_TOOL_BLOCKS["core"]
    for name in (
        "vrcforge_create_project",
        "vrcforge_register_project",
        "vrcforge_register_project_catalog",
        "vrcforge_select_project",
        "vrcforge_rollback_project_lifecycle",
        "vrcforge_rollback_project_catalog_registration",
    ):
        description = write_handlers[name].description.lower()
        assert "when-to-use" in description
        assert "when-not-to-use" in description
        assert "negative example" in description


def test_external_tool_block_index_is_compact_and_loads_definitions_lazily() -> None:
    index = dashboard_server.AGENT_GATEWAY.external_mcp_tool_block_index()

    assert index["ok"] is True
    assert index["root"] == "external"
    assert index["definitionsLoaded"] is False
    children = {item["block"]: item for item in index["children"]}
    assert {"core", "project", "avatar", "assets", "integrations"} <= set(children)
    assert children["avatar"]["loadWith"] == {
        "method": "tools/list",
        "toolBlocks": ["avatar"],
    }
    assert children["avatar"]["readToolCount"] > 0
    assert children["avatar"]["writeToolCount"] > 0
    integration_children = {
        item["block"]: item for item in children["integrations"]["children"]
    }
    assert {
        "integrations/modular-avatar",
        "integrations/vrcfury",
    } <= set(integration_children)
    assert integration_children["integrations/modular-avatar"]["loadWith"] == {
        "method": "tools/list",
        "toolBlocks": ["integrations/modular-avatar"],
    }
    assert integration_children["integrations/vrcfury"]["writeToolCount"] > 0
    assert "vrcforge_manage_fx_animator" in children["avatar"]["toolNames"]
    assert "vrcforge_set_material_shader" in children["materials"]["toolNames"]
    assert not any("tools" in item for item in index["children"])
    assert "inputSchema" not in str(index)


def test_project_selection_persists_exact_project_and_updates_runtime_state(monkeypatch) -> None:
    selected = r"D:\Unity\AvatarProject"
    previous = dashboard_server.DASHBOARD_STATE.selected_project_path
    previous_instance = dashboard_server.DASHBOARD_STATE.unity_instance
    persisted: list[str] = []
    refreshes: list[bool] = []
    service = SimpleNamespace(
        canonical_selected_project_path=lambda value: selected if value == selected else "",
        load_persisted_selected_project_path=lambda: "",
        persist_selected_project_path=lambda value: persisted.append(value) or value,
        schedule_project_snapshot_refresh=lambda *, force: refreshes.append(force),
    )
    monkeypatch.setattr(dashboard_server, "PROJECT_SNAPSHOT_SELECTION", service)
    dashboard_server.DASHBOARD_STATE.selected_project_path = ""
    dashboard_server.DASHBOARD_STATE.unity_instance = ""
    try:
        result = dashboard_server.select_project_lifecycle_sync({"projectPath": selected})
    finally:
        dashboard_server.DASHBOARD_STATE.selected_project_path = previous
        dashboard_server.DASHBOARD_STATE.unity_instance = previous_instance

    assert result["ok"] is True
    assert result["schema"] == "vrcforge.project_select_result.v1"
    assert result["projectPath"] == selected
    assert result["unityInstance"] == "AvatarProject"
    assert result["mutationStarted"] is True
    assert result["commitState"] == "complete"
    assert persisted == [selected]
    assert refreshes == [True]
