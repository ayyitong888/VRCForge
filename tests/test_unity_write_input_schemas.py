from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import agent_gateway
import unity_read_input_schemas
import unity_write_input_schemas


def test_write_schemas_keep_one_owner_and_exact_pre_extraction_values() -> None:
    schemas = unity_write_input_schemas.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
    assert agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS is schemas
    # The new relocation contract must not alter any pre-existing public schema.
    existing = {name: schema for name, schema in schemas.items() if name != "vrcforge_relocate_generated_assets"}
    encoded = json.dumps(existing, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "1cec5367cc2a6f7ccfbe36e64685b789b176b0a2279fc6040c5143ed18fb16bc"
    for name, schema in schemas.items():
        preview = "vrcforge_preview_" + name.removeprefix("vrcforge_")
        read = unity_read_input_schemas.UNITY_READ_TOOL_INPUT_SCHEMAS.get(preview)
        if read == schema:
            assert read is schema


def test_write_schema_owner_has_no_execution_or_reverse_import() -> None:
    tree = ast.parse(Path(unity_write_input_schemas.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {
        "__future__", "typing", "path_to_skill_controller", "unity_read_input_schemas", "unity_shared_input_schemas",
    }
    assert not any(isinstance(node, (ast.Import, ast.FunctionDef, ast.ClassDef)) for node in ast.walk(tree))
    assert len(Path(unity_write_input_schemas.__file__).read_bytes()) < 31_000
