from __future__ import annotations

import ast
from pathlib import Path

import agent_gateway
import unity_read_input_schemas
import unity_write_input_schemas


def test_write_schemas_keep_one_owner_and_reviewed_texture_mask_contract() -> None:
    schemas = unity_write_input_schemas.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
    assert agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS is schemas
    for name, schema in schemas.items():
        preview = "vrcforge_preview_" + name.removeprefix("vrcforge_")
        read = unity_read_input_schemas.UNITY_READ_TOOL_INPUT_SCHEMAS.get(preview)
        if read == schema:
            assert read is schema


def test_write_schema_owner_has_no_execution_or_reverse_import() -> None:
    tree = ast.parse(Path(unity_write_input_schemas.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {
        "__future__", "typing", "path_to_skill_controller", "unity_read_input_schemas", "unity_shared_input_schemas", "package_input_schemas", "checkpoint_recovery_input_schemas",
    }
    assert not any(isinstance(node, (ast.Import, ast.FunctionDef, ast.ClassDef)) for node in ast.walk(tree))
