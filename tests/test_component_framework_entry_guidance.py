"""Both agent lanes guide plugin configuration toward existing dedicated tools."""
import dashboard_server as server
from agent_builtin_skill_catalog import BUILTIN_SKILL_GROUPS
from runtime_planner_service import planner_tool_usage_description
from tool_usage_contract import tool_usage_description


def test_generic_component_catalog_guides_both_agent_lanes_to_dedicated_entries():
    server.register_agent_gateway_tools()
    for name in ("vrcforge_add_component", "vrcforge_set_property", "vrcforge_get_property"):
        write = name != "vrcforge_get_property"
        registered = (server.AGENT_GATEWAY._write_handlers if write else server.AGENT_GATEWAY._tools)[name]
        external = tool_usage_description(name, registered.description, write=write)
        internal = planner_tool_usage_description(name, registered.description, write=write)
        assert external == internal
        for tool in ("vrcforge_inspect_modular_avatar_component", "vrcforge_preview_add_modular_avatar_component",
                     "vrcforge_add_modular_avatar_component", "vrcforge_scan_vrcfury",
                     "vrcforge_preview_component_feature", "vrcforge_create_component_feature"):
            assert tool in external, (name, tool)
        assert "action=update" in external
        assert "existing" in external and "unsupported" in external


def test_scan_skills_explain_cross_tool_entry_without_gaining_write_permission():
    groups = {g["name"]: g for g in BUILTIN_SKILL_GROUPS}
    for name, preview, writer in (("modular-avatar-toolkit", "vrcforge_preview_add_modular_avatar_component", "vrcforge_add_modular_avatar_component"),
                                  ("vrcfury-toolkit", "vrcforge_preview_component_feature", "vrcforge_create_component_feature")):
        group = groups[name]
        assert group["permissionMode"] == "read_only"
        assert preview in group["instructions"] and writer in group["instructions"]
        assert writer not in group["allowedTools"]
