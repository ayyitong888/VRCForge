from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import agent_gateway
import unity_tool_schema_projection as projection
from mcp_tool_descriptor import identity_scope, read_runtime_identity_required, standardize_tool_descriptor


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


def test_all_registered_read_identity_decisions_match_descriptor_policy() -> None:
    bootstrap = {
        "vrcforge_list_execution_targets",
        "vrcforge_bind_execution_target",
        "vrcforge_refresh_execution_target",
    }
    runtime_scopes = {"scene", "avatar", "object", "component"}
    names = set(projection.UNITY_READ_TOOL_INPUT_SCHEMAS)
    names.update(
        "vrcforge_preview_" + name.removeprefix("vrcforge_")
        for name in projection.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
    )
    for name in names:
        legacy_decision = name not in bootstrap and identity_scope(name) in runtime_scopes
        assert read_runtime_identity_required(name) == legacy_decision
        assert projection._read_uses_runtime_identity(name) == legacy_decision


def test_descriptor_runtime_requirement_uses_the_same_read_policy() -> None:
    names = set(projection.UNITY_READ_TOOL_INPUT_SCHEMAS)
    names.update(
        "vrcforge_preview_" + name.removeprefix("vrcforge_")
        for name in projection.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
    )
    for name in names:
        descriptor = standardize_tool_descriptor(
            {"name": name, "inputSchema": {"type": "object"}},
            write=False,
        )
        assert descriptor["_meta"]["inputEnvelopeExtension"]["requiredAtRuntime"] == read_runtime_identity_required(name)

    write_descriptor = standardize_tool_descriptor(
        {"name": "vrcforge_set_property", "inputSchema": {"type": "object"}},
        write=True,
    )
    assert write_descriptor["_meta"]["inputEnvelopeExtension"]["requiredAtRuntime"] is True


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
