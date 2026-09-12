from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import agent_gateway
import unity_tool_schema_projection as projection


def test_gateway_exports_the_same_canonical_projection_functions() -> None:
    for name in ("_with_execution_target_schema", "canonical_unity_read_tool_input_schema", "canonical_unity_write_tool_input_schema"):
        assert getattr(agent_gateway, name) is getattr(projection, name)
    tree = ast.parse(Path(projection.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {
        "__future__", "copy", "typing", "mcp_tool_descriptor", "unity_read_input_schemas", "unity_write_input_schemas",
    }
    assert {item.name for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names} == {"runtime_planner_service"}


def test_projection_never_mutates_shared_nested_schemas() -> None:
    source = projection.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_manage_fx_animator"]
    before = deepcopy(source)
    write = projection.canonical_unity_write_tool_input_schema("vrcforge_manage_fx_animator")
    preview = projection.canonical_unity_read_tool_input_schema("vrcforge_preview_manage_fx_animator")
    assert write == preview
    assert "executionTarget" in write["required"]
    write["properties"]["action"]["enum"].clear()
    write["required"].clear()
    assert source == before
    assert preview == projection.canonical_unity_read_tool_input_schema("vrcforge_preview_manage_fx_animator")


def test_planner_schema_fallback_remains_late_bound(monkeypatch) -> None:
    hint = {"type": "object", "properties": {"projectPath": {"type": "string"}}, "required": ["projectPath"]}
    monkeypatch.setattr(agent_gateway.planner_policy, "planner_tool_input_schema", lambda _name: hint)
    result = agent_gateway.canonical_unity_write_tool_input_schema("newly_registered_tool")
    assert result["required"] == ["projectPath", "executionTarget"]
    assert "executionTarget" not in hint["properties"]


def test_runtime_identity_reads_get_optional_execution_target_projection() -> None:
    for name in ("vrcforge_scan_materials", "vrcforge_scan_animation_bindings"):
        schema = projection.canonical_unity_read_tool_input_schema(name)
        assert schema["additionalProperties"] is False
        assert "executionTarget" in schema["properties"]
        assert "executionTarget" not in schema.get("required", [])


def test_execution_target_bootstrap_remains_unwrapped() -> None:
    for name in (
        "vrcforge_list_execution_targets",
        "vrcforge_bind_execution_target",
    ):
        schema = projection.canonical_unity_read_tool_input_schema(name)
        assert "executionTarget" not in schema.get("properties", {})


def test_refresh_asset_database_exposes_core_parameters() -> None:
    schema = projection.canonical_unity_write_tool_input_schema(
        "vrcforge_refresh_asset_database"
    )
    assert schema["additionalProperties"] is False
    assert {"projectPath", "resolvePackages", "packageResolveTimeoutSeconds"} <= set(
        schema["properties"]
    )
    assert schema["properties"]["resolvePackages"]["default"] is False
    timeout = schema["properties"]["packageResolveTimeoutSeconds"]
    assert timeout["minimum"] == 5
    assert timeout["maximum"] == 300


def test_unitypackage_import_status_exposes_exact_readonly_poll_contract() -> None:
    schema = projection.canonical_unity_read_tool_input_schema(
        "vrcforge_get_unitypackage_import_status"
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"projectPath", "jobId"}
    assert set(schema["properties"]) == {"projectPath", "jobId"}
    assert schema["properties"]["jobId"]["pattern"] == "^[0-9a-fA-F]{32}$"
