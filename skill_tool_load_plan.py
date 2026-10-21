"""Advisory load recipes from an already-read Skill and the visible catalog."""
from __future__ import annotations

from runtime_planner_service import resolve_catalog_tool


def skill_read_loads(result, catalog, *, retained_tool_definitions=None):
    loader = resolve_catalog_tool(catalog.visible_tools, "vrcforge_load_internal_tool_block")
    if loader is None or loader.write or loader.requires_user_activation:
        return []
    grouped = {}
    retained = retained_tool_definitions or {}
    names = result.get("allowedTools")
    if not isinstance(names, list):
        return []
    for name in names:
        if not isinstance(name, str):
            continue
        tool = resolve_catalog_tool(catalog.visible_tools, name)
        if tool is None or tool.write or tool.advanced or tool.requires_user_activation or tool.block == "core":
            continue
        if tool.name in retained:
            continue
        selected = grouped.setdefault(tool.block, [])
        if tool.name not in selected:
            selected.append(tool.name)
    return [{"tool": loader.name, "arguments": {"block": block, "tools": names}}
            for block, names in grouped.items()]
