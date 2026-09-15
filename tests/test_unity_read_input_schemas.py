from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import agent_gateway
import unity_read_input_schemas
import unity_shared_input_schemas


def test_read_schemas_keep_single_owner_and_reviewed_revision_159_contract() -> None:
    schemas = unity_read_input_schemas.UNITY_READ_TOOL_INPUT_SCHEMAS
    assert agent_gateway.UNITY_READ_TOOL_INPUT_SCHEMAS is schemas
    # Revision 159 includes the reviewed reader additions and shader paging,
    # removes ignored output options, and requires explicit copy destinations.
    encoded = json.dumps(schemas, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "10e028a58c7d0129faff971bc0dc7c734908a4cc8105a660449ce62d36f8e072"
    assert schemas["vrcforge_preview_texture_import_settings"] is unity_shared_input_schemas.TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA
    assert schemas["vrcforge_preview_manage_fx_animator"] is unity_shared_input_schemas.MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA


def test_read_schema_owner_has_only_forward_dependencies() -> None:
    tree = ast.parse(Path(unity_read_input_schemas.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {
        "__future__", "typing", "external_mcp_tool_blocks", "path_to_skill_controller", "unity_shared_input_schemas", "checkpoint_recovery_input_schemas", "component_property_batch", "package_input_schemas",
    }
    assert not any(isinstance(node, (ast.Import, ast.FunctionDef, ast.ClassDef)) for node in ast.walk(tree))
