from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import agent_gateway
import external_mcp_tool_blocks


# Exact pre-extraction definitions, including ordering and memberships.
BASELINE_AST_SHA256 = {
    'EXTERNAL_MCP_DEFAULT_TOOL_BLOCK': '7edd868d2f63e8383a58742e56a1a17bf6325cb5fafd9b9aa475de29a53abbc0',
    'EXTERNAL_MCP_TOOL_BLOCK_BRANCHES': '2ff86db4033cd36d6d68dae15a438cd9a280fd9a99485e36b93a2563aab42dd9',
    'EXTERNAL_MCP_TOOL_BLOCK_ROOTS': '1ce6ef051989a1d3394f7244594809cb0e5d2d3e42f2d4cc4218cacea4218eda',
    'EXTERNAL_MCP_TOOL_BLOCKS': '3aa389653eb4c2f04b76a8b206cd54039353ccb96c38704be9ad8caeff2ed915',
    'EXTERNAL_MCP_READ_TOOL_BLOCKS': 'dd9643d707819deff10a5a854a1bbca3e0d5704af652237aba3c0e8305050007',
    'EXTERNAL_MCP_WRITE_TOOL_BLOCKS': 'bd9bac78acd577411f4e70fd25c7ec151194d25b419a96ce93707f78a3743e4a',
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
        node = definitions[name]
        if name == "EXTERNAL_MCP_WRITE_TOOL_BLOCKS":
            # Reviewed relocation and typed chat repair are additive entries;
            # retain the immutable AST digest for every other membership.
            assert "vrcforge_repair_project_chat_store" in external_mcp_tool_blocks.EXTERNAL_MCP_WRITE_TOOL_BLOCKS["checkpoint"]
            class _RemoveReviewedAdditions(ast.NodeTransformer):
                def visit_Set(self, current):
                    current = self.generic_visit(current)
                    current.elts = [
                        item for item in current.elts
                        if not (
                            isinstance(item, ast.Constant)
                            and item.value in {"vrcforge_relocate_generated_assets", "vrcforge_repair_project_chat_store"}
                        )
                    ]
                    return current

            node = _RemoveReviewedAdditions().visit(node)
        assert hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest() == expected
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import)) for node in ast.walk(tree))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {"__future__"}
    assert len(Path(external_mcp_tool_blocks.__file__).read_bytes()) < 16_000
