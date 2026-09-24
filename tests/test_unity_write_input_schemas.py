from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import agent_gateway
import unity_read_input_schemas
import unity_write_input_schemas


def test_write_schemas_keep_one_owner_and_reviewed_revision_160_contract() -> None:
    schemas = unity_write_input_schemas.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
    assert agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS is schemas
    # Revision 160 includes reviewed writer schemas, explicit copy destinations,
    # and the existing constraint params/arguments envelope. Relocation remains
    # separately covered and excluded from this reviewed contract digest.
    existing = {name: schema for name, schema in schemas.items() if name != "vrcforge_relocate_generated_assets"}
    encoded = json.dumps(existing, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "03ef055c1b772fc06689a89659611323ee60b5f44bc53c7d6355878efee88888"
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
