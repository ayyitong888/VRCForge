from __future__ import annotations

import ast
from pathlib import Path

import agent_gateway
import external_mcp_tool_blocks


_EXPORTED_DEFINITIONS = {
    "EXTERNAL_MCP_DEFAULT_TOOL_BLOCK",
    "EXTERNAL_MCP_TOOL_BLOCK_BRANCHES",
    "EXTERNAL_MCP_TOOL_BLOCK_ROOTS",
    "EXTERNAL_MCP_TOOL_BLOCKS",
    "EXTERNAL_MCP_READ_TOOL_BLOCKS",
    "EXTERNAL_MCP_WRITE_TOOL_BLOCKS",
}


def test_tool_blocks_have_one_definition_and_shared_gateway_exports() -> None:
    tree = ast.parse(Path(external_mcp_tool_blocks.__file__).read_text(encoding="utf-8"))
    definition_counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            if name in _EXPORTED_DEFINITIONS:
                definition_counts[name] = definition_counts.get(name, 0) + 1

    assert definition_counts == {name: 1 for name in _EXPORTED_DEFINITIONS}
    for name in _EXPORTED_DEFINITIONS:
        assert getattr(agent_gateway, name) is getattr(external_mcp_tool_blocks, name)


def test_tool_block_membership_preserves_domains_and_read_write_layers() -> None:
    module = external_mcp_tool_blocks
    assert module.EXTERNAL_MCP_DEFAULT_TOOL_BLOCK == "core"
    leaves = {leaf for children in module.EXTERNAL_MCP_TOOL_BLOCK_BRANCHES.values() for leaf in children}
    branch_roots = set(module.EXTERNAL_MCP_TOOL_BLOCK_BRANCHES)
    assert set(module.EXTERNAL_MCP_TOOL_BLOCKS) == (set(module.EXTERNAL_MCP_TOOL_BLOCK_ROOTS) - branch_roots) | leaves
    assert set(module.EXTERNAL_MCP_READ_TOOL_BLOCKS) == set(module.EXTERNAL_MCP_TOOL_BLOCKS)
    assert set(module.EXTERNAL_MCP_WRITE_TOOL_BLOCKS) == set(module.EXTERNAL_MCP_TOOL_BLOCKS) - {"core", "encryption"}

    def memberships(blocks: dict[str, frozenset[str]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for block, names in blocks.items():
            for name in names:
                assert name not in result, f"{name} is assigned to multiple blocks"
                result[name] = block
        return result

    read = memberships(module.EXTERNAL_MCP_READ_TOOL_BLOCKS)
    write = memberships(module.EXTERNAL_MCP_WRITE_TOOL_BLOCKS)

    # These two tools intentionally have read and write projections; all other
    # names stay on one side of the external MCP capability boundary.
    assert set(read) & set(write) == {
        "vrcforge_install_user_unity_tools",
        "vrcforge_invoke_user_unity_tool",
    }

    assert read["vrcforge_preview_save_asset"] == "project"
    assert write["vrcforge_save_asset"] == "project"
    assert read["vrcforge_list_checkpoints"] == "checkpoint"
    assert read["vrcforge_preview_restore_checkpoint"] == "checkpoint"
    assert write["vrcforge_create_project_checkpoint"] == "checkpoint"
    assert write["vrcforge_restore_checkpoint"] == "checkpoint"
    assert read["vrcforge_list_execution_targets"] == "core"
    assert write["vrcforge_select_scene_object"] == "avatar"
