from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import agent_gateway
import external_mcp_tool_blocks


# Exact pre-extraction definitions, including ordering and memberships.
BASELINE_AST_SHA256 = {
    'EXTERNAL_MCP_DEFAULT_TOOL_BLOCK': '7edd868d2f63e8383a58742e56a1a17bf6325cb5fafd9b9aa475de29a53abbc0',
    'EXTERNAL_MCP_TOOL_BLOCK_BRANCHES': '3f550a38ae319d7f58eca8d5a030ecd30c23a9dedb7883dfb71bfcaa155f5600',
    'EXTERNAL_MCP_TOOL_BLOCK_ROOTS': '1ce6ef051989a1d3394f7244594809cb0e5d2d3e42f2d4cc4218cacea4218eda',
    'EXTERNAL_MCP_TOOL_BLOCKS': '1e4c95bde5dc91b91c996951c51adf7876caf446c3d5926de1f0879a4046fdcb',
    'EXTERNAL_MCP_READ_TOOL_BLOCKS': '0c9607962185effe1ffd2682d83b5d8a8620b6cd801575eb788e36c5b30b7fdc',
    'EXTERNAL_MCP_WRITE_TOOL_BLOCKS': '15a5e19b4e5d25d9af050df66aea59bf5bef2dbb4022b5b1be1bf69130953362',
}


def test_tool_blocks_keep_one_definition_and_existing_exports() -> None:
    tree = ast.parse(Path(external_mcp_tool_blocks.__file__).read_text(encoding="utf-8"))
    definitions = {
        node.target.id if isinstance(node, ast.AnnAssign) else node.targets[0].id: node
        for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
    }
    assert definitions.keys() == BASELINE_AST_SHA256.keys()
    for name, expected in BASELINE_AST_SHA256.items():
        assert getattr(agent_gateway, name) is getattr(external_mcp_tool_blocks, name)
        assert hashlib.sha256(ast.dump(definitions[name], include_attributes=False).encode()).hexdigest() == expected
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import)) for node in ast.walk(tree))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {"__future__"}
    assert len(Path(external_mcp_tool_blocks.__file__).read_bytes()) < 16_000
